"""Small charts for the Month tab's quarter and year views, drawn with the
standard library only: a stacked histogram of tracked hours with the
utilization trend laid over it, a one-line utilization gauge, and the
rows-with-meters table that sits under the histogram.

Tk's canvas draws shapes with hard, un-smoothed edges, which shows on thin
lines and small circles. So the graphics here (bars, lines, dots, meters)
are rendered by :class:`Raster` — a tiny anti-aliased software renderer
that works out how much of each pixel a shape covers, the way a vector
renderer does — and shown as an image; only the text is left to the canvas,
which smooths it already."""

from __future__ import annotations

import base64
import math
import struct
import tkinter as tk
import zlib
from dataclasses import dataclass
from typing import Callable

from . import theme


def _rgb(colour: str) -> tuple[int, int, int]:
    colour = colour.lstrip("#")
    return int(colour[0:2], 16), int(colour[2:4], 16), int(colour[4:6], 16)


class Raster:
    """An RGB pixel buffer with anti-aliased shapes: each pixel at a shape's
    edge is blended in proportion to how much of it the shape covers."""

    def __init__(self, width: int, height: int, bg: str) -> None:
        self.w, self.h = max(1, width), max(1, height)
        self.buf = bytearray(bytes(_rgb(bg)) * (self.w * self.h))

    # -- pixels -------------------------------------------------------------

    def _blend(self, x: int, y: int, rgb: tuple, cov: float) -> None:
        if cov <= 0.0 or not (0 <= x < self.w and 0 <= y < self.h):
            return
        i = (y * self.w + x) * 3
        buf = self.buf
        if cov >= 0.999:
            buf[i], buf[i + 1], buf[i + 2] = rgb
        else:
            inv = 1.0 - cov
            buf[i] = round(buf[i] * inv + rgb[0] * cov)
            buf[i + 1] = round(buf[i + 1] * inv + rgb[1] * cov)
            buf[i + 2] = round(buf[i + 2] * inv + rgb[2] * cov)

    def _row(self, y: int, x_start: int, cover: list, rgb: tuple) -> None:
        for offset, cov in enumerate(cover):
            if cov > 0.0:
                self._blend(x_start + offset, y, rgb, cov)

    def _span(self, y: int, xa: float, xb: float, weight: float, rgb: tuple) -> None:
        """One pixel row from ``xa`` to ``xb`` at ``weight`` vertical coverage;
        the run between the fractional ends is filled in one go."""
        if not (0 <= y < self.h) or xb <= xa:
            return
        xa, xb = max(xa, 0.0), min(xb, float(self.w))
        first, last = int(math.floor(xa)), int(math.ceil(xb))
        if last <= first:
            return
        if last - first == 1:
            self._blend(first, y, rgb, (xb - xa) * weight)
            return
        self._blend(first, y, rgb, (first + 1 - xa) * weight)
        self._blend(last - 1, y, rgb, (xb - (last - 1)) * weight)
        if first + 1 < last - 1:
            if weight >= 0.999:
                i, j = (y * self.w + first + 1) * 3, (y * self.w + last - 1) * 3
                self.buf[i:j] = bytes(rgb) * (last - 1 - first - 1)
            else:
                for x in range(first + 1, last - 1):
                    self._blend(x, y, rgb, weight)

    # -- shapes ---------------------------------------------------------------

    def rrect(
        self, x0: float, y0: float, x1: float, y1: float, colour: str,
        top: float = 0.0, bottom: float = 0.0,
    ) -> None:
        """A rectangle with its top corners rounded to ``top`` and its bottom
        corners to ``bottom`` (pixels). Edges are exact horizontally and
        sampled four times per pixel vertically, so curves come out smooth."""
        rgb = _rgb(colour)
        top = min(top, (y1 - y0) / 2, (x1 - x0) / 2)
        bottom = min(bottom, (y1 - y0) / 2, (x1 - x0) / 2)
        for y in range(max(0, int(math.floor(y0))), min(self.h, int(math.ceil(y1)))):
            weight = min(y + 1, y1) - max(y, y0)
            if weight <= 0:
                continue
            if top <= 0 and bottom <= 0 or (y0 + top <= y and y + 1 <= y1 - bottom):
                self._span(y, x0, x1, weight, rgb)  # straight sides
                continue
            first, last = int(math.floor(x0)), int(math.ceil(x1))
            # Only the two ends of a corner row are curved: work out coverage
            # in a window at each end, and fill what lies between in one go.
            reach = int(math.ceil(max(top, bottom))) + 2
            split = last - first > 2 * reach + 2
            if split:
                left, right = [0.0] * reach, [0.0] * reach
            else:
                cover = [0.0] * max(0, last - first)
            middle = 0.0
            for sub in range(4):
                ys = y + (sub + 0.5) / 4
                if not y0 <= ys <= y1:
                    continue
                inset = 0.0
                if ys < y0 + top:
                    inset = top - math.sqrt(max(top * top - (ys - (y0 + top)) ** 2, 0.0))
                elif ys > y1 - bottom:
                    inset = bottom - math.sqrt(max(bottom * bottom - (ys - (y1 - bottom)) ** 2, 0.0))
                if not split:
                    self._accumulate(cover, first, x0 + inset, x1 - inset, 0.25)
                    continue
                xa, xb = x0 + inset, x1 - inset
                middle += 0.25
                self._accumulate(left, first, xa, min(xb, first + reach), 0.25)
                self._accumulate(right, last - reach, max(xa, last - reach), xb, 0.25)
            if split:
                self._row(y, first, left, rgb)
                self._span(y, first + reach, last - reach, middle, rgb)
                self._row(y, last - reach, right, rgb)
            else:
                self._row(y, first, cover, rgb)

    @staticmethod
    def _accumulate(cover: list, base: int, xa: float, xb: float, weight: float) -> None:
        if xb <= xa:
            return
        a, b = int(math.floor(xa)), int(math.ceil(xb))
        if b - a == 1:
            cover[a - base] += (xb - xa) * weight
            return
        cover[a - base] += (a + 1 - xa) * weight
        cover[b - 1 - base] += (xb - (b - 1)) * weight
        for k in range(a + 1 - base, b - 1 - base):
            cover[k] += weight

    def line(self, points: list[tuple[float, float]], width: float, colour: str) -> None:
        """A polyline of ``width`` pixels, anti-aliased across its length."""
        rgb = _rgb(colour)
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            self._segment(x0, y0, x1, y1, width, rgb)

    def _segment(self, x0, y0, x1, y1, width, rgb) -> None:
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length == 0:
            return
        half = width / 2
        horizontal = abs(dx) >= abs(dy)
        # Walk the major axis a pixel at a time, covering only the few
        # pixels either side of the line on the other axis.
        reach = (half + 1.0) * length / (abs(dx) if horizontal else abs(dy))
        lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
        if not horizontal:
            lo, hi = (y0, y1) if y0 <= y1 else (y1, y0)
        for step in range(int(math.floor(lo)), int(math.ceil(hi))):
            major = step + 0.5
            if horizontal:
                t = (major - x0) / dx
                centre = y0 + t * dy
            else:
                t = (major - y0) / dy
                centre = x0 + t * dx
            for minor in range(int(math.floor(centre - reach)), int(math.ceil(centre + reach)) + 1):
                px, py = (major, minor + 0.5) if horizontal else (minor + 0.5, major)
                distance = abs((px - x0) * dy - (py - y0) * dx) / length
                cov = min(1.0, half + 0.5 - distance)
                if cov > 0:
                    if horizontal:
                        self._blend(step, minor, rgb, cov)
                    else:
                        self._blend(minor, step, rgb, cov)

    def disc(self, cx: float, cy: float, radius: float, colour: str) -> None:
        rgb = _rgb(colour)
        for y in range(int(math.floor(cy - radius - 1)), int(math.ceil(cy + radius + 1))):
            for x in range(int(math.floor(cx - radius - 1)), int(math.ceil(cx + radius + 1))):
                distance = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
                self._blend(x, y, rgb, min(1.0, radius + 0.5 - distance))

    def ring(self, cx: float, cy: float, radius: float, stroke: float, edge: str, fill: str) -> None:
        """A dot with an outline: ``edge`` out to ``radius``, ``fill`` inside."""
        self.disc(cx, cy, radius, edge)
        self.disc(cx, cy, radius - stroke, fill)

    # -- output ---------------------------------------------------------------

    def photo(self, master: tk.Misc) -> tk.PhotoImage:
        stride = self.w * 3
        rows = b"".join(
            b"\x00" + bytes(self.buf[y * stride:(y + 1) * stride]) for y in range(self.h)
        )

        def chunk(kind: bytes, data: bytes) -> bytes:
            body = kind + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 1))
            + chunk(b"IEND", b"")
        )
        return tk.PhotoImage(master=master, data=base64.b64encode(png))


