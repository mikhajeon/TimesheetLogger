"""Keeps a second launch from opening a duplicate overlay.

A named Win32 mutex rather than a lock file: it's released automatically
when the process ends — even a crash, or a kill from Task Manager — so
there's nothing that can go stale and nothing to clean up on exit. The
handle itself is never closed on purpose; there's no corresponding
``release()`` here for the same reason.
"""

from __future__ import annotations

import ctypes
import hashlib
import time
from ctypes import wintypes

from . import paths

ERROR_ALREADY_EXISTS = 183
SW_SHOW = 5

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32

#: HANDLE is pointer-width. ctypes defaults a return value to a 32-bit int,
#: which silently halves a handle on 64-bit Python, so it's declared (same
#: reasoning as ``tray.py``'s own window handles).
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]

#: The window title ``Overlay.__init__`` sets — matched here to hand off to
#: an already-running instance instead of opening a second one on top of it.
WINDOW_TITLE = "Timesheet Logger"


def _mutex_name() -> str:
    # Scoped to the data folder rather than fixed, so pointing TIMESHEET_HOME
    # somewhere else for a one-off run — what the test suite and the preview
    # scripts both do — can never collide with a real, already-running
    # instance using the default one.
    digest = hashlib.sha1(str(paths.home()).encode("utf-8")).hexdigest()
    return f"TimesheetLoggerOverlay-{digest}"


def already_running() -> bool:
    """Claims this process as the one instance for this data folder.

    True means another process already holds it — the caller should hand
    off to it (see :func:`focus_existing`) rather than open a second overlay
    on the same data.
    """
    kernel32.CreateMutexW(None, False, _mutex_name())
    return kernel32.GetLastError() == ERROR_ALREADY_EXISTS


def focus_existing() -> bool:
    """Brings the running instance's window to the front, wherever it is —
    collapsed to its badge, hidden to the tray, or just buried under
    something else. True if a window was actually found.

    Retried a few times over two seconds: this can be called moments after
    the first instance claimed the mutex but before its window exists yet —
    plausible if it's starting from the sign-in Startup folder at the same
    moment it's also started by hand from the desktop.
    """
    for _ in range(20):
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, SW_SHOW)
            user32.SetForegroundWindow(hwnd)
            return True
        time.sleep(0.1)
    return False
