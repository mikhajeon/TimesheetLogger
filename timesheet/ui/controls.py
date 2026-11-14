"""Modern, drawn form controls for the Preferences tab.

Tk's own widgets (``OptionMenu``, ``Checkbutton``, ``Scale``) are square,
native-looking and can't be restyled past colours, so they read as a different
app next to the rest of this one. These are the replacements: each is a small
Canvas (or a Frame of them) painted with the theme's tokens and the same
smoothed rounded rectangles as :func:`theme.chip`, so they pick up palette
changes the next time the tab is built and match the cards around them.

* :func:`section`  - a titled, rounded card to hold a group of settings.
* ``Card.row``     - one "label + hint on the left, control on the right" line.
* :class:`Switch`  - an animated on/off toggle (replaces the checkbox).
* :class:`Select`  - a rounded dropdown with a popup list (replaces OptionMenu).
* :class:`Segmented` - a pill of two to four mutually exclusive choices.
* :class:`Slider`  - a draggable track with a thumb (replaces ``tk.Scale``).
* :class:`Swatch`  - a colour square that runs a command when clicked.
* :func:`action`   - a small rounded text button.

Everything is standard library only.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Sequence

from . import theme

CARD_RADIUS = 10
#: Height shared by the field-like controls so a row of them lines up.
FIELD_HEIGHT = 28


def _measure(text: str, font: tuple) -> int:
    return tkfont.Font(font=font).measure(text)


class Card(tk.Frame):
    """A rounded, outlined panel of settings rows on the raised surface."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, bg=theme.BG_RAISED)
        theme.round_corners(self, CARD_RADIUS, border=theme.LINE)
        self.body = tk.Frame(self, bg=theme.BG_RAISED)
        self.body.pack(fill="x", padx=14, pady=4)
        self._rows = 0

    def row(self, title: str, hint: str | None = None) -> tk.Frame:
        """Adds one line and returns the frame its control goes in (pack the
        control ``side="right"``)."""
        if self._rows:
            tk.Frame(self.body, bg=theme.LINE, height=1).pack(fill="x")
        self._rows += 1
        line = tk.Frame(self.body, bg=theme.BG_RAISED)
        line.pack(fill="x", pady=8)
        text = tk.Frame(line, bg=theme.BG_RAISED)
        text.pack(side="left", fill="x", expand=True)
        theme.label(
            text, title, bg=theme.BG_RAISED, fg=theme.TEXT, font=theme.FONT_SMALL, anchor="w"
        ).pack(anchor="w")
        if hint:
            theme.label(
                text, hint, bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
                anchor="w", justify="left", wraplength=260,
            ).pack(anchor="w")
        holder = tk.Frame(line, bg=theme.BG_RAISED)
        holder.pack(side="right", padx=(12, 0))
        return holder

    def note(self, text: str) -> tk.Label:
        """A quiet full-width line under the rows, for explanations."""
        if self._rows:
            tk.Frame(self.body, bg=theme.LINE, height=1).pack(fill="x")
        self._rows += 1
        label = theme.label(
            self.body, text, bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
            anchor="w", justify="left", wraplength=360,
        )
        label.pack(fill="x", pady=8)
        return label


def section(parent: tk.Misc, title: str) -> Card:
    """A small-caps heading followed by an empty :class:`Card`."""
    theme.label(parent, title.upper(), fg=theme.MUTED, font=theme.FONT_HEAD).pack(
        fill="x", pady=(14, 5), padx=2, anchor="w"
    )
    card = Card(parent)
    card.pack(fill="x")
    return card


class _Control(tk.Canvas):
    """Shared plumbing: a flat, borderless canvas on the card surface."""

    def __init__(self, parent: tk.Misc, width: int, height: int, bg: str | None = None) -> None:
        super().__init__(
            parent, width=width, height=height, bg=bg or theme.BG_RAISED,
            highlightthickness=0, bd=0, cursor="hand2",
        )


