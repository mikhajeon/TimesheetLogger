"""A vertically scrollable container, which Tk doesn't provide out of the box."""

from __future__ import annotations

import tkinter as tk

from . import theme


class ScrollFrame(tk.Frame):
    """Pack content into ``.body``; it scrolls when it outgrows the window."""

    def __init__(self, parent: tk.Misc, bg: str = theme.BG) -> None:
        super().__init__(parent, bg=bg)

        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._scrollbar = tk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self.body = tk.Frame(self._canvas, bg=bg)

        self._window = self._canvas.create_window((0, 0), window=self.body, anchor="nw")
        self._canvas.configure(yscrollcommand=self._set_scroll)

        self._canvas.pack(side="left", fill="both", expand=True)

        self.body.bind("<Configure>", self._on_body_resize)
        self._canvas.bind("<Configure>", self._on_canvas_resize)
        # Tk delivers the wheel to the hovered widget, so bind on enter/leave
        # rather than globally; otherwise nested scroll areas fight each other.
        self.bind("<Enter>", lambda _e: self._canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.bind("<Leave>", lambda _e: self._canvas.unbind_all("<MouseWheel>"))

    def _set_scroll(self, first: str, last: str) -> None:
        # Only take up space when there is actually something to scroll.
        if float(first) <= 0.0 and float(last) >= 1.0:
            self._scrollbar.pack_forget()
        else:
            self._scrollbar.pack(side="right", fill="y")
        self._scrollbar.set(first, last)

    def _on_body_resize(self, _event: tk.Event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_resize(self, event: tk.Event) -> None:
        self._canvas.itemconfigure(self._window, width=event.width)

    def _on_wheel(self, event: tk.Event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()

    def to_top(self) -> None:
        self._canvas.yview_moveto(0)
