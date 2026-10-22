"""Where the data and the generated notes live.

Defaults to the repo itself so Cursor can read `exports/` without any extra
configuration. Set ``TIMESHEET_HOME`` to put them somewhere else.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def home() -> Path:
    override = os.environ.get("TIMESHEET_HOME")
    return Path(override).expanduser().resolve() if override else PROJECT_ROOT


def data_file() -> Path:
    return home() / "data" / "timesheet.json"


def window_state_file() -> Path:
    return home() / "data" / "window.json"


def exports_dir() -> Path:
    return home() / "exports"
