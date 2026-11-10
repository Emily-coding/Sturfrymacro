# Sturfrymacro

Automatically refreshed chart data for the UK economy: **GDP**, **inflation**,
**labour markets**, **investment** and **trade**, plus a cleaned, continuously
updated dataset built from the ONS **Business Insights and Conditions Survey (BICS)**.
Data comes from the ONS, the OECD (for international comparisons), the Bank of
England and the OBR. Nowcasts (published ones first, then our own bottom-up
model) are next: see NEXT.md.

Each area has a *builder* that GitHub Actions runs every weekday morning. It
fetches the latest data, writes chart JSON into `data/<area>/` and commits only
when something has actually changed, so the git history is a log of real data
releases.

Started from [Emily-coding/ukmacro](https://github.com/Emily-coding/ukmacro); the
shared code still lives in a Python package called `ukmacro/`.

## Layout

```
ukmacro/                 shared source clients + the chart builder
  ons.py                 ONS time series by CDID (path lookup cached in ons_uris.json)
  oecd.py                OECD SDMX API
  boe.py                 Bank of England statistics database (CSV)
  nomis.py               Nomis (APS/LFS breakdowns)
  hmrc_trade.py          HMRC uktradeinfo (trade by CN8 code x country)
  obr.py                 OBR spreadsheet downloads (no API: scrapes /download/ links),
                         incl. the historical forecasts database
  dmp.py                 Decision Maker Panel spreadsheets
  boe_mpr.py             Bank of England Monetary Policy Report forecasts
  markets.py             market prices: US Treasury, Bundesbank, EIA oil, Ofgem gas, Yahoo FTSE 100
  lastgood.py            last-good store (see below)
  builder.py             config.yaml -> chart JSON
  bics.py                BICS wave download, parsing and the harvest store
builders/<area>/
  config.yaml            which charts, which series: edit this to add a chart
  build.py               runs the config; bespoke analyses go here too
                         (areas: gdp, inflation, labour-markets, investment, trade, bics, markets)
data/<area>/
  <slug>.json            one file per chart
  index.json             chart list with data_through / next_release / status
  last-good.json         last live value of every series
scripts/check_sources.py one known-good request per source: run when something goes red
.github/workflows/       one workflow per area, sharing _refresh.yml
NEXT.md                  to-do list + "Decided against"
```

## Dashboard

**https://emily-coding.github.io/Sturfrymacro/** (the page is `index.html`; GitHub Pages
serves it straight from `main`).

Six sections (GDP, inflation, investment, labour market, wages, financial markets),
each with a box of the latest outturn and forecasts for the next releases, and charts
showing outturn (solid) with the latest published forecasts (dashed):

- **Bank of England**, latest Monetary Policy Report: central projections, plus Bank
  staff near-term projections (CPI monthly for six months, GDP for this quarter and
  next). Reader: `ukmacro/boe_mpr.py`.
- **Outside forecasters' average**, the Bank's survey of about 20 forecasters,
  published with each MPR.
- **OBR**, latest Economic and fiscal outlook detailed economy tables (`ukmacro/obr.py`).

**Financial markets** (`builders/markets/`, refreshed every weekday morning) uses free
sources, all official except one:

| Chart | Source |
|---|---|
| 10-year yields | Bank of England (UK gilt), US Treasury, Bundesbank (German Bund) |
| Bank Rate, sterling ($/£, €/£) | Bank of England database |
| Brent and WTI oil | US Energy Information Administration (updated weekly, so up to a week behind) |
| UK wholesale gas | Ofgem, day-ahead price, monthly average (a few weeks behind) |
| FTSE 100 | Yahoo Finance's chart feed: **unofficial**, as FTSE Russell licenses its index data. If it breaks, the last good copy is kept and the run goes red |

The dashed lines on these charts are the market assumptions each forecast was
conditioned on (futures prices and market rate expectations at the cut-off), not
forecasts of their own: the Bank's Bank Rate path (MPR databank) and the OBR's table
1.9. The Bank's oil and gas paths are blanked out in its chart data (licensed), and
nobody publishes a 10-year yield path (the OBR's is for 20-year gilts). On the
dashboard, daily prices are thinned to weekly (the last trading day of each week).

`builders/dashboard/build.py` combines these with the other areas' chart files into
`data/dashboard/dashboard.json`; its docstring explains how forecasts are lined up
with outturn (level forecasts are rescaled to the latest ONS figure). The
`dashboard` workflow reruns it after each area refresh. To view it locally:
`python3 -m http.server` in the repo, then open http://localhost:8000.

## Market commentary

**https://emily-coding.github.io/Sturfrymacro/commentary.html** (linked from the dashboard
nav). A fortnightly summary of free external commentary on gilts, the Bank of England,
fiscal policy and sterling: themes, then one row per note with key points in our own words,
one short quote and a link to the original. Data live in `data/commentary/editions.json`.

