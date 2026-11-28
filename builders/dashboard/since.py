"""Since the forecast: how the economy has moved away from the OBR's last forecast.

The OBR forecasts only twice a year. In between, data come in and the Bank of England
publishes newer forecasts. This module works out, for each of the economic
assumptions the fiscal ready reckoner (fiscal.html) uses, how far today's picture is
from the OBR's latest forecast, year by year. The fiscal page turns those gaps into
lever settings, and so into a change in borrowing.

For each variable and each quarter, the "updated" path is:

  1. Outturn, where we have it (ONS data; market prices). Comparing with the OBR's own
     figures for those quarters means revisions to past data count as surprises too.
  2. After the latest data, one of four choices (all are written out; the page picks):
       newest   follow the newer forecast: the Bank's, if it was published after the
                OBR's (its path from the latest data point), otherwise the OBR's.
                Variables the Bank doesn't forecast keep today's gap.
       average  halfway between following the Bank and following the OBR
       hold     the surprise so far persists: each variable stays as far from the OBR's
                forecast as it is in the latest data (so levels then grow at the OBR's rates)
       fade     the surprise so far shrinks steadily to nothing over three years
     Beyond the end of the Bank's forecast, the gap stays where the Bank left it.
  3. Each fiscal year's gap is the average over its four quarters (April to March).

How the gap is measured depends on the variable:
  level    GDP and share prices: per cent difference in the level, after setting
           both series equal in a base quarter (they're in different units)
  rate     CPI inflation, unemployment, Bank Rate, gilt yields, oil and gas prices:
           difference in the units shown
  chain    average earnings: the OBR publishes growth on a year earlier, so the level
           gap builds up from the growth gaps, four quarters at a time

Called by build.py after forecasts.json is saved; writes data/dashboard/since.json.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DATA = REPO / "data"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
# what happens after the latest data (labels as shown in the page's dropdown)
OPTIONS = {
    "newest": "follow the newest forecast (the Bank of England's, where newer)",
    "average": "halfway between the Bank's and the OBR's forecasts",
    "hold": "surprises so far persist (the economy stays as far from the OBR's forecast as it is now)",
    "fade": "surprises so far fade away over three years",
}
FADE_QUARTERS = 12


# ---------------------------------------------------------------- quarters
def qi(q: str) -> int:
    """'2026-Q3' -> a running quarter number, so quarters can be added and compared."""
    return int(q[:4]) * 4 + int(q[-1]) - 1


def qs(i: int) -> str:
    return f"{i // 4}-Q{i % 4 + 1}"


def quarter_of(d: str) -> str:
    """'2026-08' or '2026-08-14' -> '2026-Q3'."""
    return f"{d[:4]}-Q{(int(d[5:7]) - 1) // 3 + 1}"


def fy_quarters(fy: str) -> list[int]:
    """'2026-27' -> April-June 2026 to January-March 2027."""
    y = int(fy[:4])
    return [qi(f"{y}-Q2"), qi(f"{y}-Q3"), qi(f"{y}-Q4"), qi(f"{y + 1}-Q1")]


def vintage_date(label: str) -> date:
    """'October 2026 EFO' -> 1 October 2026 (to see which forecast is newer)."""
    m = re.search(r"([A-Za-z]+) (\d{4})", label)
    return date(int(m.group(2)), MONTHS.index(m.group(1).lower()) + 1, 1)


# ---------------------------------------------------------------- outturns
def chart_rows(path: str, key: str) -> list[tuple[str, float]]:
    """(date, value) pairs for one series in one of the builders' chart files."""
    d = json.loads((DATA / path).read_text())
    return [(r["date"], r[key]) for r in d["data"] if r.get(key) is not None]


def quarterly_mean(rows, latest_days: int = 0) -> dict[int, float]:
    """Average within each quarter. For daily market prices, the latest quarter instead
    gets the average of the last `latest_days` observations (10 working days, as the OBR uses for its market assumptions): today's level matters more
    than the average of a part-finished quarter."""
    by: dict[int, list[float]] = {}
    for d, v in rows:
        by.setdefault(qi(quarter_of(d)), []).append(v)
    out = {q: sum(v) / len(v) for q, v in by.items()}
    if latest_days and rows:
        last = rows[-latest_days:]
        out[qi(quarter_of(rows[-1][0]))] = sum(v for _, v in last) / len(last)
    return out


def quarter_end(rows) -> dict[int, float]:
    """Labour market figures are three-month averages dated by their last month: the
    figure for March, June, September or December is that quarter's. Whole quarters only."""
    return {qi(quarter_of(d)): v for d, v in rows if int(d[5:7]) % 3 == 0}