@dataclass
class Bucket:
    """One bar: a week of a quarter, or a month of a year."""

    label: str
    total: int       # minutes tracked
    billable: int    # of which billable
    key: str         # what a click on it should jump to (an ISO date)
    current: bool = False  # the bucket containing today

    @property
    def utilization(self) -> float | None:
        return self.billable / self.total if self.total else None


def percent(billable: float, total: float) -> str:
    """``75%``, or a dash with nothing tracked."""
    return f"{round(100 * billable / total)}%" if total > 0 else "\u2013"


def _nice_ceiling(value: float) -> float:
    """The next 1/2/5 x 10^k at or above ``value`` (hours), for an axis top."""
    if value <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def gauge(parent: tk.Misc, fraction: float | None, bg: str | None = None) -> tk.Canvas:
    """A slim full-width meter, filled to ``fraction`` (billable share)."""
    bg = bg or theme.BG
    canvas = tk.Canvas(parent, height=8, bg=bg, highlightthickness=0, bd=0)
    drawn = {"width": 0}

    def draw(_event=None) -> None:
        width = canvas.winfo_width()
        if width < 2 or width == drawn["width"]:
            return
        drawn["width"] = width
        raster = Raster(width, 8, bg)
        raster.rrect(0, 0, width, 8, theme.BG_INPUT, 4, 4)
        if fraction:
            raster.rrect(0, 0, max(8.0, width * min(fraction, 1.0)), 8, theme.BILLABLE, 4, 4)
        canvas.delete("all")
        canvas._image = raster.photo(canvas)  # type: ignore[attr-defined]
        canvas.create_image(0, 0, anchor="nw", image=canvas._image)  # type: ignore[attr-defined]

    canvas.bind("<Configure>", draw)
    return canvas


