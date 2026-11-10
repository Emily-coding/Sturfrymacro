"""Collect the latest published forecasts into one tidy dictionary.

Sources (all free):
- Bank of England, latest Monetary Policy Report: central projections plus Bank staff
  near-term projections (see ukmacro/boe_mpr.py).
- Average of outside forecasters, surveyed by the Bank and published with the MPR.
- OBR, latest Economic and fiscal outlook: quarterly paths from its detailed
  economy tables.

Each variable maps forecaster -> list of [period, value]. Periods are 'YYYY-Qn',
'YYYY-MM' or a year (int), as published; build.py turns them into chart lines.
"""

from __future__ import annotations

import io
import re

from ukmacro import boe_mpr, obr


# ---------------------------------------------------------------- Bank of England

def bank_of_england() -> tuple[dict, dict]:
    """Returns (source info, {variable: {forecaster: [[period, value], ...]}})."""
    label, zf, url = boe_mpr.latest_report()
    quarterly, annual = boe_mpr.databank(zf)
    near = boe_mpr.near_term(zf)
    others = boe_mpr.other_forecasters(zf)

    data = {
        # GDP level index (2008 = 100): only its shape is used, rescaled onto ONS data
        "gdp_level": {"boe": quarterly["GDP level"]},
        "gdp_qoq": {"boe": near["gdp_qoq"], "ext": others.get("gdp_qoq", [])},
        "gdp_yoy": {"ext": others["gdp_yoy"]},  # four-quarter growth at Q4
        "cpi": {"boe": quarterly["CPI inflation"], "ext": others["cpi"]},
        "cpi_monthly": {"boe": near["cpi"]},
        "unemployment": {"boe": quarterly["Unemployment rate"], "ext": others["unemployment"]},
        "unemployment_monthly": {"boe": near["unemployment"]},
        # annual only: calendar-year growth, and growth in the year to Q4
        "business_investment_annual": {"boe": annual["Business investment"]},
        "pay_private_regular_q4": {"boe": annual["Average weekly earnings (private sector regular pay) (Q4)"]},
        # market-implied Bank Rate path the projections are conditioned on (quarterly averages)
        "bank_rate": {"boe": quarterly["Bank Rate"]},
    }
    sources = {
        "boe": {"name": "Bank of England", "vintage": f"{label} MPR", "url": url},
        "ext": {"name": "Outside forecasters' average", "vintage": f"Bank survey, {label} MPR", "url": url},
    }
    return sources, data


# ---------------------------------------------------------------- OBR

def _obr_columns(ws, wanted: dict[str, str]) -> dict[str, list]:
    """Read named columns from one OBR table. `wanted` maps our name -> words in the
    OBR column header. Quarter labels ('2026Q3') sit in the first non-empty column;
    header text is matched loosely because the OBR's wording shifts a little."""
    rows = list(ws.iter_rows(values_only=True))
    # header cells may wrap over two rows, so join the first few rows' text per column
    width = max(len(r) for r in rows[:6])
    headers = [" ".join(str(r[j]) for r in rows[:6] if j < len(r) and r[j] is not None).lower()
               for j in range(width)]
    cols = {}
    for name, words in wanted.items():
        matches = [j for j, h in enumerate(headers) if words.lower() in h]
        if not matches:
            raise LookupError(f"OBR {ws.title}: no column matching {words!r}")
        cols[name] = matches[0]
    out = {name: [] for name in wanted}
    for r in rows:
        q = next((c for c in r if c is not None), None)
        m = re.fullmatch(r"(\d{4})Q([1-4])", str(q).strip()) if q is not None else None
        if not m:
            continue
        for name, j in cols.items():
            if j < len(r) and isinstance(r[j], (int, float)):
                out[name].append([f"{m[1]}-Q{m[2]}", float(r[j])])
    return out


def obr_forecast() -> tuple[dict, dict]:
    """Returns (source info, {variable: {'obr': [[quarter, value], ...]}})."""
    import openpyxl

    label, raw, url = obr.latest_efo_economy()
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    gdp = _obr_columns(wb["1.1"], {"gdp_level": "real gdp", "business_investment_level": "business investment"})
    lab = _obr_columns(wb["1.6"], {"employment_rate": "employment rate", "unemployment": "unemployment rate",
                                   "pay_total": "average weekly earnings growth"})
    infl = _obr_columns(wb["1.7"], {"cpi": "cpi"})  # first CPI column is the year-on-year rate
    # Market-derived assumptions (quarterly averages of futures prices and market
    # expectations at the OBR's cut-off). Equity prices are the FTSE All-Share index;
    # gas is in £ per therm, converted here to pence to match Ofgem's figures.
    mkt = _obr_columns(wb["1.9"], {"bank_rate": "bank rate", "usd": "us$/£", "eur": "€/£",
                                   "oil": "oil prices", "gas": "gas prices", "equities": "equity prices"})
    mkt["gas"] = [[q, v * 100] for q, v in mkt["gas"]]
    data = {k: {"obr": v} for k, v in {**gdp, **lab, **infl, **mkt}.items()}
    return {"obr": {"name": "OBR", "vintage": f"{label} EFO", "url": url}}, data


def collect() -> tuple[dict, dict, list[str]]:
    """Run every source; one failing doesn't stop the others.
    Returns (sources, merged data, list of failures)."""
    sources, data, failures = {}, {}, []
    for name, fn in [("Bank of England", bank_of_england), ("OBR", obr_forecast)]:
        try:
            s, d = fn()
        except Exception as e:  # noqa: BLE001
            failures.append(f"{name}: {e}")
            continue
        sources.update(s)
        for var, by_forecaster in d.items():
            data.setdefault(var, {}).update(by_forecaster)
    return sources, data, failures
