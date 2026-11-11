"""Policy rules for Bank Rate: a simple Taylor rule and the three simple rules Bank of
England staff use (Monetary Policy Report, Annex 1, November 2025), each applied to the
Bank's latest MPR projections to give a path for Bank Rate from today.

The rules (i = Bank Rate, pi = annual CPI inflation, y = output gap, all %, quarterly):

    Simple Taylor (1993)      i = 0.5 + pi + 0.5(pi - 2) + 0.5y
    Contemporaneous (Bank)    i = 0.85 i[t-1] + 0.15(3 + 0.375(piE - 2) + 1.5(piN - 2) + 0.5y)
    Forward-looking (Bank)    i = 0.85 i[t-1] + 0.15(3 + 1.5(pi[t+5] - 2) + 0.5y[t+5])
    First-difference (Bank)   i = i[t-1] + 0.1(pi[t+3] - 2) + 0.1(gdp[t+3] - trend)

piE and piN are energy and non-energy inflation; [t+5] means the projection for five
quarters later; gdp is quarterly GDP growth. The Bank writes its rules with variables
measured from their steady state, so energy and non-energy inflation are measured from
2% and GDP growth from a trend of 0.375% a quarter (1.5% a year; our assumption, as the
Bank doesn't publish one for this rule). i* = 3% is the Bank's: a 2% inflation target
plus an illustrative 1% neutral real rate.

Inputs, all from the latest MPR: CPI inflation (quarterly), GDP (quarterly level) and
the output gap (published by calendar year only, so each year's figure is placed at
mid-year and quarters in between are filled in on a straight line). The Bank doesn't
publish energy inflation beyond six months, only energy's direct contribution to CPI
inflation in Q4 of each year: that contribution, divided by energy's share of the
basket, gives energy inflation, and the rest of CPI gives non-energy inflation (Q4
figures are joined on a straight line through the year).

The three Bank rules move gradually (0.85 of last quarter's rate, or a change on it),
so they start from today's Bank Rate, as the Bank's own simulations do. Unlike the
Bank's simulations, the forecasts they feed on don't respond to the different rate path.
"""

from __future__ import annotations

TARGET = 2.0             # CPI inflation target, %
SIMPLE_REAL = 0.5        # simple Taylor rule's neutral real rate, %
BANK_I_STAR = 3.0        # the Bank's neutral nominal rate for its rules, %
TREND_GROWTH_Q = 0.375   # trend quarterly GDP growth, % (first-difference rule)


def q_add(q: str, k: int) -> str:
    y, n = int(q[:4]), int(q[-1]) - 1 + k
    return f"{y + n // 4}-Q{n % 4 + 1}"


def quarter_of(d: str) -> str:
    return f"{d[:4]}-Q{(int(d[5:7]) - 1) // 3 + 1}"


def annual_to_quarterly(annual: list, at: float = 1.5) -> dict:
    """Yearly figures -> quarterly, on a straight line between them and held flat beyond
    the ends. `at` is where in the year each figure sits, in quarters from the start of Q1:
    1.5 = mid-year (a calendar-year average, e.g. the output gap), 3 = Q4 (a Q4 figure)."""
    pts = sorted((int(y) * 4 + at, v) for y, v in annual)
    out = {}
    for i in range(int(pts[0][0]) - 4, int(pts[-1][0]) + 5):
        lo = max((p for p in pts if p[0] <= i), default=pts[0])
        hi = min((p for p in pts if p[0] >= i), default=pts[-1])
        out[f"{i // 4}-Q{i % 4 + 1}"] = lo[1] if hi[0] == lo[0] else lo[1] + (hi[1] - lo[1]) * (i - lo[0]) / (hi[0] - lo[0])
    return out


def rules(fc: dict, w_energy: float, today: list, end: str) -> list[dict]:
    """The four rules as [{id, name, formula (HTML), path: [[quarter, rate]]}], each from
    the current quarter to `end`. `w_energy` is energy's share of the CPI basket;
    `today` is [date, Bank Rate]."""
    boe = lambda var: fc.get(var, {}).get("boe") or []  # noqa: E731
    cpi = dict(map(tuple, boe("cpi")))
    gdp = dict(map(tuple, boe("gdp_level")))
    gap = annual_to_quarterly(boe("output_gap_annual"))                     # calendar-year averages
    contribution = annual_to_quarterly(boe("energy_contribution_q4"), at=3)  # energy's contribution in Q4

    def growth(q):  # quarterly GDP growth, %
        return (gdp[q] / gdp[q_add(q, -1)] - 1) * 100 if q in gdp and q_add(q, -1) in gdp else None

    def simple(q, prev):
        if q not in cpi or q not in gap:
            return None
        return SIMPLE_REAL + cpi[q] + 0.5 * (cpi[q] - TARGET) + 0.5 * gap[q]

    def contemporaneous(q, prev):
        if q not in cpi or q not in contribution or q not in gap:
            return None
        c = contribution[q]
        energy, non_energy = c / w_energy, (cpi[q] - c) / (1 - w_energy)
        return 0.85 * prev + 0.15 * (BANK_I_STAR + 0.375 * (energy - TARGET) + 1.5 * (non_energy - TARGET) + 0.5 * gap[q])

    def forward(q, prev):
        ahead = q_add(q, 5)
        if ahead not in cpi or ahead not in gap:
            return None
        return 0.85 * prev + 0.15 * (BANK_I_STAR + 1.5 * (cpi[ahead] - TARGET) + 0.5 * gap[ahead])

    def first_difference(q, prev):
        ahead = q_add(q, 3)
        g = growth(ahead)
        if ahead not in cpi or g is None:
            return None
        return prev + 0.1 * (cpi[ahead] - TARGET) + 0.1 * (g - TREND_GROWTH_Q)

    specs = [
        ("simple", "Simple Taylor rule", simple,
         "i = 0.5 + π + 0.5(π − 2) + 0.5y. Taylor (1993), with a 0.5% neutral real rate; no smoothing."),
        ("contemporaneous", "Contemporaneous Taylor-type rule (Bank)", contemporaneous,
         "i<sub>t</sub> = 0.85i<sub>t−1</sub> + 0.15(3 + 0.375(π<sup>E</sup><sub>t</sub> − 2) + 1.5(π<sup>N</sup><sub>t</sub> − 2) "
         "+ 0.5y<sub>t</sub>), with π<sup>E</sup> and π<sup>N</sup> energy and non-energy CPI inflation."),
        ("forward", "Forward-looking Taylor-type rule (Bank)", forward,
         "i<sub>t</sub> = 0.85i<sub>t−1</sub> + 0.15(3 + 1.5(π<sub>t+5|t</sub> − 2) + 0.5y<sub>t+5|t</sub>), "
         "using projections for five quarters ahead."),
        ("first_difference", "Forward-looking first-difference rule (Bank)", first_difference,
         "Δi<sub>t</sub> = 0.1(π<sub>t+3|t</sub> − 2) + 0.1(ΔGDP<sub>t+3|t</sub> − 0.375), using projections "
         "for three quarters ahead (quarterly GDP growth, measured from a 0.375% trend)."),
    ]
    out = []
    for rid, name, rule, formula in specs:
        path, prev, q = [], today[1], quarter_of(today[0])   # start from today's Bank Rate
        while q <= end:
            v = rule(q, prev)
            if v is None:
                break
            path.append([q, round(v, 2)])
            prev, q = v, q_add(q, 1)
        out.append({"id": rid, "name": name, "formula": formula, "path": path})
    return out
