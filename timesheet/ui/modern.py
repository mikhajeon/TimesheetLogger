"""Anti-aliased building blocks for the Day tab's cards and its entry dialog.

Tk's canvas has no anti-aliasing, so every shape here is drawn by
``charts.Raster`` (stdlib only) into a small image that is cached by its
size and colours: a card, an input field, a round button, a pill button, a
radio dot, a soft shadow. Each widget is a plain ``tk.Canvas`` showing one of
those images (plus, where needed, a real Tk widget laid on top), so it
behaves like any other widget when packed or gridded.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from typing import Callable

from . import charts, theme, yielding

_CACHE: dict = {}


def _photo(master: tk.Misc, key: tuple, draw: Callable[[], "charts.Raster"]) -> tk.PhotoImage:
    """The image ``draw`` makes, remembered by ``key`` for as long as the app
    runs (a PhotoImage is also dropped by Tk the moment nothing refers to it,
    so the cache doubles as the thing that keeps it alive)."""
    full = (id(master._root()), key)
    image = _CACHE.get(full)
    if image is None:
        if len(_CACHE) > 600:
            _CACHE.clear()
        image = _CACHE[full] = draw().photo(master)
        yielding.checkpoint()  # drawing is the slow part of building a card
    return image


def _bg_of(widget: tk.Misc) -> str:
    try:
        return widget.cget("bg")
    except tk.TclError:
        return theme.BG


# -- surfaces --------------------------------------------------------------------


class Surface(tk.Canvas):
    """A rounded, bordered card with an anti-aliased edge. Put its contents
    in ``.body``; the canvas grows to fit them.

    The body sits ``inset`` pixels in from the edge so its square corners stay
    inside the curve (3px suits a radius of about 8; use ``radius * 0.3``)."""

    def __init__(
        self, parent: tk.Misc, *, fill: str | None = None, border: str | None = None,
        radius: int = 8, thickness: float = 1, inset: int | None = None,
        outer: str | None = None,
        backdrop: Callable[[int, int, int, int], bytes | None] | None = None,
    ) -> None:
        """``backdrop(x, y, w, h)`` — when what lies behind the card is a
        picture, not a flat colour — returns that picture's RGB bytes for
        the rectangle the card covers, so the rounded corners and the edge
        blend into it exactly instead of into a flat stand-in."""
        self._backdrop = backdrop
        self._frozen = False
        self._outer = outer or _bg_of(parent)
        super().__init__(parent, bg=self._outer, highlightthickness=0, bd=0)
        self._fill = fill or theme.BG_RAISED
        self._border = border or theme.LINE
        self._thick = thickness
        self._radius = radius
        self._inset = inset if inset is not None else max(2, round(radius * 0.38))
        self._img = self.create_image(0, 0, anchor="nw")
        self.body = tk.Frame(self, bg=self._fill, bd=0, highlightthickness=0)
        self._win = self.create_window(self._inset, self._inset, anchor="nw", window=self.body)
        self.configure(height=2 * self._inset + 1)
        self.bind("<Configure>", self._on_size)
        self.body.bind("<Configure>", self._fit)

    def freeze(self, value: bool) -> None:
        """While frozen the card keeps the picture it has (it is being moved
        or clipped by an animation); unfreezing repaints it once."""
        self._frozen = value
        if not value:
            self._paint()

    def set_border(self, colour: str, thickness: float = 1) -> None:
        if (colour, thickness) != (self._border, self._thick):
            self._border, self._thick = colour, thickness
            self._paint()

    def _fit(self, _event: tk.Event | None = None) -> None:
        height = self.body.winfo_reqheight() + 2 * self._inset
        if int(self.cget("height")) != height:
            self.configure(height=height)

    def _on_size(self, _event: tk.Event) -> None:
        width = self.winfo_width()
        if width > 2 * self._inset + 2:
            self.itemconfigure(self._win, width=width - 2 * self._inset)
        self._paint()

    def _paint(self) -> None:
        if self._frozen:
            return
        w, h = self.winfo_width(), self.winfo_height()
        if w < 8 or h < 8:
            return
        where = (self.winfo_x(), self.winfo_y()) if self._backdrop else None
        key = ("surface", w, h, self._fill, self._border, self._thick, self._radius, self._outer,
               where, getattr(self._backdrop, "token", None))

        def draw() -> charts.Raster:
            raster = charts.Raster(w, h, self._outer)
            if self._backdrop is not None:
                behind = self._backdrop(where[0], where[1], w, h)
                if behind is not None and len(behind) == w * h * 3:
                    raster.buf = bytearray(behind)
            r, t = self._radius, self._thick
            raster.rrect(0, 0, w, h, self._border, r, r)
            raster.rrect(t, t, w - t, h - t, self._fill, max(r - t, 0), max(r - t, 0))
            return raster

        self.itemconfigure(self._img, image=_photo(self, key, draw))


def soft_shadow(master: tk.Misc, width: int, height: int, radius: int, spread: int,
                bg: str, strength: float = 0.5) -> tk.PhotoImage:
    """A soft drop shadow for a ``width`` x ``height`` card: the image is
    ``spread`` pixels bigger on every side and fades out to ``bg``."""
    key = ("shadow", width, height, radius, spread, bg, strength)

    def draw() -> charts.Raster:
        w, h = width + 2 * spread, height + 2 * spread
        raster = charts.Raster(w, h, bg)
        for i in range(spread):
            fade = (i + 1) / spread  # darkest at the card's own edge
            colour = theme.blend(bg, "#000000", strength * fade * fade)
            raster.rrect(i, i, w - i, h - i, colour, radius + spread - i, radius + spread - i)
        return raster

    return _photo(master, key, draw)


# -- fields ----------------------------------------------------------------------


class Field(tk.Canvas):
    """A rounded text field: a real ``tk.Entry`` (or, with ``lines``, a
    ``tk.Text``) in ``.entry``, inside an anti-aliased box whose border turns
    to the accent colour while it has focus."""

    PAD = 6

    def __init__(
        self, parent: tk.Misc, *, bg: str | None = None, lines: int = 0,
        font: tuple = theme.FONT_SMALL, radius: int = 8, pad: int | None = None, lift: int = 0, **kw,
    ) -> None:
        self._outer = bg or _bg_of(parent)
        super().__init__(parent, bg=self._outer, highlightthickness=0, bd=0)
        if pad is not None:
            self.PAD = pad
        self._radius = radius
        self._lift = lift  # px the text sits above the box's centre line
        self._focused = False
        common = dict(
            bg=theme.BG_INPUT, fg=theme.TEXT, font=font, relief="flat", bd=0,
            highlightthickness=0, insertbackground=theme.TEXT,
        )
        if lines:
            self.entry: tk.Entry | tk.Text = tk.Text(
                self, height=lines, wrap="word", padx=0, pady=0, **common, **kw
            )
        else:
            self.entry = tk.Entry(self, disabledbackground=theme.BG_INPUT, **common, **kw)
        self._img = self.create_image(0, 0, anchor="nw")
        self._win = self.create_window(self.PAD + 1, self.PAD, anchor="nw", window=self.entry)
        self._lead: tk.Misc | None = None
        self._lead_w = 0
        self._lead_win: int | None = None
        self._divider: int | None = None
        self.configure(height=self.entry.winfo_reqheight() + 2 * self.PAD)
        self.bind("<Configure>", self._on_size)
        self.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda _e: self._focus(True), add="+")
        self.entry.bind("<FocusOut>", lambda _e: self._focus(False), add="+")

    def _focus(self, value: bool) -> None:
        self._focused = value
        self._paint()

    def set_lead(self, widget: tk.Misc) -> None:
        """Puts ``widget`` (a child of this field) inside the box at its far
        left, set off from the text by a thin vertical divider."""
        widget.update_idletasks()
        self._lead = widget
        self._lead_w = widget.winfo_reqwidth() + 13
        self._lead_win = self.create_window(
            self.PAD + 2, 0, anchor="w", window=widget,
        )
        self._divider = self.create_line(0, 0, 0, 0, fill=theme.LINE, width=1)
        self._layout()

    def _layout(self) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        x = self.PAD + 1 + self._lead_w
        if width > 2 * self.PAD + 4:
            self.itemconfigure(self._win, width=max(width - 2 * self.PAD - 2 - self._lead_w, 4))
        self.itemconfigure(self._win, anchor="w")
        self.coords(self._win, x, max(height, 2 * self.PAD + 4) / 2 - self._lift)
        if self._lead is not None and height > 8:
            self.coords(self._lead_win, self.PAD + 2, height / 2)
            dx = x - 5.5
            self.coords(self._divider, dx, height * 0.27, dx, height * 0.73)
            self.tag_raise(self._lead_win)

    def _on_size(self, _event: tk.Event) -> None:
        self._layout()
        self._paint()

    def _paint(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 8 or h < 8:
            return
        edge = theme.ACCENT if self._focused else theme.LINE
        key = ("field", w, h, edge, self._focused, self._radius, self._outer, theme.BG_INPUT)

        def draw() -> charts.Raster:
            raster = charts.Raster(w, h, self._outer)
            r = self._radius
            raster.rrect(0, 0, w, h, edge, r, r)
            t = 1.6 if self._focused else 1.0
            raster.rrect(t, t, w - t, h - t, theme.BG_INPUT, max(r - t, 0), max(r - t, 0))
            return raster

        self.itemconfigure(self._img, image=_photo(self, key, draw))


class ValueButton(tk.Canvas):
    """The Actual / Planned Hours box: a rounded field showing a value and a
    chevron, lighter on hover. Quacks like the ``tk.Button`` it replaces
    (``config(text=...)``, ``cget("text")``, ``invoke()``)."""

    def __init__(
        self, parent: tk.Misc, text: str, command: Callable[[], None], *,
        font: tuple = theme.FONT_SMALL, width: int | None = None, pady: int = 2,
        radius: int = 7, bg: str | None = None, chevron: bool = True,
    ) -> None:
        self._chevron = chevron
        self._fg: str | None = None
        self._outer = bg or _bg_of(parent)
        self._font = tkfont.Font(root=parent, font=font)
        self._text = text
        self._command = command
        self._radius = radius
        self._hover = False
        self._pressed = False
        chars = width or max(len(text), 4)
        wd = self._font.measure("0" * chars) + (30 if chevron else 20)
        ht = self._font.metrics("linespace") + 2 * pady + 4
        super().__init__(
            parent, width=wd, height=ht, bg=self._outer, highlightthickness=0, bd=0,
            cursor="hand2",
        )
        self._img = self.create_image(0, 0, anchor="nw")
        self._label = self.create_text(
            0, 0, text=text, font=font, fill=theme.TEXT, anchor="w",
        )
        self.bind("<Configure>", lambda _e: self._paint())
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)

    # tk.Button-alike surface ------------------------------------------------------

    def configure(self, cnf=None, **kw):  # noqa: D401 - mirrors tk
        text = kw.pop("text", None)
        kw.pop("width", None)  # sized once, from the characters asked for
        fg = kw.pop("fg", None)
        if fg is not None:
            self._fg = fg
            self.itemconfigure(self._label, fill=fg)
        if text is not None:
            self._text = text
            self.itemconfigure(self._label, text=text)
            self._paint()
        if cnf or kw:
            return super().configure(cnf, **kw)
        return None

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        return super().cget(key)

    def invoke(self) -> None:
        self._command()

    # ------------------------------------------------------------------------------

    def _set(self, hover: bool | None = None, pressed: bool | None = None) -> None:
        if hover is not None:
            self._hover = hover
        if pressed is not None:
            self._pressed = pressed
        self._paint()

    def _release(self, event: tk.Event) -> None:
        was, self._pressed = self._pressed, False
        self._paint()
        if was and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self._command()

    def _paint(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 12 or h < 8:
            return
        state = "press" if self._pressed else "hover" if self._hover else "rest"
        key = ("value", w, h, state, self._radius, self._outer, theme.BG_INPUT, theme.LINE,
               theme.MUTED, theme.TEXT)

        def draw() -> charts.Raster:
            fill = theme.BG_INPUT
            if state != "rest":
                fill = theme.blend(fill, theme.TEXT, 0.09 if state == "hover" else 0.16)
            raster = charts.Raster(w, h, self._outer)
            r = self._radius
            raster.rrect(0, 0, w, h, theme.LINE, r, r)
            raster.rrect(1, 1, w - 1, h - 1, fill, max(r - 1, 0), max(r - 1, 0))
            if self._chevron:
                cx, cy = w - 12, h / 2
                colour = theme.TEXT if state != "rest" else theme.MUTED
                raster.line([(cx - 3.2, cy - 1.6), (cx, cy + 1.6), (cx + 3.2, cy - 1.6)], 1.6, colour)
            return raster

        self.itemconfigure(self._img, image=_photo(self, key, draw))
        self.coords(self._label, 10, h / 2)
        self.tag_raise(self._label)


# -- buttons ---------------------------------------------------------------------


class RoundButton(tk.Canvas):
    """A small circular button with a drawn glyph (``plus``, ``minus``,
    ``close`` or ``down``): a soft filled disc with a fine edge that lightens
    on hover and darkens while pressed. ``set_enabled`` dims it and makes it
    inert, the way the minus button is when there is nothing left to take."""

    def __init__(
        self, parent: tk.Misc, glyph: str, command: Callable[[], None], *,
        size: int = 20, fill: str | None = None, glyph_colour: str | None = None,
        bg: str | None = None, enabled: bool = True,
    ) -> None:
        self._outer = bg or _bg_of(parent)
        super().__init__(
            parent, width=size, height=size, bg=self._outer, highlightthickness=0, bd=0,
            cursor="hand2" if enabled else "arrow",
        )
        self._glyph, self._command, self._size = glyph, command, size
        self._fill = fill or theme.BG_INPUT
        self._colour = glyph_colour or theme.MUTED
        self._enabled = enabled
        self._hover = self._pressed = False
        self._img = self.create_image(0, 0, anchor="nw")
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self._paint()

    def set_enabled(self, value: bool, text_fill: str | None = None) -> None:
        if text_fill is not None:
            self._colour = text_fill
        self._enabled = value
        self.configure(cursor="hand2" if value else "arrow")
        self._paint()

    def invoke(self) -> None:
        if self._enabled:
            self._command()

    def _set(self, hover: bool | None = None, pressed: bool | None = None) -> None:
        if hover is not None:
            self._hover = hover
        if pressed is not None:
            self._pressed = pressed
        self._paint()

    def _release(self, event: tk.Event) -> None:
        was, self._pressed = self._pressed, False
        self._paint()
        inside = 0 <= event.x < self._size and 0 <= event.y < self._size
        if was and inside and self._enabled:
            self._command()

    def _paint(self) -> None:
        state = "rest"
        if self._enabled:
            state = "press" if self._pressed else "hover" if self._hover else "rest"
        key = ("round", self._glyph, self._size, state, self._enabled, self._fill, self._colour,
               self._outer, theme.LINE, theme.TEXT)

        def draw() -> charts.Raster:
            s = self._size
            fill = self._fill
            if state != "rest":
                fill = theme.blend(fill, theme.TEXT, 0.10 if state == "hover" else 0.18)
            raster = charts.Raster(s, s, self._outer)
            c = s / 2
            raster.disc(c, c, c - 0.4, theme.LINE)
            raster.disc(c, c, c - 1.4, fill)
            colour = self._colour if self._enabled else theme.IDLE
            if self._enabled and state != "rest" and self._glyph != "trash":
                colour = theme.TEXT  # (a trash can keeps its warning colour)
            _glyph(raster, self._glyph, s, colour)
            return raster

        self.itemconfigure(self._img, image=_photo(self, key, draw))


def _glyph(
    raster: charts.Raster, name: str, s: float, colour: str,
    cx: float | None = None, cy: float | None = None,
) -> None:
    cx = s / 2 if cx is None else cx
    cy = s / 2 if cy is None else cy
    k = s * 0.22
    width = max(1.5, s * 0.09)
    if name in ("plus", "minus"):
        raster.line([(cx - k, cy), (cx + k, cy)], width, colour)
        if name == "plus":
            raster.line([(cx, cy - k), (cx, cy + k)], width, colour)
    elif name == "close":
        k = s * 0.19
        raster.line([(cx - k, cy - k), (cx + k, cy + k)], width, colour)
        raster.line([(cx - k, cy + k), (cx + k, cy - k)], width, colour)
    elif name == "down":
        raster.line([(cx - k, cy - k / 2), (cx, cy + k / 2), (cx + k, cy - k / 2)], width, colour)
    elif name == "trash":
        w = max(1.3, s * 0.075)
        top, bottom = cy - k * 0.6, cy + k * 1.15
        raster.line([(cx - k * 1.15, top), (cx + k * 1.15, top)], w, colour)  # lid
        raster.line([(cx - k * 0.45, top), (cx - k * 0.45, top - k * 0.45),
                     (cx + k * 0.45, top - k * 0.45), (cx + k * 0.45, top)], w, colour)  # handle
        raster.line([(cx - k * 0.85, top), (cx - k * 0.7, bottom),
                     (cx + k * 0.7, bottom), (cx + k * 0.85, top)], w, colour)  # bin
        for dx in (-k * 0.3, k * 0.3):  # slats
            raster.line([(cx + dx, top + k * 0.45), (cx + dx, bottom - k * 0.4)], w * 0.8, colour)
    elif name == "dots":
        dot = max(1.3, s * 0.075)
        for dx in (-k * 0.95, 0, k * 0.95):
            raster.disc(cx + dx, cy, dot, colour)


class EdgeButton(tk.Canvas):
    """A +/- key shaped to sit inside the card's own outline: it fills the
    space it is given, its two outer corners (``outer`` = ``top`` or
    ``bottom``) curve to follow the card's corner, the inner ones barely.
    Same ``set_enabled``/``invoke`` surface as ``RoundButton``."""

    def __init__(
        self, parent: tk.Misc, glyph: str, command: Callable[[], None], *,
        outer: str = "top", width: int = 20, radius: float = 5, fill: str | None = None,
        bg: str | None = None, enabled: bool = True,
    ) -> None:
        self._bg = bg or _bg_of(parent)
        super().__init__(
            parent, width=width, height=12, bg=self._bg, highlightthickness=0, bd=0,
            cursor="hand2" if enabled else "arrow",
        )
        self._glyph, self._command, self._edge = glyph, command, outer
        self._radius = radius
        self._fill = fill or theme.BG_INPUT
        self._colour = theme.MUTED
        self._enabled = enabled
        self._hover = self._pressed = False
        self._img = self.create_image(0, 0, anchor="nw")
        self.bind("<Configure>", lambda _e: self._paint())
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)

    def set_enabled(self, value: bool, text_fill: str | None = None) -> None:
        if text_fill is not None:
            self._colour = text_fill
        self._enabled = value
        self.configure(cursor="hand2" if value else "arrow")
        self._paint()

    def invoke(self) -> None:
        if self._enabled:
            self._command()

    def _set(self, hover: bool | None = None, pressed: bool | None = None) -> None:
        if hover is not None:
            self._hover = hover
        if pressed is not None:
            self._pressed = pressed
        self._paint()

    def _release(self, event: tk.Event) -> None:
        was, self._pressed = self._pressed, False
        self._paint()
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        if was and inside and self._enabled:
            self._command()

    def _paint(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 6 or h < 6:
            return
        state = "rest"
        if self._enabled:
            state = "press" if self._pressed else "hover" if self._hover else "rest"
        key = ("edge", self._glyph, self._edge, w, h, state, self._enabled, self._fill,
               self._colour, self._bg, self._radius, theme.TEXT, theme.IDLE)

        def draw() -> charts.Raster:
            fill = self._fill
            if state != "rest":
                fill = theme.blend(fill, theme.TEXT, 0.10 if state == "hover" else 0.18)
            raster = charts.Raster(w, h, self._bg)
            big, small = self._radius, 2
            top, bottom = (big, small) if self._edge == "top" else (small, big)
            raster.rrect(0, 0, w, h, fill, top, bottom)
            colour = self._colour if self._enabled else theme.IDLE
            if self._enabled and state != "rest":
                colour = theme.TEXT
            _glyph(raster, self._glyph, min(w, 26), colour, w / 2, h / 2)
            return raster

        self.itemconfigure(self._img, image=_photo(self, key, draw))


class PillButton(tk.Canvas):
    """A fully rounded text button. ``kind``: ``primary`` (accent), ``danger``,
    ``ghost`` (a quiet input-coloured pill) or ``outline`` (edge only)."""

    def __init__(
        self, parent: tk.Misc, text: str, command: Callable[[], None], *,
        kind: str = "ghost", font: tuple = theme.FONT_SMALL, padx: int = 14, pady: int = 4,
        bg: str | None = None,
    ) -> None:
        self._outer = bg or _bg_of(parent)
        font_obj = tkfont.Font(root=parent, font=font)
        wd = font_obj.measure(text) + 2 * padx
        ht = font_obj.metrics("linespace") + 2 * pady + 2
        super().__init__(
            parent, width=wd, height=ht, bg=self._outer, highlightthickness=0, bd=0,
            cursor="hand2",
        )
        self._kind, self._command, self._wd, self._ht = kind, command, wd, ht
        self._hover = self._pressed = False
        self._img = self.create_image(0, 0, anchor="nw")
        self._text = self.create_text(wd / 2, ht / 2, text=text, font=font, fill=self._text_colour())
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda _e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self._paint()

    def _text_colour(self) -> str:
        return {
            "primary": theme.ON_ACCENT, "danger": theme.ON_DANGER,
            "outline": theme.DANGER,
        }.get(self._kind, theme.TEXT)

    def invoke(self) -> None:
        self._command()

    def _set(self, hover: bool | None = None, pressed: bool | None = None) -> None:
        if hover is not None:
            self._hover = hover
        if pressed is not None:
            self._pressed = pressed
        self._paint()

    def _release(self, event: tk.Event) -> None:
        was, self._pressed = self._pressed, False
        self._paint()
        if was and 0 <= event.x < self._wd and 0 <= event.y < self._ht:
            self._command()

    def _paint(self) -> None:
        state = "press" if self._pressed else "hover" if self._hover else "rest"
        key = ("pill", self._kind, self._wd, self._ht, state, self._outer, theme.ACCENT,
               theme.DANGER, theme.BG_INPUT, theme.LINE, theme.TEXT)

        def draw() -> charts.Raster:
            base = {
                "primary": theme.ACCENT, "danger": theme.DANGER, "ghost": theme.BG_INPUT,
                "outline": theme.BG_RAISED,
            }.get(self._kind, theme.BG_INPUT)
            if state == "hover":
                base = theme.blend(base, theme.TEXT, 0.10)
            elif state == "press":
                base = theme.darken(base, 0.16)
            raster = charts.Raster(self._wd, self._ht, self._outer)
            r = self._ht / 2
            if self._kind in ("outline", "ghost"):
                edge = theme.DANGER if self._kind == "outline" else theme.LINE
                raster.rrect(0, 0, self._wd, self._ht, edge, r, r)
                raster.rrect(1, 1, self._wd - 1, self._ht - 1, base, r - 1, r - 1)
            else:
                raster.rrect(0, 0, self._wd, self._ht, base, r, r)
            return raster

        self.itemconfigure(self._img, image=_photo(self, key, draw))
        self.tag_raise(self._text)


# -- selection indicator -------------------------------------------------------------


def radio(
    parent: tk.Misc, selected: bool, command: Callable[[], None], *,
    fill: str | None = None, bg: str | None = None, size: int = 20, enabled: bool = True,
) -> tk.Canvas:
    """A round, single-choice indicator: a ring that fills with a centred dot
    when selected, firms up its edge on hover and dips while pressed.

    ``canvas.set_selected(value)`` repaints it in place, so whoever owns the
    selection can show a change without rebuilding the row around it."""
    fill = fill or theme.ACCENT
    outer = bg or _bg_of(parent)
    canvas = tk.Canvas(
        parent, width=size, height=size, bg=outer, highlightthickness=0, bd=0,
        cursor="hand2" if enabled else "arrow",
    )
    image_id = canvas.create_image(0, 0, anchor="nw")
    state = {"on": selected, "hover": False, "down": False}

    def paint() -> None:
        on, hover, down = state["on"], state["hover"], state["down"]
        key = ("radio", size, on, fill, outer, enabled, hover, down, theme.MUTED, theme.TEXT,
               theme.IDLE)

        def draw() -> charts.Raster:
            raster = charts.Raster(size, size, outer)
            c = size / 2
            radius = c - 1.0
            if on:
                # Pressed shrinks the dot and tints the ring, so the click is
                # felt even though the result is identical once released.
                shade = theme.blend(fill, "#000000", 0.25) if down else fill
                raster.disc(c, c, radius, shade)
                raster.disc(c, c, radius - 1.8, outer)
                raster.disc(c, c, max(radius * (0.30 if down else 0.42), 1.5), shade)
            else:
                edge = theme.IDLE if not enabled else fill if down else theme.TEXT if hover else theme.MUTED
                raster.disc(c, c, radius, edge)
                raster.disc(c, c, radius - 1.4, outer)
                if down and enabled:
                    raster.disc(c, c, radius * 0.5, theme.blend(outer, fill, 0.55))
                elif hover and enabled:
                    raster.disc(c, c, radius * 0.34, theme.blend(outer, theme.TEXT, 0.25))
            return raster

        canvas.itemconfigure(image_id, image=_photo(canvas, key, draw))

    def set_selected(value: bool) -> None:
        state["on"] = bool(value)
        paint()

    canvas.set_selected = set_selected  # type: ignore[attr-defined]
    paint()
    if enabled:
        def enter(_e) -> None:
            state["hover"] = True
            paint()

        def leave(_e) -> None:
            state["hover"] = state["down"] = False
            paint()

        def press(_e) -> None:
            state["down"] = True
            paint()

        def release(e) -> None:
            was_down = state["down"]
            state["down"] = False
            paint()
            if was_down and 0 <= e.x < size and 0 <= e.y < size:
                command()

        canvas.bind("<Enter>", enter)
        canvas.bind("<Leave>", leave)
        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<ButtonRelease-1>", release)
    return canvas


# -- switch, chips, segmented control -----------------------------------------------


class Toggle(tk.Canvas):
    """An on/off switch: a pill track with a sliding white knob. ``command``
    gets the new value when clicked."""

    W, H = 34, 20

    def __init__(self, parent: tk.Misc, on: bool, command: Callable[[bool], None], *,
                 bg: str | None = None) -> None:
        self._outer = bg or _bg_of(parent)
        super().__init__(parent, width=self.W, height=self.H, bg=self._outer,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._on, self._command = bool(on), command
        self._hover = self._down = False
        self._img = self.create_image(0, 0, anchor="nw")
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, down=False))
        self.bind("<ButtonPress-1>", lambda _e: self._set(down=True))
        self.bind("<ButtonRelease-1>", self._release)
        self._paint()

    def _set(self, hover=None, down=None) -> None:
        if hover is not None:
            self._hover = hover
        if down is not None:
            self._down = down
        self._paint()

    def _release(self, event: tk.Event) -> None:
        was, self._down = self._down, False
        if was and 0 <= event.x < self.W and 0 <= event.y < self.H:
            self._on = not self._on
            self._paint()
            self._command(self._on)
        else:
            self._paint()

    def _paint(self) -> None:
        on, hover, down = self._on, self._hover, self._down
        key = ("toggle", on, hover, down, self._outer, theme.ACCENT, theme.BG_INPUT, theme.LINE)

        def draw() -> charts.Raster:
            w, h = self.W, self.H
            raster = charts.Raster(w, h, self._outer)
            r = h / 2
            if on:
                track = theme.ACCENT_HOVER if hover else theme.ACCENT
                raster.rrect(0, 0, w, h, track, r, r)
            else:
                raster.rrect(0, 0, w, h, theme.LINE, r, r)
                inner = theme.blend(theme.BG_INPUT, theme.TEXT, 0.08 if hover else 0.0)
                raster.rrect(1, 1, w - 1, h - 1, inner, r - 1, r - 1)
            knob = 7.5 if down else 7.0
            cx = w - r if on else r
            if down:
                cx += -2 if on else 2
            raster.disc(cx, h / 2 + 0.6, knob + 0.8, theme.blend(self._outer, "#000000", 0.35))
            raster.disc(cx, h / 2, knob, "#ffffff" if on else theme.MUTED if not hover else theme.TEXT)
            return raster

        self.itemconfigure(self._img, image=_photo(self, key, draw))


class ToggleChip(tk.Canvas):
    """A rounded on/off chip that fills its grid cell: accent when on, a soft
    input-coloured pill when off, lighter on hover."""

    def __init__(self, parent: tk.Misc, text: str, on: bool, command: Callable[[], None], *,
                 bg: str | None = None, font: tuple = theme.FONT_TINY, pady: int = 3,
                 radius: int = 8) -> None:
        self._outer = bg or _bg_of(parent)
        font_obj = tkfont.Font(root=parent, font=font)
        super().__init__(
            parent, width=10, height=font_obj.metrics("linespace") + 2 * pady + 2,
            bg=self._outer, highlightthickness=0, bd=0, cursor="hand2",
        )
        self._on, self._command, self._radius = bool(on), command, radius
        self._hover = self._down = False
        self._img = self.create_image(0, 0, anchor="nw")
        self._label = self.create_text(0, 0, text=text, font=font, anchor="center")
        self.bind("<Configure>", lambda _e: self._paint())
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False, down=False))
        self.bind("<ButtonPress-1>", lambda _e: self._set(down=True))
        self.bind("<ButtonRelease-1>", self._release)

    def set_on(self, value: bool) -> None:
        """Flips the chip in place, without rebuilding anything around it."""
        if bool(value) != self._on:
            self._on = bool(value)
            self._paint()

    def _set(self, hover=None, down=None) -> None:
        if hover is not None:
            self._hover = hover
        if down is not None:
            self._down = down
        self._paint()

    def _release(self, event: tk.Event) -> None:
        was, self._down = self._down, False
        self._paint()
        if was and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self._command()

    def _paint(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 8 or h < 8:
            return
        on, hover, down = self._on, self._hover, self._down
        key = ("chip", w, h, on, hover, down, self._radius, self._outer, theme.ACCENT,
               theme.ACCENT_HOVER, theme.BG_INPUT, theme.LINE)

        def draw() -> charts.Raster:
            if on:
                fill = theme.ACCENT_HOVER if hover else theme.ACCENT
                if down:
                    fill = theme.darken(theme.ACCENT, 0.18)
            else:
                fill = theme.blend(theme.BG_INPUT, theme.TEXT, 0.16 if down else 0.07 if hover else 0.0)
            raster = charts.Raster(w, h, self._outer)
            r = self._radius
            raster.rrect(0, 0, w, h, fill, r, r)
            return raster

        self.itemconfigure(self._img, image=_photo(self, key, draw))
        self.coords(self._label, w / 2, h / 2 - 1)
        self.itemconfigure(
            self._label,
            fill=theme.ON_ACCENT if on else (theme.TEXT if hover else theme.MUTED),
        )
        self.tag_raise(self._label)


class Segmented(tk.Canvas):
    """A small segmented control: a soft track with the chosen option on an
    accent pill. ``command`` gets the clicked option's label."""

    def __init__(self, parent: tk.Misc, options: list[str], value: str,
                 command: Callable[[str], None], *, bg: str | None = None,
                 font: tuple = theme.FONT_SMALL, pady: int = 3) -> None:
        self._outer = bg or _bg_of(parent)
        font_obj = tkfont.Font(root=parent, font=font)
        self._seg = max(font_obj.measure(o) for o in options) + 20
        self._choices, self._value, self._command = list(options), value, command
        self._wd = self._seg * len(options) + 6
        self._ht = font_obj.metrics("linespace") + 2 * pady + 6
        self._hover: int | None = None
        super().__init__(parent, width=self._wd, height=self._ht, bg=self._outer,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._img = self.create_image(0, 0, anchor="nw")
        self._labels = [
            self.create_text(3 + self._seg * (i + 0.5), self._ht / 2 - 1, text=o, font=font)
            for i, o in enumerate(self._choices)
        ]
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self.bind("<ButtonRelease-1>", self._release)
        self._paint()

    def _index(self, x: int) -> int | None:
        i = int((x - 3) // self._seg)
        return i if 0 <= i < len(self._choices) else None

    def _motion(self, event: tk.Event) -> None:
        self._set_hover(self._index(event.x))

    def _set_hover(self, index: int | None) -> None:
        if index != self._hover:
            self._hover = index
            self._paint()

    def _release(self, event: tk.Event) -> None:
        i = self._index(event.x)
        if i is not None and 0 <= event.y < self._ht and self._choices[i] != self._value:
            self._value = self._choices[i]
            self._paint()
            self._command(self._value)

    def _paint(self) -> None:
        sel = self._choices.index(self._value) if self._value in self._choices else -1
        key = ("segmented", self._wd, self._ht, self._seg, sel, self._outer, theme.ACCENT,
               theme.BG_INPUT, theme.LINE)

        def draw() -> charts.Raster:
            raster = charts.Raster(self._wd, self._ht, self._outer)
            r = self._ht / 2
            raster.rrect(0, 0, self._wd, self._ht, theme.LINE, r, r)
            raster.rrect(1, 1, self._wd - 1, self._ht - 1, theme.BG_INPUT, r - 1, r - 1)
            if sel >= 0:
                x0 = 3 + self._seg * sel
                raster.rrect(x0, 3, x0 + self._seg, self._ht - 3, theme.ACCENT,
                             (self._ht - 6) / 2, (self._ht - 6) / 2)
            return raster

        self.itemconfigure(self._img, image=_photo(self, key, draw))
        for i, item in enumerate(self._labels):
            colour = (theme.ON_ACCENT if i == sel
                      else theme.TEXT if i == self._hover else theme.MUTED)
            self.itemconfigure(item, fill=colour)
            self.tag_raise(item)
