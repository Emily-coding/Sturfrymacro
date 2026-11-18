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
3. Energy, in layers (each one replaces the one before where it has information):
   a. the Bank of England's latest staff projections for electricity, gas and fuels
      (conditioned on futures prices at the Bank's cut-off);
   b. electricity and gas: the Ofgem price cap. CPI electricity and gas move almost
      one-for-one with the cap in the month it changes (Jan, Apr, Jul, Oct) and very
      little in between, so for every month covered by an announced cap we use the
      cap's percentage change (and no change in the other months). Before Ofgem
      announces a cap, a projection can be set by hand in stif_assumptions.yaml;
   c. motor fuels: CPI fuels track the monthly average of the government's weekly
      pump prices (65% petrol, 35% diesel) very closely. Weeks already published are
      used as they are; later weeks hold the price before tax at its latest level and
      add fuel duty (with any scheduled change in stif_assumptions.yaml) and VAT.
      That is the standard "oil follows a random walk" assumption.
4. Easter: airfares jump in April when Easter falls late (on or after 12 April, so
   the April price collection lands in the school holidays) and not when it falls
   early. For airfares in March-May, the seasonal pattern uses only past years with
   Easter on the same side of that line (all years since 2010).
5. The policy calendar (stif_assumptions.yaml): known or assumed changes such as duty
   rises, rail fares and regulated bills, each either replacing the baseline for that
   month or adding to it, plus price cap projections and the fuel duty schedule.
6. Add up: components are combined with CPI's January chain-linking, and only the
   *change* from the latest published month is used, applied to the published
   headline index (this avoids small rounding errors in the component indices).

backtest() re-runs the forecast as if made in each past month, using only data
available then, and compares it with what actually happened. It is run twice: with no
energy information at all (energy from the seasonal baseline), and with the price
caps and pump prices that were known on the day (a forecast using data to month t is
made around the 17th of month t+1, when caps starting up to month t+2 have been
announced and two weeks of month t+1's pump prices are out). The Bank's projections
are not used in the backtest.
"""

from __future__ import annotations

import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # repo root, so `ukmacro` imports
from ukmacro.mm23 import aggregate, next_month, twelve_month_rate  # noqa: E402

HORIZON = 6
SEASONAL_YEARS = 8
SKIP_YEARS = {2020, 2021}
ELECTRICITY_GAS = ("04.5.1", "04.5.2")
CAP_FUEL = {"04.5.1": "electricity", "04.5.2": "gas"}
FUELS = "07.2.2"
PETROL_SHARE = 0.65  # petrol's share of CPI motor fuels: the split that best matches CPI since 2010
# Oct 2022 - Jun 2023: the government's Energy Price Guarantee, not the cap, set bills
EPG_MONTHS = {"2022-10", "2022-11", "2022-12", "2023-01", "2023-02", "2023-03", "2023-04", "2023-05", "2023-06"}
AIRFARES = "07.3.3"
EASTER_MONTHS = (3, 4, 5)
LATE_EASTER = (4, 12)  # Easter Sunday on or after 12 April counts as late
EASTER_FIRST_YEAR = 2010
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


def easter(y: int) -> date:
    """Easter Sunday in year y (the standard Gregorian calendar calculation)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return date(y, month, (h + l - 7 * m + 114) % 31 + 1)


def late_easter(y: int) -> bool:
    return easter(y) >= date(y, *LATE_EASTER)


def seasonal_deviations(changes: dict[str, float], years) -> dict[int, list[float]]:
    """For each calendar month, how far that month's change was from its year's
    average monthly change, in each of `years` (complete calendar years only)."""
    devs: dict[int, list] = {}
    for y in years:
        yr = {int(m[5:]): v for m, v in changes.items() if m.startswith(f"{y}-")}
        if len(yr) == 12:
            avg = sum(yr.values()) / 12
            for mo, v in yr.items():
                devs.setdefault(mo, []).append(v - avg)
    return devs


def baseline(index: dict[str, float], last: str, months: list[str], code: str = "") -> dict[str, float]:
    """Seasonal-plus-trend projection of one component's m/m % change for `months`,
    using data up to and including `last`."""
    changes = {m: v for m, v in mm(index).items() if m <= last}
    y_last = int(last[:4])
    # seasonal pattern: each month's deviation from its year's average monthly change
    years = [y for y in range(y_last - SEASONAL_YEARS, y_last + 1) if y not in SKIP_YEARS]
    devs = seasonal_deviations(changes, years)
    seasonal = {mo: sum(v) / len(v) for mo, v in devs.items()}
    # recent trend: average monthly change over the last 12 months
    year_ago = next_month(last, -12)
    trend = ((index[last] / index[year_ago]) ** (1 / 12) - 1) * 100 if year_ago in index else 0.0
    out = {m: seasonal.get(int(m[5:]), 0.0) + trend for m in months}

    if code == AIRFARES:  # March-May: only past years with Easter on the same side of 12 April
        for m in months:
            y, mo = int(m[:4]), int(m[5:])
            if mo in EASTER_MONTHS:
                alike = [x for x in range(EASTER_FIRST_YEAR, y_last + 1)
                         if x not in SKIP_YEARS and late_easter(x) == late_easter(y)]
                d = seasonal_deviations(changes, alike).get(mo)
                if d:
                    out[m] = sum(d) / len(d) + trend
    return out


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


# ---------------------------------------------------------------- energy layers

def cap_mm(caps: dict[str, dict[str, float]], months: list[str],
           projected: list[dict] | None = None) -> dict[str, dict[str, float]]:
    """Month-on-month % changes in CPI electricity and gas implied by the price cap,
    for the forecast months the cap covers: {'04.5.1': {month: %}, '04.5.2': {...}}.

    caps: Ofgem cap levels by start month (ukmacro.energy.price_cap), only those
          announced by the time of the forecast.
    projected: hand-set % changes for caps not yet announced, from
          stif_assumptions.yaml: [{'month': '2027-01', 'electricity': -1.0, 'gas': 2.0}].
          Ignored for any month Ofgem has already announced.

    In a cap's first month the change is the cap's own % change; in its other
    months, no change. Months after the last known cap period are left out, so
    the layer below (Bank of England or baseline) stays in charge there. Caps run
    for three months (six before October 2022)."""
    levels = {m: dict(v) for m, v in caps.items() if m not in EPG_MONTHS}
    for p in projected or []:  # turn hand-set % changes into levels, after the last real cap
        m = str(p["month"])
        if m in levels:
            continue
        prev = max((k for k in levels if k < m), default=None)
        if prev and all(f in p for f in ("electricity", "gas")):
            levels[m] = {f: levels[prev][f] * (1 + float(p[f]) / 100) for f in ("electricity", "gas")}
    starts = sorted(levels)
    out: dict[str, dict[str, float]] = {c: {} for c in CAP_FUEL}
    for m in months:
        start = max((s for s in starts if s <= m), default=None)
        if start is None:
            continue
        length = 3 if start >= "2022-10" else 6
        if m > next_month(start, length - 1):
            continue  # beyond the last known cap period
        prev = max((s for s in starts if s < start), default=None)
        for code, fuel in CAP_FUEL.items():
            if m == start:
                # a cap after a gap (e.g. the first after the price guarantee) has nothing to compare with
                if prev and next_month(prev, 3 if prev >= "2022-10" else 6) == start:
                    out[code][m] = (levels[start][fuel] / levels[prev][fuel] - 1) * 100
            else:
                out[code][m] = 0.0
    return out


def fuel_mm(pumps: list[dict], last: str, months: list[str], cutoff: str | None = None,
            duty_changes: list[dict] | None = None) -> dict[str, float]:
    """Month-on-month % changes in CPI motor fuels for `months`, from weekly pump prices.

    Each month's price is the average of that month's weekly prices (65% petrol,
    35% diesel). Weeks dated up to `cutoff` (default: all) are used as published.
    Later weeks keep the latest price before duty and VAT, then add back duty (the
    latest rate, or a scheduled change from duty_changes: [{'from': '2027-03-23',
    'pence_per_litre': 57.95}]) and VAT at the latest rate."""
    weeks = [w for w in pumps if cutoff is None or w["date"] <= cutoff]
    if not weeks:
        return {}
    latest = weeks[-1]
    vat = 1 + latest["vat"] / 100
    pre_tax = {f: latest[f] / vat - latest[f + "_duty"] for f in ("petrol", "diesel")}
    schedule = sorted((str(d["from"]), float(d["pence_per_litre"])) for d in duty_changes or [])

    def duty(day: str, fuel: str) -> float:
        rate = latest[fuel + "_duty"]
        for start, r in schedule:
            if start <= day:
                rate = r
        return rate

    def blend(w):
        return PETROL_SHARE * w["petrol"] + (1 - PETROL_SHARE) * w["diesel"]

    prices = {w["date"]: blend(w) for w in weeks}
    # extend week by week (the government's prices are for Mondays) to the end of the horizon
    day = date.fromisoformat(latest["date"]) + timedelta(days=7)
    end = next_month(months[-1])
    while day.isoformat()[:7] < end:
        d = day.isoformat()
        prices[d] = blend({f: (pre_tax[f] + duty(d, f)) * vat for f in ("petrol", "diesel")})
        day += timedelta(days=7)

    monthly: dict[str, list] = {}
    for d, v in prices.items():
        monthly.setdefault(d[:7], []).append(v)
    avg = {m: sum(v) / len(v) for m, v in monthly.items()}
    return {m: (avg[m] / avg[next_month(m, -1)] - 1) * 100
            for m in months if m in avg and next_month(m, -1) in avg}


# ---------------------------------------------------------------- the forecast

def forecast(indices, weights, headline: dict[str, float], last: str,
             energy: dict[str, list] | None = None, assumptions: dict | None = None,
             horizon: int = HORIZON, caps: dict | None = None, pumps: list | None = None,
             pump_cutoff: str | None = None) -> dict:
    """Project every component `horizon` months beyond `last` and add up.

    energy: Bank of England projected 12-month rates, {'Electricity and gas': [...],
            'Fuels and lubricants': [...]}; None = use the seasonal baseline.
    caps:   Ofgem cap levels known at the time (ukmacro.energy.price_cap); None = not used.
    pumps:  weekly pump prices (ukmacro.energy.pump_prices); weeks after pump_cutoff
            are treated as unknown. None = not used.
    Returns {'index': {month: headline index}, 'rate': {month: 12-month %},
             'contributions': {group: {month: percentage points}}, 'components': {...}}.
    """
    months = [next_month(last, k) for k in range(1, horizon + 1)]
    # next year's weights aren't published until February: carry the latest ones forward
    end_year = int(months[-1][:4])
    weights = {c: {**w, **{y: w[max(w)] for y in range(max(w) + 1, end_year + 1)}} for c, w in weights.items() if w}
    trimmed = {c: {m: v for m, v in s.items() if m <= last} for c, s in indices.items()}
    changes = {c: baseline(s, last, months, c) for c, s in trimmed.items() if last in s}
    assumptions = assumptions or {}
    sources = {}  # where each energy component's forecast came from, for the notes

    if energy:
        if "Electricity and gas" in energy:
            eg = implied_mm(combined(trimmed, weights, ELECTRICITY_GAS), energy["Electricity and gas"], months)
            for c in ELECTRICITY_GAS:
                changes[c] = dict(eg)
                sources[c] = "Bank of England"
        if "Fuels and lubricants" in energy:
            changes[FUELS] = implied_mm(trimmed[FUELS], energy["Fuels and lubricants"], months)
            sources[FUELS] = "Bank of England"

    if caps:  # price cap: replaces the layer below for every month an announced (or hand-set) cap covers
        announced = cap_mm(caps, months)  # without hand-set projections, to say which is which
        for c, by_month in cap_mm(caps, months, assumptions.get("price_cap")).items():
            if c in changes and by_month:
                changes[c].update(by_month)
                real = max(announced[c], default=None)
                sources[c] = (f"Ofgem price cap to {real}" if real else "") + \
                    (f"{', then ' if real else ''}our price cap assumption to {max(by_month)}"
                     if max(by_month) != real else "")
    if pumps and FUELS in changes:
        f = fuel_mm(pumps, last, months, pump_cutoff, assumptions.get("fuel_duty"))
        if f:
            changes[FUELS].update(f)
            sources[FUELS] = f"pump prices to {min(pump_cutoff or pumps[-1]['date'], pumps[-1]['date'])}"

    for o in assumptions.get("overrides") or []:  # hand-set policy changes
        c, m = o["component"], str(o["month"])
        if c in changes and m in months:
            if "change" in o:  # replace the baseline
                changes[c][m] = float(o["change"])
            elif "add" in o:  # on top of the baseline
                changes[c][m] += float(o["add"])

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
    return {"index": index, "rate": rate, "contributions": contributions, "changes": changes,
            "sources": sources}


# ---------------------------------------------------------------- backtest

def backtest(indices, weights, headline: dict[str, float], first: str = "2017-01",
             last: str | None = None, caps: dict | None = None, pumps: list | None = None) -> dict:
    """Mean absolute error (percentage points of the 12-month rate) by horizon, for
    the STIF and for a 'no change' benchmark (inflation stays at its latest rate).
    Forecasts made in 2020-21 are left out.

    With caps and pumps, a second set of forecasts ('stif_energy') also uses what was
    known on the day: caps starting up to two months after the latest data, and pump
    prices up to the 16th of the following month."""
    actual = twelve_month_rate(headline)
    last = last or next_month(max(headline), -1)
    keys = ["stif", "no_change"] + (["stif_energy"] if caps or pumps else [])
    errors = {h: {k: [] for k in keys} for h in range(1, HORIZON + 1)}
    m = first
    while m <= last:
        if int(m[:4]) not in SKIP_YEARS:
            runs = {"stif": forecast(indices, weights, headline, m)}
            if "stif_energy" in keys:
                known = {k: v for k, v in (caps or {}).items() if k <= next_month(m, 2)}
                runs["stif_energy"] = forecast(indices, weights, headline, m, caps=known, pumps=pumps,
                                               pump_cutoff=f"{next_month(m)}-16")
            for h in range(1, HORIZON + 1):
                t = next_month(m, h)
                if t in actual and t in runs["stif"]["rate"]:
                    for k, f in runs.items():
                        errors[h][k].append(abs(f["rate"][t] - actual[t]))
                    errors[h]["no_change"].append(abs(actual[m] - actual[t]))
        m = next_month(m)
    return {h: {k: round(statistics.mean(v), 2) for k, v in e.items()} | {"n": len(e["stif"])}
            for h, e in errors.items() if e["stif"]}


if __name__ == "__main__":
    # Print the component codes and names, for writing overrides in stif_assumptions.yaml
    from ukmacro.mm23 import MM23
    for code, name in MM23().components().items():
        print(f"{code:8} {name}")
