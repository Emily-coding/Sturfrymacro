#!/usr/bin/env python3
"""Employment by age after downturns: the % change in the number of people in work
since the start of each downturn, month by month, for 18-24s, 25-49s and over-50s.
Run with the labour markets builder every weekday.

Downturns (start month = 0):
- Financial crisis, from April 2008: Labour Force Survey (LFS) employment, three-month
  averages dated by the final month. Payroll data by age only start in 2014.
- Covid-19, from March 2020: payrolled employees from HMRC PAYE Real Time Information.
- Now, from June 2023, when payrolled employment of 18-24s peaked: PAYE as for Covid.
(The same sources and the first two dates as the Resolution Foundation's version of
this chart.) The LFS counts everyone in work, including the self-employed; PAYE counts
employees on a payroll only.

Every downturn is shown over the same number of months as the current one has run.
Past downturns are grey and the current one is coloured, on the dashboard's labour
market page (labour.html) and in the PNGs.

Writes data/labour-markets/employment-downturns-{18-24,25-49,50plus}.json (and PNGs).
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from ukmacro import charts, ons, rti  # noqa: E402

# LFS employment (thousands, 16+ split by age) and PAYE bands that make up each group
GROUPS = {
    "18-24": {"title": "aged 18-24", "lfs": ["YBTR"], "rti": ["18 to 24"]},
    "25-49": {"title": "aged 25-49", "lfs": ["YBTU", "YBTX"], "rti": ["25 to 34", "35 to 49"]},
    "50plus": {"title": "aged 50 and over", "lfs": ["LF26", "LFK4"], "rti": ["50 to 64", "65 and over"]},
}
# (key, label, start month, source); the last is the current downturn, drawn in colour.
# Labels are short because grey lines are named at their right-hand ends.
DOWNTURNS = [
    ("gfc", "2008", "2008-04", "lfs"),
    ("covid", "Covid-19", "2020-03", "rti"),
    ("now", "Now", "2023-06", "rti"),
]


def month_add(m: str, k: int) -> str:
    n = int(m[:4]) * 12 + int(m[5:7]) - 1 + k
    return f"{n // 12}-{n % 12 + 1:02d}"


def months_between(a: str, b: str) -> int:
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


def total(series: list[dict[str, float]]) -> dict[str, float]:
    """Add age bands together, for months all of them have."""
    common = set.intersection(*(set(s) for s in series))
    return {m: sum(s[m] for s in series) for m in common}


def build() -> None:
    by_age, rti_url = rti.employees_by_age()
    lfs = {c: dict(ons.fetch(c, "LMS", "M")["obs"]) for g in GROUPS.values() for c in g["lfs"]}
    span = months_between(DOWNTURNS[-1][2], max(by_age["UK"]))  # how long the current downturn has run
    for slug, g in GROUPS.items():
        level = {"lfs": total([lfs[c] for c in g["lfs"]]), "rti": total([by_age[b] for b in g["rti"]])}
        rows = [{"date": str(k)} for k in range(span + 1)]
        series = []
        for key, label, start, source in DOWNTURNS:
            data = level[source]
            for k, row in enumerate(rows):
                m = month_add(start, k)
                if m in data:
                    row[key] = round(100 * (data[m] / data[start] - 1), 2)
            current = key == DOWNTURNS[-1][0]
            series.append({"key": key, "label": label, **({"slot": 0} if current else {"color": "neutral"})})
        chart = {
            "slug": f"employment-downturns-{slug}", "category": "labour-markets", "type": "line", "freq": "M",
            "title": f"Employment after downturns, {g['title']}",
            "units": "% change since the start of each downturn: now (from June 2023) in blue, past downturns in grey",
            "x_units": "Months since the start of the downturn",
            "source": "ONS Labour Force Survey; HMRC PAYE Real Time Information",
            "note": "2008: financial crisis, from April 2008; everyone in work (LFS, three-month averages). Covid-19: "
                    "from March 2020. Covid-19 and now: employees on a "
                    "payroll (PAYE, monthly), where the latest month is an early estimate. The current downturn is "
                    "dated from June 2023, when payroll employment of 18-24s peaked.",
            "decimals": 1,
            "series": series,
            "data_through": max(by_age["UK"]), "next_release": None, "status": "ok",
            "source_url": rti_url, "data": rows,
        }
        charts.publish(chart, REPO / "data" / "labour-markets" / f"{chart['slug']}.json",
                       REPO / "img" / "labour-markets" / f"{chart['slug']}.png")
        print(f"[labour-markets] {chart['slug']}: now {rows[-1].get('now')}% after {span} months")


if __name__ == "__main__":
    build()
