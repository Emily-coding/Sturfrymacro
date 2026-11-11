#!/usr/bin/env python3
"""Labour markets builder: headline charts from config.yaml
(employment, unemployment, vacancies, pay, G7).

Bespoke analyses (e.g. a decomposition that needs more than plotting a series)
go below `run(...)` as their own functions writing into the same data/ folder.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # repo root, so `ukmacro` imports

from ukmacro.builder import run  # noqa: E402

import downturns  # noqa: E402
import neet  # noqa: E402

if __name__ == "__main__":
    status = run(HERE / "config.yaml")
    for step in (neet.build, downturns.build):  # charts for labour.html's NEETs section
        try:
            step()
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            status = 1
    sys.exit(status)
