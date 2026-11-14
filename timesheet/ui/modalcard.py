"""The dialog card of a modal, as one reusable component.

``ModalCard`` is the rounded panel the dialog's widgets are built into, *and*
the soft drop shadow behind it, kept together: whatever sizes or moves the
card does it through ``place_rect`` / ``centre`` / ``animate_to``, and the
shadow is redrawn for exactly that rectangle every time, so it can never lag
or be left behind at an old size.

The widgets inside are never moved while the card changes: ``hide_contents``
simply takes them off the card for a moment and ``show_contents`` puts them
back, so only the background card (and its shadow) moves.
"""

from __future__ import annotations

import math
import tkinter as tk
from typing import Callable

from .. import anim
from . import charts, modern, theme

MARGIN = 8  # how far the shadow reaches past the card's edge
_CAST = 2  # the shadow is cast a touch downward


def crop_clamped(buf, bw: int, bh: int, x: int, y: int, w: int, h: int) -> bytes:
    """The ``w`` x ``h`` rectangle at (``x``, ``y``) of an RGB picture
    ``bw`` x ``bh``. Where the rectangle reaches past the picture, the
    nearest edge pixel is repeated, so a request is never refused (a refusal
    meant a flat stand-in colour, which showed as a box behind the modal)."""
    rows = []
    left, right = max(0, -x), max(0, x + w - bw)
    for row in range(h):
        yy = min(max(y + row, 0), bh - 1)
        base = yy * bw
        a, b = max(x, 0), min(x + w, bw)
        if b <= a:
            px = min(max(x, 0), bw - 1)
            rows.append(bytes(buf[(base + px) * 3:(base + px) * 3 + 3]) * w)
            continue
        parts = []
        if left:
            parts.append(bytes(buf[(base + a) * 3:(base + a) * 3 + 3]) * left)
        parts.append(bytes(buf[(base + a) * 3:(base + b) * 3]))
        if right:
            parts.append(bytes(buf[(base + b - 1) * 3:(base + b) * 3]) * right)
        rows.append(b"".join(parts))
    return b"".join(rows)


class _Falloff:
    """The shadow's darkening, as a factor per pixel, for one corner radius.
    Everything that does not depend on the card's size — the four corner
    patches, and the profile across a straight edge — is worked out once."""

    def __init__(self, radius: int, margin: int) -> None:
        self.radius, self.margin = radius, margin
        self.pad = margin + 4  # the picture reaches a little past the falloff
        self.edge = self.pad + radius + 1  # side of a corner patch
        self._tables: dict[int, bytes] = {}
        pad, edge = self.pad, self.edge
        far = 1e6
        # Top corners, rows 0..edge, columns 0..edge (left one; mirrored).
        self.top_left = [
            [
                self.k(pad + radius - (i + 0.5), pad + _CAST + radius - (j + 0.5),
                       pad + radius - (j + 0.5))
                for i in range(edge)
            ]
            for j in range(edge)
        ]
        # Bottom corners: row j counted from the top of the bottom patch.
        self.bottom_left = [
            [self.k(pad + radius - (i + 0.5), j - 0.5 - _CAST, j - 0.5) for i in range(edge)]
            for j in range(edge)
        ]
        # Straight stretches: across a top / bottom edge, and a side.
        self.top_rows = [
            self.k(-far, pad + _CAST + radius - (j + 0.5), pad + radius - (j + 0.5))
            for j in range(edge)
        ]
        self.bottom_rows = [self.k(-far, j - 0.5 - _CAST, j - 0.5) for j in range(edge)]
        self.side_cols = [
            self.k(pad + radius - (i + 0.5), -far, -far) for i in range(pad)
        ]

    def k(self, dx: float, dy: float, dy0: float) -> float | None:
        """The factor at a pixel whose offsets from the card's rounded
        shape are ``dx`` / ``dy`` (``dy0``: measured without the downward
        cast, to know where the card itself is); ``None`` under the card."""
        radius, margin = self.radius, self.margin
        d0 = math.hypot(max(dx, 0), max(dy0, 0)) + min(max(dx, dy0), 0) - radius
        if d0 <= 0:
            return None
        d = max(math.hypot(max(dx, 0), max(dy, 0)) + min(max(dx, dy), 0) - radius, 0.0)
        if d >= margin:
            return 1.0
        return 1 - 0.6 * (1 - d / margin) ** 2

    def table(self, k: float) -> bytes:
        key = round(k * 400)
        table = self._tables.get(key)
        if table is None:
            factor = key / 400
            table = self._tables[key] = bytes(int(v * factor) for v in range(256))
        return table


