"""Builds the real window, visits every tab, then closes.

Not part of the unittest suite because it needs a desktop session.
Run with: python tests/gui_smoke.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

workspace = tempfile.mkdtemp(prefix="tslog-smoke-")
os.environ["TIMESHEET_HOME"] = workspace

from timesheet.ui.overlay import Overlay  # noqa: E402  (must follow the env var)


def main() -> int:
    overlay = Overlay()
    store = overlay.store

    client = store.add_client("Acme Ltd")
    project = store.add_project(client.id, "Platform Migration", "K-10234", True)
    task = store.add_task(project.id, "Cutover rehearsal", True)
    from timesheet.dates import today

    store.set_plan(task.id, today(), 120)
    store.add_entry(task.id, today(), 95, "Ran the staging cutover with their DBA")

    for tab in ("Today", "Week", "Export", "Setup"):
        overlay.show_tab(tab)
        overlay.root.update()
        print(f"  {tab} rendered")

    overlay._expanded = {task.id}
    overlay.show_tab("Today")
    overlay.root.update()
    print("  Today (task expanded) rendered")

    overlay.collapse(True)
    overlay.root.update()
    overlay.collapse(False)
    overlay.root.update()
    print("  collapse / expand OK")

    overlay.toggle_always_on_top()
    overlay.toggle_always_on_top()
    overlay.toggle_wide()
    overlay.root.update()
    print("  pin and wide toggles OK")

    overlay.root.after(150, overlay.quit)
    overlay.run()
    print("GUI smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