def outturns(gilts_20y) -> dict[str, dict[int, float]]:
    out = {
        "gdp": {qi(d): v for d, v in chart_rows("gdp/gdp-level.json", "gdp")},
        "cpi": quarterly_mean(chart_rows("inflation/cpi-headline.json", "cpi")),
        "unemployment": quarter_end(chart_rows("labour-markets/lm-rates-16plus.json", "unemployment")),
        "earnings": quarter_end(chart_rows("labour-markets/pay-growth.json", "awe_total")),
        "bank_rate": quarterly_mean(chart_rows("markets/bank-rate.json", "bank_rate"), 10),
        "oil": quarterly_mean(chart_rows("markets/oil-prices.json", "brent"), 10),
        "gas": quarterly_mean(chart_rows("markets/gas-price-uk.json", "gas")),
        "equities": quarterly_mean(chart_rows("markets/ftse-100.json", "ftse100"), 10),
    }
    if gilts_20y:
        out["gilt"] = quarterly_mean(gilts_20y, 10)
    return out


def fetch_gilts_20y():
    """20-year nominal par gilt yield, daily (Bank of England), to compare with the OBR's
    long-term interest rate assumption. Returns None if the Bank's database is down."""
    from ukmacro import boe
    try:
        return boe.fetch(["IUDLNPY"], start="01/Jan/2024")["IUDLNPY"]
    except Exception as e:  # noqa: BLE001
        print(f"since: 20-year gilt yields unavailable ({e!r}); gilt gap left out")
        return None


# ---------------------------------------------------------------- the variables
# id, label, unit, how the gap is measured, OBR series, Bank series (or None),
# which outturn, base quarter for 'level' variables (how many quarters before the
# OBR's publication quarter both series are set equal)
VARIABLES = [
    ("gdp", "Real GDP", "%", "level", "gdp_level", "gdp_level", "gdp", 6),
    ("cpi", "CPI inflation", "pt", "rate", "cpi", "cpi", "cpi", None),
    ("unemployment", "Unemployment rate", "pt", "rate", "unemployment", "unemployment", "unemployment", None),
    ("earnings", "Average earnings (level)", "%", "chain", "pay_total", None, "earnings", 6),
    ("bank_rate", "Bank Rate", "pt", "rate", "bank_rate", "bank_rate", "bank_rate", None),
    ("gilt", "Gilt yields (20-year)", "pt", "rate", "gilt_20y", None, "gilt", None),
    ("oil", "Oil price (Brent)", "$/barrel", "rate", "oil", None, "oil", None),
    ("gas", "Gas price (UK wholesale)", "p/therm", "rate", "gas", None, "gas", None),
    # share prices: FTSE 100 against the OBR's FTSE All-Share, set equal just before the forecast
    ("equities", "Share prices", "%", "level", "equities", None, "equities", 1),
]


def series(fc: dict, var: str, who: str) -> dict[int, float]:
    rows = fc.get(var, {}).get(who) or []
    return {qi(q): v for q, v in rows if isinstance(q, str) and "Q" in q}


def gap_path(kind, obr, boe, out, base, horizon_end, option, boe_newer):
    """Quarterly gap (updated path minus the OBR's) from the base quarter to horizon_end.
    'level' and 'chain' gaps are per cent; 'rate' gaps are in the variable's units."""
    known = sorted(q for q in out if q in obr and (base is None or q >= base))
    if not known:
        return {}, None
    last = known[-1]
    gap: dict[int, float] = {}

    # 1. quarters with data
    if kind == "level":
        for q in known:
            gap[q] = 100 * ((out[q] / out[base]) / (obr[q] / obr[base]) - 1)
    elif kind == "rate":
        for q in known:
            gap[q] = out[q] - obr[q]
    else:  # chain: earnings growth on a year earlier -> a level gap, built from the base
        for q in range(base - 3, last + 1):
            if q <= base:
                gap[q] = 0.0
            elif q in out and q in obr:
                gap[q] = 100 * ((1 + gap[q - 4] / 100) * (1 + out[q] / 100) / (1 + obr[q] / 100) - 1)
            else:
                gap[q] = gap[q - 1]
        gap = {q: v for q, v in gap.items() if q >= base}

    # 2. after the latest data
    g0 = gap[last]
    def follow(f):
        """gap if the updated path moves as forecast f does from the latest quarter"""
        res, end = {}, max(f) if f else last
        for q in range(last + 1, horizon_end + 1):
            qq = min(q, end)
            if f is None or last not in f or qq not in f or qq not in obr or last not in obr:
                res[q] = g0
            elif kind == "rate":
                res[q] = g0 + (f[qq] - f[last]) - (obr[qq] - obr[last])
            else:
                res[q] = 100 * ((1 + g0 / 100) * (f[qq] / f[last]) / (obr[qq] / obr[last]) - 1)
        return res
    bank = follow(boe) if (boe and boe_newer) else follow(None)
    held = follow(None)
    for q in range(last + 1, horizon_end + 1):
        if option == "newest":
            gap[q] = bank[q]
        elif option == "average":
            gap[q] = (bank[q] + held[q]) / 2 if (boe and boe_newer) else held[q]
        elif option == "hold":
            gap[q] = held[q]
        else:  # fade
            gap[q] = g0 * max(0.0, 1 - (q - last) / FADE_QUARTERS)
    return gap, last


