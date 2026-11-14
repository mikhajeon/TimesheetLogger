"""The previous / today / next row under the tab bar, shared by the Day, Week
and Utilization views.

One canvas holding three fully rounded, anti-aliased pills — chevron, label,
chevron — in the same 1 : 2 : 1 proportions on every tab: the label pill is
half the row and the two arrows share the rest. Each pill lightens on hover,
darkens while pressed, and the label pill can carry an accent ring (shown
when the period on screen contains today).
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import charts, theme

GAP = 6
#: Which pill a point is over, by name.
PREV, MIDDLE, NEXT = "prev", "middle", "next"


class NavRow(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        on_previous: Callable[[], None],
        on_today: Callable[[], None],
        on_next: Callable[[], None],
        text: str = "",
        highlight: bool = False,
        height: int | None = None,
    ) -> None:
        self._h = height or theme.NAV_CHIP_SIZE
        super().__init__(
            parent, height=self._h, bg=theme.BG, highlightthickness=0, bd=0, cursor="hand2",
        )
        self._actions = {PREV: on_previous, MIDDLE: on_today, NEXT: on_next}
        self._text = text
        self._highlight = highlight
        self._rects: dict[str, tuple[int, int]] = {}
        self._hover: str | None = None
        self._pressed: str | None = None
        self._cache: dict = {}
        self._items: dict[str, int] = {}
        self._label: int | None = None
        self.bind("<Configure>", lambda _e: self._layout())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    # -- public ---------------------------------------------------------------

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            if self._label is not None:
                self.itemconfigure(self._label, text=text)

    def set_highlight(self, value: bool) -> None:
        if value != self._highlight:
            self._highlight = value
            self._paint(MIDDLE)

    # -- layout ---------------------------------------------------------------

    def _layout(self) -> None:
        width = self.winfo_width()
        if width < 40:
            return
        middle = width // 2
        arrow = (width - middle - 2 * GAP) // 2
        middle_x = arrow + GAP
        next_x = middle_x + middle + GAP
        self._rects = {
            PREV: (0, arrow),
            MIDDLE: (middle_x, middle),
            NEXT: (next_x, width - next_x),
        }
        self.delete("all")
        self._items = {}
        for name, (x, w) in self._rects.items():
            self._items[name] = self.create_image(x, 0, anchor="nw")
            self._paint(name)
        x, w = self._rects[MIDDLE]
        self._label = self.create_text(
            x + w / 2, self._h / 2, text=self._text, fill=theme.TEXT, font=theme.FONT_BOLD,
        )

    def _state(self, name: str) -> str:
        if self._pressed == name and self._hover == name:
            return "press"
        return "hover" if self._hover == name else "rest"

    def _paint(self, name: str) -> None:
        if name not in self._rects or name not in self._items:
            return
        _x, w = self._rects[name]
        state = self._state(name)
        highlight = self._highlight and name == MIDDLE
        key = (
            name, w, self._h, state, highlight, theme.ACTIVE, theme.BG_INPUT,
            theme.ACCENT, theme.TEXT,
        )
        image = self._cache.get(key)
        if image is None:
            image = self._render(name, w, state, highlight)
            if len(self._cache) > 60:
                self._cache.clear()
            self._cache[key] = image
        self.itemconfigure(self._items[name], image=image)

    def _render(self, name: str, w: int, state: str, highlight: bool) -> tk.PhotoImage:
        h = self._h
        fill = theme.BG_INPUT
        if state == "hover":
            fill = theme.blend(fill, theme.TEXT, 0.10)
        elif state == "press":
            fill = theme.blend(fill, theme.TEXT, 0.18)
        raster = charts.Raster(w, h, theme.BG)
        radius = h / 2
        if highlight:
            raster.rrect(0, 0, w, h, theme.ACCENT, radius, radius)
            raster.rrect(2, 2, w - 2, h - 2, fill, radius - 2, radius - 2)
        else:
            raster.rrect(0, 0, w, h, fill, radius, radius)
        if name != MIDDLE:
            cx, cy = w / 2, h / 2
            sign = -1 if name == PREV else 1
            raster.line(
                [(cx - sign * 3, cy - 5), (cx + sign * 3, cy), (cx - sign * 3, cy + 5)],
                2.2, theme.TEXT,
            )
        return raster.photo(self)

    # -- pointer ----------------------------------------------------------------

    def _at(self, x: int) -> str | None:
        for name, (x0, w) in self._rects.items():
            if x0 <= x < x0 + w:
                return name
        return None

    def _set_hover(self, name: str | None) -> None:
        if name == self._hover:
            return
        previous, self._hover = self._hover, name
        for changed in (previous, name):
            if changed:
                self._paint(changed)

    def _on_motion(self, event: tk.Event) -> None:
        self._set_hover(self._at(event.x))

    def _on_press(self, event: tk.Event) -> None:
        self._pressed = self._at(event.x)
        self._hover = self._pressed
        if self._pressed:
            self._paint(self._pressed)

    def _on_release(self, event: tk.Event) -> None:
        pressed, self._pressed = self._pressed, None
        target = self._at(event.x)
        if pressed:
            self._paint(pressed)
        if pressed and pressed == target:
            self._actions[pressed]()
