"""ONS consumer price dataset (MM23) as one file: every CPI and CPIH index and weight.

One request (about 23 MB, a second or two) instead of ~170 calls to the per-series
API. Used by the short-term inflation forecast, which needs every CPI component.

Layout of the CSV: one column per series; the first rows are metadata (Title, CDID,
units, release dates...), then one row per period labelled '2026', '2026 Q3' or
'2026 SEP'.

How CPI is put together (needed to add components back up to the headline):
- The basket is split into COICOP components. The finest level the ONS publishes
  for every part of the basket is the "class" (e.g. 01.1.1 bread and cereals), but a
  few groups are only published as a whole (e.g. 04.1 actual rents, 02.2 tobacco).
  `components()` picks the finest complete level: 84 components whose weights add
  up to 1000.
- Weights (parts per thousand of spending) change every year.
- CPI is chain-linked each January: January is priced with last year's weights
  relative to last January; February to December use this year's weights relative
  to this January. `aggregate()` does exactly that.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date

from .http import get

URL = "https://www.ons.gov.uk/file?uri=/economy/inflationandpriceindices/datasets/consumerpriceindices/current/mm23.csv"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


class MM23:
    def __init__(self, raw: bytes | None = None):
        rows = list(csv.reader(io.StringIO((raw or get(URL, timeout=300)).decode("utf-8-sig"))))
        self.titles, self.cdids = rows[0], rows[1]
        meta = {r[0]: r for r in rows[:7]}
        self.release_date = meta["Release Date"][1] if "Release Date" in meta else None
        self.next_release = meta["Next release"][1] if "Next release" in meta else None
        self.rows = {r[0]: r for r in rows[7:] if r}

    # ------------------------------------------------------------ basic access

    def _columns(self, kind: str) -> dict[str, int]:
        """{COICOP code: column} for 'CPI INDEX ...' or 'CPI WEIGHTS ...' series.
        Codes like '08.2/3' are keyed by their first part ('08.2')."""
        out = {}
        for j, t in enumerate(self.titles):
            m = re.match(rf"^CPI {kind}:? *(\d\d(?:\.\d+)*)(?![\d.])", t)
            if m:
                out.setdefault(m[1], j)
        return out

    def monthly(self, col: int) -> dict[str, float]:
        """{'2026-09': value} for one column's monthly rows."""
        out = {}
        for label, r in self.rows.items():
            m = re.fullmatch(r"(\d{4}) ([A-Z]{3})", label)
            if m and col < len(r) and r[col].strip():
                out[f"{m[1]}-{MONTHS.index(m[2]) + 1:02d}"] = float(r[col])
        return dict(sorted(out.items()))

    def annual(self, col: int) -> dict[int, float]:
        return {int(label): float(r[col]) for label, r in self.rows.items()
                if re.fullmatch(r"\d{4}", label) and col < len(r) and r[col].strip()}

    def series(self, cdid: str) -> dict[str, float]:
        """Monthly series by CDID, e.g. 'D7BT' (CPI all items index) or 'D7G7' (12-month rate)."""
        return self.monthly(self.cdids.index(cdid))

    # ------------------------------------------------------------ components

    def components(self, year: int | None = None) -> dict[str, str]:
        """{code: name} for the finest set of components covering the whole basket:
        each group's classes if their weights add up to the group's, else the group."""
        year = year or max(self.annual(self.cdids.index("CHZQ")))
        weights = {k: w for k, j in self._columns("WEIGHTS").items() if (w := self.annual(j).get(year))}
        index_cols = self._columns("INDEX")
        out = {}
        for g in sorted(k for k in weights if k.count(".") == 1):
            classes = [k for k in weights if k.startswith(g + ".") and k.count(".") == 2]
            picked = classes if classes and abs(sum(weights[k] for k in classes) - weights[g]) < 0.5 else [g]
            for k in picked:
                if k in index_cols:
                    name = re.sub(r"^CPI INDEX:? *[\d./]+ *:? *|\s*2015=100$", "", self.titles[index_cols[k]])
                    out[k] = name.strip().capitalize()
        return out

    def component_data(self) -> tuple[dict[str, dict[str, float]], dict[str, dict[int, float]]]:
        """(indices {code: {month: index}}, weights {code: {year: weight}}) for components()."""
        comps = self.components()
        ic, wc = self._columns("INDEX"), self._columns("WEIGHTS")
        return ({k: self.monthly(ic[k]) for k in comps}, {k: self.annual(wc[k]) for k in comps})


# ---------------------------------------------------------------- adding up

def aggregate(indices: dict[str, dict[str, float]], weights: dict[str, dict[int, float]],
              start: str, base: float) -> dict[str, float]:
    """Headline index from component indices, using CPI's January chain-linking.

    start: the first month (a January) to build from, where the headline is `base`.
    Each later month is the previous January's headline times the weighted average of
    the components' price change since that January:
      - January of year t:  weights of year t-1, prices relative to January t-1
      - Feb-Dec of year t:  weights of year t,   prices relative to January t
    Components with no weight that year are skipped and the weights re-normalised.
    """
    months = sorted({m for s in indices.values() for m in s if m >= start})
    out = {start: base}
    jan_level = {int(start[:4]): base}
    for m in months[1:]:
        y, mo = int(m[:4]), int(m[5:])
        link_year, w_year = (y - 1, y - 1) if mo == 1 else (y, y)
        link = f"{link_year}-01"
        num = den = 0.0
        for k, s in indices.items():
            w = weights[k].get(w_year)
            if w and m in s and link in s:
                num += w * s[m] / s[link]
                den += w
        if den == 0 or link_year not in jan_level:
            break
        out[m] = jan_level[link_year] * num / den
        if mo == 1:
            jan_level[y] = out[m]
    return out


def twelve_month_rate(index: dict[str, float]) -> dict[str, float]:
    """12-month % change of a monthly index."""
    out = {}
    for m, v in index.items():
        prev = f"{int(m[:4]) - 1}{m[4:]}"
        if prev in index:
            out[m] = (v / index[prev] - 1) * 100
    return out


def next_month(m: str, k: int = 1) -> str:
    """'2026-12' + 1 -> '2027-01'."""
    n = int(m[:4]) * 12 + int(m[5:]) - 1 + k
    return f"{n // 12}-{n % 12 + 1:02d}"


def _today() -> date:  # kept separate so tests can fake the date
    return date.today()