_FALLOFFS: dict[tuple[int, int], _Falloff] = {}


def shade(base: bytes, w: int, h: int, radius: int, margin: int = MARGIN) -> tuple[bytearray, int, int, int]:
    """``base`` is the picture behind a ``w`` x ``h`` card, ``pad`` pixels
    bigger on every side (``W`` x ``H``); returns it with the card's soft
    shadow darkened into it, and ``(W, H, pad)``. Done mostly in whole rows
    and columns at C speed — only the four corners are per pixel."""
    fall = _FALLOFFS.get((radius, margin))
    if fall is None:
        fall = _FALLOFFS[(radius, margin)] = _Falloff(radius, margin)
    pad, edge = fall.pad, fall.edge
    W, H = w + 2 * pad, h + 2 * pad
    buf = bytearray(base)
    if W < 2 * edge + 2 or H < 2 * edge + 2:
        _shade_small(buf, W, H, w, h, fall)
        return buf, W, H, pad
    stride = W * 3

    # Rows above and below the card (straight part, between the corners).
    for j in range(edge):
        k = fall.top_rows[j]
        if k is not None and k < 1.0:
            o = j * stride
            buf[o + edge * 3:o + (W - edge) * 3] = bytes(
                buf[o + edge * 3:o + (W - edge) * 3]
            ).translate(fall.table(k))
        k = fall.bottom_rows[j]
        if k is not None and k < 1.0:
            o = (H - edge + j) * stride
            buf[o + edge * 3:o + (W - edge) * 3] = bytes(
                buf[o + edge * 3:o + (W - edge) * 3]
            ).translate(fall.table(k))

    # Columns left and right of the card, between the corner patches.
    rows = slice(edge * stride, (H - edge) * stride, stride)
    for i in range(pad):
        k = fall.side_cols[i]
        if k is None or k >= 1.0:
            continue
        table = fall.table(k)
        for column in (i, W - 1 - i):
            for ch in range(3):
                start = edge * stride + column * 3 + ch
                stop = (H - edge) * stride
                buf[start:stop:stride] = bytes(buf[start:stop:stride]).translate(table)

    # The four corner patches, a pixel at a time.
    for j in range(edge):
        for i in range(edge):
            for (grid, py, px) in (
                (fall.top_left, j, i), (fall.top_left, j, W - 1 - i),
                (fall.bottom_left, H - edge + j, i), (fall.bottom_left, H - edge + j, W - 1 - i),
            ):
                k = grid[j][i]
                if k is None or k >= 1.0:
                    continue
                o = (py * W + px) * 3
                buf[o:o + 3] = bytes(buf[o:o + 3]).translate(fall.table(k))
    return buf, W, H, pad


def _shade_small(buf: bytearray, W: int, H: int, w: int, h: int, fall: _Falloff) -> None:
    """The same shadow computed pixel by pixel, for a card too small for the
    fast row/column method (when it is folded right down)."""
    pad, radius = fall.pad, fall.radius
    half_w, half_h = w / 2 - radius, h / 2 - radius
    cx, cy = W / 2, H / 2 + _CAST
    for py in range(H):
        dy = abs(py + 0.5 - cy) - half_h
        dy0 = abs(py + 0.5 - H / 2) - half_h
        for px in range(W):
            dx = abs(px + 0.5 - cx) - half_w
            k = fall.k(dx, dy, dy0)
            if k is None or k >= 1.0:
                continue
            o = (py * W + px) * 3
            buf[o:o + 3] = bytes(buf[o:o + 3]).translate(fall.table(k))


