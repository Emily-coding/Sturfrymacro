"""Read the OBR ready reckoner workbook into plain data the browser can compute with.

The OBR's spreadsheet (https://obr.uk/download/economic-and-fiscal-outlook-ready-reckoner/)
works in four steps, and this module pulls out what each step needs:

1. Scenario inputs. The user types differences from the OBR's forecast for ~33
   economic "determinants" (earnings, employment, CPI inflation, house prices...), by
   fiscal year. A few more are calculated from those (the CPI price level from CPI
   inflation, the triple lock from earnings and inflation...).
2. Determinant changes. Each tax and spending line looks at a particular measure of
   those changes, sometimes a year later (benefits are uprated by last September's
   CPI, so a CPI shock in one year moves spending the next) or rescaled (the oil
   price is read in $10 steps). We record each as a "change vector": which input,
   scaled by how much, shifted by how many years.
3. Effects. Every effect cell in the workbook is (one determinant change) x (one
   elasticity, £ million per 1% or 1 point). We record each cell as a "term":
   which tax or spending line, which year it lands in, which change vector, which
   year of it, and the elasticity. The whole fiscal effect is the sum of the terms.
   Benefit uprating and debt interest are matrices (a shock in one year changes
   spending in every later year), which the terms represent naturally.
4. Aggregation. Terms add up to tax and spending lines, then to receipts, spending,
   borrowing and debt, including a "financing loop": extra debt interest is itself
   borrowed and so costs more interest.

Because the terms are read from the workbook's own formulas, they reproduce the
spreadsheet cell for cell; builders/fiscal/check_engine.py tests that.
"""

from __future__ import annotations

import io
import re

import openpyxl
from openpyxl.utils import column_index_from_string as col_num

# Scenario_inputs headers (row 4) -> short ids used in the browser.
INPUTS = {
    "Earnings level": "earnings",
    "Mixed income": "mixed_income",
    "Employment": "employment",
    "Private dwelling investment": "dwelling_investment",
    "Nominal consumer spending": "nominal_consumption",
    "Real consumer spending": "real_consumption",
    "Consumption of durables": "durables",
    "Non-oil, non-financial profits": "profits_nonfinancial",
    "Private non-financial corporations' GFCF": "pnfc_investment",
    "Financial profits": "profits_financial",
    "Real GDP": "real_gdp",
    "Non-seasonally adjusted nominal GDP": "nominal_gdp_fy",
    "Calendar year nominal GDP": "nominal_gdp_cy",
    "Equity prices": "equity_prices",
    "House prices": "house_prices",
    "Property transactions": "property_transactions",
    "Imports": "imports",
    "Personal deposits": "personal_deposits",
    "Oil price": "oil_price",
    "Gas price": "gas_price",
    "Wholesale electricity price": "electricity_price",
    "Dollar/sterling exchange rate": "usd_gbp",
    "RPI inflation": "rpi_inflation",
    "CPI inflation": "cpi_inflation",
    "Bank Rate": "bank_rate",
    "Deposit rate": "deposit_rate",
    "Long-term interest rates": "gilt_rate",
    "March-centred GDP": "nominal_gdp_march",
    "Unemployment (millions)": "unemployment",
    "Q2 average earnings growth": "earnings_growth_q2",
    "Q3 average earnings growth": "earnings_growth_q3",
    "Q3 CPI inflation": "cpi_q3",
    "Q2 RPI inflation": "rpi_q2",
}
# Columns the workbook calculates from the inputs (Scenario_inputs AI:AQ, "Calculated").
CALCULATED = {
    "AI": "gas_price_pct", "AJ": "cpi_q3_floor", "AK": "cpi_q3_cap", "AL": "earnings_growth_fy",
    "AM": "triple_lock", "AN": "employment_millions", "AO": "rpi_index", "AP": "cpi_index",
    "AQ": "rpi_q2_index",
}
# Determinants sheet columns -> ids, for the baseline paths the calculations need.
BASELINE = {
    "B": "earnings", "D": "employment", "M": "nominal_gdp_fy", "U": "gas_price",
    "X": "rpi_level", "Y": "rpi_inflation", "Z": "cpi_level", "AA": "cpi_inflation",
    "AE": "nominal_gdp_march", "AG": "earnings_growth_q2", "AH": "earnings_growth_q3",
    "AI": "cpi_q3", "AJ": "rpi_q2_level", "AK": "rpi_q2", "AL": "cpi_q3_floor",
    "AM": "cpi_q3_cap", "AN": "earnings_growth_fy", "AO": "triple_lock",
}

