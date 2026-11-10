#!/usr/bin/env python3
"""Pay growth across the pay distribution, from the ONS Annual Survey of Hours and
Earnings (ASHE). Run by hand once a year, after the ASHE release (late October).

ASHE is a 1% sample of employee jobs from HMRC PAYE records, taken each April. Its
Table 1 gives pay at the 10th, 20th, 25th, 30th, 40th, 50th (median), 60th, 70th,
75th, 80th and 90th percentiles. We compare this year's provisional estimates with
last year's *revised* estimates (published at the same time), which is how the ONS
calculates its own annual changes.

Two measures, for all employee jobs in the UK:
- Weekly pay, gross (Table 1.1a): moves with hours as well as hourly pay, so it is
  much more variable at the bottom, where many people work part time.
- Hourly pay, excluding overtime (Table 1.6a): the ONS's preferred hourly measure;
  the bottom of it is held up by the National Living Wage.

Note: each percentile compares *whoever* is at that point in each year, not the
same people, and ASHE's April date means it misses anything after the survey week.

Writes data/labour-markets/ashe-pay-distribution.json and img/labour-markets/ashe-pay-distribution.png.
"""

from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

import openpyxl  # noqa: E402

from ukmacro import charts  # noqa: E402
from ukmacro.http import get, get_text  # noqa: E402

PAGE = ("https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/earningsandworkinghours/"
        "datasets/allemployeesashetable1")
TABLES = {"weekly": "1.1a", "hourly": "1.6a"}  # weekly gross; hourly excluding overtime
SLUG = "ashe-pay-distribution"


def latest_pair() -> tuple[int, zipfile.ZipFile, zipfile.ZipFile]:
    """The newest provisional Table 1 zip and the previous year's revised one."""
    links = re.findall(r'href="(/file\?uri=[^"]+ashetable1(\d{4})(provisional|revised)\.zip)"', get_text(PAGE))
    year = max(int(y) for _, y, kind in links if kind == "provisional")
    url = {(int(y), kind): u for u, y, kind in links}
    fetch = lambda key: zipfile.ZipFile(io.BytesIO(get("https://www.ons.gov.uk" + url[key], timeout=180)))  # noqa: E731
    return year, fetch((year, "provisional")), fetch((year - 1, "revised"))


def percentiles(zf: zipfile.ZipFile, table: str) -> dict[int, float]:
    """{percentile: £} for 'All Employees' from one ASHE table (the CV files are skipped)."""
    name = next(n for n in zf.namelist() if f"Table {table}" in n and " CV" not in n)
    rows = list(openpyxl.load_workbook(io.BytesIO(zf.read(name)), read_only=True, data_only=True)["All"]
                .iter_rows(values_only=True))
    header = next(r for r in rows if r and r[0] == "Description")
    values = next(r for r in rows if r and r[0] == "All Employees")
    out = {}
    for h, v in zip(header, values):
        if h == "Median":
            out[50] = float(v)
        elif isinstance(h, int) and isinstance(v, (int, float)):
            out[h] = float(v)
    return dict(sorted(out.items()))


def draw(chart: dict, path: Path) -> None:
    """Percentiles along the x-axis (spaced by their value, so 25 sits between 20 and 30),
    one line with dots per measure, labelled at the left end, in the house style."""
    charts.use_theme(charts.DEFAULT_THEME)
    fig, ax = charts._frame(chart)
    colours = charts.THEMES[charts.DEFAULT_THEME]["series"]
    for i, s in enumerate(chart["series"]):
        xs = [row["percentile"] for row in chart["data"] if row.get(s["key"]) is not None]
        ys = [row[s["key"]] for row in chart["data"] if row.get(s["key"]) is not None]
        ax.plot(xs, ys, color=colours[i], linewidth=2, marker="o", markersize=4.5, zorder=3)
        # name the line just left of its first point (instead of a legend)
        ax.annotate(s["label"], (xs[0], ys[0]), xytext=(-8, 0), textcoords="offset points",
                    ha="right", va="center", fontsize=11, color=colours[i])
    ax.set_xlim(0, 100)
    ax.set_xticks([10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90])
    ax.set_xticklabels(["10th", "20th", "25th", "30th", "40th", "Median", "60th", "70th", "75th", "80th", "90th"],
                       fontsize=10.5, color=charts.TEXT_2)
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0f}%")
    ax.tick_params(axis="y", labelsize=10.5, labelcolor=charts.TEXT_2)
    ax.set_xlabel("Pay percentile (lower paid → higher paid)", fontsize=10.5, color=charts.TEXT_2, labelpad=8)
    charts._save(fig, path)


if __name__ == "__main__":
    year, new, old = latest_pair()
    data = {}
    for key, table in TABLES.items():
        now, before = percentiles(new, table), percentiles(old, table)
        for p in now:
            if p in before:
                data.setdefault(p, {"percentile": p})[key] = round((now[p] / before[p] - 1) * 100, 2)
                data[p][f"{key}_{year}"], data[p][f"{key}_{year - 1}"] = now[p], before[p]
    chart = {
        "slug": SLUG, "category": "labour-markets",
        "title": f"Pay growth across the pay distribution, April {year - 1} to April {year}",
        "units": "% change on a year earlier, all employee jobs, UK",
        "source": f"ONS Annual Survey of Hours and Earnings, Table 1 ({year} provisional vs {year - 1} revised)",
        "note": "Weekly pay is gross; hourly pay excludes overtime. Each percentile compares whoever is at that point of the distribution in each year.",
        "series": [{"key": "hourly", "label": "Hourly"}, {"key": "weekly", "label": "Weekly"}],
        "data": list(data.values()),
    }
    out = REPO / "data" / "labour-markets" / f"{SLUG}.json"
    out.write_text(json.dumps(chart, indent=1) + "\n")
    draw(chart, REPO / "img" / "labour-markets" / f"{SLUG}.png")
    for row in chart["data"]:
        print(row["percentile"], row["hourly"], row["weekly"])
