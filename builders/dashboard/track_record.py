#!/usr/bin/env python3
"""Forecast track record: how far out were forecasters for the latest quarter?

For the most recent quarter with outturns for every variable (e.g. Q3 2026), collect
what each forecaster predicted for that quarter in every report published from a year
before it began up to now, and compare with what the ONS then published. Plotting the
forecasts by publication date shows how far out they were, and how fast they homed in
as data for the quarter arrived.

Variables (limited first version):
    GDP growth     quarter on quarter, % (implied by each forecaster's GDP level path)
    CPI inflation  quarterly average of 12-month rates, %
    Unemployment   rate aged 16+, %, three months to the quarter's last month
    Total pay      growth in average weekly earnings, %, year on year (OBR only: the
                   Bank publishes pay growth only for Q4)

Forecasters:
    Bank of England  every MPR in the projections databank, which ships with each
                     report and holds every past report back to 2004, so past
                     vintages need no extra downloads
    OBR              every Economic and fiscal outlook in the window, fetched one by one

Outturns are the ONS figures as they stand now (the first estimate, for a quarter
that has only just ended); GDP in particular will be revised.

Writes data/dashboard/track-record.csv: one row per forecast, with the outturn and the
error (forecast minus outturn; positive = forecast too high). It is a table kept in the
repo for reference only: nothing on the site reads it, and the scheduled refresh does
not run this script.

Run after the labour-markets, gdp and inflation builders: python builders/dashboard/track_record.py
"""

from __future__ import annotations

import csv
import io
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from build import chart_file, q_add, q_label, q_to_month, series  # noqa: E402
from forecasts import _obr_columns  # noqa: E402
from ukmacro import boe_mpr, obr  # noqa: E402

OUT = REPO / "data" / "dashboard"
LOOKBACK_MONTHS = 12  # reports published up to this long before the quarter starts

# variable key -> (title, units)
VARIABLES = {
    "gdp_qoq": ("GDP growth", "% quarter on quarter"),
    "cpi": ("CPI inflation", "% year on year, quarterly average"),
    "unemployment": ("Unemployment rate, aged 16+", "% of the economically active"),
    "pay_total": ("Total pay growth", "% year on year, average weekly earnings"),
}
FORECASTERS = {"boe": "Bank of England", "obr": "OBR"}
COLUMNS = ["quarter", "variable", "units", "forecaster", "vintage", "published",
           "forecast", "outturn", "error", "source_url"]


# ---------------------------------------------------------------- outturns

def _quarter_of(month: str) -> str:
    return f"{month[:4]}-Q{(int(month[5:7]) - 1) // 3 + 1}"


def _last_full_quarter(months: list[str]) -> str:
    """Latest quarter whose three months are all in `months` (YYYY-MM, sorted)."""
    q = _quarter_of(months[-1])
    return q if q_to_month(q, "end") <= months[-1] else q_add(q, -1)


def outturns() -> tuple[str, dict[str, float]]:
    """The latest quarter every variable covers, and the ONS outturn for each."""
    gdp = dict(map(tuple, series(chart_file("gdp", "gdp-growth"), "qoq")))
    cpi = dict(map(tuple, series(chart_file("inflation", "cpi-headline"), "cpi")))
    unemp = dict(map(tuple, series(chart_file("labour-markets", "lm-rates-16plus"), "unemployment")))
    pay = dict(map(tuple, series(chart_file("labour-markets", "pay-growth"), "awe_total")))
    q = min(max(gdp), _last_full_quarter(sorted(cpi)), _last_full_quarter(sorted(unemp)),
            _last_full_quarter(sorted(pay)))
    end = q_to_month(q, "end")
    months = [q_to_month(q, "end")[:5] + f"{int(end[5:]) - k:02d}" for k in (2, 1, 0)]
    return q, {
        "gdp_qoq": gdp[q],
        "cpi": round(sum(cpi[m] for m in months) / 3, 2),  # quarterly average, as forecast
        "unemployment": unemp[end],  # LFS three months to the quarter's last month = the quarter
        "pay_total": pay[end],       # likewise a three-month average dated by its last month
    }


