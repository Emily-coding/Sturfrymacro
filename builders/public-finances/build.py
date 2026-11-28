#!/usr/bin/env python3
"""Public finances builder.

1. The ONS monthly figures for receipts and spending (config.yaml -> psf-monthly.json).
2. The OBR's monthly profiles: how it expects borrowing, receipts and spending to be
   spread across the months of the current financial year, consistent with one of its
   forecasts. The OBR publishes them a little after each forecast, as a spreadsheet
   linked from its monthly public finances briefing page. We keep the newest one in
   data/public-finances/obr-profiles.json. Comparing the ONS figures for the months so
   far with the profile for the same months is how the OBR itself monitors whether
   borrowing is on track (seasonal swings, like self-assessment in January and July,
   are in both).

The dashboard builder reads both files for its public finances section.
Run on weekdays: the ONS release comes out around the 20th, and nothing is committed
on the other days because the files don't change.
"""

import io
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))  # repo root, so `ukmacro` imports

from ukmacro import obr  # noqa: E402
from ukmacro.builder import run  # noqa: E402
from ukmacro.http import get  # noqa: E402

BRIEFING_PAGE = "https://obr.uk/monthly-public-finances-briefing/"
OUT = REPO / "data" / "public-finances" / "obr-profiles.json"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]
# rows we keep from the profiles sheet: our name -> start of the OBR's row label
ROWS = {
    "borrowing": "public sector net borrowing",
    "receipts": "(less) public sector current receipts",   # shown negative in the sheet
}


def latest_profiles() -> tuple[str, str]:
    """(label e.g. 'March 2026', url) of the newest monthly profiles the OBR links."""
    found = []
    for slug, url in obr.download_links(BRIEFING_PAGE).items():
        m = re.fullmatch(r"([a-z]+)-(\d{4})-economic-and-fiscal-outlook-monthly-profiles", slug)
        if m and m.group(1) in MONTHS:
            found.append((int(m.group(2)), MONTHS.index(m.group(1)), url))
    if not found:
        raise LookupError(f"no monthly profiles linked from {BRIEFING_PAGE}")
    y, mi, url = max(found)
    return f"{MONTHS[mi].title()} {y}", url


def read_profiles(raw: bytes) -> dict:
    """The financial year, and for each row the annual forecast and the 12 monthly
    values (April to March), £ billion."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    rows = [list(r) for r in wb["Monthly Profiles"].iter_rows(values_only=True)]
    title = " ".join(str(c) for r in rows[:4] for c in r if c)
    fy = re.search(r"(\d{4})-(\d{2})", title).group(0)           # e.g. '£ billion in 2026-27'
    header = next(i for i, r in enumerate(rows) if [str(c) for c in r if c][:3] == ["Apr", "May", "Jun"])
    first = [str(c) for c in rows[header]].index("Apr")
    out = {}
    for name, start in ROWS.items():
        r = next(r for r in rows[header:] if r[1] and str(r[1]).strip().lower().startswith(start))
        annual, months = float(r[2]), [float(v) for v in r[first:first + 12]]
        if name == "receipts":                                      # make receipts positive
            annual, months = -annual, [-v for v in months]
        out[name] = {"year": round(annual, 3), "months": [round(v, 3) for v in months]}
    # total spending = borrowing + receipts (the same identity the ONS figures follow)
    out["spending"] = {"year": round(out["borrowing"]["year"] + out["receipts"]["year"], 3),
                       "months": [round(b + r, 3) for b, r in zip(out["borrowing"]["months"], out["receipts"]["months"])]}
    return {"fiscal_year": f"{fy[:4]}-{fy[-2:]}", **out}


def profiles() -> bool:
    label, url = latest_profiles()
    data = {"vintage": f"{label} EFO", "url": url, **read_profiles(get(url, timeout=120))}
    text = json.dumps(data, indent=1) + "\n"
    if not OUT.exists() or OUT.read_text() != text:   # commit only when it changes
        OUT.write_text(text)
    print(f"[public-finances] OBR profiles: {data['vintage']}, {data['fiscal_year']}")
    return True


if __name__ == "__main__":
    code = run(HERE / "config.yaml")
    try:
        profiles()
    except Exception as e:  # noqa: BLE001 — keep the previous profiles file, go red
        print(f"OBR profiles failed: {e!r}", file=sys.stderr)
        code = 1
    sys.exit(code)
