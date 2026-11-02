"""A vertically scrollable container, which Tk doesn't provide out of the box."""

from __future__ import annotations

import tkinter as tk

from . import theme


class ScrollFrame(tk.Frame):
    """Pack content into ``.body``; it scrolls when it outgrows the window."""

    def __init__(self, parent: tk.Misc, bg: str | None = None) -> None:
        # Resolved here rather than in the signature: a default argument binds
        # the colour at import, which would survive a palette change.
        bg = bg or theme.BG
        super().__init__(parent, bg=bg)

        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._scrollbar = theme.scrollbar(self, self._on_scrollbar)
        self.body = tk.Frame(self._canvas, bg=bg)

        self._window = self._canvas.create_window((0, 0), window=self.body, anchor="nw")
        self._canvas.configure(yscrollcommand=self._set_scroll)

        #: Pixels from the top to scroll back to once a rebuild has settled.
        self._pending_offset: float | None = None

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
            # ``before=`` pins it ahead of the canvas in pack's slave order
            # even though it's packed well after it in call order. Without
            # this, a container whose own size is already fixed (a popup
            # sized via ``geometry()`` before its content exists, rather than
            # one left to grow to fit it) never reclaims room from the
            # canvas's earlier ``expand=True`` claim — the scrollbar ends up
            # packed but zero-width and unmapped, no matter how many times
            # the container is told to update.
            self._scrollbar.pack(side="right", fill="y", before=self._canvas)
        self._scrollbar.set(first, last)

    def _on_body_resize(self, _event: tk.Event) -> None:
        region = self._canvas.bbox("all")
        self._canvas.configure(scrollregion=region)

        if self._pending_offset is not None and region:
            height = region[3] - region[1]
            if height > 0:
                self._canvas.yview_moveto(self._pending_offset / height)
            # A rebuild grows the body in steps — five Configure events for the
            # Setup tab — so the offset is held until enough content exists to
            # sit at it. Applying it to a half-built body would land short, and
            # forgetting it there would strand the view near the top.
            if height - self._canvas.winfo_height() >= self._pending_offset:
                self._pending_offset = None

    def _on_canvas_resize(self, event: tk.Event) -> None:
        self._canvas.itemconfigure(self._window, width=event.width)

    def _on_wheel(self, event: tk.Event) -> None:
        self._pending_offset = None  # where the user scrolls to wins
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_scrollbar(self, *args) -> None:
        self._pending_offset = None
        self._canvas.yview(*args)

    def clear(self) -> None:
        """Empties the body, holding the scroll position for the rebuild.

        Views rebuild themselves wholesale on every store change, so without
        this the list jumps back to the top each time anything is added.
        Measured in pixels rather than as a fraction, which would drift as
        soon as the new content is a different height from the old.
        """
        self._pending_offset = self._canvas.canvasy(0)
        for child in self.body.winfo_children():
            child.destroy()

    def to_top(self) -> None:
        self._pending_offset = None  # an explicit jump outranks a restore
        self._canvas.yview_moveto(0)

    def center_on(self, widget: tk.Misc) -> None:
        """Scrolls so ``widget`` — a direct child of :attr:`body` — sits
        roughly in the middle of what's visible, rather than always opening
        at the top. Used by ``theme.duration_picker``'s popup, so picking an
        already-chosen figure well down a long list doesn't mean scrolling
        to find it again every time.
        """
        self._pending_offset = None  # an explicit jump outranks a restore
        self.update_idletasks()
        region = self._canvas.bbox("all")
        if not region:
            return
        total_height = region[3] - region[1]
        if total_height <= 0:
            return
        target_y = widget.winfo_y() - self._canvas.winfo_height() / 2
        self._canvas.yview_moveto(max(0.0, min(1.0, target_y / total_height)))
