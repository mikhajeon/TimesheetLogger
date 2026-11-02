"""Launches the overlay.

Use the ``.pyw`` extension so Windows starts it without a console window.
Run ``python run.pyw`` to see tracebacks while developing.
"""

from timesheet import singleton
from timesheet.ui.overlay import Overlay


def main() -> None:
    # A second launch doesn't get a second overlay on the same data — it
    # hands off to the one already running instead, same as most tray apps.
    if singleton.already_running():
        singleton.focus_existing()
        return
    Overlay().run()


if __name__ == "__main__":
    main()
