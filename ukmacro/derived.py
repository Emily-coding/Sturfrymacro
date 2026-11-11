"""Series we calculate ourselves from ONS levels, to more decimal places than the ONS
publishes.

The ONS rounds rates to one decimal place, so on a scatter chart many months land on
exactly the same spot. Rebuilding a rate from its (unrounded, in thousands) parts
spreads them out. Each function returns ([(date, value)], meta) like a fetched series.

    unemployment_rate   unemployed / economically active, aged 16+ (MGSC / MGSF).
                        Rounded to 1dp it reproduces the ONS rate (MGSX).
    job_openings_rate   vacancies / (employee jobs + vacancies) (AP2Y, BCAJ). Employee
                        jobs come from Workforce Jobs, which is quarterly: each quarter's
                        figure is placed at its middle month, months in between are filled
                        in on a straight line, and the latest quarter is carried forward
                        until the next one is published. Within 0.1 percentage point of the
                        ONS job openings rate (N4JE), which is calculated the same way but
                        with ONS's own monthly jobs estimates.
"""

from __future__ import annotations

from . import ons

DP = 3  # decimal places kept


def _meta(r: dict) -> dict:
    return {"source_url": r["url"], "next_release": r["next_release"]}


def unemployment_rate() -> tuple[list, dict]:
    unemployed, active = ons.fetch("MGSC", "LMS", "M"), ons.fetch("MGSF", "LMS", "M")
    a = dict(active["obs"])
    obs = [(d, round(100 * u / a[d], DP)) for d, u in unemployed["obs"] if a.get(d)]
    return obs, _meta(unemployed)


def _month_index(d: str) -> int:
    return int(d[:4]) * 12 + int(d[5:7]) - 1


def _monthly_from_quarterly(quarterly: list[tuple[str, float]], through: str) -> dict[str, float]:
    """Quarterly levels ('2026-Q2') -> monthly, on a straight line between quarters'
    middle months, the last quarter held flat up to `through` (YYYY-MM)."""
    points = [(_month_index(f"{d[:4]}-{(int(d[6]) - 1) * 3 + 2:02d}"), v) for d, v in quarterly]
    out = {}
    for (m0, v0), (m1, v1) in zip(points, points[1:]):
        for m in range(m0, m1):
            out[m] = v0 + (v1 - v0) * (m - m0) / (m1 - m0)
    last_m, last_v = points[-1]
    for m in range(last_m, max(last_m, _month_index(through)) + 1):
        out[m] = last_v
    return {f"{m // 12}-{m % 12 + 1:02d}": v for m, v in out.items()}


def job_openings_rate() -> tuple[list, dict]:
    vacancies = ons.fetch("AP2Y", "LMS", "M")
    jobs = _monthly_from_quarterly(ons.fetch("BCAJ", "LMS", "Q")["obs"], vacancies["obs"][-1][0])
    obs = [(d, round(100 * v / (v + jobs[d]), DP)) for d, v in vacancies["obs"] if d in jobs]
    return obs, _meta(vacancies)