class Switch(_Control):
    """An on/off toggle whose knob slides across."""

    WIDTH, HEIGHT, FRAMES = 38, 22, 7

    def __init__(
        self, parent: tk.Misc, value: bool, command: Callable[[bool], None] | None = None
    ) -> None:
        super().__init__(parent, self.WIDTH, self.HEIGHT)
        self.value = bool(value)
        self._command = command
        self._t = 1.0 if self.value else 0.0
        self._job: str | None = None
        self.bind("<Button-1>", lambda _e: self.toggle())
        self.bind("<space>", lambda _e: self.toggle())
        self._paint()

    def toggle(self) -> None:
        self.set(not self.value)
        if self._command:
            self._command(self.value)

    def set(self, value: bool) -> None:
        self.value = bool(value)
        if self._job is None:
            self._step()

    def _step(self) -> None:
        goal = 1.0 if self.value else 0.0
        delta = 1 / self.FRAMES
        if abs(goal - self._t) <= delta:
            self._t, self._job = goal, None
        else:
            self._t += delta if goal > self._t else -delta
            self._job = self.after(14, self._step)
        self._paint()

    def _paint(self) -> None:
        self.delete("all")
        w, h = self.WIDTH, self.HEIGHT
        colour = theme.blend(theme.IDLE, theme.ACCENT, self._t)
        theme._round_rect(self, 1, 1, w - 1, h - 1, h / 2, colour)
        r = h / 2 - 3
        x = 3 + r + (w - 6 - 2 * r) * self._t
        self.create_oval(x - r, h / 2 - r, x + r, h / 2 + r, fill="#ffffff", outline="")


class Segmented(_Control):
    """A pill of mutually exclusive choices with the selected one filled."""

    FONT = theme.FONT_SMALL

    def __init__(
        self, parent: tk.Misc, options: Sequence[str], value: str,
        command: Callable[[str], None] | None = None, bg: str | None = None,
    ) -> None:
        self._choices = list(options)
        self._widths = [_measure(o, self.FONT) + 22 for o in self._choices]
        super().__init__(parent, sum(self._widths) + 4, FIELD_HEIGHT, bg=bg)
        self.value = value
        self._command = command
        self.bind("<Button-1>", self._click)
        self._paint()

    def _click(self, event: tk.Event) -> None:
        x = 2
        for option, width in zip(self._choices, self._widths):
            if x <= event.x < x + width:
                if option != self.value:
                    self.value = option
                    self._paint()
                    if self._command:
                        self._command(option)
                return
            x += width

    def _paint(self) -> None:
        self.delete("all")
        w, h = int(self["width"]), FIELD_HEIGHT
        theme._round_rect(self, 0, 0, w, h, 8, theme.BG_INPUT)
        x = 2
        for option, width in zip(self._choices, self._widths):
            selected = option == self.value
            if selected:
                theme._round_rect(self, x, 2, x + width, h - 2, 6, theme.ACCENT)
            self.create_text(
                x + width / 2, h / 2, text=option, font=self.FONT,
                fill=theme.ON_ACCENT if selected else theme.MUTED,
            )
            x += width


