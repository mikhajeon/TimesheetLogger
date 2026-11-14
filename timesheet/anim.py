"""A tiny frame-loop animation helper — standard library only.

The overlay's expand/collapse used to be driven by Tk ``after`` timers, whose
resolution on Windows is ~15 ms and whose every tick re-lays-out widgets.
This is the replacement building block: a blocking loop that eases a value
from 0 to 1 over a duration and paces each frame to the display's refresh
(``DwmFlush``), so frames land on vsync instead of on a coarse timer.

Kept free of any Tk/GDI knowledge on purpose; ``timesheet.winfx`` supplies
what actually gets drawn each frame.
"""

from __future__ import annotations

import ctypes
import time
from typing import Callable


def smoothstep(t: float) -> float:
    """Gentle at both ends."""
    return t * t * (3 - 2 * t)


def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_rect(src: tuple, dst: tuple, t: float) -> tuple[int, int, int, int]:
    return tuple(round(lerp(a, b, t)) for a, b in zip(src, dst))  # type: ignore[return-value]


def _vsync() -> None:
    try:
        ctypes.windll.dwmapi.DwmFlush()
    except (OSError, AttributeError):
        time.sleep(0.008)


def run(
    duration_ms: float,
    on_frame: Callable[[float], None],
    easing: Callable[[float], float] = smoothstep,
) -> int:
    """Calls ``on_frame(eased_t)`` once per display frame until the duration
    is up, always finishing on exactly ``on_frame(1.0)``. Returns the number
    of frames drawn."""
    start = time.perf_counter()
    frames = 0
    while True:
        t = (time.perf_counter() - start) * 1000 / duration_ms
        if t >= 1:
            on_frame(1.0)
            return frames + 1
        on_frame(easing(t))
        frames += 1
        _vsync()


def run_rect(
    src: tuple,
    dst: tuple,
    duration_ms: float,
    put: Callable[[int, int, int, int], None],
    easing: Callable[[float], float] = smoothstep,
    abort: Callable[[], bool] | None = None,
) -> tuple[int, int, int, int]:
    """Eases a window rectangle ``(x, y, w, h)`` from ``src`` to ``dst``,
    calling ``put(x, y, w, h)`` once per display frame. Returns the rectangle
    it stopped at: ``dst``, or wherever it was when ``abort()`` turned true.

    Position and size are tweened together so a window anchored to a screen
    corner grows out of that corner instead of sliding as it resizes.
    """
    current = tuple(src)

    def frame(t: float) -> None:
        nonlocal current
        current = lerp_rect(src, dst, t)
        put(*current)

    start = time.perf_counter()
    while True:
        if abort is not None and abort():
            return current  # type: ignore[return-value]
        t = (time.perf_counter() - start) * 1000 / duration_ms
        if t >= 1:
            frame(1.0)
            return tuple(dst)  # type: ignore[return-value]
        frame(easing(t))
        _vsync()
