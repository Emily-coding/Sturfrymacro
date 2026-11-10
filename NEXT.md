# NEXT — Sturfrymacro

Bigger items here; day-to-day to-dos go in GitHub Issues.

## Nowcasts
- ~~Published forecasts: Bank of England MPR (incl. staff near-term projections),
  outside forecasters' average, OBR~~ done (dashboard, builders/dashboard/).
- NIESR monthly GDP tracker: published only as an article (no data file), so not
  included. Could parse the article text if it's wanted.
- Keep past forecast vintages (the BoE databank already holds every report since 2004;
  the OBR historical forecasts database has the rest) for forecast-vs-outturn charts.
- **Our own bottom-up model**: method to be agreed first (written up as a short note
  next to the code), then built; add it to the dashboard as another forecaster.

## Investment
- A monthly indicator: the ONS publishes business investment quarterly only. Candidates
  to look at: capital goods imports (monthly trade), construction new work, and the
  DMP's investment expectations.

## Inflation
- Short-term forecast (builders/inflation/stif.py) next steps: item-level data
  (~700 items instead of 84 components) like the OBR; our own fuel model from weekly
  pump prices and Ofgem cap assumptions, instead of the Bank's energy paths; a
  services/goods split of the contributions; track our forecast errors each month.
- Bank Rate alongside CPI (BoE data is daily-dated: convert to monthly first).
- G7 inflation comparison: the OECD price dataset is out of date for France, Italy
  and Japan (checked 2026-11-10); find another source.
- Contributions to CPI inflation by component.

## GDP
- OBR forecast vs outturn: `obr.historical_forecasts("UKGDP")` already reads the database.