A Claude Code cloud routine ("Sturfrymacro market commentary (fortnightly)", manage at
claude.ai/code/routines) fires every Tuesday at 07:00 UTC, adds an edition when the last one
is at least 13 days old, and commits only that file. It has no connectors attached.

## Short-term inflation forecast

`builders/inflation/stif.py`, run with the inflation builder every weekday. CPI
inflation for the next six months, built bottom-up in the style of the OBR's
short-term inflation forecast:

1. All 84 CPI components and their weights, from the ONS MM23 file (`ukmacro/mm23.py`).
2. Each component's month-on-month change = its usual pattern for that calendar month
   (last 8 years, skipping 2020-21) + its average monthly change over the past year.
3. Electricity, gas and motor fuels follow the Bank of England's latest staff
   projections (which build in the Ofgem price cap and oil and gas futures).
4. Known policy changes can be set by hand in `builders/inflation/stif_assumptions.yaml`.
5. Components are added up with CPI's January chain-linking; only the change from
   the latest month is used, applied to the published headline index.

Backtest (forecasts made monthly since 2017, excluding 2020-21, with no energy
information): average error 0.23 percentage points one month ahead and 0.89 six
months ahead, against 0.31 and 1.10 for assuming inflation stays where it is. The
figures are refreshed each month in `data/inflation/stif.json`. The forecast appears
on the dashboard as "Sturfrymacro" and in `img/inflation/cpi-stif.png`.

## BICS dataset

BICS is published as one spreadsheet per fortnightly wave, with questions rotating
in and out. The `bics` builder turns it into continuous series:

1. **Harvest (automatic, minimal downloads).** Each wave file carries the full
   history of every question it asks, so only the newest file asking a question is
   needed. The builder reads the newest wave's question log (which records the
   waves every question was asked in) and downloads just those files. After that,
   it takes each new wave as it is published, one file a fortnight. Sheets are
   merged into `data/bics/harvest/`, one CSV per question, and newer files win
   where they overlap, which picks up ONS revisions.
2. **Menus.** `data/bics/question_log.csv` lists every question BICS has ever
   asked and in which waves. `data/bics/catalogue.csv` lists the questions
   harvested so far, with population, weighting (by count or employment) and
   answer options.
3. **Curate (you edit this).** `builders/bics/series.yaml` lists the series you
   want: a few words of the question, the answers to add up, and a measure
   (share, net balance, or 100 minus). Instructions are at the top of the file.
   Every series is built for all businesses, each industry and each size band.
   Reworded questions can be joined as `versions`. Where a series switches version,
   the chart file lists a `version_breaks` entry and the CSV's `version` column
   changes: check the levels either side before reading a trend across it.

Outputs:
- `data/bics/bics_series.csv`: the complete curated dataset, long format
  (`series_id, topic, label, measure, weighting, wave, ref_start, ref_end, group, breakdown, value, version`)
- `data/bics/series/<id>.json`: one chart file per series, one column per breakdown
- `data/bics/index.json`: series list with coverage and the latest total
- `data/bics/charts/` and `img/bics/`: charts defined in the `charts:` section of
  `series.yaml` (e.g. AI adoption, kinds of AI, AI and headcount), as PNGs

```r
bics <- read.csv("https://raw.githubusercontent.com/Emily-coding/Sturfrymacro/main/data/bics/bics_series.csv")
```

Caveats: values are percentages of *businesses* (a sole trader counts the same as
a large employer) unless `weighting: employment`. Points are irregular because
questions rotate, and are dated by the survey's reference period (two weeks early
on, a calendar month later). Suppressed cells (`[c]`) are dropped, and a
combined measure is left blank where any of its parts was suppressed.

