"""System-wide hotkeys via Win32 ``RegisterHotKey``.

Hotkeys are registered on a dedicated thread running its own message loop.
Tk's loop would swallow ``WM_HOTKEY`` (it arrives as a thread message with no
window to dispatch to), so the listener owns the registration and hands
callbacks back to the UI thread through a queue.
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes
from typing import Callable

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
}

user32 = ctypes.windll.user32


def _parse(accelerator: str) -> tuple[int, int]:
    """``"ctrl+alt+t"`` -> ``(modifier flags, virtual key code)``."""
    parts = [p.strip().lower() for p in accelerator.split("+") if p.strip()]
    mods = MOD_NOREPEAT
    key = 0
    for part in parts:
        if part in _MODIFIERS:
            mods |= _MODIFIERS[part]
        elif len(part) == 1:
            key = ord(part.upper())
        else:
            raise ValueError(f"Unsupported hotkey component: {part!r}")
    if not key:
        raise ValueError(f"No key in accelerator {accelerator!r}")
    return mods, key


class HotkeyListener:
    def __init__(self) -> None:
        self._bindings: dict[int, Callable[[], None]] = {}
        self._accelerators: list[tuple[int, str]] = []
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        #: Accelerators another app already owns; surfaced in the Setup tab.
        self.failed: list[str] = []

    def bind(self, accelerator: str, callback: Callable[[], None]) -> None:
        hotkey_id = len(self._bindings) + 1
        self._bindings[hotkey_id] = callback
        self._accelerators.append((hotkey_id, accelerator))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        for hotkey_id, accelerator in self._accelerators:
            try:
                mods, key = _parse(accelerator)
            except ValueError:
                self.failed.append(accelerator)
                continue
            # A hotkey another app already owns must not stop the app starting.
            if not user32.RegisterHotKey(None, hotkey_id, mods, key):
                self.failed.append(accelerator)

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY:
                self._queue.put(int(message.wParam))

        for hotkey_id, _ in self._accelerators:
            user32.UnregisterHotKey(None, hotkey_id)

    def pump(self) -> None:
        """Runs queued callbacks. Call periodically from the UI thread."""
        while True:
            try:
                hotkey_id = self._queue.get_nowait()
            except queue.Empty:
                return
            callback = self._bindings.get(hotkey_id)
            if callback:
                callback()

    def stop(self) -> None:
        if self._thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
