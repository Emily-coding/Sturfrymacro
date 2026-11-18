"""Household energy prices for the short-term inflation forecast: the Ofgem price cap
and weekly pump prices. One small function each, like markets.py.

Source                      What                                          Updated
Ofgem                       Default tariff cap levels, every cap period    quarterly, about five
                            since 2017 (typical annual bill, £, inc VAT)   weeks before each cap starts
                                                                           (late Feb, May, Aug, Nov)
Department for Energy       UK average pump prices for petrol and diesel,  weekly (Monday prices,
  Security and Net Zero     with fuel duty and VAT rates, since 2003       published Tuesday)

Why these two: electricity and gas prices in CPI move almost one-for-one with the
cap in the month it changes (Jan, Apr, Jul, Oct), and CPI motor fuels track the
monthly average of the weekly pump prices to within about 0.05 percentage points a
month. Both are known before the ONS publishes, so they are the most reliable part
of a near-term inflation forecast.
"""

from __future__ import annotations

import csv
import io
import re

from .http import get, get_text

OFGEM_PAGE = ("https://www.ofgem.gov.uk/energy-regulation/domestic-and-non-domestic/"
              "energy-pricing-rules/energy-price-cap/energy-price-cap-default-tariff-levels")
FUEL_PAGE = "https://www.gov.uk/government/statistics/weekly-road-fuel-prices"
MONTH = {"Jan": "01", "Apr": "04", "Jul": "07", "Oct": "10"}


# ---------------------------------------------------------------- Ofgem price cap

def price_cap() -> dict[str, dict[str, float]]:
    """Ofgem price cap for each cap period, keyed by the month it starts:
    {'2026-10': {'electricity': 1715.1, 'gas': 1263.0}, ...}.

    The figure is the annual bill at typical consumption, paying by direct debit
    (the most common way to pay), single-rate electricity meter, including VAT. Only
    the percentage changes matter for the forecast. The cap for the next quarter
    appears here as soon as Ofgem announces it.

    Read from Ofgem's 'Default tariff cap level' workbook, sheet '1b Historical
    level tables'. The workbook's file name changes with each update (v1.31, v1.32,
    ...), so we look for the newest link on Ofgem's cap levels page."""
    import openpyxl

    page = get_text(OFGEM_PAGE)
    links = re.findall(r'href="([^"]*Default-tariff-cap-level[^"]*\.xlsx)"', page)
    if not links:
        raise ValueError("Ofgem: no 'Default tariff cap level' workbook linked from the cap levels page")
    # newest first: by the upload folder (/2026-08/) then the version number
    def key(u):
        folder = re.search(r"/(\d{4}-\d\d)/", u)
        ver = re.search(r"v(\d+)\.(\d+)", u)
        return (folder.group(1) if folder else "", tuple(map(int, ver.groups())) if ver else (0, 0))
    url = max(links, key=key)
    url = url if url.startswith("http") else "https://www.ofgem.gov.uk" + url

    wb = openpyxl.load_workbook(io.BytesIO(get(url)), read_only=True, data_only=True)
    rows = list(wb["1b Historical level tables"].iter_rows(values_only=True))

    # The sheet has blocks side by side (single-rate electricity, multi-register
    # electricity, gas...), one for each payment method stacked down the page. We
    # want the first 'Total inc VAT' row under 'Typical consumption' (direct debit).
    def find(label, start=0, col=None):
        for i in range(start, len(rows)):
            for j, v in enumerate(rows[i]):
                if isinstance(v, str) and v.strip().startswith(label) and (col is None or j == col):
                    return i, j
        raise ValueError(f"Ofgem workbook: '{label}' not found")

    typical_row, _ = find("Typical consumption")
    total_row, _ = find("Total inc VAT", typical_row)
    out: dict[str, dict[str, float]] = {}
    for fuel, block in (("electricity", "Electricity: Single-Rate"), ("gas", "Gas")):
        _, col = find(block)
        header = rows[typical_row]
        # this block's columns run up to the next block's label (or the sheet's edge);
        # there can be blank spacer columns inside a block
        later = [j for j, v in enumerate(header) if j > col and isinstance(v, str) and v.startswith("Typical")]
        for j in range(col + 1, later[0] if later else len(header)):
            m = re.match(r"(Jan|Apr|Jul|Oct)\w* (\d{4})", str(header[j] or ""))  # 'Oct 2026 - Dec 2026'
            value = rows[total_row][j]
            if m and isinstance(value, (int, float)):
                out.setdefault(f"{m.group(2)}-{MONTH[m.group(1)]}", {})[fuel] = float(value)
    if not out:
        raise ValueError("Ofgem workbook: no cap levels read")
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- pump prices

def pump_prices() -> list[dict]:
    """Weekly UK average pump prices since 2003, oldest first:
    [{'date': '2026-11-16', 'petrol': 167.07, 'diesel': 196.77,
      'petrol_duty': 52.95, 'diesel_duty': 52.95, 'vat': 20.0}, ...]
    Prices and duty in pence per litre, VAT in %.

    The government publishes two CSV files (2003-2017 and 2018 onwards) whose links
    change every week, so we read them from the statistics page."""
    page = get_text(FUEL_PAGE)
    links = sorted(set(re.findall(r'https://assets\.publishing\.service\.gov\.uk/[^"]+\.csv', page)))
    if not links:
        raise ValueError("Pump prices: no CSV files linked from the weekly road fuel prices page")
    out = {}
    for url in links:
        text = get(url).decode("utf-8-sig", errors="replace")
        for r in csv.reader(io.StringIO(text)):
            if len(r) < 7 or not re.match(r"\d\d/\d\d/\d{4}$", r[0].strip()):
                continue
            d, m, y = r[0].strip().split("/")
            try:
                petrol, diesel, pduty, dduty, vat = (float(x) for x in r[1:6])
            except ValueError:
                continue
            out[f"{y}-{m}-{d}"] = {"date": f"{y}-{m}-{d}", "petrol": petrol, "diesel": diesel,
                                   "petrol_duty": pduty, "diesel_duty": dduty, "vat": vat}
    if not out:
        raise ValueError("Pump prices: no rows read")
    return [out[k] for k in sorted(out)]
