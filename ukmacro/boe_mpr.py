"""Bank of England Monetary Policy Report (MPR) forecasts.

Each MPR (four a year) comes with a zip file of its data. Three things in it are
useful here:

1. The **projections databank** (a CSV): the Bank's central projections, quarterly,
   for every report back to 2004. We take the latest report's paths for GDP (as an
   index), CPI inflation and the unemployment rate, and its Q4-on-Q4 paths for
   business investment and private-sector regular pay.
2. The **chart data** spreadsheet: Bank staff's near-term projections, which are
   finer than the databank: CPI month by month for about six months ahead, GDP
   growth for the current and next quarter, and the unemployment rate.
3. **Other forecasters' expectations**: the average of around 20 outside forecasters
   surveyed by the Bank (a free stand-in for a "consensus").

There is no API, so we guess the zip's address from the report month and walk back
month by month until one exists.
"""

from __future__ import annotations

import csv
import io
import re
import urllib.error
import zipfile
from datetime import date, datetime

from .http import get

ZIP_URL = ("https://www.bankofengland.co.uk/-/media/boe/files/monetary-policy-report/"
           "{y}/{m}/mpr-{m}-{y}-charts-slides-and-data.zip")
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]


def latest_report(today: date | None = None, lookback: int = 8) -> tuple[str, zipfile.ZipFile, str]:
    """Find the newest MPR data zip. Returns (label e.g. 'November 2026', zip, url)."""
    today = today or date.today()
    y, m = today.year, today.month
    for _ in range(lookback):
        url = ZIP_URL.format(y=y, m=MONTHS[m - 1])
        try:
            return f"{MONTHS[m - 1].title()} {y}", zipfile.ZipFile(io.BytesIO(get(url, timeout=120))), url
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        m -= 1  # no report this month: try the month before
        if m == 0:
            y, m = y - 1, 12
    raise LookupError(f"no MPR data zip found in the last {lookback} months")


def _member(zf: zipfile.ZipFile, pattern: str) -> bytes:
    """Read the one file in the zip whose name matches `pattern` (case-insensitive)."""
    names = [n for n in zf.namelist() if re.search(pattern, n, re.I)]
    if not names:
        raise LookupError(f"MPR zip has no file matching {pattern!r}")
    return zf.read(names[0])


def _quarter(label: str) -> str:
    """'2026 Q3' or '2026 Q3 (Bank staff projections)' -> '2026-Q3'."""
    y, q = re.match(r"(\d{4})\s*Q([1-4])", str(label)).groups()
    return f"{y}-Q{q}"


def _month(cell) -> str:
    """A date cell or a label like 'Oct 2026 (Bank staff projections)' -> '2026-10'."""
    if isinstance(cell, datetime):
        return cell.strftime("%Y-%m")
    return datetime.strptime(" ".join(str(cell).split()[:2]), "%b %Y").strftime("%Y-%m")


# ---------------------------------------------------------------- projections databank

def databank(zf: zipfile.ZipFile) -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[tuple[int, float]]]]:
    """The latest report's central projections, as two dicts:
    quarterly {variable: [(quarter, value), ...]} and annual {variable: [(year, value), ...]}.

    The CSV holds every report since 2004; report dates look like 'Nov-26'. Most
    variables are quarterly ('2026 Q3'); some only annual ('2026'), e.g. 'Business
    investment' (calendar-year growth) and 'Average weekly earnings (private sector
    regular pay) (Q4)' (growth in the year to Q4). Names are as the Bank writes them.
    """
    text = _member(zf, r"Projections Databank CSV.*\.csv$").decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    latest = max({r["Report date"] for r in rows}, key=lambda s: datetime.strptime(s, "%b-%y"))
    quarterly: dict[str, list] = {}
    annual: dict[str, list] = {}
    for r in rows:
        if r["Report date"] != latest:
            continue
        try:
            value = float(r["Value"])
        except ValueError:
            continue  # blank or 'n.a.'
        when, var = r["Projection date"].strip(), r["Variable"].strip()
        if re.fullmatch(r"\d{4}", when):
            annual.setdefault(var, []).append((int(when), value))
        elif re.match(r"\d{4}\s*Q[1-4]", when):
            quarterly.setdefault(var, []).append((_quarter(when), value))
    return ({k: sorted(v) for k, v in quarterly.items()}, {k: sorted(v) for k, v in annual.items()})


