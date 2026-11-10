#!/usr/bin/env python3
"""Inflation builder.

1. Headline charts from config.yaml (CPI, CPIH, core; goods, services, food).
2. Short-term inflation forecast (stif.py): CPI for the next six months, built up
   from its 84 components. Writes data/inflation/stif.json (forecast, contributions,
   backtest) and the chart cpi-stif (outturn, our forecast, Bank staff forecast).

If the forecast fails, the headline charts still publish and the previous forecast
is kept, but the run exits 1 so it shows red in GitHub Actions.
"""

import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))  # repo root, so `ukmacro` imports

from ukmacro import boe_mpr, charts  # noqa: E402
from ukmacro.builder import run  # noqa: E402
from ukmacro.mm23 import MM23  # noqa: E402

import stif  # noqa: E402

OUT = REPO / "data" / "inflation"


def build_stif() -> None:
    data = MM23()
    indices, weights = data.component_data()
    headline = data.series("D7BT")  # CPI all-items index, 2015 = 100
    last = max(headline)

    # Energy conditioning: Bank of England staff projections, if we can get them
    energy, energy_note = None, "Energy from the seasonal baseline (Bank of England projections unavailable)."
    boe_cpi = {}
    try:
        label, zf, _ = boe_mpr.latest_report()
        energy = {k: v for k, v in boe_mpr.near_term_components(zf).items()
                  if k in ("Electricity and gas", "Fuels and lubricants")}
        boe_cpi = dict(boe_mpr.near_term(zf)["cpi"])
        energy_note = f"Electricity, gas and motor fuels follow Bank of England staff projections ({label} MPR)."
    except Exception as e:  # noqa: BLE001
        print(f"stif: Bank of England projections unavailable ({e}); energy uses the baseline", file=sys.stderr)

    assumptions = stif.load_assumptions()
    f = stif.forecast(indices, weights, headline, last, energy=energy, assumptions=assumptions)

    # The backtest takes ~30 seconds, so only redo it when there's a new month of data
    path = OUT / "stif.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    by_horizon = old.get("backtest", {}).get("by_horizon") if old.get("data_through") == last else None
    if not by_horizon:
        by_horizon = stif.backtest(indices, weights, headline)

    result = {
        "data_through": last,
        "next_release": data.next_release,
        "forecast": [{"month": m, "cpi": f["rate"][m],
                      "contributions": {g: f["contributions"][g][m] for g in stif.GROUPS}} for m in f["rate"]],
        "energy": energy_note,
        "overrides": assumptions.get("overrides") or [],
        "backtest": {"note": "Mean absolute error in the 12-month CPI rate (percentage points) of forecasts "
                             "made each month since 2017 (excluding 2020-21), using only data available then "
                             "and no energy information. 'no_change' assumes inflation stays at its latest rate.",
                     "by_horizon": by_horizon},
    }
    path.write_text(json.dumps(result, indent=1) + "\n")

    # Chart: outturn since 2024, our forecast, and Bank staff's for comparison.
    # Both forecast lines start from the latest outturn so they join the data.
    actual = {m: round(v, 1) for m, v in data.series("D7G7").items() if m >= "2024-01"}
    rows = []
    for m in sorted(set(actual) | set(f["rate"])):
        row = {"date": m}
        if m in actual:
            row["cpi"] = actual[m]
        if m in f["rate"] or m == last:
            row["stif"] = round(f["rate"].get(m, actual.get(m)), 1)
        if boe_cpi and (m in boe_cpi or m == last):
            row["boe"] = round(boe_cpi.get(m, actual.get(m)), 1)
        rows.append(row)
    chart = {
        "slug": "cpi-stif", "category": "inflation", "type": "line", "freq": "M",
        "title": "CPI inflation: short-term forecast",
        "units": "% change on a year earlier",
        "source": "ONS; Bank of England; Sturfrymacro short-term inflation forecast",
        "note": "Our forecast adds up projections for 84 CPI components (seasonal pattern plus recent trend). " + energy_note,
        # Bank of England in amber and ours in teal, matching the dashboard's Bank colour
        "series": [{"key": "cpi", "label": "CPI inflation (ONS)", "no_end_label": True},
                   {"key": "stif", "label": "Our forecast", "dash": True, "slot": 2},
                   {"key": "boe", "label": "Bank of England staff", "dash": True, "slot": 1}],
        "data_through": last, "next_release": data.next_release, "status": "ok", "data": rows,
    }
    charts.publish(chart, OUT / "cpi-stif.json", REPO / "img" / "inflation" / "cpi-stif.png")
    print(f"[inflation] stif: {', '.join(f'{m} {v:.1f}%' for m, v in f['rate'].items())}")


if __name__ == "__main__":
    status = run(HERE / "config.yaml")
    try:
        build_stif()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        status = 1
    sys.exit(status)
