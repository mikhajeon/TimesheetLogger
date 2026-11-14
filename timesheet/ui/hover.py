"""A small hover card: text that appears beside the pointer after a short
rest over a widget, and goes on leaving, pressing or destroying it.

It is a borderless Toplevel of this same process, so the overlay's
"clicked outside" check (which compares process ids) never reads it as
another program taking the foreground.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import theme

DELAY_MS = 350
OFFSET = (14, 18)


class Hover:
    def __init__(self, text: Callable[[], str]) -> None:
        self._text = text
        self._job: str | None = None
        self._tip: tk.Toplevel | None = None
        self._owner: tk.Misc | None = None

    def attach(self, *widgets: tk.Misc) -> None:
        for widget in widgets:
            widget.bind("<Enter>", self._enter, add="+")
            widget.bind("<Leave>", self._leave, add="+")
            widget.bind("<ButtonPress>", self._leave, add="+")
            widget.bind("<Destroy>", self._leave, add="+")

    def _enter(self, event: tk.Event) -> None:
        self._leave()
        self._owner = event.widget
        self._job = event.widget.after(DELAY_MS, self._show)

    def _leave(self, _event: tk.Event | None = None) -> None:
        if self._job and self._owner is not None:
            try:
                self._owner.after_cancel(self._job)
            except tk.TclError:
                pass
        self._job = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None

    def _show(self) -> None:
        self._job = None
        owner = self._owner
        try:
            if owner is None or not owner.winfo_exists():
                return
            text = self._text()
            if not text:
                return
            tip = tk.Toplevel(owner)
            tip.withdraw()
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            tip.config(bg=theme.LINE)
            tk.Label(
                tip, text=text, justify="left", anchor="w", bg=theme.BG_RAISED,
                fg=theme.TEXT, font=theme.FONT_SMALL, padx=8, pady=6,
            ).pack(padx=1, pady=1)
            tip.update_idletasks()
            x = owner.winfo_pointerx() + OFFSET[0]
            y = owner.winfo_pointery() + OFFSET[1]
            width, height = tip.winfo_reqwidth(), tip.winfo_reqheight()
            screen_w, screen_h = owner.winfo_screenwidth(), owner.winfo_screenheight()
            if x + width > screen_w - 4:
                x = max(4, owner.winfo_pointerx() - width - 8)
            if y + height > screen_h - 4:
                y = max(4, owner.winfo_pointery() - height - 8)
            tip.geometry(f"+{x}+{y}")
            tip.deiconify()
            self._tip = tip
        except tk.TclError:
            self._tip = None
