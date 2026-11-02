"""Screen geometry, so the overlay can sit in a corner of any monitor.

Tk only ever describes the primary screen: ``winfo_screenwidth`` can't see a
second monitor, and nothing in Tk reports where one sits relative to another.
Win32 ``EnumDisplayMonitors`` does, and it hands back each screen's *work
area* — the usable rectangle with the taskbar already subtracted — which is
exactly the box a corner should be measured from.

Deliberately free of any tkinter import, so the placement maths can be tested
without a desktop session.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

#: Gap left between the overlay and the screen edge when it is snapped. Flush
#: against the edge, at whatever size the window currently is — a corner is
#: for docking the window out of the way, not for floating it a few pixels
#: off the edge it's meant to be pinned to.
MARGIN = 0

#: The smallest patch of window that has to stay on a screen for the header to
#: still be grabbable. Used to reject a saved position on an unplugged monitor.
GRAB_WIDTH = 90
GRAB_HEIGHT = 30

#: Menu label -> stored value. "" (free placement) is deliberately not here.
CORNERS = {
    "Top left": "top-left",
    "Top right": "top-right",
    "Bottom left": "bottom-left",
    "Bottom right": "bottom-right",
}
FREE_LABEL = "Free (drag anywhere)"

MONITORINFOF_PRIMARY = 0x1

user32 = ctypes.windll.user32


@dataclass(frozen=True)
class Monitor:
    """One screen's work area, in virtual-desktop coordinates.

    ``x`` and ``y`` are offsets from the primary screen's top-left corner, and
    go negative for a monitor placed to the left of, or above, the primary one.
    """

    #: Win32 device name, e.g. ``\\\\.\\DISPLAY2``. Stable enough to remember a
    #: choice across a reconnect, and empty for the Tk fallback screen.
    name: str
    x: int
    y: int
    width: int
    height: int
    primary: bool = False

    def label(self, index: int) -> str:
        """Menu text. The index keeps two identical screens apart."""
        return f"Screen {index} — {self.width}×{self.height}" + (
            " · primary" if self.primary else ""
        )


class _MonitorInfoExW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


_ENUM_PROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMONITOR,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)

# Spelled out so ctypes doesn't truncate the 64-bit monitor handle.
user32.EnumDisplayMonitors.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    _ENUM_PROC,
    wintypes.LPARAM,
]
user32.EnumDisplayMonitors.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
user32.GetMonitorInfoW.restype = wintypes.BOOL


def detect() -> list[Monitor]:
    """Every connected screen, primary first. Empty if Windows won't say."""
    found: list[Monitor] = []

    def collect(handle, _hdc, _rect, _param) -> int:
        info = _MonitorInfoExW()
        info.cbSize = ctypes.sizeof(_MonitorInfoExW)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            work = info.rcWork
            found.append(
                Monitor(
                    name=info.szDevice,
                    x=work.left,
                    y=work.top,
                    width=work.right - work.left,
                    height=work.bottom - work.top,
                    primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                )
            )
        return 1  # keep enumerating

    try:
        user32.EnumDisplayMonitors(None, None, _ENUM_PROC(collect), 0)
    except OSError:
        return []

    found.sort(key=lambda m: (not m.primary, m.x, m.y))
    return found


def find(screens: list[Monitor], name: str) -> Monitor:
    """The remembered screen, falling back to the primary one once unplugged."""
    return next((s for s in screens if s.name == name), screens[0])


def corner_position(
    screen: Monitor, corner: str, width: int, height: int, margin: int = MARGIN
) -> tuple[int, int]:
    """Top-left coordinate that puts a ``width`` x ``height`` window in a corner."""
    left, top = screen.x + margin, screen.y + margin
    right = screen.x + screen.width - width - margin
    bottom = screen.y + screen.height - height - margin

    x = right if corner.endswith("right") else left
    y = bottom if corner.startswith("bottom") else top
    # A window larger than the screen would otherwise hang off the top or left,
    # taking its header — and so any way to move or close it — with it.
    return max(left, x), max(top, y)


def is_on_screen(x: int, y: int, width: int, height: int, screens: list[Monitor]) -> bool:
    """True if enough of the window lands on a connected screen to grab it."""
    for screen in screens:
        across = min(x + width, screen.x + screen.width) - max(x, screen.x)
        down = min(y + height, screen.y + screen.height) - max(y, screen.y)
        if across >= GRAB_WIDTH and down >= GRAB_HEIGHT:
            return True
    return False
