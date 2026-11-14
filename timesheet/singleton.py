"""Makes sure only one overlay runs per data folder: a new launch replaces the old one.

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
SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32

#: HANDLE is pointer-width. ctypes defaults a return value to a 32-bit int,
#: which silently halves a handle on 64-bit Python, so it's declared (same
#: reasoning as ``tray.py``'s own window handles).
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, ctypes.c_uint]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

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


def _event_name() -> str:
    return _mutex_name().replace("Overlay", "Quit")


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


def watch_for_quit(root, on_quit, interval_ms: int = 300):
    """Lets a newer launch ask this instance to exit: a named event the
    newer one sets, polled from the Tk loop, so ``on_quit`` (a clean quit
    — data saved, tray icon and hotkeys released) runs on the UI thread."""
    handle = kernel32.CreateEventW(None, True, False, _event_name())
    if not handle:
        return

    def poll() -> None:
        if kernel32.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0:
            on_quit()
            return
        root.after(interval_ms, poll)

    root.after(interval_ms, poll)


def replace_existing(graceful_s: float = 4.0) -> bool:
    """Closes the running instance so this launch can take over.

    Asks it to quit (see :func:`watch_for_quit`) and waits for the process
    to end; an older build that does not listen, or one that is hung, is
    ended outright after ``graceful_s`` — the data is saved on every
    change, so nothing is lost. True once it is gone, False if no running
    instance could be found.
    """
    hwnd = None
    for _ in range(20):
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            break
        time.sleep(0.1)
    if not hwnd:
        return False
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE, False, pid.value)
    if not process:
        return False
    try:
        event = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, _event_name())
        if event:
            kernel32.SetEvent(event)
            kernel32.CloseHandle(event)
        if kernel32.WaitForSingleObject(process, int(graceful_s * 1000)) != WAIT_OBJECT_0:
            kernel32.TerminateProcess(process, 0)
            kernel32.WaitForSingleObject(process, 3000)
        return kernel32.WaitForSingleObject(process, 0) == WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(process)
