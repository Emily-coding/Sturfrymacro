"""Rescale the OBR ready reckoner (March 2025 EFO) to the latest EFO.

What can and cannot be updated
------------------------------
The elasticities come from HMRC and DWP models that only the OBR can rerun, so they
stay as published. What we can update is everything they are applied to:

* Taxes. The OBR's effects are proportional to the size of each tax (a 1% rise in
  earnings raises PAYE by about 1.35% of PAYE receipts). So each tax's effects are
  scaled by (latest forecast for that tax) / (March 2025 forecast for that tax), year
  by year. This keeps the per cent elasticities and applies them to today's tax bases.
  What it cannot capture is a change in a tax's *structure* since March 2025 (e.g.
  frozen thresholds making income tax more responsive to earnings).
* Welfare. The reckoner splits welfare into categories that do not line up with the
  published tables, so all welfare effects are scaled by total welfare spending.
* Other spending lines are scaled by their own latest forecast, except unfunded public
  service pensions, which is a small net figure (pensions paid less contributions)
  whose ratio would be meaningless: those effects are left as published.
* Debt interest. The OBR publishes a debt interest ready reckoner with every EFO (a
  permanent 1 point rise in gilt yields, Bank Rate or inflation, and £10bn more
  borrowing each year). The workbook's year-by-year matrices keep their shape, and are
  scaled so a permanent shock reproduces the latest published table exactly.
* Baselines (GDP, prices, earnings, employment, debt...) come from the latest EFO, so
  shares of GDP, price levels and the triple lock are calculated from today's forecast.
* Years. The latest forecast runs two years beyond the workbook (to 2031-32). Those
  years reuse the final workbook year's elasticities, scaled to the latest forecast.

One deliberate change from the workbook: the triple lock uses September (Q3) CPI
inflation, as the legislation does, rather than financial-year CPI inflation.
"""

from __future__ import annotations

import io
import re

import openpyxl

from ukmacro import obr
from ukmacro.http import get

# Reckoner tax lines -> rows of the EFO's Table A.5 (or table 4.1 for two small ones).
RECEIPT_LINES = {
    "PAYE income tax": ["Pay as you earn"],
    "SA income tax": ["Self assessment"],
    "NICs": ["National Insurance contributions"],
    "VAT": ["Value added tax"],
    "Onshore corporation tax": ["Onshore"],
    "Offshore corporation tax": ["Offshore"],
    "Fuel duties": ["Fuel duties"],
    "Business rates": ["Business rates"],
    "Capital gains tax": ["Capital gains tax"],
    "Inheritance tax": ["Inheritance tax"],
    "Stamp taxes": ["Property transaction taxes", "Stamp taxes on shares"],
    "Tobacco duties": ["Tobacco duties"],
    "Alcohol duties": ["Alcohol duties"],
    "Air passenger duty": ["Air passenger duty"],
    "Insurance premium tax": ["Insurance premium tax"],
    "Climate change levy": ["Climate change levy"],
    "Vehicle excise duty": ["Vehicle excise duties"],
    "Energy profits levy": ["Energy profits levy"],
    "Electricity generators levy": ["Electricity generator levy"],
    "Licence fee": ["Licence fee receipts"],
    "Interest and dividend receipts": ["Interest and dividends"],
}
RECEIPT_LINES_41 = {"Betting and gaming duties": "Betting and gaming taxes", "Customs duties": "Customs duties"}
# Reckoner spending lines -> rows of Table A.7
SPENDING_LINES = {
    "PSCE in RDEL": "PSCE in RDEL",   # departmental spending: used by the 'held in real terms' switch
    "PSGI in CDEL": "PSGI in CDEL",
    "Welfare spending": "Welfare spending",
    "Locally financed current expenditure": "Locally financed current expenditure",
    "Locally financed capital expenditure": "Locally financed capital expenditure",
    "Public corporations' capital expenditure": "Public corporations' capital expenditure",
    "Company and other tax credits": "Company and other tax credits",
    "BBC current expenditure": "BBC current expenditure",
}
NOT_RESCALED = {"Unfunded public sector pensions"}