class Histogram(tk.Canvas):
    """Stacked bars (billable under non-billable, in hours) with the
    utilization percentage drawn as a line over them on its own 0-100%
    scale. Clicking a bar calls ``on_click(bucket.key)``."""

    HEIGHT = 200
    LEFT, RIGHT, TOP, BOTTOM = 40, 34, 18, 24

    def __init__(
        self, parent: tk.Misc, buckets: list[Bucket], on_click: Callable[[str], None],
    ) -> None:
        super().__init__(
            parent, height=self.HEIGHT, bg=theme.BG, highlightthickness=0, bd=0,
        )
        self._buckets = buckets
        self._on_click = on_click
        self._drawn_width = 0
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event: tk.Event) -> None:
        if event.width != self._drawn_width:
            self._drawn_width = event.width
            self._draw()

    def _draw(self) -> None:
        self.delete("all")
        buckets = self._buckets
        width, height = self.winfo_width(), self.HEIGHT
        if width < 80 or not buckets:
            return
        left, right = self.LEFT, width - self.RIGHT
        top, bottom = self.TOP, height - self.BOTTOM
        plot_h = bottom - top
        slot = (right - left) / len(buckets)
        axis_hours = _nice_ceiling(max(b.total for b in buckets) / 60)
        font = theme.FONT_TINY
        raster = Raster(width, height, theme.BG)

        # Axis lines first: the baseline solid, the half and full marks dashed.
        for step in range(3):
            y = bottom - plot_h * step / 2
            if step == 0:
                raster.rrect(left, y - 0.5, right, y + 0.5, theme.LINE)
            else:
                x = left
                while x < right:
                    raster.rrect(x, y - 0.5, min(x + 2, right), y + 0.5, theme.LINE)
                    x += 5

        points: list[tuple[float, float] | None] = []
        geometry = []
        for index, bucket in enumerate(buckets):
            cx = left + slot * (index + 0.5)
            bar_w = max(4.0, slot * 0.58)
            x0, x1 = cx - bar_w / 2, cx + bar_w / 2
            billable_h = plot_h * (bucket.billable / 60) / axis_hours
            total_h = plot_h * (bucket.total / 60) / axis_hours
            radius = min(3.0, bar_w / 2)
            if bucket.total:
                if total_h > billable_h:
                    raster.rrect(x0, bottom - total_h, x1, bottom, theme.IDLE, top=radius)
                if billable_h > 0:
                    cap = radius if total_h <= billable_h + 0.01 else 0.0
                    raster.rrect(x0, bottom - billable_h, x1, bottom, theme.BILLABLE, top=cap)
            util = bucket.utilization
            points.append(None if util is None else (cx, bottom - plot_h * util))
            geometry.append((cx, index))

        # The trend: a line through consecutive weeks/months that have time,
        # broken where a bucket is empty rather than dropping to zero.
        run: list[tuple[float, float]] = []
        for point in points + [None]:
            if point is not None:
                run.append(point)
                continue
            if len(run) > 1:
                raster.line(run, 2.0, theme.ACCENT)
            run = []
        for point in points:
            if point is not None:
                raster.ring(point[0], point[1], 3.6, 1.8, theme.ACCENT, theme.BG)

        self._image = raster.photo(self)
        self.create_image(0, 0, anchor="nw", image=self._image)

        # Text, hit areas and cursor handling stay on the canvas.
        label_every = 1 if slot >= 26 else 2
        for step in range(3):
            fraction = step / 2
            y = bottom - plot_h * fraction
            self.create_text(
                left - 6, y, text=f"{axis_hours * fraction:g}h", anchor="e",
                font=font, fill=theme.MUTED,
            )
            self.create_text(
                right + 6, y, text=f"{round(100 * fraction)}%", anchor="w",
                font=font, fill=theme.MUTED,
            )
        for (cx, index), bucket, point in zip(geometry, buckets, points):
            hit = self.create_rectangle(
                left + slot * index, top, left + slot * (index + 1), height,
                fill="", outline="",
            )
            self.tag_bind(hit, "<Button-1>", lambda _e, k=bucket.key: self._on_click(k))
            self.tag_bind(hit, "<Enter>", lambda _e: self.config(cursor="hand2"))
            self.tag_bind(hit, "<Leave>", lambda _e: self.config(cursor=""))
            if index % label_every == 0:
                self.create_text(
                    cx, bottom + 12, text=bucket.label, font=font,
                    fill=theme.ACCENT if bucket.current else theme.MUTED,
                )
            if point is not None and slot >= 30:
                self.create_text(
                    point[0], point[1] - 11, text=percent(bucket.billable, bucket.total),
                    font=font, fill=theme.ACCENT,
                )


