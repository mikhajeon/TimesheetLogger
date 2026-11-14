"""How long since the user last touched the mouse or keyboard.

Windows tracks this itself — ``GetLastInputInfo`` — so this reads straight
from the OS rather than hooking every keystroke and click ourselves, which
would also clash with any corporate input-monitoring policy and a thousand
other things that already watch for exactly this to drive the lock screen,
screen savers and Microsoft Teams' own "Away" status, which is the whole
point: matching that same signal (see ``Store.Settings.idle_pause_minutes``)
is what makes the timer's own auto-pause line up with it by default.

Deliberately free of any tkinter import, same as :mod:`timesheet.monitors`,
so the idle maths can be tested without a desktop session.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


#: Both return an unsigned tick count — left as plain ``ctypes`` defaults
#: (signed ``int``), a tick count past ~24.8 days (``2**31`` ms) would read
#: back negative and throw every idle reading off from then on.
_get_last_input_info = ctypes.windll.user32.GetLastInputInfo
_get_last_input_info.argtypes = [ctypes.POINTER(_LASTINPUTINFO)]
_get_last_input_info.restype = wintypes.BOOL

_get_tick_count = ctypes.windll.kernel32.GetTickCount
_get_tick_count.restype = wintypes.DWORD


def seconds_since_last_input() -> float:
    """Seconds since the last mouse click, mouse move or key press, system
    wide — not scoped to this app, since someone reading a document on
    another screen or in another window is just as idle, for a timer's
    purposes, as someone who's stepped away entirely.

    Falls back to reporting "just active" (``0.0``) rather than raising if
    the call ever fails — idle-triggered auto-pause missing a stretch of
    actual idle time is a far smaller problem than it crashing the app
    outright."""
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not _get_last_input_info(ctypes.byref(info)):
        return 0.0
    # Both are DWORDs (see above), so this wraps the same way they do —
    # right around the moment either one wraps back to 0, which reads as
    # "just active" for one tick instead of an enormous idle stretch.
    idle_ms = (_get_tick_count() - info.dwTime) & 0xFFFFFFFF
    return idle_ms / 1000.0
