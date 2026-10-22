"""Opens the overlay on one tab at a fixed position, then signals it is painted.

Used by capture.ps1; takes: <tab> <x> <y> <width> <height>
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timesheet.ui.overlay import Overlay


def main() -> None:
    tab, x, y, width, height = sys.argv[1], *map(int, sys.argv[2:6])

    overlay = Overlay()
    overlay.root.geometry(f"{width}x{height}+{x}+{y}")
    overlay.show_tab(tab)
    overlay.root.lift()

    ready = Path(tempfile.gettempdir()) / "tslog-ready"

    def signal() -> None:
        overlay.root.update_idletasks()
        ready.write_text("ok", "utf-8")

    overlay.root.after(900, signal)
    overlay.run()


if __name__ == "__main__":
    main()
