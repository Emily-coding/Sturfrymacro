#!/usr/bin/env python3
"""Dashboard builder: combines outturn data with published forecasts.

Reads the chart files the other builders write (data/gdp, data/inflation, ...),
fetches the latest forecasts (forecasts.py), and writes one file the dashboard page
(index.html at the repo root) draws from:

    data/dashboard/dashboard.json   sections -> charts (outturn + forecast lines) + summary box
    data/dashboard/forecasts.json   the raw forecasts, as published (kept for reuse)

How forecasts are drawn:
- Levels (GDP, business investment): forecasters publish their own level paths,
  which don't match today's ONS data exactly (revisions, different base years). So
  each path is rescaled to equal the latest ONS figure in that quarter: the line
  shows the forecaster's *growth* from where the data are now.
- Rates (inflation, unemployment, pay growth) are drawn as published.
- Only forecast points after the latest outturn are shown, and each forecast line
  starts from the latest outturn point so it joins the data.
- Quarterly forecasts on monthly charts sit at the middle month of the quarter for
  CPI (a quarterly average) and the last month for LFS and earnings figures (which
  are three-month averages dated by their final month, so Q3 = "3 months to Sep").

Run after the other builders. If a forecast source fails, its last good forecasts
are kept from the previous forecasts.json and the run exits 1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

import forecasts  # noqa: E402
import taylor  # noqa: E402

OUT = REPO / "data" / "dashboard"
START_M, START_Q = "2022-01", "2022-Q1"  # charts start here (late enough for forecasts to be legible)...
END_Q = "2028-Q4"                         # ...and forecasts stop here

# Fixed colour per forecaster, so each looks the same on every chart (house palette)
FORECASTERS = {
    "boe": {"colour": "#DE911D"},  # amber
    "obr": {"colour": "#27AB83"},  # teal
    "ext": {"colour": "#BA2525"},  # red
    "own": {"colour": "#102A43"},  # navy: our own short-term inflation forecast
}
OUTTURN = "#186FAF"        # blue
OUTTURN_SECOND = "#9FB3C8"  # grey: a second outturn measure on the same chart
TAYLOR_COLOUR = "#102A43"   # navy: policy rules on the Bank Rate chart


# ---------------------------------------------------------------- reading outturn

def chart_file(category: str, slug: str) -> dict:
    return json.loads((REPO / "data" / category / f"{slug}.json").read_text())


def series(chart: dict, key: str) -> list[list]:
    """[[date, value], ...] for one series of a chart file, gaps dropped."""
    return [[row["date"], row[key]] for row in chart["data"] if row.get(key) is not None]


# ---------------------------------------------------------------- dates

def q_to_month(q: str, where: str) -> str:
    """'2026-Q3' -> '2026-08' (where='mid') or '2026-09' (where='end')."""
    y, n = q.split("-Q")
    month = int(n) * 3 - (1 if where == "mid" else 0)
    return f"{y}-{month:02d}"


def q_add(q: str, k: int) -> str:
    """Quarter arithmetic: q_add('2026-Q4', 1) == '2027-Q1'."""
    y, n = int(q[:4]), int(q[-1]) - 1 + k
    return f"{y + n // 4}-Q{n % 4 + 1}"


def month_label(m: str) -> str:
    import calendar
    return f"{calendar.month_abbr[int(m[5:7])]} {m[:4]}"


def q_label(q: str) -> str:
    return f"{q[-2:]} {q[:4]}"


# ---------------------------------------------------------------- building lines

def rescale(path: list, outturn: list) -> list:
    """Rescale a forecaster's level path so it equals the latest outturn in the
    latest quarter both share, and keep only the quarters after it."""
    have = dict(map(tuple, path))
    anchor = next((d for d, _ in reversed(outturn) if d in have), None)
    if anchor is None:
        return []
    factor = dict(map(tuple, outturn))[anchor] / have[anchor]
    return [[d, round(v * factor, 3)] for d, v in path if d > anchor]


def after(points: list, last: str, end: str) -> list:
    """Points dated after the latest outturn and no later than `end`."""
    return [[d, v] for d, v in points if last < d <= end]


def line(name: str, points: list, colour: str, kind: str, join: list | None = None) -> dict | None:
    """One chart line. Forecast lines start from `join` (the latest outturn point)."""
    if kind == "forecast":
        if not points:
            return None
        points = [join] + points if join else points
    return {"name": name, "kind": kind, "colour": colour, "data": points}


def forecast_lines(fc: dict, sources: dict, var: str, last_point: list, end: str,
                   place=None, rescale_to: list | None = None, extra: dict | None = None) -> list:
    """Forecast lines for one variable, one per forecaster.
    place: turns a quarter into the chart's date format (None = keep quarters).
    rescale_to: outturn to rescale level paths onto. extra: {forecaster: points}
    to put in front (e.g. Bank staff monthly near-term projections)."""
    out = []
    for who, pts in fc.get(var, {}).items():
        pts = rescale(pts, rescale_to) if rescale_to else [list(p) for p in pts]
        pts = [[place(d) if place else d, round(v, 3)] for d, v in pts if d <= END_Q]
        pts = after(pts, last_point[0], end)
        if extra and who in extra:  # near-term detail first, then the quarterly path beyond it
            near = after([list(p) for p in extra[who]], last_point[0], end)
            pts = near + [p for p in pts if not near or p[0] > near[-1][0]]
        name = f"{sources[who]['name']} ({sources[who]['vintage']})"
        ln = line(name, pts, FORECASTERS[who]["colour"], "forecast", last_point)
        if ln:
            out.append(ln)
    return out


# ---------------------------------------------------------------- summary-box helpers

def qoq_from_levels(path: list, q: str) -> float | None:
    """Quarter-on-quarter % growth in quarter q from a level path."""
    d = dict(map(tuple, path))
    prev = q_add(q, -1)
    return round((d[q] / d[prev] - 1) * 100, 1) if q in d and prev in d else None


def calendar_year_growth(path: list, year: int) -> float | None:
    """% growth in the annual total of a quarterly level path (calendar year)."""
    d = dict(map(tuple, path))
    cur = [d.get(f"{year}-Q{n}") for n in range(1, 5)]
    prev = [d.get(f"{year - 1}-Q{n}") for n in range(1, 5)]
    if None in cur or None in prev:
        return None
    return round((sum(cur) / sum(prev) - 1) * 100, 1)


def value_at(points: list, when) -> float | None:
    return next((round(v, 1) for d, v in points if d == when), None)


def items(sources: dict, values: dict) -> list:
    """[{who, colour, value}] for the forecasters that have a value."""
    return [{"who": sources[w]["name"], "colour": FORECASTERS[w]["colour"], "value": v}
            for w, v in values.items() if v is not None and w in sources]


# ---------------------------------------------------------------- sections

def gdp_section(fc, sources):
    monthly_chart, level_chart = chart_file("gdp", "gdp-monthly"), chart_file("gdp", "gdp-level")
    growth_chart, g7_chart = chart_file("gdp", "gdp-growth"), chart_file("gdp", "gdp-g7-qoq")

    # Monthly GDP rebased so that the average of Oct-Dec 2019 = 100, matching the
    # quarterly chart's 2019 Q4 = 100
    m = series(monthly_chart, "gva")
    base = sum(v for d, v in m if d in ("2019-10", "2019-11", "2019-12")) / 3
    m = [[d, round(v / base * 100, 2)] for d, v in m if d >= START_M]

    q = [p for p in series(level_chart, "gdp") if p[0] >= START_Q]
    last_q = q[-1][0]
    q_fc = forecast_lines(fc, sources, "gdp_level", q[-1], END_Q, rescale_to=q)

    # G7: growth so far this year, compounded from quarterly growth. Every country is
    # cut at the UK's latest quarter so all cover the same period: when the US
    # publishes Q3 before the UK, the comparison stays at Q2 until the UK catches up.
    uk = series(g7_chart, "gbr")
    cutoff = uk[-1][0]                      # e.g. '2026-Q2'
    year = cutoff[:4]
    period = "Q1" if cutoff.endswith("Q1") else f"Q1–{cutoff[-2:]}"
    bars = []
    for s in g7_chart["series"]:
        qs = [(d, v) for d, v in series(g7_chart, s["key"]) if d.startswith(year) and d <= cutoff]
        if not qs:
            continue
        growth = 1.0
        for _, v in qs:
            growth *= 1 + v / 100
        behind = qs[-1][0] < cutoff         # this country hasn't published the UK's latest quarter yet
        bars.append({"name": s["label"], "value": round((growth - 1) * 100, 2),
                     "period": f"to {qs[-1][0][-2:]} only" if behind else "",  # the title gives the period
                     "colour": OUTTURN if s["key"] == "gbr" else "#9FB3C8"})
    bars.sort(key=lambda b: -b["value"])

    # Box: latest quarter and month, then forecasts for the next two quarters
    qoq = series(growth_chart, "qoq")
    nxt, nxt2 = q_add(last_q, 1), q_add(last_q, 2)
    levels = {w: fc["gdp_level"][w] for w in fc.get("gdp_level", {})}
    def quarter_forecasts(qq):
        vals = {w: qoq_from_levels(p, qq) for w, p in levels.items()}
        for w, p in fc.get("gdp_qoq", {}).items():  # near-term nowcasts beat the rounded index
            vals[w] = value_at(p, qq) if value_at(p, qq) is not None else vals.get(w)
        return items(sources, vals)
    mom = round((m[-1][1] / m[-2][1] - 1) * 100, 1)
    box = {
        "latest": [
            {"label": "GDP, quarter on quarter", "period": q_label(qoq[-1][0]), "value": qoq[-1][1], "unit": "%"},
            {"label": "Monthly GDP, month on month", "period": month_label(m[-1][0]), "value": mom, "unit": "%"},
        ],
        "forecasts": [
            {"heading": f"{q_label(nxt)} GDP growth, q/q", "release": level_chart.get("next_release"),
             "unit": "%", "items": quarter_forecasts(nxt)},
            {"heading": f"{q_label(nxt2)} GDP growth, q/q", "unit": "%", "items": quarter_forecasts(nxt2)},
        ],
    }
    return {
        "id": "gdp", "title": "GDP",
        "box": box,
        "charts": [
            {"title": "Real GDP, quarterly", "units": "Index, 2019 Q4 = 100", "freq": "Q",
             "lines": [line("ONS outturn", q, OUTTURN, "outturn")] + q_fc,
             "note": "Chained volumes, seasonally adjusted. Forecast lines apply each forecaster's growth to the latest ONS level.",
             "source": "ONS; Bank of England; OBR"},
            {"title": "Real GDP, monthly", "units": "Index, Oct–Dec 2019 average = 100", "freq": "M",
             "lines": [line("ONS outturn", m, OUTTURN, "outturn")],
             "note": "Monthly GVA at basic prices, chained volumes, seasonally adjusted. None of the forecasters publish monthly GDP forecasts.",
             "source": "ONS Monthly GDP estimate"},
            {"title": f"Real GDP growth in {period} {year}, G7", "units": f"% change from Q4 {int(year) - 1}", "type": "bars",
             "bars": bars,
             "note": "Quarterly growth compounded over the year so far. All countries are cut at the UK's latest published quarter, so each covers the same period.",
             "source": "OECD Quarterly National Accounts"},
        ],
    }


def inflation_section(fc, sources):
    chart = chart_file("inflation", "cpi-headline")
    cpi = [p for p in series(chart, "cpi") if p[0] >= START_M]
    last = cpi[-1]
    lines = forecast_lines(fc, sources, "cpi", last, q_to_month(END_Q, "mid"),
                           place=lambda d: q_to_month(d, "mid"), extra=fc.get("cpi_monthly"))
    nxt_month = f"{int(last[0][:4]) + (last[0][5:] == '12')}-{int(last[0][5:]) % 12 + 1:02d}"
    q_now = f"{nxt_month[:4]}-Q{(int(nxt_month[5:]) - 1) // 3 + 1}"
    box = {
        "latest": [{"label": "CPI inflation, 12-month rate", "period": month_label(last[0]), "value": last[1], "unit": "%"}],
        "forecasts": [
            {"heading": f"{month_label(nxt_month)} CPI inflation", "release": chart.get("next_release"), "unit": "%",
             "items": items(sources, {w: value_at(p, nxt_month) for w, p in fc.get("cpi_monthly", {}).items()})},
            {"heading": f"{q_label(q_now)} average CPI inflation", "unit": "%",
             "items": items(sources, {w: value_at(p, q_now) for w, p in fc.get("cpi", {}).items()})},
        ],
    }
    return {
        "id": "inflation", "title": "Inflation", "box": box,
        "charts": [{"title": "CPI inflation", "units": "% change on a year earlier", "freq": "M",
                    "lines": [line("ONS outturn", cpi, OUTTURN, "outturn")] + lines,
                    "note": "Bank of England line is Bank staff's monthly projection for the next six months, then its quarterly projection. OBR and outside forecasters are quarterly averages, plotted mid-quarter. Sturfrymacro is our own short-term forecast, built up from 84 CPI components.",
                    "source": "ONS; Bank of England; OBR"},
                   essentials_chart(fc, sources)],
    }


# Essentials: one colour per component; the Bank's projection for it (variable in
# forecasts.json) is dashed in the same colour.
ESSENTIALS = [
    ("food", "Food and non-alcoholic drinks", "#27AB83", "cpi_food_monthly"),
    ("energy", "Household energy", "#BA2525", "cpi_elec_gas_monthly"),
    ("fuels", "Motor fuels", "#DE911D", "cpi_fuels_monthly"),
]


def essentials_chart(fc, sources):
    """Inflation in food, household energy and motor fuels (ONS), with Bank of England
    staff projections for the next six months."""
    chart = chart_file("inflation", "cpi-essentials")
    lines, forecasts = [], []
    for key, name, colour, var in ESSENTIALS:
        pts = [p for p in series(chart, key) if p[0] >= START_M]
        lines.append(line(name, pts, colour, "outturn"))
        proj = after([list(p) for p in fc.get(var, {}).get("boe", [])], pts[-1][0], "9999")
        ln = line(f"{name}, Bank of England projection", proj, colour, "forecast", pts[-1])
        if ln:
            forecasts.append({**ln, "legend": False})  # one shared legend entry instead (legend_extra)
    vintage = sources.get("boe", {}).get("vintage", "")
    return {
        "title": "Inflation in essentials", "units": "% change on a year earlier", "freq": "M",
        "lines": lines + forecasts,
        "legend_extra": [{"name": f"Bank of England staff projection ({vintage})", "colour": "#627D98", "kind": "forecast"}]
                        if forecasts else [],
        "note": chart["note"] + " Dashed lines are Bank of England staff projections for the next six months. The Bank's "
                "energy projection covers electricity and gas, a little narrower than the ONS household energy line.",
        "source": "ONS; Bank of England",
    }


def investment_section(fc, sources):
    chart = chart_file("investment", "investment-level")
    bi = [p for p in series(chart, "business") if p[0] >= START_Q]
    last_q = bi[-1][0]
    lines = forecast_lines(fc, sources, "business_investment_level", bi[-1], END_Q, rescale_to=bi)
    nxt = q_add(last_q, 1)
    year = int(nxt[:4])
    obr_path = fc.get("business_investment_level", {}).get("obr", [])
    box = {
        "latest": [{"label": "Business investment, quarter on quarter", "period": q_label(last_q),
                    "value": round((bi[-1][1] / bi[-2][1] - 1) * 100, 1), "unit": "%"}],
        "forecasts": [
            {"heading": f"{q_label(nxt)} business investment, q/q", "release": chart.get("next_release"), "unit": "%",
             "items": items(sources, {"obr": qoq_from_levels(obr_path, nxt)})},
            {"heading": f"{year} business investment growth", "unit": "%",
             "items": items(sources, {"boe": value_at(fc.get("business_investment_annual", {}).get("boe", []), year),
                                      "obr": calendar_year_growth(obr_path, year)})},
        ],
    }
    return {
        "id": "investment", "title": "Investment", "box": box,
        "charts": [{"title": "Business investment", "units": "Index, 2019 Q4 = 100", "freq": "Q",
                    "lines": [line("ONS outturn", bi, OUTTURN, "outturn")] + lines,
                    "note": "Chained volumes, seasonally adjusted. The ONS publishes business investment quarterly only: there is no official monthly series. The Bank of England publishes calendar-year growth only (in the box).",
                    "source": "ONS Business investment; OBR; Bank of England"}],
    }


def labour_section(fc, sources):
    chart = chart_file("labour-markets", "lm-rates-16plus")
    unemp = [p for p in series(chart, "unemployment") if p[0] >= START_M]
    emp = [p for p in series(chart, "employment") if p[0] >= START_M]
    end = q_to_month(END_Q, "end")
    place = lambda d: q_to_month(d, "end")  # noqa: E731
    u_lines = forecast_lines(fc, sources, "unemployment", unemp[-1], end, place=place,
                             extra=fc.get("unemployment_monthly"))
    e_lines = forecast_lines(fc, sources, "employment_rate", emp[-1], end, place=place)
    m = unemp[-1][0]
    q_next = f"{m[:4]}-Q{(int(m[5:]) - 1) // 3 + 1}"
    if q_to_month(q_next, "end") <= m:  # latest data already cover that quarter
        q_next = q_add(q_next, 1)
    q_end = q_to_month(q_next, "end")
    box = {
        "latest": [
            {"label": "Unemployment rate, 16+", "period": f"3 months to {month_label(m)}", "value": unemp[-1][1], "unit": "%"},
            {"label": "Employment rate, 16+", "period": f"3 months to {month_label(emp[-1][0])}", "value": emp[-1][1], "unit": "%"},
        ],
        "forecasts": [
            {"heading": f"Unemployment rate, {q_label(q_next)} (3 months to {month_label(q_end)[:3]})",
             "release": chart.get("next_release"), "unit": "%",
             "items": items(sources, {w: value_at(p, q_next) for w, p in fc.get("unemployment", {}).items()})},
            {"heading": f"Employment rate, {q_label(q_next)}", "unit": "%",
             "items": items(sources, {w: value_at(p, q_next) for w, p in fc.get("employment_rate", {}).items()})},
        ],
    }
    note = "Rolling three-month averages, dated by the final month; quarterly forecasts sit at the quarter's last month. LFS estimates are official statistics in development."
    return {
        "id": "labour", "title": "Labour market", "box": box,
        "charts": [
            {"title": "Unemployment rate, aged 16+", "units": "% of the economically active", "freq": "M",
             "lines": [line("ONS outturn", unemp, OUTTURN, "outturn")] + u_lines, "note": note,
             "source": "ONS Labour Force Survey; Bank of England; OBR"},
            {"title": "Employment rate, aged 16+", "units": "% of people aged 16+", "freq": "M",
             "lines": [line("ONS outturn", emp, OUTTURN, "outturn")] + e_lines,
             "note": note + " Only the OBR forecasts the employment rate.", "source": "ONS Labour Force Survey; OBR"},
        ],
    }


def wages_section(fc, sources):
    chart = chart_file("labour-markets", "pay-growth")
    total = [p for p in series(chart, "awe_total") if p[0] >= START_M]
    private = [p for p in series(chart, "awe_private_regular") if p[0] >= START_M]
    end = q_to_month(END_Q, "end")
    lines = forecast_lines(fc, sources, "pay_total", total[-1], end, place=lambda d: q_to_month(d, "end"))
    for ln in lines:
        ln["name"] = ln["name"].replace(" (", ", total pay (", 1)
    # Bank of England: private-sector regular pay growth in the year to Q4, drawn at December
    boe_q4 = [[f"{y}-12", v] for y, v in fc.get("pay_private_regular_q4", {}).get("boe", [])]
    boe_line = line(f"Bank of England, private regular pay ({sources['boe']['vintage']})" if "boe" in sources else "",
                    after(boe_q4, private[-1][0], end), FORECASTERS["boe"]["colour"], "forecast", private[-1])

    # Real pay growth by pay percentile from ASHE (annual; builders/labour-markets/ashe_distribution.py)
    ashe = chart_file("labour-markets", "ashe-pay-distribution")
    ashe_lines = [line(s["label"], [[r["percentile"], r[s["key"]]] for r in ashe["data"] if r.get(s["key"]) is not None],
                       colour, "outturn")
                  for s, colour in zip(ashe["series"], [OUTTURN, "#102A43"])]
    m = total[-1][0]
    q_next = f"{m[:4]}-Q{(int(m[5:]) - 1) // 3 + 1}"
    if q_to_month(q_next, "end") <= m:
        q_next = q_add(q_next, 1)
    year = int(q_next[:4])
    box = {
        "latest": [
            {"label": "Total pay growth", "period": f"3 months to {month_label(m)}", "value": total[-1][1], "unit": "%"},
            {"label": "Private-sector regular pay growth", "period": f"3 months to {month_label(private[-1][0])}",
             "value": private[-1][1], "unit": "%"},
        ],
        "forecasts": [
            {"heading": f"Total pay growth, {q_label(q_next)}", "release": chart.get("next_release"), "unit": "%",
             "items": items(sources, {"obr": value_at(fc.get("pay_total", {}).get("obr", []), q_next)})},
            {"heading": f"Private-sector regular pay, year to Q4 {year}", "unit": "%",
             "items": items(sources, {"boe": value_at(fc.get("pay_private_regular_q4", {}).get("boe", []), year)})},
        ],
    }
    return {
        "id": "wages", "title": "Wages", "box": box,
        "charts": [{"title": "Average weekly earnings growth", "units": "% change on a year earlier, 3-month average", "freq": "M",
                    "y_min": 0,  # start the axis at zero
                    "lines": [line("Total pay (ONS)", total, OUTTURN, "outturn"),
                              line("Private-sector regular pay (ONS)", private, OUTTURN_SECOND, "outturn")]
                             + lines + ([boe_line] if boe_line else []),
                    "note": "Nominal. The OBR forecasts whole-economy total pay (quarterly); the Bank of England forecasts private-sector regular pay growth in the year to each Q4 (dots at December).",
                    "source": "ONS Average Weekly Earnings; OBR; Bank of England"},
                   {"title": ashe["title"], "units": ashe["units"], "type": "percentile", "lines": ashe_lines,
                    "note": ashe["note"], "source": ashe["source"]},
                  ],
    }


def weekly(points: list) -> list:
    """Thin a daily series to its last trading day each week, plus the latest day:
    plenty for a chart several years wide, and a fraction of the file size."""
    import datetime as dt
    out = {}
    for d, v in points:
        out[dt.date.fromisoformat(d).isocalendar()[:2]] = [d, v]  # later days in a week overwrite earlier ones
    thinned = list(out.values())
    return thinned if thinned[-1] == points[-1] else thinned + [points[-1]]


def day_label(d: str) -> str:
    import calendar
    return f"{int(d[8:])} {calendar.month_abbr[int(d[5:7])]} {d[:4]}"


def markets_section(fc, sources):
    """Financial markets: daily prices (data/markets, builders/markets) with the
    market assumptions the Bank of England and OBR conditioned their forecasts on.
    Those assumptions are quarterly averages, drawn mid-quarter on the daily charts."""
    start = START_M + "-01"
    def get(slug, key):
        return [p for p in series(chart_file("markets", slug), key) if p[0] >= start]
    def mid(q):  # '2027-Q1' -> '2027-02-15'
        return q_to_month(q, "mid") + "-15"
    end = mid(END_Q)

    gilt, ust, bund = get("bond-yields-10y", "uk"), get("bond-yields-10y", "us"), get("bond-yields-10y", "de")
    rate = get("bank-rate", "bank_rate")
    # Policy rules (builders/dashboard/taylor.py), applied to the Bank's latest projections
    # from today's Bank Rate; the chart lets readers pick one at a time
    variants = []
    weights = json.loads((REPO / "data" / "inflation" / "cpi-energy-weight.json").read_text())
    for r in taylor.rules(fc, weights[max(weights)], rate[-1], END_Q):
        ln = line(f"{r['name']}, from Bank of England projections",
                  [[mid(q), v] for q, v in r["path"] if mid(q) > rate[-1][0]], TAYLOR_COLOUR, "forecast", rate[-1])
        variants.append({"id": r["id"], "name": r["name"], "formula": r["formula"], "lines": [ln] if ln else []})
    brent, wti = get("oil-prices", "brent"), get("oil-prices", "wti")
    gas_chart = chart_file("markets", "gas-price-uk")
    gas = [p for p in series(gas_chart, "gas") if p[0] >= START_M]
    ftse = get("ftse-100", "ftse100")
    usd, eur = get("sterling", "usd"), get("sterling", "eur")

    def assumed(var, last, who=("boe", "obr"), place=mid, scale=None, rename=None):
        """Forecast lines for one market assumption, joined to the latest price."""
        lines = forecast_lines({var: {w: p for w, p in fc.get(var, {}).items() if w in who}}, sources, var,
                               last, end if place is mid else q_to_month(END_Q, "mid"), place=place, rescale_to=scale)
        for ln in lines:
            if rename:
                ln["name"] = ln["name"].replace(" (", f", {rename} (", 1)
        return lines

    # The OBR's equity assumption is the FTSE All-Share: apply its growth to the FTSE 100,
    # anchored on this quarter's average level so far
    by_q: dict[str, list] = {}
    for d, v in ftse:
        by_q.setdefault(f"{d[:4]}-Q{(int(d[5:7]) - 1) // 3 + 1}", []).append(v)
    ftse_q = [[q, sum(v) / len(v)] for q, v in by_q.items()]

    # Box: latest prices, then market expectations for Bank Rate and oil
    q_now = f"{rate[-1][0][:4]}-Q{(int(rate[-1][0][5:7]) - 1) // 3 + 1}"
    q_next = q_add(q_now, 1)
    year = int(q_next[:4]) + (q_next[-1] != "1")  # next full calendar year
    def year_avg(path):
        vals = [v for q, v in path if q.startswith(f"{year}-")]
        return round(sum(vals) / 4, 1) if len(vals) == 4 else None
    box = {
        "latest": [
            {"label": "UK 10-year gilt yield", "period": day_label(gilt[-1][0]), "value": gilt[-1][1], "unit": "%", "decimals": 2},
            {"label": "Brent crude oil, $ per barrel", "period": day_label(brent[-1][0]), "value": brent[-1][1], "unit": "", "prefix": "$"},
            {"label": "FTSE 100", "period": day_label(ftse[-1][0]), "value": ftse[-1][1], "unit": "", "decimals": 0},
            {"label": "US$ per £", "period": day_label(usd[-1][0]), "value": usd[-1][1], "unit": "", "prefix": "$", "decimals": 2},
        ],
        "forecasts": [
            {"card": "Market expectations", "heading": f"Bank Rate, {q_label(q_next)} average",
             "note": f"Bank Rate today: {fmt_pct(rate[-1][1])}", "unit": "%", "decimals": 2,
             "items": items(sources, {w: next((round(v, 2) for d, v in p if d == q_next), None)
                                      for w, p in fc.get("bank_rate", {}).items()})},
            {"card": "Oil price assumption", "heading": f"Brent crude oil, {year} average", "unit": "", "prefix": "$",
             "items": items(sources, {"obr": year_avg(fc.get("oil", {}).get("obr", []))})},
        ],
    }
    note_assumed = ("Dashed lines are the market prices the forecasters assumed (quarterly averages of futures "
                    "prices at their cut-off dates, plotted mid-quarter), not forecasts of their own.")
    return {
        "id": "markets", "title": "Financial markets", "box": box,
        "charts": [
            {"title": "10-year government bond yields", "units": "% a year", "freq": "D", "decimals": 2,
             "lines": [line("UK", weekly(gilt), OUTTURN, "outturn"), line("US", weekly(ust), "#102A43", "outturn"),
                       line("Germany", weekly(bund), OUTTURN_SECOND, "outturn")],
             "note": "Weekly (last trading day). No forecaster publishes a 10-year yield path: the OBR's assumption is for 20-year gilts.",
             "source": "Bank of England; US Treasury; Bundesbank"},
            {"title": "Bank Rate", "units": "%", "freq": "D",  # no y_min: the Taylor rule can go below zero
             "lines": [line("Bank Rate", weekly(rate), OUTTURN, "outturn")] + assumed("bank_rate", rate[-1]),
             "variants": variants, "variant_label": "Policy rule",
             "note": "Dashed amber and teal lines are the market-implied path of Bank Rate each forecast was conditioned on "
                     "(quarterly averages, plotted mid-quarter). The navy line is the chosen policy rule applied to the Bank "
                     "of England's latest projections, starting from today's Bank Rate (quarterly; i = Bank Rate, \u03c0 = CPI "
                     "inflation, y = output gap, all %). The Bank's three rules are from its November 2025 MPR (Annex 1), with "
                     "i* = 3%. The Bank publishes its output gap by year (interpolated to quarters) and energy only as its "
                     "contribution to CPI inflation in Q4, from which energy and non-energy inflation are worked out. Unlike "
                     "the Bank's own simulations, the projections don't respond to the different rate path.",
             "source": "Bank of England; ONS"},
            {"title": "Crude oil prices", "units": "$ per barrel", "freq": "D", "y_min": 0,
             "lines": [line("Brent", weekly(brent), OUTTURN, "outturn"), line("WTI", weekly(wti), OUTTURN_SECOND, "outturn")]
                      + assumed("oil", brent[-1], who=("obr",), rename="Brent"),
             "note": "Spot prices, weekly; the EIA updates weekly, so the latest can be a week old. " + note_assumed +
                     " The Bank of England's oil path can't be republished (licensed data).",
             "source": "US Energy Information Administration; OBR"},
            {"title": "UK wholesale gas price", "units": "Pence per therm, monthly average", "freq": "M", "y_min": 0,
             "lines": [line("Day-ahead gas", gas, OUTTURN, "outturn")]
                      + assumed("gas", gas[-1], who=("obr",), place=lambda q: q_to_month(q, "mid")),
             "note": "Day-ahead contracts, Great Britain. " + note_assumed,
             "source": "Ofgem; OBR"},
            {"title": "FTSE 100", "units": "Index level, daily close (weekly)", "freq": "D",
             "lines": [line("FTSE 100", weekly(ftse), OUTTURN, "outturn")] + assumed("equities", ftse[-1], who=("obr",), scale=ftse_q),
             "note": "From Yahoo Finance's unofficial feed (FTSE Russell licenses official index data). The OBR line applies its "
                     "FTSE All-Share assumption's growth to the FTSE 100's average this quarter.",
             "source": "Yahoo Finance; OBR"},
            {"title": "Sterling exchange rates", "units": "Foreign currency per £", "freq": "D", "decimals": 2,
             "lines": [line("US$ per £", weekly(usd), OUTTURN, "outturn"), line("€ per £", weekly(eur), OUTTURN_SECOND, "outturn")]
                      + assumed("usd", usd[-1], who=("obr",), rename="US$") + assumed("eur", eur[-1], who=("obr",), rename="€"),
             "note": "Daily 4pm spot rates, weekly. The OBR assumes exchange rates stay flat from its cut-off.",
             "source": "Bank of England; OBR"},
        ],
    }


def fmt_pct(v: float) -> str:
    return f"{v:.2f}%".replace(".00%", "%")


# ---------------------------------------------------------------- main

def load_forecasts() -> tuple[dict, dict, bool]:
    """Fetch forecasts; for any source that fails, keep its previous forecasts."""
    sources, data, failures = forecasts.collect()
    path = OUT / "forecasts.json"
    if failures:
        for f in failures:
            print(f"forecasts: {f}", file=sys.stderr)
        if path.exists():
            prev = json.loads(path.read_text())
            for who, info in prev["sources"].items():
                if who not in sources:
                    sources[who] = {**info, "stale": True}
                    for var, by in prev["data"].items():
                        if who in by:
                            data.setdefault(var, {})[who] = by[who]
    # lists of tuples -> lists, so the comparison with the saved file is like for like
    data = json.loads(json.dumps(data))
    return sources, data, not failures


def add_own_forecast(fc: dict, sources: dict) -> None:
    """Add our short-term inflation forecast (builders/inflation/stif.py) as one more
    forecaster: monthly CPI for six months, plus averages for the quarters it covers."""
    path = REPO / "data" / "inflation" / "stif.json"
    if not path.exists():
        return
    stif = json.loads(path.read_text())
    monthly = [[row["month"], row["cpi"]] for row in stif["forecast"]]
    by_q: dict[str, list] = {}
    for m, v in monthly:
        by_q.setdefault(f"{m[:4]}-Q{(int(m[5:]) - 1) // 3 + 1}", []).append(v)
    quarterly = [[q, round(sum(v) / 3, 2)] for q, v in by_q.items() if len(v) == 3]  # whole quarters only
    fc.setdefault("cpi_monthly", {})["own"] = monthly
    fc.setdefault("cpi", {})["own"] = quarterly
    sources["own"] = {"name": "Sturfrymacro", "vintage": f"short-term forecast, data to {month_label(stif['data_through'])}"}


def write_if_changed(path: Path, obj) -> None:
    """Only rewrite when content changes, so a quiet day makes no commit."""
    text = json.dumps(obj, indent=1, ensure_ascii=False) + "\n"
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    sources, fc, ok = load_forecasts()
    write_if_changed(OUT / "forecasts.json", {"sources": sources, "data": fc})
    add_own_forecast(fc, sources)  # after saving: forecasts.json holds published forecasts only
    sections = []
    for build in (gdp_section, inflation_section, investment_section, labour_section, wages_section,
                  markets_section):
        try:
            sections.append(build(fc, sources))
        except Exception as e:  # noqa: BLE001
            print(f"{build.__name__} failed: {e!r}", file=sys.stderr)
            ok = False
    for s in sections:  # drop empty forecast lines
        for c in s["charts"]:
            if "lines" in c:
                c["lines"] = [ln for ln in c["lines"] if ln]
    write_if_changed(OUT / "dashboard.json", {"sources": sources, "sections": sections})
    print(f"[dashboard] {len(sections)} sections, forecasts from {', '.join(s['vintage'] for s in sources.values())}")
    sys.exit(0 if ok else 1)
