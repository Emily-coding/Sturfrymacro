"""Bank of England Agents' scores.

The Bank's twelve regional Agents talk to around 700 businesses a quarter and turn what
they hear into scores from -5 to +5 (0 = normal conditions, positive = stronger or
more than usual). The scores are judgements, not survey counts, but they go back to
1997 and are free, unlike most business-survey indicators of the labour market (e.g.
the KPMG/REC Report on Jobs, which is licensed from S&P Global).

The Bank publishes every score in one spreadsheet, with one column per score on the
"Agents' scores" sheet. Scores were monthly until 2023; since then there are about
eight a year (two per quarter). Dates in the sheet are mixed: month-ends earlier,
the first of the month later, so we keep just the year and month.

Columns used here (names as in row 4 of the sheet):
    "Recruitment Difficulties"            from 1997: how hard firms find it to hire
    "Employment Intentions (new scores)"  from late 2017: planned headcount, next 6 months
"""

from __future__ import annotations

import io

import openpyxl

from .http import get

URL = "https://www.bankofengland.co.uk/-/media/boe/files/agents-summary/agentsscores.xlsx"
SHEET = "Agents' scores"
HEADER_ROW = 4  # row holding the score names (1-based, as in Excel)


def fetch(column: str) -> list[tuple[str, float]]:
    """One score as [(YYYY-MM, value)], oldest first. `column` is the score's name as
    written in the sheet (spaces at either end ignored)."""
    wb = openpyxl.load_workbook(io.BytesIO(get(URL, timeout=90)), read_only=True, data_only=True)
    rows = list(wb[SHEET].iter_rows(values_only=True))
    names = [str(c).strip() if c else "" for c in rows[HEADER_ROW - 1]]
    if column.strip() not in names:
        raise LookupError(f"no Agents' score called {column!r}; the sheet has {[n for n in names if n]}")
    j = names.index(column.strip())
    obs = {}
    for r in rows[HEADER_ROW:]:
        when, value = r[0], r[j] if j < len(r) else None
        if hasattr(when, "year") and isinstance(value, (int, float)):
            obs[f"{when.year}-{when.month:02d}"] = round(float(value), 3)
    if not obs:
        raise ValueError(f"Agents' score {column!r} has no values")
    return sorted(obs.items())
