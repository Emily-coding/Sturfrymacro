# NEXT — Sturfrymacro

Bigger items here; day-to-day to-dos go in GitHub Issues.

## Nowcasts
- **Published nowcasts and short-term forecasts** (free sources only), next two
  quarters of GDP growth and CPI inflation, each vintage kept:
  - Bank of England staff nowcasts: Monetary Policy Report chart-data spreadsheet (quarterly).
  - OBR: quarterly GDP and CPI path from the Economic and Fiscal Outlook detailed
    tables (twice a year; `ukmacro/obr.py` already downloads these).
  - NIESR monthly GDP tracker.
  - Bank of England Market Participants Survey medians (in place of Bloomberg consensus,
    which can't be republished).
- **Our own bottom-up model**: method to be agreed first (written up as a short note
  next to the code), then built.

## Inflation
- Bank Rate alongside CPI (BoE data is daily-dated: convert to monthly first).
- G7 inflation comparison: the OECD price dataset is out of date for France, Italy
  and Japan (checked 2026-11-10); find another source.
- Contributions to CPI inflation by component.

## GDP
- OBR forecast vs outturn: `obr.historical_forecasts("UKGDP")` already reads the database.
