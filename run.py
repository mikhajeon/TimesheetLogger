"""Entry point: python run.py"""

from __future__ import annotations

import sys

from timesheet.ui.overlay import Overlay


def main() -> int:
    if sys.platform != "win32":
        print("The global hotkeys use the Win32 API, so this only runs on Windows.")
        return 1
    Overlay().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
