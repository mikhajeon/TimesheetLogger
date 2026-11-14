"""Launches the overlay.

Use the ``.pyw`` extension so Windows starts it without a console window.
Run ``python run.pyw`` to see tracebacks while developing.
"""

from timesheet import singleton
from timesheet.ui.overlay import Overlay


def main() -> None:
    # A new launch replaces the running one: the old instance is closed
    # (cleanly where it can be) and this one takes over its data folder.
    # Only if no running window can be found to close does it fall back to
    # bringing that instance forward instead.
    if singleton.already_running() and not singleton.replace_existing():
        singleton.focus_existing()
        return
    overlay = Overlay()
    singleton.watch_for_quit(overlay.root, overlay.quit)
    overlay.run()


if __name__ == "__main__":
    main()