def _start(fy: str) -> int:
    """'2026-27' -> 2026."""
    return int(fy[:4])


def _clean(label) -> str:
    """Table labels carry footnote digits ('Income tax1'): drop them and spaces."""
    return re.sub(r"\d+(,\d+)*$", "", str(label or "")).strip()


def _num(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())  # the OBR writes some zeros as text: '0'
    except ValueError:
        return 0.0


def _annual_table(ws, after: str | None = None) -> tuple[list[str], dict[str, list[float]]]:
    """An EFO table with fiscal years across the top: (years, {row label: values}).
    `after` reads only the rows below the first cell containing that text (Table A.9
    repeats its labels: first as % of GDP, then in £ billion)."""
    rows = list(ws.iter_rows(values_only=True))
    header = next(r for r in rows if sum(bool(re.fullmatch(r"\d{4}-\d{2}", str(c or ""))) for c in r) >= 5)
    cols = [j for j, c in enumerate(header) if re.fullmatch(r"\d{4}-\d{2}", str(c or ""))]
    years = [str(header[j]) for j in cols]
    if after:
        start = next(i for i, r in enumerate(rows) if any(after in str(c) for c in r if c is not None))
        rows = rows[start:]
    out = {}
    for r in rows:
        labels = [_clean(c) for c in r[: cols[0]] if isinstance(c, str) and _clean(c)]
        if labels:
            # 'of which:' rows keep the sub-label (e.g. 'Onshore', 'Pay as you earn')
            label = labels[-1] if labels[0].lower().startswith("of which") or len(labels) > 1 else labels[0]
            out.setdefault(label, [_num(r[j]) for j in cols])
    return years, out


def _quarterly(ws, wanted: dict[str, str]) -> dict[str, dict[str, float]]:
    """Columns of an economy table by header words: {name: {'2026Q3': value}}."""
    rows = list(ws.iter_rows(values_only=True))
    width = max(len(r) for r in rows[:6])
    heads = [" ".join(str(r[j]) for r in rows[:6] if j < len(r) and r[j] is not None).lower() for j in range(width)]
    out = {}
    for name, words in wanted.items():
        j = next(j for j, h in enumerate(heads) if words in h)
        out[name] = {}
        for r in rows:
            q = next((c for c in r if c is not None), None)
            if re.fullmatch(r"\d{4}Q[1-4]", str(q).strip()) and j < len(r) and isinstance(r[j], (int, float)):
                out[name][str(q).strip()] = float(r[j])
    return out


def _efo_files(year: int, month: int) -> dict:
    """Latest EFO tables we need, from the OBR site."""
    page = obr.EFO_PAGE.format(month=obr.MONTHS[month - 1], year=year)
    slug = f"{obr.MONTHS[month - 1]}-{year}-economic-and-fiscal-outlook-"
    links = obr.find(re.escape(slug), page)
    want = {"annex": "charts-and-tables-annex-a", "economy": "detailed-forecast-tables-economy",
            "receipts": "detailed-forecast-tables-receipts",
            "debt": "detailed-forecast-tables-debt-interest-ready-reckoner"}
    out = {}
    for key, tail in want.items():
        url = links.get(slug + tail)
        if not url:
            raise LookupError(f"EFO {year}-{month:02d}: no {tail} download")
        out[key] = openpyxl.load_workbook(io.BytesIO(get(url, timeout=180)), read_only=True, data_only=True)
    return out


def _latest_efo() -> tuple[str, dict]:
    """(label, workbooks) for the newest EFO with all the tables."""
    from datetime import date

    y, m = date.today().year, date.today().month
    for _ in range(14):
        if obr.efo_economy(y, m):
            return f"{obr.MONTHS[m - 1].title()} {y}", _efo_files(y, m)
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    raise LookupError("no EFO found in the last 14 months")