def legend(parent: tk.Misc) -> tk.Frame:
    """Key for the histogram: billable, non-billable, utilization."""
    row = theme.frame(parent)
    for colour, text in (
        (theme.BILLABLE, "Billable"), (theme.IDLE, "Non-billable"), (theme.ACCENT, "Utilization %"),
    ):
        raster = Raster(10, 10, theme.BG)
        raster.disc(5, 5, 4.2, colour)
        swatch = tk.Label(row, bg=theme.BG, bd=0)
        swatch._image = raster.photo(swatch)  # type: ignore[attr-defined]
        swatch.config(image=swatch._image)  # type: ignore[attr-defined]
        swatch.pack(side="left", padx=(0, 3))
        theme.label(row, text, fg=theme.MUTED, font=theme.FONT_TINY).pack(
            side="left", padx=(0, 10)
        )
    return row


def meter_cell(parent: tk.Misc, fraction: float | None, bg: str, width: int = 90) -> tk.Canvas:
    """A short in-row meter with the percentage written beside it."""
    canvas = tk.Canvas(parent, width=width + 34, height=16, bg=bg, highlightthickness=0, bd=0)
    raster = Raster(width, 16, bg)
    raster.rrect(0, 5, width, 11, theme.BG_INPUT, 3, 3)
    if fraction:
        raster.rrect(0, 5, max(6.0, width * min(fraction, 1.0)), 11, theme.BILLABLE, 3, 3)
    canvas._image = raster.photo(canvas)  # type: ignore[attr-defined]
    canvas.create_image(0, 0, anchor="nw", image=canvas._image)  # type: ignore[attr-defined]
    canvas.create_text(
        width + 6, 8, anchor="w", font=theme.FONT_TINY,
        fill=theme.TEXT if fraction is not None else theme.MUTED,
        text="\u2013" if fraction is None else f"{round(100 * fraction)}%",
    )
    return canvas


