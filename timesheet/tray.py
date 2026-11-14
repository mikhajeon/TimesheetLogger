"""A notification-area icon via Win32 ``Shell_NotifyIcon``.

Built the same way as :mod:`timesheet.hotkeys`, and for the same reason: the
tray talks to its icon by posting messages to a window, and Tk's loop won't
dispatch them. So the icon owns a message-only window on its own thread and
hands clicks back to the UI thread through a queue.

Nothing here raises. A tray icon is a convenience, and a machine that won't
give us one should still get the overlay.
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_NULL = 0x0000
WM_QUIT = 0x0012

#: Our own messages. WM_APP is the first value Windows promises not to use.
WM_APP = 0x8000
WM_TRAY = WM_APP + 1  #: the icon reporting a click
WM_REFRESH = WM_APP + 2  #: the UI thread asking for a redraw of tooltip/menu

#: Broadcast by explorer.exe whenever it (re)starts — after a crash, an
#: update, or because this app launched at logon before it had finished
#: coming up. Every icon the old explorer knew about is gone when that
#: happens; nothing short of adding it again brings it back, and nothing
#: tells us to unless we ask Windows to relay this by name, since the
#: message's numeric value isn't fixed across versions/sessions.
WM_TASKBARCREATED = user32.RegisterWindowMessageW("TaskbarCreated")

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04

IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE, LR_SHARED = 0x0010, 0x0040, 0x8000

MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100

#: Parent for a window that exists only to receive messages.
HWND_MESSAGE = wintypes.HWND(-3)
IDI_APPLICATION = 32512

#: Tooltips are a fixed-width field in the struct; longer text is dropped
#: silently by the shell, so it gets trimmed here instead.
TIP_LIMIT = 127

#: LRESULT and HWND are pointer-width. ctypes defaults every return value to
#: a 32-bit int, which silently halves a handle on 64-bit Python, so every
#: call that returns or takes one is declared.
LRESULT = ctypes.c_ssize_t

WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadIconW.restype = wintypes.HICON
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, wintypes.LPVOID,
]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class TrayIcon:
    """An icon in the notification area.

    Menu entries are ``(label, command)`` pairs, or ``(None, None)`` for a
    separator. The command is whatever string the caller wants back; it is
    handed to the callback registered with :meth:`on`, on the UI thread, when
    :meth:`pump` next runs.
    """

    #: What a left click sends, so it needs no entry in the menu.
    CLICK = "click"

    def __init__(self, tooltip: str, icon: Path | None = None) -> None:
        self.icon_file = icon
        #: Set if the icon could not be created, for the Setup tab to show.
        self.failed = ""

        self._tooltip = tooltip
        self._menu: list[tuple[str | None, str | None]] = []
        self._handlers: dict[str, callable] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        # Guards the two fields the UI thread writes and the tray thread reads.
        self._lock = threading.Lock()
        self._ready = threading.Event()

        self._thread: threading.Thread | None = None
        self._hwnd: int | None = None
        self._hicon = None
        # ctypes only weakly ties a callback to its Python function, so the
        # window procedure has to outlive the window by being kept here.
        self._wndproc = WNDPROC(self._handle)
        self._class = f"TimesheetTray{id(self):x}"

    # -- the UI thread's side ------------------------------------------------

    def on(self, command: str, callback) -> None:
        self._handlers[command] = callback

    def set_menu(self, items: list[tuple[str | None, str | None]]) -> None:
        """Replaces the right-click menu.

        Pushed from the UI thread rather than built on demand, so the tray
        thread never reads application state while it's being changed.
        """
        with self._lock:
            self._menu = list(items)

    def set_tooltip(self, text: str) -> None:
        with self._lock:
            if text == self._tooltip:
                return
            self._tooltip = text
        self._post(WM_REFRESH)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()
        # Briefly, so a failure shows up in Setup straight away rather than
        # after the first refresh.
        self._ready.wait(timeout=2.0)

    def pump(self) -> None:
        """Runs queued callbacks. Call periodically from the UI thread."""
        while True:
            try:
                command = self._queue.get_nowait()
            except queue.Empty:
                return
            handler = self._handlers.get(command)
            if handler:
                handler()

    def stop(self) -> None:
        self._post(WM_DESTROY)
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _post(self, message: int) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, message, 0, 0)

    # -- the tray thread's side ----------------------------------------------

    def _run(self) -> None:
        try:
            self._create()
        except OSError as error:
            self.failed = str(error)
        finally:
            self._ready.set()
        if self.failed:
            return

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def _create(self) -> None:
        instance = kernel32.GetModuleHandleW(None)

        window_class = WNDCLASS()
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = instance
        window_class.lpszClassName = self._class
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            raise OSError("could not register the tray window class")

        # A message-only window: never shown, never in the taskbar, but it
        # has an HWND, which is all the shell needs to call us back on.
        self._hwnd = user32.CreateWindowExW(
            0, self._class, "Timesheet", 0,
            0, 0, 0, 0,
            HWND_MESSAGE, None, instance, None,
        )
        if not self._hwnd:
            raise OSError("could not create the tray window")

        self._hicon = self._load_icon(instance)
        # Launching at logon races explorer.exe's own startup — the add can
        # fail simply because its notification host isn't listening yet.
        # A few short retries covers that without the window staying open
        # long enough for a user to notice the wait.
        added = False
        for attempt in range(5):
            if attempt:
                time.sleep(0.5)
            if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._data())):
                added = True
                break
        if not added:
            raise OSError("the shell refused the tray icon")

    def _load_icon(self, instance: int):
        if self.icon_file and self.icon_file.exists():
            handle = user32.LoadImageW(
                None, str(self.icon_file), IMAGE_ICON, 0, 0,
                LR_LOADFROMFILE | LR_DEFAULTSIZE,
            )
            if handle:
                return handle
        # The stock application icon, so a missing or unreadable file still
        # leaves something clickable in the tray. Resource ids are passed
        # where a string pointer goes, which is what MAKEINTRESOURCE does.
        return user32.LoadIconW(
            None, ctypes.cast(ctypes.c_void_p(IDI_APPLICATION), wintypes.LPCWSTR)
        )

    def _data(self, flags: int = NIF_MESSAGE | NIF_ICON | NIF_TIP) -> NOTIFYICONDATA:
        data = NOTIFYICONDATA()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._hicon
        with self._lock:
            data.szTip = self._tooltip[:TIP_LIMIT]
        return data

    def _handle(self, hwnd, message, wparam, lparam) -> int:
        if message == WM_TRAY:
            # The mouse message is in the low word; the high word is the icon id.
            event = lparam & 0xFFFF
            if event == WM_LBUTTONUP:
                self._queue.put(self.CLICK)
            elif event == WM_RBUTTONUP:
                self._show_menu()
            return 0

        if message == WM_REFRESH:
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._data(NIF_TIP)))
            return 0

        if message == WM_TASKBARCREATED:
            shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._data()))
            return 0

        if message == WM_DESTROY:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data(0)))
            user32.PostQuitMessage(0)
            return 0

        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _show_menu(self) -> None:
        with self._lock:
            items = list(self._menu)
        if not items:
            return

        menu = user32.CreatePopupMenu()
        commands: dict[int, str] = {}
        for index, (label, command) in enumerate(items, start=1):
            if label is None:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            user32.AppendMenuW(menu, MF_STRING, index, label)
            commands[index] = command

        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        # Without this the menu stays open after a click elsewhere: a tray
        # menu only dismisses itself if its owner is the foreground window.
        user32.SetForegroundWindow(self._hwnd)
        chosen = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0, self._hwnd, None
        )
        user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)

        if chosen in commands:
            self._queue.put(commands[chosen])
