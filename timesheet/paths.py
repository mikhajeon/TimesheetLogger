"""Where the data and the generated notes live.

Defaults to the repo itself so Cursor can read `exports/` without any extra
configuration. Set ``TIMESHEET_HOME`` to put them somewhere else.

The single-file ``.exe`` build unpacks itself into a temporary folder on every
launch, so there the default is a ``Timesheet Logger`` folder under ``%LOCALAPPDATA%``:
anything written beside the code would vanish when the app closed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: True inside the PyInstaller build, where there is no ``pythonw.exe`` and
#: no ``run.pyw`` to point a shortcut at: the executable is the app.
FROZEN = bool(getattr(sys, "frozen", False))


def home() -> Path:
    override = os.environ.get("TIMESHEET_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if FROZEN:
        base = os.environ.get("LOCALAPPDATA") or Path.home()
        return Path(base) / "Timesheet Logger"
    return PROJECT_ROOT


def data_file() -> Path:
    return home() / "data" / "timesheet.json"


def window_state_file() -> Path:
    return home() / "data" / "window.json"


def exports_dir() -> Path:
    return home() / "exports"
