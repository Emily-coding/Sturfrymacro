#!/usr/bin/env python3
"""Check that assets/reckoner.js reproduces the OBR's own spreadsheet.

For a set of scenarios (none, single shocks, and random mixes of every input), this
  1. calculates the OBR workbook itself with the `formulas` package (an Excel
     formula engine), reading the change in every tax and spending line and in the
     aggregates (receipts, spending, borrowing, debt, and the same as % of GDP);
  2. runs assets/reckoner.js on the same scenario with the basis that workbook.py
     extracts from that workbook;
  3. reports the largest difference.

Developer tool, not part of the build: needs `pip install formulas` and macOS (it
runs the JavaScript with the system's JavaScript for Automation, `osascript`).

    python builders/fiscal/check_engine.py path/to/ready-reckoner.xlsx
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
import workbook  # noqa: E402

JXA = r"""
ObjC.import('Foundation');
function read(p) { return $.NSString.stringWithContentsOfFileEncodingError(p, $.NSUTF8StringEncoding, null).js; }
function run(argv) {
  (0, eval)(read(argv[0]));                       // defines Reckoner
  const job = JSON.parse(read(argv[1]));
  return JSON.stringify(job.scenarios.map(s => {
    const r = Reckoner.compute(job.basis, s);
    return {lines: r.lines, bn: r.bn, pct_gdp: r.pct_gdp};
  }));
}
"""


def scenarios(input_ids: list[str], n_years: int) -> list[dict]:
    rng = random.Random(42)
    unit_size = {"oil_price": 10, "gas_price": 20, "electricity_price": 10, "usd_gbp": 0.1, "unemployment": 0.2}
    rates = {"rpi_inflation", "cpi_inflation", "bank_rate", "deposit_rate", "gilt_rate", "earnings_growth_q2",
             "earnings_growth_q3", "cpi_q3", "rpi_q2"}

    def draw(i):
        size = unit_size.get(i, 1.0 if i in rates else 2.0)
        return [round(rng.uniform(-size, size), 2) for _ in range(n_years)]

    out = [
        {},                                                     # nothing changes
        {"earnings": [1.0] * n_years},                          # earnings 1% higher throughout
        {"cpi_inflation": [2.0] * n_years, "cpi_q3": [2.0] * n_years, "rpi_inflation": [2.0] * n_years},
        {"earnings_growth_q2": [3.0] * n_years},                # triple lock goes to earnings
        {"gilt_rate": [1.0] * n_years, "bank_rate": [1.0] * n_years},
        {"employment": [1.0] * n_years, "unemployment": [-0.3] * n_years},
    ]
    for _ in range(4):                                          # every input at once, random sizes
        out.append({i: draw(i) for i in input_ids})
    return out


def reference(path: str, cases: list[dict], col_of: dict[str, str], years: int) -> list[dict]:
    """Evaluate the workbook for each scenario; return line and aggregate changes."""
    import formulas

    book = Path(path).name
    model = formulas.ExcelModel().loads(path).finish()
    key = lambda sheet, cell: f"'[{book}]{sheet.upper()}'!{cell}"  # noqa: E731
    wb = openpyxl.load_workbook(path, data_only=True)
    rs = wb["Receipts_and_spending"]
    names = {r: rs.cell(r, 1).value.strip() for r in range(4, 49) if rs.cell(r, 1).value}
    cols = [openpyxl.utils.get_column_letter(11 + k) for k in range(years)]  # K.. = changes
    out = []
    for case in cases:
        inputs = {}
        for i, letter in col_of.items():
            for t in range(years):
                inputs[key("SCENARIO_INPUTS", f"{letter}{5 + t}")] = float((case.get(i) or [0] * years)[t])
        sol = model.calculate(inputs=inputs)
        val = lambda sheet, cell: float(sol[key(sheet, cell)].value[0][0])  # noqa: E731
        res = {"lines": {}, "agg": {}}
        for r, name in names.items():
            res["lines"][name] = [val("RECEIPTS_AND_SPENDING", f"{c}{r}") for c in cols]
        for label, row in [("receipts", 3), ("spending", 6), ("borrowing", 9), ("debt", 24)]:
            res["agg"][label] = [val("AGGREGATES", f"{c}{row}") for c in cols]
        for label, row in [("receipts_pct", 4), ("spending_pct", 7), ("borrowing_pct", 10), ("debt_pct", 25)]:
            res["agg"][label] = [val("AGGREGATES", f"{c}{row}") for c in cols]
        out.append(res)
    return out


def main(path: str) -> None:
    raw = Path(path).read_bytes()
    basis = workbook.parse(raw)
    n = len(basis["years"])
    si = openpyxl.load_workbook(path)["Scenario_inputs"]
    # input id -> Scenario_inputs column, skipping the calculated columns (one of which
    # is also headed "Gas price")
    col_of = {workbook.INPUTS[si.cell(4, c).value]: si.cell(4, c).column_letter
              for c in range(2, si.max_column + 1)
              if si.cell(4, c).value in workbook.INPUTS and si.cell(4, c).column_letter not in workbook.CALCULATED}
    cases = scenarios(list(col_of), n)

    with tempfile.TemporaryDirectory() as tmp:
        job, script = Path(tmp) / "job.json", Path(tmp) / "run.js"
        job.write_text(json.dumps({"basis": basis, "scenarios": cases}))
        script.write_text(JXA)
        got = json.loads(subprocess.run(
            ["osascript", "-l", "JavaScript", str(script), str(REPO / "assets" / "reckoner.js"), str(job)],
            check=True, capture_output=True, text=True).stdout)
    want = reference(path, cases, col_of, n)

    worst = 0.0
    for k, (g, w) in enumerate(zip(got, want)):
        diffs = []
        for name, exp in w["lines"].items():
            if name == "Central government debt interest (net of APF)":
                mine = g["lines"]["debt_interest"]
            else:
                mine = g["lines"]["receipts"].get(name) or g["lines"]["spending"].get(name) or [0] * n
            if name in ("Public sector current receipts", "Total managed expenditure", "Primary receipts",
                        "Primary spending", "Other current primary expenditure", "Other capital expenditure"):
                continue  # totals: checked via the aggregates below
            diffs += [(abs(a - b), name, t) for t, (a, b) in enumerate(zip(mine, exp))]
        for label, mine in [("receipts", g["bn"]["receipts"]), ("spending", g["bn"]["spending"]),
                            ("borrowing", g["bn"]["borrowing"]), ("debt", g["bn"]["debt"]),
                            ("receipts_pct", g["pct_gdp"]["receipts"]), ("spending_pct", g["pct_gdp"]["spending"]),
                            ("borrowing_pct", g["pct_gdp"]["borrowing"]), ("debt_pct", g["pct_gdp"]["debt"])]:
            diffs += [(abs(a - b), label, t) for t, (a, b) in enumerate(zip(mine, w["agg"][label]))]
        top = max(diffs)
        if top[0] > 1e-6:
            print("   ", sorted({(round(d, 3), nm) for d, nm, _ in diffs if d > 1e-6 and not nm.endswith("pct")}, reverse=True)[:8])
        worst = max(worst, top[0])
        print(f"scenario {k}: largest difference {top[0]:.2e} ({top[1]}, {basis['years'][top[2]]}); "
              f"borrowing change {', '.join(f'{z:+.2f}' for z in w['agg']['borrowing'])}")
    print("OK" if worst < 1e-6 else "MISMATCH", f"(largest difference {worst:.2e}, £ billion or % of GDP)")
    sys.exit(0 if worst < 1e-6 else 1)


if __name__ == "__main__":
    main(sys.argv[1])