REF = r"(?:'[^']+'|\w+)!\$?([A-Z]+)\$?(\d+)"  # Sheet!$C$5 -> (column, row)


def _formula(cell) -> str | None:
    """A cell's formula as text (array formulas included), or None for values."""
    v = cell.value
    if hasattr(v, "text"):  # array formula, e.g. {=TRANSPOSE(...)}
        return v.text
    return v if isinstance(v, str) and v.startswith("=") else None


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def parse(raw: bytes) -> dict:
    """The workbook as a 'basis' dict (see build.py for the layout)."""
    wf = openpyxl.load_workbook(io.BytesIO(raw))                  # formulas
    wv = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)  # cached values

    # ---- years: effects run over Receipts_RR£ C2:I2; baselines start a year earlier
    rr = wv["Receipts_RR£"]
    years = [str(rr.cell(2, c).value) for c in range(3, 3 + 7)]
    det = wv["Determinants"]
    det_years = [str(det.cell(r, 1).value) for r in range(5, 13)]
    assert det_years[1:] == years, (det_years, years)

    # ---- inputs: column letter -> id (by header text, so column moves are caught)
    si = wf["Scenario_inputs"]
    input_col = {}
    for c in range(2, si.max_column + 1):
        head = si.cell(4, c).value
        letter = si.cell(4, c).column_letter
        if letter in CALCULATED:
            input_col[letter] = CALCULATED[letter]
        elif head in INPUTS:
            input_col[letter] = INPUTS[head]
        elif head:
            raise LookupError(f"unknown Scenario_inputs column {letter}: {head!r}")
    missing = set(INPUTS.values()) - set(input_col.values())
    if missing:
        raise LookupError(f"Scenario_inputs is missing {sorted(missing)}")
    input_year_row = {r: years.index(str(si.cell(r, 1).value)) for r in range(5, 12)}

    # ---- baseline determinants (2022-23 onwards, so price levels can be chained)
    baseline = {name: [_num(det[f"{col}{r}"].value) for r in range(5, 13)] for col, name in BASELINE.items()}

    # ---- step 2: change vectors from the three determinant sheets
    vectors, vector_at = [], {}  # vector_at[(sheet, row)] -> index

    def read_vectors(sheet: str, first_year_col: int = 2):
        ws = wf[sheet]
        for r in range(3, ws.max_row + 1):
            label = ws.cell(r, 1).value
            anchor = next(((c, _formula(ws.cell(r, c))) for c in range(2, 10) if _formula(ws.cell(r, c))), None)
            if not label or not anchor:
                continue
            c0, f = anchor
            if "Aggregates!K12" in f:  # debt interest: change in the primary deficit (£ billion)
                vectors.append({"label": label, "source": "primary_deficit", "factor": 1.0, "shift": 0})
            else:
                m = re.search(r"Scenario_inputs!\$?([A-Z]+)\$?(\d+):\$?[A-Z]+\$?(\d+)", f)
                src, start = m.group(1), int(m.group(2))
                factor = 1.0
                for op, k in re.findall(r"([/*])\s*(\d+(?:\.\d+)?)", f[m.end():]):
                    factor = factor / float(k) if op == "/" else factor * float(k)
                # first year the vector fills (anchor column) vs first input year it reads
                shift = (c0 - first_year_col) - input_year_row[start]
                vectors.append({"label": label, "source": input_col[src], "factor": factor, "shift": shift})
            vector_at[(sheet, r)] = len(vectors) - 1

    for sheet in ("Receipts_determinants", "Spending_determinants", "Debt_interest_determinants"):
        read_vectors(sheet)

    # ---- step 3: effect terms
    terms, loop = [], []
    ref_pair = re.compile(rf"^=\(?{REF}\*{REF}\)?(?:/(\d+(?:\.\d+)?))?$")

    def year_of_effect_col(c: int, first: int) -> int:
        return c - first

    def read_terms(sheet, det_sheet, rr_sheet, first_col, stream_of, block):
        ws, rrv = wf[sheet], wv[rr_sheet]
        det_first = 2  # determinant sheets: years start in column B
        for row in ws.iter_rows(min_row=1):
            for cell in row:
                f = _formula(cell)
                # only the seven year columns hold effects (column K repeats stream names)
                if not f or not first_col <= cell.column < first_col + 7 or "SUM" in f:
                    continue
                m = ref_pair.match(f.replace("$", ""))
                if not m:
                    raise ValueError(f"{sheet}!{cell.coordinate}: unexpected formula {f}")
                dcol, drow, rcol, rrow, div = m.groups()
                coef = _num(rrv[f"{rcol}{rrow}"].value) / (float(div) if div else 1.0)
                if coef == 0:
                    continue
                terms.append({
                    "block": block, "stream": stream_of(cell.row),
                    "e": year_of_effect_col(cell.column, first_col),
                    "v": vector_at[(det_sheet, int(drow))],
                    "d": col_num(dcol) - det_first,
                    "coef": coef,
                })

    rcpt = wv["Receipts_effects"]
    read_terms("Receipts_effects", "Receipts_determinants", "Receipts_RR£", 3,
               lambda r: rcpt.cell(r, 2).value, "receipts")
    spnd = wv["Spending_effects"]
    read_terms("Spending_effects", "Spending_determinants", "Spending_RR£", 4,
               lambda r: spnd.cell(r, 11).value, "spending")

    # debt interest: ordinary terms in rows 6-33, the financing loop below them
    ws, rrv = wf["Debt_interest_effects"], wv["Debt_interest_RR"]
    for row in ws.iter_rows(min_row=6):
        for cell in row:
            f = _formula(cell)
            if not f or not 3 <= cell.column < 10:
                continue
            if f.startswith("=SUM("):
                m = re.match(r"=SUM\(\$?([A-Z]+)\$?6:\$?[A-Z]+\$?33\)/1000\*" + REF + "$", f)
                src_col, rcol, rrow = m.groups()
                coef = _num(rrv[f"{rcol}{rrow}"].value) / 1000
                if coef:
                    loop.append({"e": col_num(rcol) - 3, "d": col_num(src_col) - 3, "coef": coef})
                continue
            m = ref_pair.match(f.replace("$", ""))
            dcol, drow, rcol, rrow, _ = m.groups()
            coef = _num(rrv[f"{rcol}{rrow}"].value)
            if coef:
                terms.append({"block": "debt", "stream": "Central government debt interest (net of APF)",
                              "e": cell.column - 3, "v": vector_at[("Debt_interest_determinants", int(drow))],
                              "d": col_num(dcol) - 2, "coef": coef})

    # ---- step 4: baseline lines (£ billion) and aggregates
    rs = wv["Receipts_and_spending"]
    lines = {}
    for r in range(3, 54):
        name = rs.cell(r, 1).value
        if name:
            lines[name.strip()] = [_num(rs.cell(r, c).value) for c in range(2, 9)]
    ag = wv["Aggregates"]
    aggregates = {
        "psnfl": [_num(ag.cell(21, c).value) for c in range(2, 9)],
        "psnd": [_num(ag.cell(24, c).value) for c in range(2, 9)],
        "psnd_ex_boe": [_num(ag.cell(27, c).value) for c in range(2, 9)],
        "current_deficit": [_num(ag.cell(15, c).value) for c in range(2, 9)],
    }
    # welfare lines that make up "Welfare spending" (Spending_by_stream rows 6-12)
    sbs = wv["Spending_by_stream"]
    welfare = [sbs.cell(r, 1).value for r in range(6, 13)]
    assert "Universal credit" in welfare and "State pensions" in welfare, welfare
    notes = wv["Notes"]["E1"].value or ""
    return {
        "label": "March 2025 EFO (as published by the OBR)",
        "source_note": " ".join(str(notes).split()),
        "years": years,
        "base_year": det_years[0],
        "baseline": baseline,
        "vectors": vectors,
        "terms": terms,
        "loop": loop,
        "lines": lines,
        "welfare_streams": welfare,
        "aggregates": aggregates,
        # the workbook fixes the triple lock for its first two years (already set)
        "triple_lock_fixed_years": 2,
        # the workbook's scenario triple lock uses financial-year CPI inflation
        "triple_lock_cpi": "cpi_inflation",
        # first-year index chaining: the workbook's 2022-23 levels are not shocked
    }