# ---------------------------------------------------------------- forecasts

def _qoq(levels: dict[str, float], q: str) -> float | None:
    """Quarterly growth (%) implied by a level path."""
    prev = q_add(q, -1)
    if q in levels and prev in levels:
        return round(100 * (levels[q] / levels[prev] - 1), 2)
    return None


def _months_between(start: str, end: str) -> list[tuple[int, int]]:
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while f"{y}-{m:02d}" <= end:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def bank_of_england(q: str, since: str) -> list[dict]:
    """The Bank's forecasts for quarter q from every MPR published since `since` (YYYY-MM)."""
    _, zf, url = boe_mpr.latest_report()
    rows = []
    for report in boe_mpr.databank_reports(zf):
        published = datetime.strptime(report, "%b-%y").strftime("%Y-%m")
        if published < since:
            continue
        quarterly, _ = boe_mpr.databank(zf, report)
        get = lambda name: dict(quarterly.get(name, []))  # noqa: E731
        values = {"gdp_qoq": _qoq(get("GDP level"), q),
                  "cpi": get("CPI inflation").get(q),
                  "unemployment": get("Unemployment rate").get(q)}
        label = datetime.strptime(report, "%b-%y").strftime("%B %Y") + " MPR"
        rows += [{"forecaster": "boe", "vintage": label, "published": published, "variable": k,
                  "forecast": v, "source_url": url} for k, v in values.items() if v is not None]
    return rows


def obr_forecasts(q: str, since: str) -> list[dict]:
    """The OBR's forecasts for quarter q from every EFO published since `since`."""
    import openpyxl

    rows = []
    for y, m in _months_between(since, date.today().strftime("%Y-%m")):
        found = obr.efo_economy(y, m)
        if not found:
            continue
        label, raw, url = found
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        gdp = _obr_columns(wb["1.1"], {"gdp": "real gdp"})["gdp"]
        lab = _obr_columns(wb["1.6"], {"unemployment": "unemployment rate",
                                       "pay_total": "average weekly earnings growth"})
        cpi = _obr_columns(wb["1.7"], {"cpi": "cpi"})["cpi"]
        values = {"gdp_qoq": _qoq(dict(map(tuple, gdp)), q),
                  "cpi": dict(map(tuple, cpi)).get(q),
                  "unemployment": dict(map(tuple, lab["unemployment"])).get(q),
                  "pay_total": dict(map(tuple, lab["pay_total"])).get(q)}
        rows += [{"forecaster": "obr", "vintage": f"{label} EFO", "published": f"{y}-{m:02d}",
                  "variable": k, "forecast": round(v, 2), "source_url": url}
                 for k, v in values.items() if v is not None]
    return rows


# ---------------------------------------------------------------- output

def main() -> None:
    q, actual = outturns()
    start = q_to_month(q, "end")
    y, m = int(start[:4]), int(start[5:]) - 2 - LOOKBACK_MONTHS
    while m < 1:
        y, m = y - 1, m + 12
    since = f"{y}-{m:02d}"
    rows = bank_of_england(q, since) + obr_forecasts(q, since)
    for r in rows:
        r["outturn"] = actual[r["variable"]]
        r["error"] = round(r["forecast"] - r["outturn"], 2)  # positive = forecast too high
    rows.sort(key=lambda r: (list(VARIABLES).index(r["variable"]), r["published"], r["forecaster"]))

    # One row per forecast. The table is kept in the repo only: no page on the site reads it.
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "track-record.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "quarter": q_label(q), "variable": VARIABLES[r["variable"]][0],
                        "units": VARIABLES[r["variable"]][1], "forecaster": FORECASTERS[r["forecaster"]]})

    # a quick table in the log
    print(f"[track-record] {q_label(q)}: forecast vs outturn (error = forecast - outturn)")
    for r in rows:
        print(f"  {VARIABLES[r['variable']][0]:<28} {r['vintage']:<20} {r['forecast']:>6.2f} "
              f"vs {r['outturn']:>5.2f}  {r['error']:+.2f}")


if __name__ == "__main__":
    main()