class Select(_Control):
    """A rounded dropdown. ``options`` are the labels shown; ``command`` gets
    the chosen label."""

    FONT = theme.FONT_SMALL
    ROW_HEIGHT = 28
    VISIBLE_ROWS = 8

    def __init__(
        self, parent: tk.Misc, options: Sequence[str], value: str,
        command: Callable[[str], None] | None = None, min_width: int = 0,
        bg: str | None = None,
    ) -> None:
        self._choices = list(options)
        widest = max((_measure(o, self.FONT) for o in self._choices), default=40)
        super().__init__(parent, max(min_width, widest + 44), FIELD_HEIGHT, bg=bg)
        self.value = value
        self._command = command
        self._popup: tk.Toplevel | None = None
        self._hover = False
        self.bind("<Button-1>", lambda _e: self._open())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self._paint()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._paint()

    def set_value(self, value: str) -> None:
        """Shows ``value`` as chosen, without calling the command."""
        if value != self.value:
            self.value = value
            self._paint()

    def _paint(self) -> None:
        self.delete("all")
        w, h = int(self["width"]), FIELD_HEIGHT
        theme._round_rect(self, 0, 0, w, h, 8, theme.BG_HOVER if self._hover else theme.BG_INPUT)
        self.create_text(12, h / 2, text=self.value, anchor="w", font=self.FONT, fill=theme.TEXT)
        cx, cy = w - 14, h / 2
        self.create_line(
            cx - 4, cy - 2, cx, cy + 2, cx + 4, cy - 2,
            fill=theme.MUTED, width=2, capstyle="round", joinstyle="round",
        )

    # -- popup -----------------------------------------------------------

    def _open(self) -> None:
        if self._popup is not None:
            self._close()
            return
        count = len(self._choices)
        shown = min(count, self.VISIBLE_ROWS)
        width = int(self["width"])
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.config(bg=theme.BG_RAISED)
        x = self.winfo_rootx()
        y = self.winfo_rooty() + FIELD_HEIGHT + 4
        height = shown * self.ROW_HEIGHT + 8
        screen_h = self.winfo_screenheight()
        if y + height > screen_h - 8:  # no room below: open upward
            y = self.winfo_rooty() - height - 4
        popup.geometry(f"{width}x{height}+{x}+{y}")
        theme.round_corners(popup, 8, border=theme.LINE)
        self._popup = popup
        self._offset = 0
        if count > shown and self.value in self._choices:
            self._offset = max(
                0, min(self._choices.index(self.value) - shown // 2, count - shown)
            )
        self._shown = shown
        self._list = tk.Canvas(
            popup, width=width, height=height, bg=theme.BG_RAISED, highlightthickness=0, bd=0
        )
        self._list.pack(fill="both", expand=True)
        self._hover_row = -1
        self._list.bind("<Motion>", self._on_motion)
        self._list.bind("<Leave>", lambda _e: self._set_row(-1))
        self._list.bind("<Button-1>", self._on_pick)
        self._list.bind("<MouseWheel>", self._on_wheel)
        popup.bind("<Escape>", lambda _e: self._close())
        popup.bind("<FocusOut>", lambda _e: popup.after(80, self._close_if_unfocused))
        self._paint_list()
        popup.focus_force()

    def _close_if_unfocused(self) -> None:
        popup = self._popup
        if popup is None or not popup.winfo_exists():
            return
        if popup.focus_displayof() is None:
            self._close()

    def _close(self) -> None:
        popup, self._popup = self._popup, None
        if popup is not None and popup.winfo_exists():
            popup.destroy()

    def _row_at(self, y: int) -> int:
        row = (y - 4) // self.ROW_HEIGHT
        return row if 0 <= row < self._shown else -1

    def _set_row(self, row: int) -> None:
        if row != self._hover_row:
            self._hover_row = row
            self._paint_list()

    def _on_motion(self, event: tk.Event) -> None:
        self._set_row(self._row_at(event.y))

    def _on_wheel(self, event: tk.Event) -> None:
        step = -1 if event.delta > 0 else 1
        limit = len(self._choices) - self._shown
        self._offset = max(0, min(self._offset + step, limit))
        self._paint_list()

    def _on_pick(self, event: tk.Event) -> None:
        row = self._row_at(event.y)
        if row < 0:
            return
        choice = self._choices[self._offset + row]
        self._close()
        if choice != self.value:
            self.value = choice
            self._paint()
            if self._command:
                self._command(choice)

    def _paint_list(self) -> None:
        canvas = self._list
        canvas.delete("all")
        width = int(self["width"])
        for row in range(self._shown):
            option = self._choices[self._offset + row]
            top = 4 + row * self.ROW_HEIGHT
            selected = option == self.value
            if row == self._hover_row:
                theme._round_rect(canvas, 4, top, width - 4, top + self.ROW_HEIGHT, 6, theme.ACCENT_DIM)
            canvas.create_text(
                14, top + self.ROW_HEIGHT / 2, text=option, anchor="w", font=self.FONT,
                fill=theme.ACCENT if selected else theme.TEXT,
            )
            if selected:
                cx, cy = width - 16, top + self.ROW_HEIGHT / 2
                canvas.create_line(
                    cx - 4, cy, cx - 1, cy + 3, cx + 4, cy - 3,
                    fill=theme.ACCENT, width=2, capstyle="round", joinstyle="round",
                )


class Slider(tk.Frame):
    """A draggable track with a thumb and, to its left, the live value."""

    TRACK_HEIGHT = 4
    THUMB = 9

    def __init__(
        self, parent: tk.Misc, low: float, high: float, value: float, step: float,
        command: Callable[[float], None] | None = None,
        fmt: Callable[[float], str] = lambda v: f"{v:g}", width: int = 150,
    ) -> None:
        super().__init__(parent, bg=theme.BG_RAISED)
        self._low, self._high, self._step = low, high, step
        self._fmt = fmt
        self._command = command
        self.value = self._snap(value)
        self._readout = theme.label(
            self, fmt(self.value), bg=theme.BG_RAISED, fg=theme.MUTED,
            font=theme.FONT_TINY, width=5, anchor="e",
        )
        self._readout.pack(side="left", padx=(0, 8))
        self._canvas = _Control(self, width, 22)
        self._canvas.pack(side="left")
        self._width = width
        self._canvas.bind("<Button-1>", self._drag)
        self._canvas.bind("<B1-Motion>", self._drag)
        self._paint()

    def _snap(self, value: float) -> float:
        value = max(self._low, min(self._high, value))
        steps = round((value - self._low) / self._step)
        return round(self._low + steps * self._step, 6)

    def _drag(self, event: tk.Event) -> None:
        span = self._width - 2 * self.THUMB
        fraction = (event.x - self.THUMB) / span
        value = self._snap(self._low + fraction * (self._high - self._low))
        if value != self.value:
            self.value = value
            self._readout.config(text=self._fmt(value))
            self._paint()
            if self._command:
                self._command(value)

    def _paint(self) -> None:
        canvas, w, mid = self._canvas, self._width, 11
        canvas.delete("all")
        span = w - 2 * self.THUMB
        fraction = (self.value - self._low) / ((self._high - self._low) or 1)
        x = self.THUMB + span * fraction
        half = self.TRACK_HEIGHT / 2
        theme._round_rect(canvas, self.THUMB, mid - half, w - self.THUMB, mid + half, half, theme.BG_INPUT)
        if x > self.THUMB:
            theme._round_rect(canvas, self.THUMB, mid - half, x, mid + half, half, theme.ACCENT)
        r = self.THUMB
        canvas.create_oval(x - r, mid - r, x + r, mid + r, fill="#ffffff", outline=theme.LINE)


class Swatch(_Control):
    """A colour square; clicking it runs ``command`` (typically a chooser)."""

    SIZE = 28

    def __init__(self, parent: tk.Misc, colour: str, command: Callable[[], None]) -> None:
        super().__init__(parent, self.SIZE, self.SIZE)
        theme._round_rect(self, 1, 1, self.SIZE - 1, self.SIZE - 1, 8, theme.LINE)
        theme._round_rect(self, 2, 2, self.SIZE - 2, self.SIZE - 2, 7, colour)
        self.bind("<Button-1>", lambda _e: command())


def action(parent: tk.Misc, text: str, command: Callable[[], None], *, primary: bool = False):
    """A small rounded text button, on the card surface."""
    width = _measure(text, theme.FONT_SMALL) + 26
    return theme.chip(
        parent, command,
        fill=theme.ACCENT if primary else theme.BG_INPUT,
        text=text, text_fill=theme.ON_ACCENT if primary else theme.TEXT,
        width=width, height=FIELD_HEIGHT, radius=8, bg=theme.BG_RAISED, font=theme.FONT_SMALL,
    )