#: Fixed mid-tones that read on both the light and the dark theme.
SHARE_COLOURS = ("#4f8ef7", "#34c38f", "#f6a93b", "#b57bee", "#ef6f6c", "#5cc8d7")


def _bar_canvas(parent: tk.Misc, height: int, paint) -> tk.Canvas:
    """A full-width AA image strip, repainted when the width changes."""
    canvas = tk.Canvas(parent, height=height, bg=theme.BG, highlightthickness=0, bd=0)
    drawn = {"width": 0}

    def draw(_event=None) -> None:
        width = canvas.winfo_width()
        if width < 2 or width == drawn["width"]:
            return
        drawn["width"] = width
        raster = Raster(width, height, theme.BG)
        paint(raster, width)
        canvas.delete("all")
        canvas._image = raster.photo(canvas)  # type: ignore[attr-defined]
        canvas.create_image(0, 0, anchor="nw", image=canvas._image)  # type: ignore[attr-defined]

    canvas.bind("<Configure>", draw)
    return canvas


def pace_bar(parent: tk.Misc, tracked: float, expected_so_far: float, expected_total: float) -> tk.Canvas:
    """Hours tracked against what the period calls for. The fill is what is
    tracked; the tick is where it would be by now if on pace. The fill is
    green when at or past the tick, amber when behind."""
    span = max(expected_total, tracked, 1.0)

    def paint(raster: Raster, width: int) -> None:
        raster.rrect(0, 2, width, 10, theme.BG_INPUT, 4, 4)
        if tracked > 0:
            behind = expected_so_far > 0 and tracked < expected_so_far
            raster.rrect(
                0, 2, max(8.0, width * tracked / span), 10,
                theme.PLANNED if behind else theme.BILLABLE, 4, 4,
            )
        if expected_so_far > 0:
            x = min(width - 2.0, width * expected_so_far / span)
            raster.rrect(x - 1, 0, x + 1, 12, theme.TEXT, 0, 0)

    return _bar_canvas(parent, 12, paint)


def share_bar(parent: tk.Misc, parts: list[tuple[str, float]]) -> tk.Canvas:
    """One stacked bar: ``(colour, amount)`` parts in order, left to right."""
    total = sum(amount for _c, amount in parts) or 1.0

    def paint(raster: Raster, width: int) -> None:
        x = 0.0
        for index, (colour, amount) in enumerate(parts):
            end = width if index == len(parts) - 1 else x + width * amount / total
            raster.rrect(x, 0, max(x + 3, end - 1.5), 10, colour, 3, 3)
            x = end

    return _bar_canvas(parent, 10, paint)



def swatch(parent: tk.Misc, colour: str, bg: str | None = None) -> tk.Label:
    """A small anti-aliased dot, for keys."""
    bg = bg or theme.BG
    raster = Raster(10, 10, bg)
    raster.disc(5, 5, 4.2, colour)
    label = tk.Label(parent, bg=bg, bd=0)
    label._image = raster.photo(label)  # type: ignore[attr-defined]
    label.config(image=label._image)  # type: ignore[attr-defined]
    return label
