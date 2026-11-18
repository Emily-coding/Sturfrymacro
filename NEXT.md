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
  (~700 items instead of 84 components) like the OBR; our own price cap projection
  from gas and power futures (Ofgem's wholesale formula) instead of hand-set
  Cornwall Insight figures; pass-through of recent oil and sterling moves to pump
  prices (now held flat); a services/goods split of the contributions; compare our
  errors with Bank staff and consensus, and track them each month.
- Bank Rate alongside CPI (BoE data is daily-dated: convert to monthly first).
- G7 inflation comparison: the OECD price dataset is out of date for France, Italy
  and Japan (checked 2026-11-10); find another source.
- Contributions to CPI inflation by component.

## GDP
- OBR forecast vs outturn: `obr.historical_forecasts("UKGDP")` already reads the database.

## Decided against
- **Redesigning the section summary boxes** (2026-11-18): prototyped five alternatives
  to the current cards and kept the cards for now. Rejected:
  - *Ledger* (one table per section, forecasters as rows): cramped on phones, and
    markets ends up mixing Bank Rate and oil in one table.
  - *Headline* (one large number, a sentence and a dot strip per period): the dots
    repeated the numbers written underneath.
  - *Fan* (a mini chart from the latest outturn to each forecast): needs each forecast
    linked to the figure it continues, which dashboard.json doesn't record yet.
  - *Countdown* (the next ONS release date leads): financial markets has no release
    date, so it needs a fallback.
  - *Briefing* (a written note per section in a serif): the opening sentences
    would need generating in the builder.
  Fan and Briefing were the most distinctive, so start there if this comes back.