def obr_by_year(kind, obr, years):
    """The OBR's own forecast for each fiscal year, shown beside the inputs on the page
    so people entering their own figures can see what they are varying from: the
    yearly average for rates; growth on the previous year for levels and earnings."""
    def avg(qq):
        v = [obr[q] for q in qq if q in obr]
        return sum(v) / len(v) if len(v) == 4 else None
    out = []
    for y in years:
        qq = fy_quarters(y)
        if kind == "rate":
            v = avg(qq)
        elif kind == "chain":                  # earnings: average growth on a year earlier
            v = avg(qq)
        else:                                  # levels: growth of the yearly average
            now, before = avg(qq), avg([q - 4 for q in qq])
            v = 100 * (now / before - 1) if now and before else None
        out.append(None if v is None else round(v, 2))
    return {"kind": "rate" if kind == "rate" else "growth", "values": out}


def build(fc: dict, sources: dict) -> dict:
    fiscal = json.loads((DATA / "fiscal" / "reckoner.json").read_text())
    basis = fiscal["bases"]["oct26"]
    years = basis["years"]
    obr_label, boe_label = sources["obr"]["vintage"], sources.get("boe", {}).get("vintage", "")
    # the fiscal page's latest basis must be built on the same OBR forecast
    if obr_label.split(" EFO")[0] not in basis["label"]:
        raise RuntimeError(f"fiscal basis is '{basis['label']}' but forecasts are {obr_label}: "
                           "rerun builders/fiscal/build.py")
    pub = qi(quarter_of(vintage_date(obr_label).isoformat()))
    boe_newer = bool(boe_label) and vintage_date(boe_label) > vintage_date(obr_label)
    horizon_end = max(max(fy_quarters(y)) for y in years)

    outs = outturns(fetch_gilts_20y())
    variables, targets = [], {o: {} for o in OPTIONS}
    for vid, label, unit, kind, obr_key, boe_key, out_key, base_back in VARIABLES:
        obr = series(fc, obr_key, "obr")
        boe = series(fc, boe_key, "boe") if boe_key else {}
        out = outs.get(out_key, {})
        if not obr or not out:
            print(f"since: {vid} skipped (no OBR forecast or no outturn)")
            continue
        base = pub - base_back if base_back else None
        for opt in OPTIONS:
            gap, last = gap_path(kind, obr, boe, out, base, horizon_end, opt, boe_newer)
            targets[opt][vid] = [round(sum(gap.get(q, 0.0) for q in fy_quarters(y)) / 4, 3) for y in years]
        gap, last = gap_path(kind, obr, boe, out, base, horizon_end, "hold", boe_newer)
        latest = {"quarter": qs(last), "gap": round(gap[last], 3)}
        if kind == "rate":
            latest |= {"outturn": round(out[last], 2), "obr": round(obr[last], 2)}
        variables.append({"id": vid, "label": label, "unit": unit,
                          "bank": bool(boe) and boe_newer, "latest": latest,
                          "obr_fy": obr_by_year(kind, obr, years)})

    # unemployment gap -> employment gap (per cent), assuming the labour force is unchanged:
    # employment = labour force x (1 - unemployment rate)
    obr_u = series(fc, "unemployment", "obr")
    for opt in OPTIONS:
        if "unemployment" in targets[opt]:
            u = [sum(obr_u.get(q, 4.5) for q in fy_quarters(y)) / 4 for y in years]
            targets[opt]["employment"] = [round(-du / (1 - ut / 100), 3) for du, ut in zip(targets[opt]["unemployment"], u)]

    return {
        # no build date here: the file only changes (and commits) when the data do
        "basis": "oct26", "years": years,
        "obr": obr_label, "boe": boe_label, "boe_newer": boe_newer,
        "options": OPTIONS, "default": "newest",
        "variables": variables, "targets": targets,
    }
