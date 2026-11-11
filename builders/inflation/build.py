#!/usr/bin/env python3
"""Inflation builder.

1. Headline charts from config.yaml (CPI, CPIH, core; goods, services, food).
2. Short-term inflation forecast (stif.py): CPI for the next six months, built up
   from its 84 components. Writes data/inflation/stif.json (forecast, contributions,
   backtest) and the chart cpi-stif (outturn, our forecast, Bank staff forecast).
3. Energy and non-energy inflation (build_energy_split), which the ONS doesn't publish
   as series: used by the Bank of England's contemporaneous Taylor-type rule.

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
from ukmacro.mm23 import MM23, aggregate, twelve_month_rate  # noqa: E402

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


ENERGY = ("04.5.1", "04.5.2", "04.5.3", "04.5.4", "07.2.2")  # electricity, gas, other fuels, motor fuels


def build_energy_split() -> None:
    """12-month inflation of CPI energy (the ONS definition: electricity, gas, liquid and
    solid fuels, and motor fuels) and of everything else. Each is built from the CPI
    components and their weights with CPI's own January chain-linking (ukmacro/mm23.py),
    so together they add up to headline CPI. Writes data/inflation/cpi-energy-split.json,
    with the energy weight (share of the basket) for each year."""
    data = MM23()
    indices, weights = data.component_data()
    start = "2005-01"
    energy = {k: v for k, v in indices.items() if k in ENERGY}
    other = {k: v for k, v in indices.items() if k not in ENERGY}
    rates = {name: twelve_month_rate(aggregate(group, weights, start, 100.0))
             for name, group in (("energy", energy), ("non_energy", other))}
    headline = data.series("D7G7")
    share = {y: sum(weights[k].get(y, 0) for k in ENERGY) / sum(w.get(y, 0) for w in weights.values())
             for y in range(2005, int(max(headline)[:4]) + 1)}
    months = sorted(m for m in rates["energy"] if m >= "2015-01" and m in rates["non_energy"])
    chart = {
        "slug": "cpi-energy-split", "category": "inflation", "type": "line", "freq": "M",
        "title": "CPI inflation: energy and everything else",
        "units": "% change on a year earlier",
        "source": "ONS consumer price inflation (MM23); Sturfrymacro calculations",
        "note": "Energy is household energy (electricity, gas and other fuels) plus motor fuels, about "
                f"{100 * share[max(share)]:.0f}% of the basket in {max(share)}. Both are rebuilt from CPI components and weights.",
        "series": [{"key": "energy", "label": "Energy"}, {"key": "non_energy", "label": "Everything else"},
                   {"key": "cpi", "label": "All items (ONS)", "color": "neutral"}],
        "energy_weight": {str(y): round(v, 4) for y, v in share.items()},
        "data_through": months[-1], "next_release": data.next_release, "status": "ok",
        "data": [{"date": m, "energy": round(rates["energy"][m], 2), "non_energy": round(rates["non_energy"][m], 2),
                  "cpi": headline.get(m)} for m in months],
    }
    charts.publish(chart, OUT / "cpi-energy-split.json", REPO / "img" / "inflation" / "cpi-energy-split.png")
    print(f"[inflation] cpi-energy-split: through {months[-1]}")


if __name__ == "__main__":
    status = run(HERE / "config.yaml")
    for step in (build_stif, build_energy_split):
        try:
            step()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            status = 1
    sys.exit(status)