class ModalCard:
    """The panel and its shadow. Build the dialog's widgets into ``.body``."""

    def __init__(
        self, scrim: tk.Misc, *, backdrop, dim: str, radius: int,
        fill: str | None = None, border: str | None = None,
    ) -> None:
        self.scrim = scrim
        self.backdrop = backdrop
        self.dim = dim
        self.radius = radius
        self.surface = modern.Surface(
            scrim, fill=fill or theme.BG_RAISED, border=border or theme.LINE, radius=radius,
            outer=dim, backdrop=backdrop,
        )
        self.body = self.surface.body
        self.shadow: tk.Label | None = None
        self._relwidth: float | None = None

    # -- where it is ----------------------------------------------------------

    def rect(self) -> tuple[int, int, int, int]:
        s = self.surface
        return s.winfo_x(), s.winfo_y(), s.winfo_width(), s.winfo_height()

    def centre(self, relwidth: float = 0.92) -> None:
        """Centred in the scrim at ``relwidth`` of its width and its own natural
        height; the shadow is redrawn for wherever that puts it."""
        self._relwidth = relwidth
        s = self.surface
        s.place(relx=0.5, rely=0.5, x=0, y=0, anchor="center", relwidth=relwidth,
                width="", height="")
        s.update_idletasks()
        self.restyle()

    def centred_rect(self, height: int) -> tuple[int, int, int, int]:
        """Where a centred card of ``height`` would sit, as a rectangle."""
        total_w, total_h = self.scrim.winfo_width(), self.scrim.winfo_height()
        w = round(total_w * (self._relwidth or 0.92))
        return round((total_w - w) / 2), max(0, round((total_h - height) / 2)), w, height

    def place_rect(self, x: int, y: int, w: int, h: int) -> None:
        """The card at exactly this rectangle, its shadow redrawn to match
        before it is shown there."""
        w, h = max(w, 24), max(h, 24)
        self._draw_shadow(x, y, w, h)
        s = self.surface
        s.place(relx=0, rely=0, x=x, y=y, anchor="nw", relwidth="", width=w, height=h)
        s.update_idletasks()
        s._paint()
        tk.Misc.lift(s)

    def restyle(self) -> None:
        """Redraws the shadow (and the card's edge against it) for where the
        card is right now."""
        x, y, w, h = self.rect()
        if w < 20 or h < 20:
            return
        self._draw_shadow(x, y, w, h)
        self.surface._paint()
        tk.Misc.lift(self.surface)

    def animate_to(self, target: tuple[int, int, int, int], ms: int,
                   start: tuple[int, int, int, int] | None = None,
                   after_frame: Callable[[], None] | None = None) -> None:
        """Moves the card, shadow and all, from where it is to ``target``."""
        src = start or self.rect()

        def frame(t: float) -> None:
            self.place_rect(*anim.lerp_rect(src, target, anim.smoothstep(t)))
            if after_frame is not None:
                after_frame()

        anim.run(ms, frame, easing=lambda t: t)

    # -- its contents ---------------------------------------------------------

    def hide_contents(self) -> None:
        """Takes the dialog's widgets off the card for now. They are not moved
        or touched, just not shown."""
        s = self.surface
        s.itemconfigure(s._win, state="hidden")

    def show_contents(self) -> None:
        s = self.surface
        s.itemconfigure(s._win, state="normal")
        s._fit()  # a hidden body sent no <Configure>: catch the card's height up

    def natural_height(self) -> int:
        """The height the card wants for what is in it now (read off the body,
        which is right even while the contents are hidden)."""
        s = self.surface
        return max(s.body.winfo_reqheight() + 2 * s._inset, 60)

    # -- the shadow -----------------------------------------------------------

    def _draw_shadow(self, x: int, y: int, w: int, h: int) -> None:
        pad = MARGIN + 4
        W, H = w + 2 * pad, h + 2 * pad
        base = self.backdrop(x - pad, y - pad, W, H) if self.backdrop else None
        if base is None or len(base) != W * H * 3:
            r16, g16, b16 = self.scrim.winfo_rgb(self.dim)
            base = bytes((r16 >> 8, g16 >> 8, b16 >> 8)) * (W * H)
        try:
            buf, W, H, pad = shade(base, w, h, self.radius)
            raster = charts.Raster(W, H, "#000000")
            raster.buf = buf
            image = raster.photo(self.scrim)
        except (TypeError, ValueError, tk.TclError):
            return
        label = self.shadow
        if label is None or not label.winfo_exists():
            label = self.shadow = tk.Label(self.scrim, bd=0)
            label.bind("<Button-1>", lambda _e: "break")
        label.configure(image=image)
        label.image = image
        label.place(x=x - pad, y=y - pad)

        def shadowed(rx: int, ry: int, rw: int, rh: int) -> bytes | None:
            return crop_clamped(buf, W, H, rx - (x - pad), ry - (y - pad), rw, rh)

        shadowed.token = ("shadow", x, y, w, h)  # type: ignore[attr-defined]
        self.surface._backdrop = shadowed
        tk.Misc.lift(self.surface)

    def destroy(self) -> None:
        for widget in (self.shadow, self.surface):
            if widget is not None:
                try:
                    widget.destroy()
                except tk.TclError:
                    pass
