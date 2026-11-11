"""HMRC PAYE Real Time Information (RTI): payrolled employees by age.

The ONS publishes the RTI figures each month as one spreadsheet ("Earnings and
employment from Pay As You Earn Real Time Information, seasonally adjusted"). Sheet 28
has payrolled employees by age band, monthly and seasonally adjusted, from July 2014.
The latest month is an early ("flash") estimate that is usually revised.
"""

from __future__ import annotations

import io
import re
from datetime import datetime

import openpyxl

from .http import get, get_text

PAGE = ("https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/earningsandworkinghours/"
        "datasets/realtimeinformationstatisticsreferencetableseasonallyadjusted")
SHEET = "28. Employees (Age)"


def employees_by_age() -> tuple[dict[str, dict[str, float]], str]:
    """({age band: {YYYY-MM: employees}}, download url). Bands as the ONS labels them:
    '0 to 17', '18 to 24', '25 to 34', '35 to 49', '50 to 64', '65 and over', 'UK'."""
    link = re.search(r'href="(/file\?uri=[^"]+\.xlsx)"', get_text(PAGE))
    if not link:
        raise LookupError("no RTI spreadsheet linked from the ONS dataset page")
    url = "https://www.ons.gov.uk" + link.group(1)
    rows = list(openpyxl.load_workbook(io.BytesIO(get(url, timeout=180)), read_only=True,
                                       data_only=True)[SHEET].iter_rows(values_only=True))
    header = next(r for r in rows if r and r[0] == "Date")
    out: dict[str, dict[str, float]] = {str(h): {} for h in header[1:] if h}
    for r in rows:
        try:
            month = datetime.strptime(str(r[0]).strip(), "%B %Y").strftime("%Y-%m")
        except ValueError:
            continue  # titles, notes, blank rows
        for band, v in zip(header[1:], r[1:]):
            if band and isinstance(v, (int, float)):
                out[str(band)][month] = float(v)
    return out, url
