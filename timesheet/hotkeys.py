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


def format_accelerator(accelerator: str) -> str:
    """``"ctrl+alt+t"`` -> ``"Ctrl+Alt+T"``, for display."""
    return "+".join(
        part.upper() if len(part) == 1 else part.capitalize()
        for part in accelerator.split("+")
    )


class HotkeyListener:
    def __init__(self) -> None:
        self._bindings: dict[int, Callable[[], None]] = {}
        self._accelerators: list[tuple[int, str]] = []
        #: Stable name -> hotkey id, so :meth:`rebind` can find and replace
        #: one accelerator without the caller having to track ids itself.
        self._names: dict[str, int] = {}
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        #: Accelerators another app already owns; surfaced in the Setup tab.
        self.failed: list[str] = []

    def bind(self, name: str, accelerator: str, callback: Callable[[], None]) -> None:
        """Registers ``callback`` under ``accelerator``, callable again later
        by ``name`` (see :meth:`rebind`) rather than the accelerator string,
        which is exactly what's changing."""
        hotkey_id = len(self._bindings) + 1
        self._bindings[hotkey_id] = callback
        self._accelerators.append((hotkey_id, accelerator))
        self._names[name] = hotkey_id

    def rebind(self, name: str, accelerator: str) -> None:
        """Swaps ``name``'s accelerator for a new one.

        Win32 hotkey registration is thread-affine — only the thread that
        called ``RegisterHotKey`` may ``UnregisterHotKey`` it — so rather
        than reach across into the listener thread's own message loop, this
        just restarts it via :meth:`pause`/:meth:`resume`: stop (unregisters
        everything, thread exits), swap the one entry, start fresh. A few
        milliseconds of every hotkey being briefly unregistered is not
        something a human notices. Already paused (see :meth:`pause`) — e.g.
        the Setup tab capturing this very change — just swaps the entry and
        leaves resuming to the caller, so the new accelerator doesn't start
        listening a beat early.
        """
        hotkey_id = self._names.get(name)
        if hotkey_id is None:
            return
        was_running = self._thread is not None
        self.pause()
        self._accelerators = [
            (hid, accelerator if hid == hotkey_id else current)
            for hid, current in self._accelerators
        ]
        if was_running:
            self.resume()

    def start(self) -> None:
        self.failed = []
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        """Stops listening without forgetting any binding, so a key combo
        pressed elsewhere — e.g. while capturing a *new* shortcut in the
        Setup tab — can't trigger whichever action already owns it.

        A no-op if already paused, which lets :meth:`rebind` share this
        without restarting a listener the caller is about to :meth:`resume`
        itself, keys picked up while paused included.
        """
        if self._thread is None:
            return
        self.stop()
        self._thread.join(timeout=2)
        self._thread = None
        self._thread_id = None

    def resume(self) -> None:
        """Undoes :meth:`pause`, re-registering every binding exactly as it
        was left. A no-op if not currently paused."""
        if self._thread is None:
            self.start()

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
