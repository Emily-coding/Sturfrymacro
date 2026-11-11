#!/usr/bin/env python3
"""Young people not in education, employment or training (NEET), from the ONS NEET
table (Labour Force Survey, quarterly, seasonally adjusted, from late 2001). Run with
the labour markets builder every weekday; the ONS updates the table each quarter.

Two charts for labour.html:
- neet-rate: NEETs as a % of everyone in the age group, 16-24 (the usual definition, and
  the Milburn review's) and 18-24 (most 16-17s are in education).
- neet-split: the 16-24 NEET rate split into the unemployed (looking for work) and the
  economically inactive (not looking: often ill health or caring), both as % of 16-24s.

The LFS was reweighted from January-March 2019, which causes a small step in the data.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

import openpyxl  # noqa: E402

from ukmacro import charts  # noqa: E402
from ukmacro.http import get, get_text  # noqa: E402

PAGE = ("https://www.ons.gov.uk/employmentandlabourmarket/peoplenotinwork/unemployment/datasets/"
        "youngpeoplenotineducationemploymentortrainingneettable1")
# Each age group has five columns: NEET total, NEET unemployed, NEET inactive, population, NEET %
AGES = {"16-24": 1, "16-17": 6, "18-24": 11}
QUARTERS = {"Jan-Mar": 1, "Apr-Jun": 2, "Jul-Sep": 3, "Oct-Dec": 4}
NOTE = "Labour Force Survey, quarterly, seasonally adjusted. Data from 2019 were reweighted, causing a small step."


def read() -> tuple[dict[str, dict[str, list]], str]:
    """({age: {quarter: [total, unemployed, inactive, population]}}, download url)."""
    link = re.search(r'href="(/file\?uri=[^"]+\.xlsx)"', get_text(PAGE))
    if not link:
        raise LookupError("no NEET spreadsheet linked from the ONS dataset page")
    url = "https://www.ons.gov.uk" + link.group(1)
    ws = openpyxl.load_workbook(io.BytesIO(get(url, timeout=120)), read_only=True, data_only=True)["People - SA"]
    out: dict[str, dict[str, list]] = {age: {} for age in AGES}
    for r in ws.iter_rows(values_only=True):
        # labels like 'Jan-Mar 2026', 'Jan-Mar 2026 [r]' (revised) or 'Jan-Mar 20191' (footnote 1)
        m = re.match(r"([A-Z][a-z]{2}-[A-Z][a-z]{2}) (\d{4})", str(r[0] or "").strip())
        if not m or m[1] not in QUARTERS:
            continue
        q = f"{m[2]}-Q{QUARTERS[m[1]]}"
        for age, j in AGES.items():
            vals = r[j:j + 4]
            if all(isinstance(v, (int, float)) for v in vals):
                out[age][q] = list(vals)
    return out, url


def build() -> None:
    neet, url = read()
    rate = lambda age, k: {q: round(100 * v[k] / v[3], 2) for q, v in neet[age].items()}  # noqa: E731  share of age group
    quarters = sorted(neet["16-24"])
    common = {"category": "labour-markets", "type": "line", "freq": "Q", "source": "ONS NEET table",
              "next_release": None, "status": "ok", "source_url": url, "data_through": quarters[-1], "decimals": 1}
    total_16, total_18 = rate("16-24", 0), rate("18-24", 0)
    unemp, inactive = rate("16-24", 1), rate("16-24", 2)
    for chart in (
        {"slug": "neet-rate", "title": "Young people not in education, employment or training (NEET)",
         "units": "% of the age group", "note": "16-24 is the usual definition. " + NOTE,
         "series": [{"key": "a16_24", "label": "Aged 16-24"}, {"key": "a18_24", "label": "Aged 18-24"}],
         "data": [{"date": q, "a16_24": total_16.get(q), "a18_24": total_18.get(q)} for q in quarters]},
        {"slug": "neet-split", "title": "NEETs aged 16-24: unemployed or inactive",
         "units": "% of 16-24 year olds",
         "note": "Unemployed: looking for work. Inactive: not looking, often because of ill health or caring. " + NOTE,
         "series": [{"key": "inactive", "label": "Economically inactive"}, {"key": "unemployed", "label": "Unemployed"}],
         "data": [{"date": q, "inactive": inactive.get(q), "unemployed": unemp.get(q)} for q in quarters]},
    ):
        chart = {**common, **chart}
        charts.publish(chart, REPO / "data" / "labour-markets" / f"{chart['slug']}.json",
                       REPO / "img" / "labour-markets" / f"{chart['slug']}.png")
    print(f"[labour-markets] neet: {quarters[-1]} 16-24 {total_16[quarters[-1]]:.1f}%, 18-24 {total_18[quarters[-1]]:.1f}%")


if __name__ == "__main__":
    build()
