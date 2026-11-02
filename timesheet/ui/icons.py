"""Themed PNG icons, generated from the design SVGs by ``scripts/build_icons.py``.

Two things to know about Tk images:

* A ``PhotoImage`` is dropped the moment Python stops referencing it, taking the
  picture off screen with it, so every one is cached here for the process's life.
* One can only exist once a root window does, hence loading on first use rather
  than at import.

Each helper falls back to a text glyph when an icon is missing, so a checkout
whose PNGs haven't been generated still runs with its buttons labelled.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

from . import theme

DIRECTORY = Path(__file__).resolve().parent / "icons"

_cache: dict[tuple[str, str], tk.PhotoImage | None] = {}


def get(name: str) -> tk.PhotoImage | None:
    """The icon for the active palette, or None if it was never generated."""
    key = (theme.ACTIVE, name)
    if key not in _cache:
        path = DIRECTORY / theme.ACTIVE / f"{name}.png"
        try:
            _cache[key] = tk.PhotoImage(file=str(path)) if path.exists() else None
        except tk.TclError:
            _cache[key] = None  # a truncated or half-written file
    return _cache[key]


def button(parent: tk.Misc, name: str, command, *, fallback: str = "", **kw) -> tk.Button:
    """A :func:`theme.button` showing an icon instead of a character."""
    image = get(name)
    widget = theme.button(parent, "" if image else fallback, command, **kw)
    if image is not None:
        widget.config(image=image)
    return widget


def label(parent: tk.Misc, name: str, *, fallback: str = "", **kw) -> tk.Label:
    image = get(name)
    widget = theme.label(parent, "" if image else fallback, **kw)
    if image is not None:
        widget.config(image=image, anchor="center")
    return widget


def chip(
    parent: tk.Misc,
    name: str,
    command,
    *,
    fill: str,
    fallback: str = "",
    text_fill: str | None = None,
    size: int = 26,
    width: int | None = None,
    height: int | None = None,
    radius: int = 7,
    bg: str | None = None,
    enabled: bool = True,
    font: tuple = theme.FONT_ICON_SMALL,
    circle: bool = False,
) -> tk.Canvas:
    """A :func:`theme.chip` showing an icon instead of a character."""
    image = get(name)
    return theme.chip(
        parent,
        command,
        fill=fill,
        image=image,
        text="" if image else fallback,
        text_fill=text_fill,
        size=size,
        width=width,
        height=height,
        radius=radius,
        bg=bg,
        enabled=enabled,
        font=font,
        circle=circle,
    )


def swap(widget: tk.Widget, name: str, *, fallback: str = "") -> None:
    """Points an existing icon widget at a different icon.

    Used where one button has two states — pinned or not, wide or narrow,
    running or stopped.
    """
    image = get(name)
    if image is not None:
        widget.config(image=image, text="")
    else:
        widget.config(text=fallback)
