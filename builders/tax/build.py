#!/usr/bin/env python3
"""Tax builder: HMRC's tax ready reckoner as data for tax.html.

HMRC publishes "Direct effects of illustrative tax changes": what a 1p, 1 point or 1%
change in each main tax would raise or cost in each of the next three years. This
script reads the latest edition and writes data/tax/reckoner.json with two "bases"
the page can switch between:

    hmrc    HMRC's figures exactly as published (consistent with the OBR forecast and
            the tax policy at the time of that edition)
    latest  the same figures rescaled to the latest OBR forecast: each measure is
            scaled by (latest forecast for that tax) / (forecast HMRC used), year by
            year, and carried on to the end of the latest forecast in line with
            that tax's receipts. The forecasts both come from data/fiscal/reckoner.json,
            so run builders/fiscal/build.py first after an EFO.

For each measure we store the revenue effect (in £ million; positive = more money
for the Exchequer) of a move of a given size up and, where it makes sense, down.
HMRC's table mixes "costs" and "yields", so every row is given an explicit sign here.
tax.html interpolates between these points for other sizes.

Not part of the scheduled refresh: HMRC updates the table after fiscal events, so
rerun this by hand: python builders/tax/build.py
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
import io
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from ukmacro.http import get, get_json  # noqa: E402

PAGE_URL = "https://www.gov.uk/government/statistics/direct-effects-of-illustrative-tax-changes"
API_URL = "https://www.gov.uk/api/content/government/statistics/direct-effects-of-illustrative-tax-changes"
FISCAL = REPO / "data" / "fiscal" / "reckoner.json"
OUT = REPO / "data" / "tax" / "reckoner.json"

# Which OBR receipts line(s) each kind of tax is rescaled by (names as in the fiscal
# data). None = not rescaled: Child Benefit (cost per £1 a week depends on the number
# of children, not on the economy) and taxes too small to have their own OBR line.
IT = ["PAYE income tax", "SA income tax"]
SCALE = {
    "it": IT, "nics": ["NICs"], "ct": ["Onshore corporation tax"], "cgt": ["Capital gains tax"],
    "iht": ["Inheritance tax"], "sdlt": ["Stamp taxes"], "vat": ["VAT"], "ipt": ["Insurance premium tax"],
    "alcohol": ["Alcohol duties"], "tobacco": ["Tobacco duties"], "fuel": ["Fuel duties"],
    "ccl": ["Climate change levy"], "ved": ["Vehicle excise duty"], "apd": ["Air passenger duty"],
    None: None,
}

# The four groups on the chart (series colours in the house order)
CHART_GROUPS = ["Income tax", "National Insurance", "VAT and duties", "Business, wealth, property and Child Benefit"]

# ---------------------------------------------------------------------------------
# The levers. Each one:
#   id, page group, label, unit shown, input step, help text, which OBR line rescales it,
#   up:   list of (size, [HMRC rows], sign): the revenue effect of raising it by `size`
#         is sign x (sum of those rows). Sign is +1 where HMRC shows a yield or a
#         revenue-signed figure, -1 where it shows a cost.
#   down: the same for cutting it, or "mirror" (a cut is the exact opposite of a rise,
#         as HMRC's "Change X by..." rows imply), or None (HMRC gives no figure).
# Several points (e.g. 1% and 10%) mean the effect isn't a straight line: the page
# interpolates between them.
# ---------------------------------------------------------------------------------
def L(id, group, chart, label, unit, step, help, scale, up, down="mirror"):
    return dict(id=id, group=group, chart=chart, label=label, unit=unit, step=step, help=help,
                scale=scale, up=up, down=down)

IT_G, NI_G, BW_G, SD_G, VD_G, CB_G = ("Income tax", "National Insurance", "Business and wealth taxes",
                                      "Stamp duty land tax", "VAT and duties", "Child Benefit")
OTHER = CHART_GROUPS[3]


def sdlt(id, label, row_stem, help=""):
    """Stamp duty rows come as a pair: raise (yield) and cut (cost)."""
    return L(id, SD_G, OTHER, label, "points", 1, help, "sdlt",
             up=[(1, [f"Raise {row_stem} by 1 percentage point (Yield)"], +1)],
             down=[(1, [f"Cut {row_stem} by 1 percentage point (Cost)"], -1)])


def duty(id, label, rows, scale, help=""):
    return L(id, VD_G, CHART_GROUPS[2], label, "%", 1,
             help or "Per cent change in the duty rates, on top of any uprating already planned.", scale,
             up=[(1, rows, +1)])


LEVERS = [
    # ---- income tax rates
    L("basic_rate", IT_G, "Income tax", "Basic rate", "pence", 0.5, "On non-savings, non-dividend income (England and Northern Ireland; Scotland sets its own).", "it",
      up=[(1, ["Change basic rate by 1p"], +1)]),
    L("higher_rate", IT_G, "Income tax", "Higher rate", "pence", 0.5, "", "it",
      up=[(1, ["Change higher rate by 1p"], +1)]),
    L("additional_rate", IT_G, "Income tax", "Additional rate", "pence", 0.5,
      "HMRC estimates a cut costs more than a rise raises: top earners respond strongly to this rate.", "it",
      up=[(1, ["Increase additional rate by 1p (yield)"], +1)],
      down=[(1, ["Decrease additional rate by 1p (cost)"], -1)]),
    # ---- income tax allowances and limits (raising them costs money)
    L("personal_allowance", IT_G, "Income tax", "Personal allowance", "%", 1,
      "Per cent change in the tax-free allowance. Not a straight line: HMRC gives 1% and 10%. A £100 change is roughly 0.8%.", "it",
      up=[(1, ["Change personal allowance by 1%"], -1), (10, ["Change personal allowance by 10%"], -1)]),
    L("basic_rate_limit", IT_G, "Income tax", "Basic rate limit", "%", 1,
      "Per cent change in the band of income taxed at the basic rate (raising it means fewer higher-rate taxpayers).", "it",
      up=[(1, ["Change basic rate limit by 1%"], -1), (10, ["Increase basic rate limit by 10% (cost)"], -1)],
      down=[(1, ["Change basic rate limit by 1%"], +1), (10, ["Decrease basic rate limit by 10% (yield)"], +1)]),
    L("all_thresholds", IT_G, "Income tax", "All main allowances and limits together", "%", 1,
      "Personal allowance, starting and basic rate limits moved together, e.g. an extra 1% uprating (or a freeze worth −1%). "
      "Don't combine with the two levers above: they overlap.", "it",
      up=[(1, ["Change all main allowances, starting and basic rate limits by 1%"], -1),
          (10, ["Increase all main allowances, starting and basic rate limits by 10% (cost)"], -1)],
      down=[(1, ["Change all main allowances, starting and basic rate limits by 1%"], +1),
            (10, ["Decrease all main allowances, starting and basic rate limits by 10% (yield)"], +1)]),
    L("dividend_allowance", IT_G, "Income tax", "Dividend allowance", "£", 100, "", "it",
      up=[(100, ["Change dividend allowance by £100"], -1)]),
    L("savings_allowance", IT_G, "Income tax", "Personal savings allowance", "£ (basic rate)", 100,
      "Change for basic-rate taxpayers; higher-rate taxpayers get half.", "it",
      up=[(100, ["Change Savings allowance by £100 for BR and £50 for HR taxpayers"], -1)]),
    L("savings_starting_rate", IT_G, "Income tax", "Starting rate for savings", "pence", 1, "", "it",
      up=[(1, ["Change starting rate for savings income by 1p"], +1)]),
    L("savings_starting_limit", IT_G, "Income tax", "Starting rate limit for savings", "£", 100, "", "it",
      up=[(100, ["Change starting rate limit for savings income by £100"], -1)]),
    # ---- National Insurance
    L("ee_main", NI_G, "National Insurance", "Employee main rate (Class 1)", "points", 0.5, "", "nics",
      up=[(1, ["Change Class 1 employee main rate by 1 percentage point"], +1)]),
    L("ee_additional", NI_G, "National Insurance", "Employee additional rate (Class 1)", "points", 0.5, "On earnings above the upper earnings limit.", "nics",
      up=[(1, ["Change Class 1 employee additional rate by 1 percentage point"], +1)]),
    L("er_rate", NI_G, "National Insurance", "Employer rate (Class 1)", "points", 0.5, "", "nics",
      up=[(1, ["Change Class 1 employer rate by 1 percentage point"], +1)]),
    L("c4_main", NI_G, "National Insurance", "Self-employed main rate (Class 4)", "points", 0.5, "", "nics",
      up=[(1, ["Change Class 4 main rate by 1 percentage point"], +1)]),
    L("c4_additional", NI_G, "National Insurance", "Self-employed additional rate (Class 4)", "points", 0.5, "", "nics",
      up=[(1, ["Change Class 4 additional rate by 1 percentage point"], +1)]),
    L("ee_threshold", NI_G, "National Insurance", "Employee threshold", "£ a week", 1, "Where employees start paying.", "nics",
      up=[(2, ["Change employee entry threshold by £2 per week"], -1)]),
    L("er_threshold", NI_G, "National Insurance", "Employer threshold", "£ a week", 1, "Where employers start paying on an employee's earnings.", "nics",
      up=[(2, ["Change employer threshold by £2 per week"], -1)]),
    L("uel", NI_G, "National Insurance", "Upper earnings limit", "£ a week", 1,
      "Above it employees pay the lower additional rate, so raising it raises money.", "nics",
      up=[(10, ["Change upper earnings limit by £10 per week"], +1)]),
    L("lpl", NI_G, "National Insurance", "Lower profits limit (Class 4)", "£ a year", 52, "", "nics",
      up=[(104, ["Change lower profits limit by £104 per year"], -1)]),
    L("upl", NI_G, "National Insurance", "Upper profits limit (Class 4)", "£ a year", 52,
      "Above it the self-employed pay the lower additional rate, so raising it raises money.", "nics",
      up=[(520, ["Change upper profits limit by £520 per year"], +1)]),
    # ---- business and wealth taxes
    L("ct", BW_G, OTHER, "Corporation tax main rate", "points", 1, "", "ct",
      up=[(1, ["Increase Corporation tax by 1 percentage point"], +1)]),
    # CGT rows are already revenue-signed (a rate rise can lose money as people hold on to assets)
    L("cgt_lower", BW_G, OTHER, "Capital gains tax lower rate", "points", 1,
      "Rises only. Not a straight line: HMRC gives 1, 5 and 10 points, and big rises lose money as people delay selling.", "cgt",
      up=[(1, ["Increase lower Capital Gains Tax rate by 1 percentage point"], +1),
          (5, ["Increase lower Capital Gains Tax rate by 5 percentage points"], +1),
          (10, ["Increase lower Capital Gains Tax rate by 10 percentage points"], +1)], down=None),
    L("cgt_higher", BW_G, OTHER, "Capital gains tax higher rate", "points", 1,
      "Rises only. HMRC gives 1, 5 and 10 points; the bigger rises lose money as people delay selling.", "cgt",
      up=[(1, ["Increase higher Capital Gains Tax rate by 1 percentage point"], +1),
          (5, ["Increase higher Capital Gains Tax rate by 5 percentage points"], +1),
          (10, ["Increase higher Capital Gains Tax rate by 10 percentage points"], +1)], down=None),
    L("badr", BW_G, OTHER, "Business Asset Disposal Relief rate", "points", 1, "Rises only (HMRC gives 1 and 5 points).", "cgt",
      up=[(1, ["Increase Business Asset Disposal Relief rate by 1 percentage point"], +1),
          (5, ["Increase Business Asset Disposal Relief rate by 5 percentage points"], +1)], down=None),
    L("aea", BW_G, OTHER, "Capital gains annual exempt amount", "£", 100, "For individuals (trusts get half).", "cgt",
      up=[(500, ["Increase Annual Exempt Amount by £500 for individuals and £250 for trusts"], +1)]),
    L("iht_rate", BW_G, OTHER, "Inheritance tax rate", "points", 1, "", "iht",
      up=[(1, ["Increase standard rate for estates left on death by 1 percentage point"], +1)]),
    L("nrb", BW_G, OTHER, "Inheritance tax nil rate band", "£", 1000, "", "iht",
      up=[(5000, ["Increase Nil Rate Band by £5,000 (cost)"], -1)]),
    L("rnrb", BW_G, OTHER, "Residence nil rate band", "£", 1000, "", "iht",
      up=[(5000, ["Increase Residence Nil Rate Band by £5,000 (cost)"], -1)]),
    # ---- stamp duty land tax (HMRC gives separate raise and cut figures)
    sdlt("sdlt_2", "Residential 2% band", "residential 2% marginal rate"),
    sdlt("sdlt_5", "Residential 5% band", "residential 5% marginal rate"),
    sdlt("sdlt_10", "Residential 10% band", "residential 10% marginal rate"),
    sdlt("sdlt_12", "Residential 12% band", "residential 12% marginal rate",
         "HMRC estimates a rise loses money (fewer top-end sales), and a cut raises it."),
    L("sdlt_surcharge", SD_G, OTHER, "Surcharge on additional dwellings", "points", 1, "", "sdlt",
      up=[(1, ["Increase Higher Rates of Duty on Additional Dwellings by 1 percentage point (Yield)"], +1)],
      down=[(1, ["Decrease Higher Rates of Duty on Additional Dwellings by 1 percentage point (Cost)"], -1)]),
    L("sdlt_nonresident", SD_G, OTHER, "Non-resident surcharge", "points", 1, "", "sdlt",
      up=[(1, ["Increase NRSDLT by 1 percentage point (Yield)"], +1)],
      down=[(1, ["Decrease NRSDLT by 1 percentage point (Cost)"], -1)]),
    L("sdlt_nonres5", SD_G, OTHER, "Non-residential 5% band", "points", 1, "Commercial property.", "sdlt",
      up=[(1, ["Increase non-residential 5% marginal rate by 1 percentage point (Yield)"], +1)],
      down=[(1, ["Decrease non-residential 5% marginal rate by 1 percentage point (Cost)"], -1)]),
    # ---- VAT, insurance premium tax and duties
    L("vat_standard", VD_G, CHART_GROUPS[2], "VAT standard rate", "points", 1, "", "vat",
      up=[(1, ["Change standard rate by 1 percentage point"], +1)]),
    L("vat_reduced", VD_G, CHART_GROUPS[2], "VAT reduced rate", "points", 1, "E.g. domestic energy.", "vat",
      up=[(1, ["Change reduced rate by 1 percentage point"], +1)]),
    L("ipt_standard", VD_G, CHART_GROUPS[2], "Insurance premium tax standard rate", "points", 1, "", "ipt",
      up=[(1, ["Change standard rate by 1 percentage point@IPT"], +1)]),
    L("ipt_higher", VD_G, CHART_GROUPS[2], "Insurance premium tax higher rate", "points", 1, "", "ipt",
      up=[(1, ["Change higher rate by 1 percentage point@IPT"], +1)]),
    duty("fuel", "Fuel duty", ["Petrol", "Diesel", "Rebated oil"], "fuel"),
    duty("beer_cider", "Beer and cider duty", ["Beer and cider duties"], "alcohol"),
    duty("wine", "Wine duty", ["Wine and other fermented product duties"], "alcohol"),
    duty("spirits", "Spirits duty", ["Spirits duties"], "alcohol"),
    duty("tobacco", "Tobacco duty", ["Tobacco Duties"], "tobacco",
         "Per cent change in the duty rates. Small, because more smuggling and cross-border shopping offset much of it."),
    duty("ccl", "Climate change levy", ["Climate change levy"], "ccl"),
    duty("cps", "Carbon price support", ["Carbon price support"], "ccl"),
    duty("aggregates", "Aggregates levy", ["Aggregates levy"], None),
    L("ved", VD_G, CHART_GROUPS[2], "Vehicle excise duty", "£ a car", 1, "Change per car (and van, lorry...) a year; motorbikes change by a fifth as much.", "ved",
      up=[(5, ["Increase rates by £1 for motorbikes and £5 for all other vehicles"], +1)]),
    L("apd", VD_G, CHART_GROUPS[2], "Air passenger duty, reduced rate", "£ a passenger", 1, "Economy-class short-haul flights.", "apd",
      up=[(1, ["Increase reduced rate by £1"], +1)]),
    # ---- Child Benefit (spending, run by HMRC): raising it costs money
    L("cb_first", CB_G, OTHER, "Child Benefit, eldest child", "£ a week", 0.5, "", None,
      up=[(1, ["Increase first child rate by £1 per week (cost)"], -1)],
      down=[(1, ["Decrease first child rate by £1 per week (yield)"], +1)]),
    L("cb_other", CB_G, OTHER, "Child Benefit, other children", "£ a week", 0.5, "", None,
      up=[(1, ["Increase subsequent child rate by £1 per week (cost)"], -1)],
      down=[(1, ["Decrease subsequent child rate by £1 per week (yield)"], +1)]),
]


# ---------------------------------------------------------------------------------
# Reading HMRC's spreadsheet
# ---------------------------------------------------------------------------------
def latest_ods() -> tuple[str, str]:
    """The newest spreadsheet attached to the gov.uk page: (title, url)."""
    page = get_json(API_URL)
    for a in page["details"].get("attachments", []):
        url = a.get("url", "")
        if url.lower().endswith(".ods"):
            return a.get("title", ""), url
    raise RuntimeError("no .ods attachment on " + PAGE_URL)


def read_ods(raw: bytes) -> list[list[str]]:
    """Every row of the first sheet as a list of cell strings (numbers as their raw value).
    An ODS file is a zip of XML, so this needs no extra libraries."""
    ns = {"table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
          "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
          "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0"}
    T, O = "{%s}" % ns["table"], "{%s}" % ns["office"]
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(raw)).read("content.xml"))
    sheet = next(root.iter(T + "table"))
    rows = []
    for r in sheet.iter(T + "table-row"):
        row = []
        for c in r:
            if c.tag not in (T + "table-cell", T + "covered-table-cell"):
                continue
            n = min(int(c.get(T + "number-columns-repeated", "1")), 20)  # trailing blanks repeat a lot
            text = " ".join("".join(p.itertext()) for p in c.findall("text:p", ns))
            value = c.get(O + "value")
            row += [value if value is not None else text] * n
        while row and row[-1] == "":
            row.pop()
        if row:
            rows.append(row)
    return rows


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_table(rows: list[list[str]]) -> tuple[list[str], dict[str, list[float]]]:
    """HMRC's years and {row label: [£m by year]}. Some labels repeat under different
    headings (e.g. "Change standard rate by 1 percentage point" for VAT and for IPT),
    so a repeat is stored as "label@IPT" when it follows the insurance premium tax heading."""
    head = next(r for r in rows if any("financial year" in c for c in r))
    years = []
    for c in head[1:]:
        m = re.search(r"(\d{4}) to (\d{4})", c)
        years.append(f"{m.group(1)}-{m.group(2)[2:]}")
    table, section = {}, ""
    for r in rows:
        if len(r) == 1:              # a heading row (or a note)
            section = r[0]
            continue
        if len(r) < 1 + len(years) or r is head:
            continue
        vals = []
        for c in r[1:1 + len(years)]:
            c = c.strip()
            vals.append(0.0 if c.lower() == "neg" else float(c))  # "Neg" = rounds to zero
        key = norm(r[0])
        if "insurance premium tax" in section.lower():
            key += "@ipt"
        table[key] = vals
    return years, table


# ---------------------------------------------------------------------------------
# Building the two bases
# ---------------------------------------------------------------------------------
def points(lever, table, scale_by_year):
    """Revenue effect (£m by year) at each published size, for rises and for cuts."""
    def side(spec):
        out = []
        for size, labels, sign in spec:
            vals = [0.0] * len(scale_by_year)
            for lab in labels:
                key = norm(lab)
                if key not in table:
                    raise KeyError(f"HMRC row not found (has the table changed?): {lab!r}")
                for t, v in enumerate(table[key]):
                    vals[t] += sign * v
            out.append([size, [round(v * s, 1) for v, s in zip(vals, scale_by_year)]])
        return out
    up = side(lever["up"])
    if lever["down"] == "mirror":
        down = [[s, [-v for v in vals]] for s, vals in up]
    elif lever["down"] is None:
        down = None
    else:
        down = side(lever["down"])
    return {"up": up, "down": down}


def main() -> None:
    title, url = latest_ods()
    print("HMRC table:", title, url)
    years, table = parse_table(read_ods(get(url)))
    print("years:", years, "rows:", len(table))

    fiscal = json.loads(FISCAL.read_text())
    old, new = fiscal["bases"]["mar25"], fiscal["bases"]["oct26"]

    def receipts(basis, lines, yr):
        i = basis["years"].index(yr)
        return sum(basis["lines"][n][i] for n in lines)

    def gdp(basis, yrs):
        # nominal_gdp_fy has one extra value at the front (the base year)
        g = basis["baseline"]["nominal_gdp_fy"]
        return [round(g[basis["years"].index(y) + 1], 1) for y in yrs]

    # Basis 1: as published
    hmrc = {"label": f"{title}, as published by HMRC (OBR forecast of March 2025)",
            "years": years, "gdp": gdp(old, years), "extrapolated": [False] * len(years),
            "points": {lv["id"]: points(lv, table, [1.0] * len(years)) for lv in LEVERS},
            "index": {}}

    # Basis 2: rescaled to the latest forecast and carried on to its last year
    yrs = new["years"]
    last = years[-1]
    latest_pts, index = {}, {}
    for lv in LEVERS:
        lines = SCALE[lv["scale"]]
        scale = []
        for y in yrs:
            if lines is None:
                scale.append(1.0)                       # held as published, flat after HMRC's last year
            elif y in years:                            # HMRC year: latest / March 2025 forecast for the tax
                scale.append(receipts(new, lines, y) / receipts(old, lines, y))
            else:                                       # beyond HMRC: last-year scale, grown with receipts
                scale.append(receipts(new, lines, last) / receipts(old, lines, last)
                             * receipts(new, lines, y) / receipts(new, lines, last))
        # HMRC values for each latest-basis year (repeat the last HMRC year beyond its table)
        stretched = {k: [v[years.index(y)] if y in years else v[-1] for y in yrs] for k, v in table.items()}
        latest_pts[lv["id"]] = points(lv, stretched, scale)
        key = lv["scale"] or "none"
        if key not in index:                            # receipts path, used to shift the start a year later
            index[key] = [1.0] * len(yrs) if lines is None else [round(receipts(new, lines, y), 3) for y in yrs]
    latest = {"label": f"{new['label'].split(' (')[0]}: HMRC's figures rescaled to the latest OBR forecast",
              "years": yrs, "gdp": gdp(new, yrs), "extrapolated": [y not in years for y in yrs],
              "points": latest_pts, "index": index}

    out = {
        "generated": date.today().isoformat(),
        "source": {"page": PAGE_URL, "ods": url, "edition": title, "implementation": "April 2026"},
        "chart_groups": CHART_GROUPS,
        "levers": [{k: lv[k] for k in ("id", "group", "chart", "label", "unit", "step", "help", "scale")}
                   | {"sizes": [p[0] for p in lv["up"]], "cuts": lv["down"] is not None}
                   for lv in LEVERS],
        "bases": {"latest": latest, "hmrc": hmrc},
        "default_basis": "latest",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    print("wrote", OUT.relative_to(REPO))

    # sanity checks against numbers people quote
    b = hmrc["points"]
    assert b["basic_rate"]["up"][0][1][-1] > 5000, "1p on the basic rate should raise several £bn"
    assert b["personal_allowance"]["up"][0][1][0] < 0, "raising the personal allowance should cost money"
    assert b["cb_first"]["up"][0][1][0] < 0, "raising Child Benefit should cost money"


if __name__ == "__main__":
    main()
