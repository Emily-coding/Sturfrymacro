"""Short-term inflation forecast (STIF): CPI inflation for the next six months, built
bottom-up from its 84 components, in the style of the OBR's short-term forecast
(https://articles.obr.uk/the-obrs-cpi-inflation-modelling/).

The idea: over a six-month horizon much of what happens to prices is already known
or follows a regular calendar (seasonal sales, annual price resets in April, last
year's base effects). So we project each component's month-on-month price change,
then add the components back up with their CPI weights.

Steps
1. Data: every CPI component index and weight from the ONS MM23 file (ukmacro/mm23.py).
2. Baseline for each component: its usual change in that calendar month plus its
   current trend:
       forecast m/m change = seasonal pattern for that month + recent trend
   - seasonal pattern: over the last SEASONAL_YEARS years (skipping 2020-21, when
     lockdowns broke normal patterns), how much that month's change differed from
     that year's average monthly change.
   - recent trend: the average monthly change over the last 12 months.
3. Energy: electricity and gas (Ofgem price cap) and motor fuels follow the Bank of
   England's latest staff projections, which build in the announced price cap and
   market prices for oil and gas, the way the OBR conditions on market prices and
   the cap. Beyond the Bank's horizon they are held flat.
4. Known policy changes (duty rises, a different-than-usual April reset...) can be
   set by hand in stif_assumptions.yaml; they replace the baseline for that month.
5. Add up: components are combined with CPI's January chain-linking, and only the
   *change* from the latest published month is used, applied to the published
   headline index (this avoids small rounding errors in the component indices).

backtest() re-runs the forecast as if made in each past month, using only data
available then (energy from the seasonal baseline too, so it is a tougher test than
the live forecast faces), and compares it with what actually happened.
"""

from __future__ import annotations

import statistics
from pathlib import Path

import yaml

from ukmacro.mm23 import aggregate, next_month, twelve_month_rate

HORIZON = 6
SEASONAL_YEARS = 8
SKIP_YEARS = {2020, 2021}
ELECTRICITY_GAS = ("04.5.1", "04.5.2")
FUELS = "07.2.2"
ASSUMPTIONS = Path(__file__).with_name("stif_assumptions.yaml")

# Broad groups for showing what drives the forecast (COICOP code prefixes)
GROUPS = {
    "Energy": ("04.5", "07.2.2"),
    "Food and non-alcoholic drinks": ("01",),
    "Everything else": ("",),  # whatever isn't matched above
}


# ---------------------------------------------------------------- building blocks

def mm(index: dict[str, float]) -> dict[str, float]:
    """Month-on-month % change of an index."""
    keys = list(index)
    return {b: (index[b] / index[a] - 1) * 100 for a, b in zip(keys, keys[1:])}


def baseline(index: dict[str, float], last: str, months: list[str]) -> dict[str, float]:
    """Seasonal-plus-trend projection of one component's m/m % change for `months`,
    using data up to and including `last`."""
    changes = {m: v for m, v in mm(index).items() if m <= last}
    y_last = int(last[:4])
    # seasonal pattern: each month's deviation from its year's average monthly change
    years = [y for y in range(y_last - SEASONAL_YEARS, y_last + 1) if y not in SKIP_YEARS]
    devs: dict[int, list] = {}
    for y in years:
        yr = {int(m[5:]): v for m, v in changes.items() if m.startswith(f"{y}-")}
        if len(yr) == 12:  # only complete calendar years
            avg = sum(yr.values()) / 12
            for mo, v in yr.items():
                devs.setdefault(mo, []).append(v - avg)
    seasonal = {mo: sum(v) / len(v) for mo, v in devs.items()}
    # recent trend: average monthly change over the last 12 months
    year_ago = next_month(last, -12)
    trend = ((index[last] / index[year_ago]) ** (1 / 12) - 1) * 100 if year_ago in index else 0.0
    return {m: seasonal.get(int(m[5:]), 0.0) + trend for m in months}


def extend(index: dict[str, float], changes: dict[str, float]) -> dict[str, float]:
    """Index carried forward by m/m % changes."""
    out = dict(index)
    for m in sorted(changes):
        out[m] = out[next_month(m, -1)] * (1 + changes[m] / 100)
    return out


def implied_mm(index: dict[str, float], annual_rates: list[tuple[str, float]], months: list[str]) -> dict[str, float]:
    """Turn a projected 12-month inflation path into m/m changes for this index:
    the level in month t is the level 12 months earlier times (1 + rate). Months
    without a projected rate are held flat."""
    rates = dict(annual_rates)
    path, out = dict(index), {}
    for m in months:
        prev = path[next_month(m, -1)]
        year_ago = path.get(next_month(m, -12))
        level = year_ago * (1 + rates[m] / 100) if m in rates and year_ago else prev
        out[m] = (level / prev - 1) * 100
        path[m] = level
    return out


