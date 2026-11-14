"""Placeholder screens shown the instant a tab is chosen, while the real
view is built: grey bars shaped roughly like what is about to appear, with a
slow pulse so it reads as "loading" rather than "broken"."""

from __future__ import annotations

import tkinter as tk

from . import theme

PULSE_MS = 45
PULSE_PERIOD = 1100  # one full dim-bright-dim cycle

# Per tab: a list of rows; each row is a list of bar widths as fractions of
# the row (0 < w <= 1) and a height in px. ``None`` makes a gap.
_ROWS_LIST = [(0.35, 14), (1.0, 46), (1.0, 46), (1.0, 46), (1.0, 46), (0.8, 46)]
_LAYOUTS: dict[str, list] = {
    "Today": [(0.45, 16), (1.0, 64), None, (0.3, 12), (1.0, 40), (1.0, 40), (1.0, 40)],
    "Week": [(0.5, 16), (1.0, 28), (1.0, 34), (1.0, 34), (1.0, 34), (1.0, 34), (1.0, 34)],
    "Month": [(0.4, 16), (1.0, 30), (1.0, 62), (1.0, 62), (1.0, 62), (1.0, 62)],
    "Projects": _ROWS_LIST,
    "Recurring": _ROWS_LIST,
    "Setup": [(0.3, 14), (1.0, 92), None, (0.3, 14), (1.0, 92), None, (0.3, 14), (1.0, 60)],
    "Export": [(0.4, 16), (1.0, 52), (1.0, 52), None, (0.35, 36)],
}


class Skeleton:
    """A frame of pulsing placeholder bars. ``show`` packs it into
    ``parent``; ``dispose`` stops the pulse and removes it."""

    def __init__(self, parent: tk.Misc, name: str) -> None:
        self.frame = tk.Frame(parent, bg=theme.BG)
        self._bars: list[tk.Frame] = []
        self._after: str | None = None
        self._tick = 0
        inner = tk.Frame(self.frame, bg=theme.BG)
        inner.pack(fill="both", expand=True, padx=14, pady=12)
        for row in _LAYOUTS.get(name, _ROWS_LIST):
            if row is None:
                tk.Frame(inner, bg=theme.BG, height=10).pack(fill="x")
                continue
            width, height = row
            holder = tk.Frame(inner, bg=theme.BG, height=height)
            holder.pack(fill="x", pady=(0, 8))
            holder.pack_propagate(False)
            # A bar of fractional width: placed, so it follows the window
            # whatever size it is (the animation may still be running).
            bar = tk.Frame(holder, bg=theme.BG_RAISED)
            bar.place(x=0, y=0, relwidth=width, relheight=1.0)
            self._bars.append(bar)

    def show(self) -> None:
        self.frame.pack(fill="both", expand=True)
        self._pulse()

    def show_over(self, widget: tk.Misc) -> None:
        """Lays the skeleton exactly over ``widget`` (without moving it)
        and starts the pulse."""
        self.frame.place(in_=widget, x=0, y=0, relwidth=1.0, relheight=1.0)
        self.frame.lift()
        self._pulse()

    def _pulse(self) -> None:
        self._tick += 1
        phase = (self._tick * PULSE_MS % PULSE_PERIOD) / PULSE_PERIOD
        level = 1 - abs(2 * phase - 1)  # 0 -> 1 -> 0
        colour = theme.blend(theme.BG_RAISED, theme.LINE, level)
        for bar in self._bars:
            bar.configure(bg=colour)
        self._after = self.frame.after(PULSE_MS, self._pulse)

    def dispose(self) -> None:
        if self._after is not None:
            try:
                self.frame.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        self.frame.destroy()


def run_behind(widget: tk.Misc, name: str, action, overlay=None) -> None:
    """Optimistic update for a setting changed in place: a skeleton of
    screen ``name`` covers ``widget`` straight away, ``action`` (which
    changes the setting; the view rebuilds from the idle queue) runs right
    after the skeleton has painted, and the skeleton goes once the rebuilt
    view is in place — by dissolving it (via ``overlay``'s snapshot cover), when
    given, rather than dropping it in one step."""
    try:
        skeleton = Skeleton(widget.master, name)
        skeleton.show_over(widget)
        widget.update_idletasks()
    except tk.TclError:
        action()
        return

    def run() -> None:
        cover = None
        try:
            action()
            widget.update_idletasks()  # the deferred rebuild runs here
            if overlay is not None and hasattr(overlay, "_cover_start"):
                cover = overlay._cover_start()  # the skeleton, still up, is what fades
        finally:
            skeleton.dispose()
            if cover is not None:
                overlay._cover_end(cover, fade=True)

    widget.after(12, run)
