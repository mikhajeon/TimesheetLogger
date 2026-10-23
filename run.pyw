"""Launches the overlay.

Use the ``.pyw`` extension so Windows starts it without a console window.
Run ``python run.pyw`` to see tracebacks while developing.
"""

from timesheet.ui.overlay import Overlay


def main() -> None:
    Overlay().run()


if __name__ == "__main__":
    main()