# ---------------------------------------------------------------- near-term projections

def _sheet_by_title(wb, phrase: str):
    """Chart sheets move between reports, so find one by words in its title."""
    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=1, max_row=4, values_only=True):
            if any(isinstance(c, str) and phrase.lower() in c.lower() for c in row):
                return ws
    raise LookupError(f"no MPR chart sheet titled like {phrase!r}")


def near_term(zf: zipfile.ZipFile) -> dict[str, list[tuple[str, float]]]:
    """Bank staff near-term projections (only the projected periods, not outturns):
    {'cpi': [(month, %)], 'gdp_qoq': [(quarter, %)], 'unemployment': [(month, %)]}."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(_member(zf, r"MPR chart data\.xlsx$")), read_only=True, data_only=True)
    is_proj = lambda c: isinstance(c, str) and "projection" in c.lower()  # noqa: E731
    out = {}

    # CPI: first column is the month, second is outturn-then-projection
    ws = _sheet_by_title(wb, "CPI inflation and projections")
    out["cpi"] = [(_month(r[0]), float(r[1])) for r in ws.iter_rows(values_only=True)
                  if is_proj(r[0]) and isinstance(r[1], (int, float))]

    # GDP: quarterly growth, projected quarters are labelled "(Bank staff projections)"
    ws = _sheet_by_title(wb, "Quarterly growth in headline GDP")
    out["gdp_qoq"] = [(_quarter(r[0]), float(r[1])) for r in ws.iter_rows(values_only=True)
                      if is_proj(r[0]) and isinstance(r[1], (int, float))]

    # Unemployment: sits in a second block of columns, found by its header
    ws = _sheet_by_title(wb, "unemployment rate and near-term projection")
    rows = list(ws.iter_rows(values_only=True))
    col = next(j for r in rows for j, c in enumerate(r) if c == "Unemployment rate")
    out["unemployment"] = [(_month(r[col - 1]), float(r[col])) for r in rows
                           if len(r) > col and is_proj(r[col - 1]) and isinstance(r[col], (int, float))]
    return out


# ---------------------------------------------------------------- outside forecasters

def other_forecasters(zf: zipfile.ZipFile) -> dict[str, list[tuple[str, float]]]:
    """Average of outside forecasters' central projections, at Q4 points:
    {'cpi': [...], 'gdp_yoy': [...], 'unemployment': [...]}. GDP is four-quarter growth.
    Table 5's current-quarter GDP figure is quarterly growth, so it is kept apart as 'gdp_qoq'."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(_member(zf, r"Other forecasters")), read_only=True, data_only=True)
    names = {"cpi inflation": "cpi", "gdp growth": "gdp_yoy", "lfs unemployment rate": "unemployment"}
    out: dict[str, list] = {v: [] for v in names.values()}

    # Table 1: a header row per variable ('CPI inflation (b)', '2027 Q4', ...) then 'Average'
    rows = list(wb["Table 1"].iter_rows(values_only=True))
    for i, r in enumerate(rows[:-1]):
        key = re.sub(r"\s*\(.*", "", str(r[0] or "")).strip().lower()
        if key in names and rows[i + 1][0] == "Average":
            for q, v in zip(r[1:], rows[i + 1][1:]):
                if q and isinstance(v, (int, float)):
                    out[names[key]].append((_quarter(q), float(v)))

    # Table 5: the current quarter (CPI and unemployment levels; GDP as quarterly growth)
    rows = list(wb["Table 5"].iter_rows(values_only=True))
    q = next(_quarter(c) for r in rows for c in r if isinstance(c, str) and re.match(r"\d{4} Q[1-4]$", c))
    for r in rows:
        key = re.sub(r"\s*\(.*", "", str(r[0] or "")).strip().lower()
        nums = [c for c in r if isinstance(c, (int, float))]  # the value's column varies
        if key in names and nums:
            target = "gdp_qoq" if names[key] == "gdp_yoy" else names[key]
            out.setdefault(target, []).insert(0, (q, float(nums[0])))
    return out
