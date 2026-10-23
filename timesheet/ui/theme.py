"""Colours, fonts, and flat-styled widget factories.

Tk's native widgets look dated, so everything is drawn with explicit colours and
``relief="flat"`` rather than relying on ttk theming, which barely budges on
Windows.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

BG = "#0f1117"
BG_RAISED = "#171a23"
BG_INPUT = "#1f2330"
BG_HOVER = "#2a3040"
LINE = "#262b38"
TEXT = "#e6e9f0"
MUTED = "#8b93a7"
ACCENT = "#5b8cff"
ACCENT_DIM = "#26345c"
BILLABLE = "#34c77b"
BILLABLE_BG = "#10281d"
DANGER = "#ef5f5f"

FONT = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_SMALL = ("Segoe UI", 8)
FONT_TINY = ("Segoe UI", 7)
FONT_HEAD = ("Segoe UI", 8, "bold")
FONT_CLOCK = ("Consolas", 12, "bold")
FONT_MONO = ("Consolas", 8)


def frame(parent: tk.Misc, bg: str = BG, **kw) -> tk.Frame:
    return tk.Frame(parent, bg=bg, **kw)


def label(
    parent: tk.Misc,
    text: str = "",
    *,
    bg: str = BG,
    fg: str = TEXT,
    font: tuple = FONT,
    anchor: str = "w",
    **kw,
) -> tk.Label:
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=font, anchor=anchor, **kw)


def hairline(parent: tk.Misc) -> tk.Frame:
    return tk.Frame(parent, bg=LINE, height=1)


def button(
    parent: tk.Misc,
    text: str,
    command: Callable[[], None],
    *,
    bg: str = BG_INPUT,
    fg: str = TEXT,
    hover: str = BG_HOVER,
    font: tuple = FONT,
    padx: int = 8,
    pady: int = 3,
    **kw,
) -> tk.Button:
    widget = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        font=font,
        activebackground=hover,
        activeforeground=fg,
        relief="flat",
        bd=0,
        highlightthickness=0,
        padx=padx,
        pady=pady,
        cursor="hand2",
        **kw,
    )
    widget.bind("<Enter>", lambda _e: widget.config(bg=hover))
    widget.bind("<Leave>", lambda _e: widget.config(bg=bg))
    return widget


def entry(parent: tk.Misc, *, width: int | None = None, font: tuple = FONT, **kw) -> tk.Entry:
    widget = tk.Entry(
        parent,
        bg=BG_INPUT,
        fg=TEXT,
        font=font,
        relief="flat",
        bd=0,
        insertbackground=TEXT,
        highlightthickness=1,
        highlightbackground=LINE,
        highlightcolor=ACCENT,
        disabledbackground=BG_INPUT,
        **({"width": width} if width else {}),
        **kw,
    )
    return widget


def text_box(parent: tk.Misc, *, height: int = 3, **kw) -> tk.Text:
    return tk.Text(
        parent,
        height=height,
        bg=BG_INPUT,
        fg=TEXT,
        font=FONT,
        relief="flat",
        bd=0,
        wrap="word",
        insertbackground=TEXT,
        highlightthickness=1,
        highlightbackground=LINE,
        highlightcolor=ACCENT,
        padx=5,
        pady=4,
        **kw,
    )


def checkbox(
    parent: tk.Misc,
    text: str,
    variable: tk.BooleanVar,
    command,
    *,
    bg: str = BG,
    anchor: str = "w",
    **kw,
) -> tk.Checkbutton:
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        command=command,
        bg=bg,
        fg=MUTED,
        font=FONT_SMALL,
        activebackground=bg,
        activeforeground=TEXT,
        selectcolor=BG_INPUT,
        relief="flat",
        bd=0,
        highlightthickness=0,
        cursor="hand2",
        anchor=anchor,
        **kw,
    )


def option_menu(parent: tk.Misc, variable: tk.StringVar, values: list[str], command=None) -> tk.OptionMenu:
    widget = tk.OptionMenu(parent, variable, *(values or [""]), command=command)
    widget.config(
        bg=BG_INPUT,
        fg=TEXT,
        font=FONT,
        relief="flat",
        bd=0,
        highlightthickness=0,
        activebackground=BG_HOVER,
        activeforeground=TEXT,
        anchor="w",
        cursor="hand2",
    )
    widget["menu"].config(bg=BG_INPUT, fg=TEXT, font=FONT, relief="flat", bd=0, activebackground=ACCENT_DIM)
    return widget


def ellipsize(text: str, limit: int) -> str:
    """Tk labels don't clip, so long text has to be trimmed before it's set."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def add_placeholder(widget: tk.Entry, text: str) -> Callable[[], str]:
    """Greys in hint text while the field is empty.

    Returns a getter that yields "" rather than the hint, so callers never
    persist the placeholder as if it were real input.
    """
    state = {"showing": False}

    def show() -> None:
        if not widget.get():
            widget.insert(0, text)
            widget.config(fg=MUTED)
            state["showing"] = True

    def clear(_event: tk.Event) -> None:
        if state["showing"]:
            widget.delete(0, "end")
            widget.config(fg=TEXT)
            state["showing"] = False

    widget.bind("<FocusIn>", clear, add="+")
    widget.bind("<FocusOut>", lambda _e: show(), add="+")
    show()

    return lambda: "" if state["showing"] else widget.get()


def pill(parent: tk.Misc, text: str, *, fg: str = "#a9b3cc", bg: str = BG_INPUT) -> tk.Label:
    """Small monospace chip, used for K-codes."""
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=FONT_MONO, padx=4, pady=0)


def dot(parent: tk.Misc, billable: bool, bg: str = BG) -> tk.Canvas:
    canvas = tk.Canvas(parent, width=8, height=8, bg=bg, highlightthickness=0, bd=0)
    canvas.create_oval(1, 1, 7, 7, fill=BILLABLE if billable else "#3a4156", outline="")
    return canvas
