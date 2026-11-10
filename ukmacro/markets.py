"""Free sources of financial market prices, one small function each.

Each returns [(date, value), ...] sorted by date: daily dates as '2026-11-06',
monthly as '2026-09'. The Bank of England's own database (gilt yields, Bank Rate,
exchange rates) goes through ukmacro/boe.py instead.

Source                      What                                        Updated
US Treasury                 10-year Treasury yield (par yield curve)    daily, after the US close
Bundesbank                  10-year German Bund yield                   daily, next morning
US Energy Information       Brent and WTI crude oil spot prices          weekly (Wednesdays), so
  Administration (EIA)                                                    up to about a week behind
Ofgem                       UK wholesale gas, day-ahead, monthly average monthly, a few weeks behind
Yahoo Finance               FTSE 100 closing level                      daily

The FTSE 100 is the weak link: FTSE Russell licenses its index data, so there is
no official free download. Yahoo's chart feed is free and widely used but
unofficial (no published terms for this use), so it could change or be blocked;
if it fails, the builder keeps the last good copy and the run shows red.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timezone

from .http import get, get_text

# ---------------------------------------------------------------- bond yields


def us_treasury_10y(start_year: int) -> list[tuple[str, float]]:
    """10-year US Treasury par yield, %, from the Treasury's daily yield curve files
    (one CSV per calendar year)."""
    out = []
    for year in range(start_year, date.today().year + 1):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
               f"daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
               f"&field_tdr_date_value={year}&page&_format=csv")
        for row in csv.DictReader(io.StringIO(get_text(url))):
            if row.get("10 Yr"):
                d = datetime.strptime(row["Date"], "%m/%d/%Y").date().isoformat()
                out.append((d, float(row["10 Yr"])))
    if not out:
        raise ValueError("US Treasury: no 10-year yields returned")
    return sorted(out)


def bundesbank_10y() -> list[tuple[str, float]]:
    """10-year German government bond yield, %, from the Bundesbank's yield curve
    (residual maturity of exactly 10 years). Weekends and holidays are '.'."""
    url = ("https://api.statistiken.bundesbank.de/rest/download/BBSIS/"
           "D.I.ZAR.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A?format=csv&lang=en")
    out = []
    for row in csv.reader(io.StringIO(get_text(url, encoding="utf-8-sig"))):
        if len(row) >= 2 and re.fullmatch(r"\d{4}-\d\d-\d\d", row[0]) and row[1] not in ("", "."):
            out.append((row[0], float(row[1])))
    if not out:
        raise ValueError("Bundesbank: no 10-year yields returned")
    return out


# ---------------------------------------------------------------- energy


def eia_spot(code: str) -> list[tuple[str, float]]:
    """Daily spot price from the EIA's history spreadsheets, $ per barrel.
    code: 'RBRTE' = Brent, 'RWTC' = West Texas Intermediate (WTI)."""
    import xlrd  # the EIA still publishes old-style .xls files

    book = xlrd.open_workbook(file_contents=get(f"https://www.eia.gov/dnav/pet/hist_xls/{code}d.xls"))
    sheet = book.sheet_by_name("Data 1")
    out = []
    for i in range(3, sheet.nrows):  # three header rows, then date | price
        when, price = sheet.cell_value(i, 0), sheet.cell_value(i, 1)
        if isinstance(when, float) and isinstance(price, float):
            out.append((xlrd.xldate_as_datetime(when, book.datemode).date().isoformat(), price))
    if not out:
        raise ValueError(f"EIA {code}: no prices returned")
    return out


def ofgem_gas() -> list[tuple[str, float]]:
    """UK wholesale gas price for next-day delivery, monthly average, pence per therm.

    Ofgem publishes this as an interactive chart on its 'Wholesale market
    indicators' page; the numbers behind it are served by the chart tool (everviz)
    as CSV text inside the chart's set-up script, with dates written MM/DD/YYYY."""
    text = get_text("https://app.everviz.com/inject/YFsS4g98P/")
    out = [(f"{y}-{m}", float(v)) for m, _, y, v in re.findall(r'(\d\d)/(\d\d)/(\d{4})\\?";([\d.]+)', text)]
    if not out:
        raise ValueError("Ofgem gas: no prices found in the chart data")
    return sorted(out)


# ---------------------------------------------------------------- equities


def yahoo_close(symbol: str, start: str) -> list[tuple[str, float]]:
    """Daily closing level from Yahoo Finance's chart feed (unofficial; see above).
    symbol: '^FTSE' = FTSE 100."""
    t0 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    t1 = int(datetime.now(timezone.utc).timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.replace('^', '%5E')}"
           f"?period1={t0}&period2={t1}&interval=1d")
    result = json.loads(get(url))["chart"]["result"][0]
    offset = result["meta"].get("gmtoffset", 0)  # timestamps are UTC; shift to London time for the date
    closes = result["indicators"]["quote"][0]["close"]
    out = {}
    for ts, c in zip(result["timestamp"], closes):
        if c is not None:
            out[datetime.fromtimestamp(ts + offset, timezone.utc).date().isoformat()] = round(c, 2)
    if not out:
        raise ValueError(f"Yahoo {symbol}: no closing prices returned")
    return sorted(out.items())
