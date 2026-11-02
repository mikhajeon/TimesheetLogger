"""Window corner shape and border colour, through the Desktop Window Manager.

Tk can't round off a widget's corners, and clipping the window to a rounded
region draws them stepped. The collapsed badge is a window of its own though,
so Windows can round the window itself — antialiased, and matching how the
rest of the desktop looks.

Both attributes arrived with Windows 11, so every call here is best-effort and
reports whether it took. Callers fall back to Tk's own square border.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

#: DwmSetWindowAttribute attributes, from dwmapi.h.
_CORNER_PREFERENCE = 33
_BORDER_COLOUR = 34

#: DWM_WINDOW_CORNER_PREFERENCE values.
SQUARE = 1
ROUNDED = 2
ROUNDED_SMALL = 3

#: DWMWA_COLOR_NONE: draw no border at all.
NO_BORDER = 0xFFFFFFFE

#: WM_SETREDRAW, and the RedrawWindow flags that force the one repaint a
#: freeze/thaw pair defers. Win32 proper rather than DWM, but it lives here
#: anyway: everything else reaching past Tk into window-painting territory
#: already does.
_WM_SETREDRAW = 0x000B
_RDW_INVALIDATE = 0x0001
_RDW_ERASE = 0x0004
_RDW_ALLCHILDREN = 0x0080


def freeze(hwnd: int) -> None:
    """Suspends repainting until :func:`thaw` undoes it.

    A view rebuild destroys and recreates dozens of widgets in one go; left
    alone, Windows paints every intermediate state as it happens, which is
    what reads as flicker. Tk has no call for "hold off drawing until I say
    so", so this borrows the one every double-buffered Win32 app already
    uses around a big redraw. Best-effort and silent on failure, same as
    every other call here — a frame or two of flicker beats a crash.
    """
    try:
        ctypes.windll.user32.SendMessageW(wintypes.HWND(hwnd), _WM_SETREDRAW, 0, 0)
    except OSError:
        pass


def thaw(hwnd: int) -> None:
    """Resumes painting and forces the single repaint :func:`freeze` deferred."""
    try:
        user32 = ctypes.windll.user32
        user32.SendMessageW(wintypes.HWND(hwnd), _WM_SETREDRAW, 1, 0)
        user32.RedrawWindow(
            wintypes.HWND(hwnd),
            None,
            None,
            _RDW_INVALIDATE | _RDW_ERASE | _RDW_ALLCHILDREN,
        )
    except OSError:
        pass


def corners(hwnd: int, preference: int) -> bool:
    """Rounds off or squares up a window. False on Windows 10 and older."""
    return _set(hwnd, _CORNER_PREFERENCE, preference)


def border(hwnd: int, colour: str | int) -> bool:
    """Colours the hairline DWM draws around the window.

    Takes a Tk ``#rrggbb`` string, or :data:`NO_BORDER`. Unlike a Tk border
    this follows the rounded corners, so it's what the badge uses to show a
    timer running.
    """
    return _set(hwnd, _BORDER_COLOUR, colour if isinstance(colour, int) else _colorref(colour))


def _colorref(colour: str) -> int:
    """``#rrggbb`` as a Win32 COLORREF, which orders the bytes the other way."""
    red, green, blue = (int(colour[index : index + 2], 16) for index in (1, 3, 5))
    return blue << 16 | green << 8 | red


def _set(hwnd: int, attribute: int, value: int) -> bool:
    holder = ctypes.c_uint(value)
    try:
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(attribute),
            ctypes.byref(holder),
            ctypes.sizeof(holder),
        )
    except (AttributeError, OSError):  # no dwmapi.dll: not Windows
        return False
    return result == 0