def combined(indices: dict[str, dict[str, float]], weights: dict[str, dict[int, float]],
             codes: tuple[str, ...]) -> dict[str, float]:
    """Weighted average of a few components (e.g. electricity and gas), as an index."""
    common = sorted(set.intersection(*(set(indices[c]) for c in codes)))
    out = {}
    for m in common:
        ws = [weights[c].get(int(m[:4])) or 0 for c in codes]
        out[m] = sum(w * indices[c][m] for w, c in zip(ws, codes)) / sum(ws)
    return out


def load_assumptions() -> dict:
    return yaml.safe_load(ASSUMPTIONS.read_text()) or {} if ASSUMPTIONS.exists() else {}


# ---------------------------------------------------------------- the forecast

def forecast(indices, weights, headline: dict[str, float], last: str,
             energy: dict[str, list] | None = None, assumptions: dict | None = None,
             horizon: int = HORIZON) -> dict:
    """Project every component `horizon` months beyond `last` and add up.

    energy: Bank of England projected 12-month rates, {'Electricity and gas': [...],
            'Fuels and lubricants': [...]}; None = use the seasonal baseline.
    Returns {'index': {month: headline index}, 'rate': {month: 12-month %},
             'contributions': {group: {month: percentage points}}, 'components': {...}}.
    """
    months = [next_month(last, k) for k in range(1, horizon + 1)]
    # next year's weights aren't published until February: carry the latest ones forward
    end_year = int(months[-1][:4])
    weights = {c: {**w, **{y: w[max(w)] for y in range(max(w) + 1, end_year + 1)}} for c, w in weights.items() if w}
    trimmed = {c: {m: v for m, v in s.items() if m <= last} for c, s in indices.items()}
    changes = {c: baseline(s, last, months) for c, s in trimmed.items() if last in s}

    if energy:
        if "Electricity and gas" in energy:
            eg = implied_mm(combined(trimmed, weights, ELECTRICITY_GAS), energy["Electricity and gas"], months)
            for c in ELECTRICITY_GAS:
                changes[c] = eg
        if "Fuels and lubricants" in energy:
            changes[FUELS] = implied_mm(trimmed[FUELS], energy["Fuels and lubricants"], months)

    for o in (assumptions or {}).get("overrides") or []:  # hand-set policy changes
        if o["component"] in changes and o["month"] in months:
            changes[o["component"]][o["month"]] = float(o["change"])

    paths = {c: extend(trimmed[c], changes[c]) for c in changes}

    # Add up from the January before the latest month, then apply the change since
    # `last` to the published headline index
    start = f"{last[:4]}-01" if last[5:] != "01" else f"{int(last[:4]) - 1}-01"
    agg = aggregate(paths, weights, start, 100.0)
    index = {m: v for m, v in headline.items() if m <= last}
    for m in months:
        index[m] = headline[last] * agg[m] / agg[last]
    rate = {m: round(v, 2) for m, v in twelve_month_rate(index).items() if m in months}

    # Contributions to the 12-month rate by broad group (approximate: weighted
    # 12-month changes, scaled so they add up to the headline forecast)
    contributions = {g: {} for g in GROUPS}
    for m in months:
        y = int(m[:4])
        raw = {g: 0.0 for g in GROUPS}
        for c, p in paths.items():
            w = weights[c].get(y) or 0
            prev = p.get(next_month(m, -12))
            if prev:
                group = next(g for g, pre in GROUPS.items() if any(c.startswith(x) for x in pre))
                raw[group] += w / 1000 * (p[m] / prev - 1) * 100
        total = sum(raw.values())
        for g in GROUPS:
            contributions[g][m] = round(raw[g] * rate[m] / total, 2) if total else None
    return {"index": index, "rate": rate, "contributions": contributions, "changes": changes}


# ---------------------------------------------------------------- backtest

def backtest(indices, weights, headline: dict[str, float], first: str = "2017-01",
             last: str | None = None) -> dict:
    """Mean absolute error (percentage points of the 12-month rate) by horizon, for
    the STIF and for a 'no change' benchmark (inflation stays at its latest rate).
    Forecasts made in 2020-21 are left out."""
    actual = twelve_month_rate(headline)
    last = last or next_month(max(headline), -1)
    errors = {h: {"stif": [], "no_change": []} for h in range(1, HORIZON + 1)}
    m = first
    while m <= last:
        if int(m[:4]) not in SKIP_YEARS:
            f = forecast(indices, weights, headline, m)
            for h in range(1, HORIZON + 1):
                t = next_month(m, h)
                if t in actual and t in f["rate"]:
                    errors[h]["stif"].append(abs(f["rate"][t] - actual[t]))
                    errors[h]["no_change"].append(abs(actual[m] - actual[t]))
        m = next_month(m)
    return {h: {k: round(statistics.mean(v), 2) for k, v in e.items()} | {"n": len(e["stif"])}
            for h, e in errors.items() if e["stif"]}


if __name__ == "__main__":
    # Print the component codes and names, for writing overrides in stif_assumptions.yaml
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from ukmacro.mm23 import MM23
    for code, name in MM23().components().items():
        print(f"{code:8} {name}")
