#!/usr/bin/env python3
"""Fiscal builder: the OBR ready reckoner as data for fiscal.html.

Writes data/fiscal/reckoner.json with two "bases" the page can switch between:

    mar25   the OBR's ready reckoner exactly as published (elasticities and baseline
            consistent with the March 2025 EFO), read from the workbook's formulas
            by workbook.py
    oct26   the same elasticities rescaled to the latest EFO (rescale.py): each tax's
            effects scaled to its latest forecast, debt interest matched to the latest
            debt interest ready reckoner, and baselines (GDP, debt, prices...) updated

Each basis holds: years; baseline paths of the determinants the calculations need;
"vectors" (which economic input each tax/spending line reads, rescaled and lagged
as the OBR does); "terms" (one per elasticity: line, year, vector, year of vector,
£ million effect); the debt-interest financing loop; baseline tax and spending
lines; and baseline aggregates. assets/reckoner.js does the arithmetic.

Not part of the scheduled refresh: the OBR publishes these files at each fiscal
event, so rerun this by hand after an EFO: python builders/fiscal/build.py
"""

from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

import rescale  # noqa: E402
import workbook  # noqa: E402
import openpyxl  # noqa: E402

from ukmacro import obr  # noqa: E402
from ukmacro.http import get  # noqa: E402

WORKBOOK_URL = "https://obr.uk/download/economic-and-fiscal-outlook-ready-reckoner/"
ARTICLE_URL = "https://articles.obr.uk/the-obr-ready-reckoner/index.html"
OUT = REPO / "data" / "fiscal" / "reckoner.json"

# The OBR's economic inputs, in its order, with what a change means. "pct" inputs are
# per cent differences in a level; the rest are differences in the units shown.
INPUTS = [
    # group, id, label, unit, help
    ("Labour market and incomes", "earnings", "Average earnings (level)", "pct", "Average earnings per employee."),
    ("Labour market and incomes", "employment", "Employment (level)", "pct", "Number of people in work. Feeds income tax and NICs, and working-age benefits via the change in millions."),
    ("Labour market and incomes", "unemployment", "Unemployment", "millions", "ILO unemployment, in millions of people. Feeds universal credit."),
    ("Labour market and incomes", "mixed_income", "Self-employment income", "pct", "Mixed income: profits of the self-employed. Self-assessment tax is paid a year later."),
    ("Labour market and incomes", "earnings_growth_q2", "Earnings growth in Q2", "pt", "Year-on-year earnings growth in April to June: the earnings leg of the pensions triple lock."),
    ("Labour market and incomes", "earnings_growth_q3", "Earnings growth in Q3", "pt", "Year-on-year earnings growth in July to September: used to uprate some benefits."),
    ("Spending and GDP", "nominal_consumption", "Consumer spending (cash)", "pct", "Nominal household consumption: the VAT base."),
    ("Spending and GDP", "real_consumption", "Consumer spending (volume)", "pct", "Real household consumption: drives alcohol and fuel duty volumes."),
    ("Spending and GDP", "durables", "Spending on durable goods", "pct", "Nominal consumption of durables (cars, furniture...)."),
    ("Spending and GDP", "real_gdp", "Real GDP", "pct", "Used for fuel duty and air passenger duty volumes."),
    ("Spending and GDP", "nominal_gdp_fy", "Nominal GDP (financial year)", "pct", "Cash GDP: other receipts, some spending lines, and the denominator for '% of GDP'."),
    ("Spending and GDP", "nominal_gdp_cy", "Nominal GDP (calendar year)", "pct", "Used for VAT."),
    ("Spending and GDP", "nominal_gdp_march", "Nominal GDP (centred on March)", "pct", "The denominator for debt as a % of GDP."),
    ("Spending and GDP", "imports", "Imports", "pct", "Customs duties base."),
    ("Spending and GDP", "dwelling_investment", "Private housing investment", "pct", ""),
    ("Companies", "profits_nonfinancial", "Non-financial company profits", "pct", "Non-oil, non-financial profits: onshore corporation tax (and SA tax on dividends a year later)."),
    ("Companies", "profits_financial", "Financial company profits", "pct", "Onshore corporation tax from banks and other financial firms."),
    ("Companies", "pnfc_investment", "Business investment (PNFCs)", "pct", "Private non-financial companies' investment: capital allowances reduce corporation tax."),
    ("Asset markets", "equity_prices", "Share prices", "pct", "FTSE All-Share level: capital gains tax (a year later), stamp duty on shares, inheritance tax."),
    ("Asset markets", "house_prices", "House prices", "pct", "Stamp duty land tax, inheritance tax, capital gains tax."),
    ("Asset markets", "property_transactions", "Property transactions", "pct", "Number of property sales: stamp duty land tax."),
    ("Asset markets", "personal_deposits", "Household bank deposits", "pct", "Inheritance tax base."),
    ("Prices", "cpi_inflation", "CPI inflation", "pt", "Financial-year CPI inflation. Raises the CPI price level, which uprates duties, business rates and some spending."),
    ("Prices", "cpi_q3", "CPI inflation in September (Q3)", "pt", "Q3 CPI inflation: benefits are uprated by it the following April. Change it alongside CPI inflation."),
    ("Prices", "rpi_inflation", "RPI inflation", "pt", "Financial-year RPI inflation: index-linked gilt payments (debt interest)."),
    ("Prices", "rpi_q2", "RPI inflation in Q2", "pt", "Q2 RPI inflation: some duties are uprated by the RPI."),
    ("Energy and exchange rate", "oil_price", "Oil price", "$/barrel", "Brent, US dollars a barrel: North Sea taxes and fuel duty."),
    ("Energy and exchange rate", "gas_price", "Gas price", "p/therm", "Wholesale gas, pence a therm: North Sea taxes and the energy profits levy."),
    ("Energy and exchange rate", "electricity_price", "Wholesale electricity price", "£/MWh", "Electricity generator levy."),
    ("Energy and exchange rate", "usd_gbp", "Dollar-sterling exchange rate", "$", "Dollars per pound."),
    ("Interest rates", "bank_rate", "Bank Rate", "pt", "Interest on reserves (debt interest) and on government deposits."),
    ("Interest rates", "deposit_rate", "Deposit rates", "pt", "Rates paid to savers: tax on savings income."),
    ("Interest rates", "gilt_rate", "Gilt yields", "pt", "Market interest rates on government bonds: debt interest on new borrowing."),
]


def main() -> None:
    raw = get(WORKBOOK_URL, timeout=180)
    mar25 = workbook.parse(raw)
    # employment and unemployment rates for display, from the March 2025 EFO economy tables
    _, eco, _ = obr.efo_economy(2025, 3)
    eco = openpyxl.load_workbook(io.BytesIO(eco), read_only=True, data_only=True)
    mar25["shown"].update(rescale.shown_rates(eco, mar25["years"]))
    oct26 = rescale.build(mar25)
    out = {
        "generated": date.today().isoformat(),
        "source": {"workbook": WORKBOOK_URL, "article": ARTICLE_URL},
        "inputs": [{"group": g, "id": i, "label": lab, "unit": u, "help": h} for g, i, lab, u, h in INPUTS],
        "bases": {"oct26": oct26, "mar25": mar25},
        "default_basis": "oct26",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")) + "\n")
    n = {k: len(b["terms"]) for k, b in out["bases"].items()}
    print(f"[fiscal] reckoner.json: {n} terms, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