## Running locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python builders/gdp/build.py
.venv/bin/python scripts/check_sources.py
```

No API keys are needed for anything currently configured. UN Comtrade (planned)
will need one: put it in a repo secret, never in the code.

## Adding a chart

Add an entry to `builders/<area>/config.yaml`. An ONS series needs its CDID and
dataset, which are shown on the series page, e.g. `.../timeseries/npel/qna` →
`cdid: NPEL, dataset: QNA`. The builder finds the URL path itself the first time
and caches it in `ukmacro/ons_uris.json`. Chart options are documented at the top
of `ukmacro/builder.py`.

## Chart JSON

```json
{"slug": "gdp-level", "category": "gdp", "title": "...", "type": "line", "freq": "Q",
 "units": "Index, 2019 Q4 = 100", "source": "...", "note": "...",
 "series": [{"key": "gdp", "label": "GDP", "_source": "live", "_date": "2026-Q2", "source_url": "..."}],
 "data_through": "2026-Q2", "next_release": "2026-11-12", "status": "ok",
 "data": [{"date": "2026-Q2", "gdp": 106.2, "gdp_per_head": 101.7}]}
```

Dates are `YYYY`, `YYYY-Qn` or `YYYY-MM` for every source. `next_release` comes
from the ONS series metadata.

## Charts as PNGs

`ukmacro/charts.py` renders any chart JSON to a PNG in one house style. Every chart
gets a PNG in `img/<category>/`, re-rendered only when its data changes.

- 25 x 15 cm at 200 dpi (1969 x 1181 px), 256-colour PNG, about 60 KB each
- Font: Source Sans 3 (open licence, bundled in `assets/fonts/`, so output matches on any machine)
- Colours: the "blue-amber" theme, built from the Blue, Yellow Vivid and Blue Grey
  scales of svengraziani/ui-design's professional UI palettes. Series in fixed order:
  blue `#186FAF`, amber `#DE911D`, teal `#27AB83`, red `#BA2525`. Totals in `#102A43`.
  White background, text `#102A43` / `#486581`, axes and source `#627D98`, gridlines
  `#D9E2EC`. Every series pair passes a colour-blind check. Other themes are in
  `THEMES` in `charts.py`; change `DEFAULT_THEME` to switch. The scheme, with R and
  Python snippets for reuse, is in `assets/colour-scheme.md` (and `.json`).

## AI product definition

`definitions/ai_trade.yaml` defines AI-relevant goods and services in tiers (core,
extended, full) with product codes, built on the ONS AI thematic account's CPA list
in `definitions/ai_cpa.yaml`. Key AI dates (`definitions/ai_events.yaml`) are drawn
as vertical lines on the BICS AI charts.

## Rules

**A failed fetch is a failed run.** If a source fails, the chart is still written
using that series' entry in `last-good.json`. The series is tagged
`_source: last-good`, the chart's `status` becomes `stale`, and the build exits 1
so the Action goes red. The commit step runs regardless, so a stale-but-labelled
chart still publishes. Nothing is ever silently passed off as fresh.

**Commit only on change.** Output files contain no build timestamps, and
`last-good.json` entries are rewritten only when the data changes. A run on a day
with no new releases therefore produces no commit.

**Push with retry.** Area jobs can finish at the same moment. The commit step
rebases and retries up to five times. Keep that loop in any new workflow.

## Data caveats

- **LFS** (employment, unemployment, inactivity): falling response rates and
  reweighting mean ONS labels these estimates "official statistics in development".
  Check headline LFS moves against HMRC RTI payrolled employees.
- **Trade**: headline totals include precious metals, which can swing monthly
  figures by billions.
- **OBR** forecasts are only published as spreadsheets. Each vintage is saved
  under its own name and old ones are never overwritten.
- **Seasonal adjustment**: ONS national accounts, labour market and trade series used
  here are seasonally adjusted (the chart note says so). CPI/CPIH 12-month rates are
  not, because comparing with the same month a year earlier largely removes seasonal
  patterns; never compare a raw month-on-month CPI change across months without
  adjusting it. BICS and HMRC product data are not seasonally adjusted. Say which in
  every chart `note`.
- **CPIH vs CPI**: CPIH is the ONS's lead measure, but the Bank of England's 2% target
  is on CPI, so CPI is the one to use for policy questions.

## Working together

Every change goes through a pull request reviewed by the other person. The
step-by-step guide, what to check when reviewing, and how to handle the bot's data
commits are in [CONTRIBUTING.md](CONTRIBUTING.md). To-dos go in GitHub Issues.