def build(mar25: dict, label: str | None = None, files: dict | None = None) -> dict:
    """The March 2025 basis re-expressed on the latest EFO."""
    if files is None:
        label, files = _latest_efo()
    years_all, a3 = _annual_table(files["annex"]["TA.3"])
    _, a5 = _annual_table(files["annex"]["TA.5"])
    _, a7 = _annual_table(files["annex"]["TA.7"])
    _, a9 = _annual_table(files["annex"]["TA.9"], after="£ billion")
    _, t41 = _annual_table(files["receipts"]["4.1"])
    # the first column is the latest outturn year: it becomes the base year
    base_year, years = years_all[0], years_all[1:]
    n = len(years)
    rows = lambda table, name: table[name][: n + 1]  # noqa: E731

    # ---- baselines (index 0 = base year), as the engine expects
    q6 = _quarterly(files["economy"]["1.6"], {"awe": "average weekly earnings growth"})
    q7 = _quarterly(files["economy"]["1.7"], {"rpi": "year-on-year growth rpi", "cpi": "cpi"})
    yrs = [base_year] + years
    quarter = lambda series, fy, q: series[f"{_start(fy)}Q{q}"]  # noqa: E731
    chain = lambda rates: [100 * __import__("math").prod(1 + r / 100 for r in rates[1:k + 1]) for k in range(len(rates))]  # noqa: E731

    def round1(x):  # Excel-style rounding to 1dp (halves away from zero)
        import decimal
        return float(decimal.Decimal(repr(x)).quantize(decimal.Decimal("0.1"), rounding=decimal.ROUND_HALF_UP))

    awe_fy = rows(a3, "Average weekly earnings")
    cpi_fy, rpi_fy = rows(a3, "CPI"), rows(a3, "RPI")
    cpi_q3 = [quarter(q7["cpi"], fy, 3) for fy in yrs]
    rpi_q2 = [quarter(q7["rpi"], fy, 2) for fy in yrs]
    awe_q2 = [quarter(q6["awe"], fy, 2) for fy in yrs]
    baseline = {
        "earnings": chain(awe_fy),
        "earnings_growth_fy": awe_fy,
        "employment": [1000 * v for v in rows(a3, "Employment (million)")],
        "nominal_gdp_fy": rows(a3, "Nominal GDP (£ billion)"),
        "nominal_gdp_march": rows(a3, "Nominal GDP (centred end-March £bn)"),
        "gas_price": rows(a3, "Gas prices (pence a therm)"),
        "rpi_inflation": rpi_fy, "rpi_level": chain(rpi_fy),
        "cpi_inflation": cpi_fy, "cpi_level": chain(cpi_fy),
        "earnings_growth_q2": awe_q2,
        "earnings_growth_q3": [quarter(q6["awe"], fy, 3) for fy in yrs],
        "cpi_q3": cpi_q3,
        "cpi_q3_floor": [max(round1(v), 0) for v in cpi_q3],
        "cpi_q3_cap": [min(round1(v), 3) for v in cpi_q3],
        "rpi_q2": rpi_q2, "rpi_q2_level": chain(rpi_q2),
        # computed exactly as the engine does, so no change in => no change out
        "triple_lock": [round1(max(e, round1(c), 2.5)) for e, c in zip(awe_q2, cpi_q3)],
    }
    published_lock = rows(a3, "'Triple-lock' guarantee")

    # ---- tax and spending lines (£ billion), latest forecast
    def a5row(names):
        return [sum(a5[nm][k] for nm in names) for k in range(1, n + 1)]

    new_lines = {k: a5row(v) for k, v in RECEIPT_LINES.items()}
    for k, v in RECEIPT_LINES_41.items():
        new_lines[k] = t41[v][1: n + 1]
    receipts_total = a5["Current receipts"][1: n + 1]
    named = [sum(new_lines[k][t] for k in new_lines if k != "Interest and dividend receipts") for t in range(n)]
    new_lines["Other non-interest and dividend receipts"] = [
        receipts_total[t] - named[t] - new_lines["Interest and dividend receipts"][t] for t in range(n)]
    new_lines["Public sector current receipts"] = receipts_total
    for k, v in SPENDING_LINES.items():
        new_lines[k] = a7[v][1: n + 1]
    new_lines["Total managed expenditure"] = a7["Total managed expenditure"][1: n + 1]

    # ---- rescale the terms
    old_years = mar25["years"]
    last_old = _start(old_years[-1])
    old_index = {_start(y): i for i, y in enumerate(old_years)}
    new_index = {_start(y): i for i, y in enumerate(years)}
    welfare = set(mar25["welfare_streams"])
    old_lines = mar25["lines"]

    def line_of(term):
        return "Welfare spending" if term["stream"] in welfare else term["stream"]

    def ratio(line, new_t, old_t):
        if line in NOT_RESCALED or line not in new_lines:
            return 1.0
        old, new = old_lines[line][old_t], new_lines[line][new_t]
        if abs(old) < 0.05:  # a line that was (almost) nothing: keep the effect only if it still exists
            return 1.0 if abs(new) >= 0.05 else 0.0
        return new / old

    def moved(term):
        """Copies of a workbook term placed in the new years: as it is if its year is
        covered, and the final year's terms repeated for years beyond the workbook."""
        e_old = _start(old_years[term["e"]])
        shifts = range(0, _start(years[-1]) - last_old + 1) if e_old == last_old else [0]
        for k in shifts:
            e_new, d_new = e_old + k, _start(old_years[term["d"]]) + k
            if e_new in new_index and d_new in new_index:
                yield k, new_index[e_new], new_index[d_new]

    terms, debt_terms = [], []
    for term in mar25["terms"]:
        for k, e, d in moved(term):
            t = dict(term, e=e, d=d)
            if term["block"] == "debt":
                debt_terms.append(t)
            else:
                t["coef"] = term["coef"] * ratio(line_of(term), e, old_index[_start(years[e]) - k])
                terms.append(t)
    loop = []
    for item in mar25["loop"]:
        e_old = _start(old_years[item["e"]])
        for k in (range(0, _start(years[-1]) - last_old + 1) if e_old == last_old else [0]):
            e_new, d_new = e_old + k, _start(old_years[item["d"]]) + k
            if e_new in new_index and d_new in new_index:
                loop.append({"e": new_index[e_new], "d": new_index[d_new], "coef": item["coef"]})

    # ---- debt interest: match a permanent shock to the latest debt interest reckoner
    dyears, dtab = _annual_table(files["debt"]["5.1"])
    published = {}
    for label_, values in dtab.items():
        low = label_.lower()
        key = ("gilt_rate" if "gilt" in low else "bank_rate" if "short" in low else
               "rpi_inflation" if "inflation" in low else "primary_deficit" if "cgncr" in low else None)
        if key:
            per = 10.0 if key == "primary_deficit" else 1.0  # '£10bn increase' -> per £1bn
            published[key] = {dyears[j]: v * 1000 / per for j, v in enumerate(values)}  # £ million
    source_of = lambda term: mar25["vectors"][term["v"]]["source"]  # noqa: E731
    debt_check = {}
    for key, table in published.items():
        group = [t for t in debt_terms if source_of(t) == key]
        for e in range(n):
            permanent = sum(t["coef"] for t in group if t["e"] == e)  # 1 unit in every year from the first
            target = table.get(years[e])
            f = target / permanent if permanent and target is not None else 1.0
            for t in group:
                if t["e"] == e:
                    t["coef"] *= f
            if key == "primary_deficit":  # the financing loop uses the same borrowing matrix
                for item in loop:
                    if item["e"] == e:
                        item["coef"] *= f
            debt_check[key] = debt_check.get(key, []) + [round(f, 3)]
    terms += debt_terms

    return {
        "label": f"{label} EFO (OBR elasticities from March 2025, rescaled)",
        "years": years,
        "base_year": base_year,
        "baseline": baseline,
        "vectors": mar25["vectors"],
        "terms": terms,
        "loop": loop,
        "lines": new_lines,
        "welfare_streams": mar25["welfare_streams"],
        "aggregates": {
            "psnd": a9["Public sector net debt"][1: n + 1],
            "psnfl": a9["Public sector net financial liabilities"][1: n + 1],
            "current_deficit": a9["Current budget deficit"][1: n + 1],
        },
        "triple_lock_fixed_years": 1,  # Q2 2026 earnings and September 2026 CPI were already published
        "triple_lock_cpi": "cpi_q3",
        "checks": {"triple_lock_published": published_lock, "debt_scale_factors": debt_check},
    }
