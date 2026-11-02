"""Colours, fonts, and flat-styled widget factories.

Tk's native widgets look dated, so everything is drawn with explicit colours and
``relief="flat"`` rather than relying on ttk theming, which barely budges on
Windows.

The colour tokens are module attributes rather than constants: :func:`use`
rebinds them. Every caller reads them as ``theme.ACCENT`` at the moment it
builds a widget, so swapping palettes needs no changes at the call sites — but
Tk stores colours on each widget at creation time and never re-reads them, so a
live switch still has to rebuild the tree. ``Overlay.set_theme`` does that.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

DEFAULT = "dark"

#: Token -> colour, per palette. Tokens are named for their role rather than
#: their appearance, so the same name reads correctly in either palette.
PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "BG": "#0f1117",
        "BG_RAISED": "#171a23",
        "BG_INPUT": "#1f2330",
        "BG_HOVER": "#2a3040",
        "LINE": "#262b38",
        "TEXT": "#e6e9f0",
        "MUTED": "#8b93a7",
        "ACCENT": "#5b8cff",
        "ACCENT_DIM": "#26345c",
        "ACCENT_HOVER": "#6f9bff",
        "ON_ACCENT": "#0a1020",
        "BILLABLE": "#34c77b",
        "BILLABLE_BG": "#10281d",
        "ON_BILLABLE": "#06120c",
        "RUNNING_LINE": "#27624a",
        "DANGER": "#ef5f5f",
        "ON_DANGER": "#2a0d0d",
        "IDLE": "#3a4156",
        "PILL_TEXT": "#a9b3cc",
    },
    "light": {
        "BG": "#eef1f6",
        "BG_RAISED": "#ffffff",
        "BG_INPUT": "#e4e9f0",
        "BG_HOVER": "#d3dae5",
        "LINE": "#cdd5e0",
        "TEXT": "#121722",
        "MUTED": "#5a6477",
        "ACCENT": "#2f6bdb",
        "ACCENT_DIM": "#cfdefb",
        "ACCENT_HOVER": "#4a80e8",
        "ON_ACCENT": "#ffffff",
        "BILLABLE": "#0a7a42",
        "BILLABLE_BG": "#dff3e7",
        "ON_BILLABLE": "#ffffff",
        "RUNNING_LINE": "#7cc7a4",
        "DANGER": "#c4332b",
        "ON_DANGER": "#ffffff",
        "IDLE": "#aeb7c6",
        "PILL_TEXT": "#3f4a60",
    },
}

#: Declared for readers and type checkers; ``use()`` binds the values below.
BG: str
BG_RAISED: str
BG_INPUT: str
BG_HOVER: str
LINE: str
TEXT: str
MUTED: str
ACCENT: str
ACCENT_DIM: str
ACCENT_HOVER: str
ON_ACCENT: str
BILLABLE: str
BILLABLE_BG: str
ON_BILLABLE: str
RUNNING_LINE: str
DANGER: str
ON_DANGER: str
IDLE: str
PILL_TEXT: str

#: Name of the palette currently bound — always a real key in ``PALETTES``,
#: never ``"system"`` (see ``MODE`` for that).
ACTIVE: str = DEFAULT

#: What the user actually asked for: a palette name, or ``"system"`` to track
#: the OS setting. Kept separate from ``ACTIVE`` so a preferences screen can
#: still show "System" selected even while the palette it resolved to today
#: is, say, "dark" — and so a later OS-theme change has something to compare
#: against to notice it should re-resolve.
MODE: str = DEFAULT

#: A mode that isn't a real palette name: resolved against the OS's own
#: light/dark setting at ``use()`` time instead of naming one directly.
SYSTEM = "system"


def system_palette() -> str:
    """Reads Windows' own light/dark choice. Defaults to ``DEFAULT`` wherever
    that can't be read — an old Windows version, a locked-down registry, or
    simply not being on Windows at all — so "System" is never a dead option.
    """
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        try:
            light, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        finally:
            winreg.CloseKey(key)
        return "light" if light else "dark"
    except OSError:
        return DEFAULT

FONT = ("Segoe UI", 11)
FONT_BOLD = ("Segoe UI", 11, "bold")
FONT_SMALL = ("Segoe UI", 10)
FONT_TINY = ("Segoe UI", 9)
FONT_HEAD = ("Segoe UI", 10, "bold")
FONT_CLOCK = ("Consolas", 14, "bold")
FONT_MONO = ("Consolas", 10)
#: Glyph buttons — window controls, nav arrows. Sized apart from body text
#: because a symbol needs more room than a letter to stay legible.
FONT_ICON = ("Segoe UI", 15)
#: Glyphs inside a list row, where the row height limits how big they can go.
FONT_ICON_SMALL = ("Segoe UI", 13)
#: The collapsed badge, which is nothing but its glyph.
FONT_ICON_LARGE = ("Segoe UI", 22)

#: A date-nav row's prev/next square — bigger than a plain icon button's own
#: tight padding gives, so there's real room to click before a miss lands on
#: whatever sits beside it instead (the centred "jump to ..." label, in every
#: tab that has one).
NAV_CHIP_SIZE = 32


def use(name: str) -> str:
    """Binds a palette's tokens as module attributes. Returns the mode used —
    ``"system"`` comes back as itself, not whichever of dark/light it
    resolved to today, so a caller that persists the return value
    (``Overlay.set_theme``) keeps "System" on record across a restart.

    An unknown, non-"system" name falls back to the default palette rather
    than raising, so a hand-edited settings file can't stop the app from
    starting — and that fallback is what's returned too, same as before
    "system" existed, so a garbage value doesn't get written back verbatim.
    """
    global ACTIVE, MODE
    if name == SYSTEM:
        ACTIVE = system_palette()
        if ACTIVE not in PALETTES:
            ACTIVE = DEFAULT
        MODE = SYSTEM
    else:
        ACTIVE = name if name in PALETTES else DEFAULT
        MODE = ACTIVE
    globals().update(PALETTES[ACTIVE])
    return MODE


use(DEFAULT)


def frame(parent: tk.Misc, bg: str | None = None, **kw) -> tk.Frame:
    return tk.Frame(parent, bg=bg or BG, **kw)


def commit_focused_entry(widget: tk.Misc) -> None:
    """Commits whatever's typed in a focused Entry or Text box before
    something else acts on stale data.

    Clicking a button doesn't move keyboard focus off an Entry the way
    clicking another text field does — plain Tk's buttons and this app's own
    drawn Canvas chips alike leave a just-typed note or minutes box focused
    and uncommitted, so its bound ``<FocusOut>`` handler would otherwise
    never fire before a redraw destroys the widget out from under the typed
    text. Call this right before anything that might tear down the widget
    tree — a view's own ``refresh()``, or switching tabs.
    """
    focused = widget.focus_get()
    if isinstance(focused, (tk.Entry, tk.Text)):
        focused.event_generate("<FocusOut>")


def label(
    parent: tk.Misc,
    text: str = "",
    *,
    bg: str | None = None,
    fg: str | None = None,
    font: tuple = FONT,
    anchor: str = "w",
    **kw,
) -> tk.Label:
    return tk.Label(
        parent, text=text, bg=bg or BG, fg=fg or TEXT, font=font, anchor=anchor, **kw
    )


def hairline(parent: tk.Misc) -> tk.Frame:
    return tk.Frame(parent, bg=LINE, height=1)


def vrule(parent: tk.Misc) -> tk.Frame:
    """Vertical counterpart to :func:`hairline`, for indented groups."""
    return tk.Frame(parent, bg=LINE, width=1)


def button(
    parent: tk.Misc,
    text: str,
    command: Callable[[], None],
    *,
    bg: str | None = None,
    fg: str | None = None,
    hover: str | None = None,
    font: tuple = FONT,
    padx: int = 8,
    pady: int = 3,
    **kw,
) -> tk.Button:
    bg, fg, hover = bg or BG_INPUT, fg or TEXT, hover or BG_HOVER
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


def text_box(parent: tk.Misc, *, height: int = 3, font: tuple = FONT, **kw) -> tk.Text:
    return tk.Text(
        parent,
        height=height,
        bg=BG_INPUT,
        fg=TEXT,
        font=font,
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
    bg: str | None = None,
    anchor: str = "w",
    **kw,
) -> tk.Checkbutton:
    bg = bg or BG
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


#: The duration popup's own size: wide enough for the longest usual label
#: ("12h 0m" and friends), tall enough to show a handful of rows before it
#: has to scroll — see ``duration_picker``.
_DURATION_POPUP_WIDTH = 110
_DURATION_POPUP_MAX_HEIGHT = 220


def duration_picker(
    parent: tk.Misc,
    minutes: int,
    on_change: Callable[[int], None],
    *,
    max_minutes: int = 12 * 60,
    step: int = 15,
    width: int | None = None,
    font: tuple = FONT_SMALL,
    label: Callable[[int], str] | None = None,
    padx: int = 8,
    pady: int = 3,
) -> tk.Button:
    """A button opening a scrollable popup of durations in fixed, 15-minute
    steps, in place of a free-typed box.

    Drawn the same way ``date_picker`` is — a plain button toggling a
    borderless ``Toplevel`` underneath it — rather than a native
    ``tk.OptionMenu``: a 12-hour range in 15-minute steps is 49 options (16
    hours, as the Daily target picker allows, is 65), and a ``Menu`` has no
    scrollbar of its own on Windows — opened that long, it simply runs off
    the bottom of the screen with no way to reach whatever fell past the
    edge. This scrolls instead, and opens already centred on whichever
    option is currently chosen (see ``ScrollFrame.center_on``) rather than
    always at the top of a list that long.

    ``minutes`` outside the usual run — an odd value inherited from old
    data, or simply more than ``max_minutes`` — still gets an entry of its
    own added to the list, rather than snapping silently to the nearest
    option and overwriting it the moment it's opened without anyone having
    chosen to change it.

    ``label`` formats each option's text — ``dates.format_minutes`` (e.g.
    "1h 30m") by default, but overridable somewhere as cramped as the Week
    tab's day columns, which need something shorter to avoid forcing every
    column wider than it's been measured for.
    """
    from ..dates import format_minutes
    from .scroll import ScrollFrame

    to_label = label or format_minutes
    minutes = max(0, round(minutes))
    options = list(range(0, max_minutes + 1, step))
    if minutes not in options:
        options = sorted(set(options) | {minutes})

    state = {"value": minutes, "popup": None}

    def _label_text() -> str:
        return to_label(state["value"])

    def _toggle() -> None:
        if state["popup"] is not None:
            _close()
        else:
            _open()

    display = button(parent, _label_text(), _toggle, font=font, padx=padx, pady=pady)
    if width:
        display.config(width=width)

    def _close() -> None:
        popup = state["popup"]
        state["popup"] = None
        if popup is not None and popup.winfo_exists():
            popup.destroy()

    def _choose(value: int) -> None:
        state["value"] = value
        display.config(text=_label_text())
        _close()
        on_change(value)

    def _open() -> None:
        popup = tk.Toplevel(parent)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=BG_RAISED, highlightthickness=1, highlightbackground=LINE)
        state["popup"] = popup

        scroller = ScrollFrame(popup, bg=BG_RAISED)
        scroller.pack(fill="both", expand=True)
        selected: tk.Button | None = None
        for value in options:
            is_current = value == state["value"]
            row_btn = button(
                scroller.body, to_label(value), lambda v=value: _choose(v),
                bg=ACCENT if is_current else BG_RAISED,
                fg=ON_ACCENT if is_current else TEXT,
                hover=ACCENT if is_current else BG_HOVER,
                font=font, anchor="w",
            )
            row_btn.pack(fill="x")
            if is_current:
                selected = row_btn

        popup.update_idletasks()
        height = min(_DURATION_POPUP_MAX_HEIGHT, scroller.body.winfo_reqheight())
        x = display.winfo_rootx()
        y = display.winfo_rooty() + display.winfo_height() + 2
        popup.geometry(f"{_DURATION_POPUP_WIDTH}x{height}+{x}+{y}")
        # Settles the canvas at its new, possibly-constrained size — without
        # this, the scrollbar's own visibility (decided off the canvas's
        # size at the moment the list was still free to grow to fit
        # everything, before the line above ever capped it) would be stale.
        popup.update_idletasks()
        if selected is not None:
            scroller.center_on(selected)
        popup.bind("<FocusOut>", lambda _e: _close())
        popup.bind("<Escape>", lambda _e: _close())
        popup.focus_force()

    return display


_WEEKDAY_HEADS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")


def date_picker(
    parent: tk.Misc,
    value: str,
    on_change: Callable[[str], None],
    *,
    allow_clear: bool = False,
    clear_label: str = "No date",
    placeholder: str = "Pick a date\u2026",
    font: tuple = FONT_MONO,
    width: int = 11,
) -> tk.Button:
    """A button showing ``value`` (``YYYY-MM-DD``, or ``""``), opening a
    small calendar to replace it rather than typing one by hand in the
    right format.

    Pure Tk ships no calendar widget of its own, so this draws one: a
    ``Toplevel`` (a real, borderless popup window, positioned under the
    button by its own screen coordinates — not a child widget stacked on
    top, which couldn't draw outside its parent's own frame) holding a
    month header with prev/next either side, and a Monday-start week grid
    of day buttons underneath. Picking a day, or clicking outside the
    popup, closes it; the latter is a plain ``<FocusOut>`` on the popup's
    own window, the usual way a Tk dropdown dismisses itself on an outside
    click without needing to track every other widget that could have been
    clicked instead.
    """
    from .. import dates as _dates

    state = {"value": value, "popup": None}

    def label_text() -> str:
        return state["value"] or placeholder

    display = button(parent, label_text(), lambda: _toggle(), font=font, width=width)
    display.config(fg=TEXT if state["value"] else MUTED)

    def _close() -> None:
        popup = state["popup"]
        state["popup"] = None
        if popup is not None and popup.winfo_exists():
            popup.destroy()

    def _choose(date_iso: str) -> None:
        state["value"] = date_iso
        display.config(text=label_text(), fg=TEXT if date_iso else MUTED)
        _close()
        on_change(date_iso)

    def _shown_month() -> str:
        try:
            return _dates.start_of_month(state["value"] or _dates.today())
        except ValueError:
            return _dates.start_of_month(_dates.today())

    def _render(popup: tk.Toplevel, month_start: str) -> None:
        for child in popup.winfo_children():
            child.destroy()

        header = frame(popup, bg=BG_RAISED)
        header.pack(fill="x", padx=6, pady=(6, 2))
        chip(
            header, lambda: _render(popup, _dates.add_months(month_start, -1)),
            fill=BG_RAISED, text="\u2039", text_fill=TEXT, size=22, radius=5,
        ).pack(side="left")
        label(
            header, _dates.month_label(month_start), bg=BG_RAISED, fg=TEXT,
            font=FONT_SMALL, anchor="center",
        ).pack(side="left", fill="x", expand=True)
        chip(
            header, lambda: _render(popup, _dates.add_months(month_start, 1)),
            fill=BG_RAISED, text="\u203a", text_fill=TEXT, size=22, radius=5,
        ).pack(side="right")

        grid = frame(popup, bg=BG_RAISED)
        grid.pack(padx=6, pady=(2, 6))
        for column, heading in enumerate(_WEEKDAY_HEADS):
            label(
                grid, heading, bg=BG_RAISED, fg=MUTED, font=FONT_TINY,
                anchor="center", width=3,
            ).grid(row=0, column=column, padx=1, pady=1)
        today_iso = _dates.today()
        for row, week in enumerate(_dates.month_weeks(month_start), start=1):
            for column, day in enumerate(week):
                in_month = _dates.is_same_month(day, month_start)
                selected = day == state["value"]
                btn = button(
                    grid, str(_dates.from_iso(day).day), lambda d=day: _choose(d),
                    bg=ACCENT if selected else BG_RAISED,
                    fg=(ON_ACCENT if selected else (TEXT if in_month else MUTED)),
                    hover=ACCENT if selected else BG_HOVER,
                    font=FONT_TINY, padx=0, pady=3, width=3,
                )
                if day == today_iso and not selected:
                    btn.config(highlightthickness=1, highlightbackground=ACCENT, highlightcolor=ACCENT)
                btn.grid(row=row, column=column, padx=1, pady=1)

        if allow_clear:
            hairline(popup).pack(fill="x", padx=6)
            button(
                popup, clear_label, lambda: _choose(""),
                bg=BG_RAISED, fg=MUTED, font=FONT_TINY,
            ).pack(fill="x", padx=6, pady=(4, 6))

    def _toggle() -> None:
        if state["popup"] is not None:
            _close()
            return
        popup = tk.Toplevel(parent)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=BG_RAISED, highlightthickness=1, highlightbackground=LINE)
        x, y = display.winfo_rootx(), display.winfo_rooty() + display.winfo_height() + 2
        popup.geometry(f"+{x}+{y}")
        state["popup"] = popup
        _render(popup, _shown_month())
        popup.bind("<FocusOut>", lambda _e: _close())
        popup.bind("<Escape>", lambda _e: _close())
        popup.focus_force()

    return display


def scrollbar(parent: tk.Misc, command: Callable) -> tk.Scrollbar:
    return tk.Scrollbar(
        parent,
        orient="vertical",
        command=command,
        bg=BG_HOVER,
        troughcolor=BG,
        activebackground=MUTED,
        highlightthickness=0,
        bd=0,
        relief="flat",
        width=10,
    )


def bind_tree(widget: tk.Misc, sequence: str, callback: Callable[[tk.Event], None]) -> None:
    """Binds a key on ``widget`` and everything inside it.

    A key press goes to whichever widget has focus and then up its bind tags —
    widget, class, toplevel, all — never to the enclosing frame. So a shortcut
    that should work anywhere in a panel has to be put on each of its parts.
    """
    widget.bind(sequence, callback)
    for child in widget.winfo_children():
        bind_tree(child, sequence, callback)


def ellipsize(text: str, limit: int) -> str:
    """Tk labels don't clip, so long text has to be trimmed before it's set."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def blend(color_a: str, color_b: str, t: float) -> str:
    """Linearly interpolates two ``#rrggbb`` colours, ``t`` of the way from
    ``color_a`` to ``color_b``.

    Tk widgets have no real alpha channel of their own — only a whole
    ``Toplevel`` can be made translucent, via its own ``-alpha`` attribute —
    so anything meant to look faint or "see-through" against a known
    background has to fake it by mixing toward that background's own colour
    instead. Used for the Day tab's manual-reorder drop indicator.
    """

    def channels(color: str) -> tuple[int, int, int]:
        color = color.lstrip("#")
        return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)

    ar, ag, ab = channels(color_a)
    br, bg, bb = channels(color_b)
    return "#" + "".join(
        f"{round(a + (b - a) * t):02x}" for a, b in ((ar, br), (ag, bg), (ab, bb))
    )


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


def pill(parent: tk.Misc, text: str, *, fg: str | None = None, bg: str | None = None) -> tk.Label:
    """Small monospace chip, used for K-codes."""
    return tk.Label(
        parent, text=text, bg=bg or BG_INPUT, fg=fg or PILL_TEXT, font=FONT_MONO, padx=4, pady=0
    )


def dot(parent: tk.Misc, billable: bool, bg: str | None = None) -> tk.Canvas:
    canvas = tk.Canvas(parent, width=11, height=11, bg=bg or BG, highlightthickness=0, bd=0)
    canvas.create_oval(1, 1, 10, 10, fill=BILLABLE if billable else IDLE, outline="")
    return canvas


def radio(
    parent: tk.Misc,
    selected: bool,
    command: Callable[[], None],
    *,
    fill: str | None = None,
    bg: str | None = None,
    size: int = 20,
    enabled: bool = True,
) -> tk.Canvas:
    """A circular, single-choice selector.

    Drawn rather than ``tk.Radiobutton``, which can't be made round on
    Windows — ttk theming barely budges there either, same reasoning as
    every other control in this module — and carries a dotted focus ring
    nothing else here uses. Filled when ``selected``, an outline otherwise;
    static once drawn, the same as every other row control here, since the
    whole row is rebuilt on the next refresh rather than repainted in place.
    """
    fill = fill or ACCENT
    canvas = tk.Canvas(
        parent, width=size, height=size, bg=bg or BG, highlightthickness=0, bd=0,
        cursor="hand2" if enabled else "arrow",
    )
    pad = 2
    if selected:
        canvas.create_oval(pad, pad, size - pad, size - pad, fill=fill, outline="")
    else:
        canvas.create_oval(pad, pad, size - pad, size - pad, fill="", outline=MUTED, width=1.5)
    if enabled:
        canvas.bind("<Button-1>", lambda _e: command())
    return canvas


def _darken(colour: str, amount: float) -> str:
    """Blends a hex colour toward black, for a chip's pressed/hover state.

    A fixed darken reads fine in either palette, unlike a fixed lighten —
    which would wash out a colour that's already light in the light palette.
    """
    value = colour.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    mix = lambda c: round(c * (1 - amount))
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


def _round_rect(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float, radius: float, fill: str) -> None:
    """Fills a rounded rectangle: a plain cross of two rects, plus a filled
    circle inset into each corner to round it off.

    Simpler than a smoothed polygon, and the radii this app uses are small
    enough (a handful of pixels) that the facets aren't visible.
    """
    radius = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
    canvas.create_rectangle(x0 + radius, y0, x1 - radius, y1, fill=fill, outline="")
    canvas.create_rectangle(x0, y0 + radius, x1, y1 - radius, fill=fill, outline="")
    for cx, cy in (
        (x0 + radius, y0 + radius),
        (x1 - radius, y0 + radius),
        (x0 + radius, y1 - radius),
        (x1 - radius, y1 - radius),
    ):
        canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill=fill, outline="")


def chip(
    parent: tk.Misc,
    command: Callable[[], None],
    *,
    fill: str,
    image: "tk.PhotoImage | None" = None,
    text: str = "",
    text_fill: str | None = None,
    size: int = 26,
    width: int | None = None,
    height: int | None = None,
    radius: int = 7,
    bg: str | None = None,
    enabled: bool = True,
    font: tuple = FONT_ICON_SMALL,
    circle: bool = False,
) -> tk.Canvas:
    """A small, slightly-rounded button, drawn rather than native.

    Plain ``tk.Button`` has no corner radius — nothing in this app curves
    except the collapsed badge, and that's a whole OS window rounded by DWM,
    which doesn't reach a widget living inside one. This is for the handful
    of spots meant to read as a deliberate, weightier action — confirm/deny,
    delete, the hour nudges — where a flat rectangle undersells the click.

    ``circle=True`` swaps the filled, rounded-corner backdrop (``radius`` is
    then ignored) for a plain ring traced around the canvas's own edge —
    for a chip whose icon (the hour nudges' ``circle-plus``/``circle-minus``)
    already draws its own circle, where a squared-off hover fill reads as a
    mismatched, slightly wobbly box behind a round glyph rather than a single
    coherent shape.

    Square by default (``size`` for both sides); pass ``width``/``height``
    instead for a wider one — a row of these sized as a fraction of their
    parent, say, which also needs the shape to repaint on every resize, not
    just at creation. That repaint runs off the canvas's own live size
    rather than a fixed number either way, so a caller that reconfigures
    ``width`` later (``canvas.config(width=...)``) gets a widget that tracks
    it, square or not.

    ``enabled=False`` keeps the chip present but inert — used where a row
    has nothing to act on yet, so the control sits in its usual spot (nothing
    jumps into existence the moment there's something to delete) without
    inviting a click that would have nowhere to go.
    """
    canvas = tk.Canvas(
        parent, width=width or size, height=height or size, bg=bg or BG,
        highlightthickness=0, bd=0, cursor="hand2" if enabled else "arrow",
    )
    hover = _darken(fill, 0.16)
    #: The last colour asked for and the text currently showing, so a
    #: resize-triggered repaint (which knows neither) still comes out right
    #: rather than resetting to ``fill`` or the text first passed in.
    state = {"colour": fill, "text": text}

    def paint(colour: str | None = None) -> None:
        state["colour"] = colour or state["colour"]
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w <= 1 or h <= 1:  # not yet laid out — fall back to the ask
            w, h = width or size, height or size
        canvas.delete("all")
        if circle:
            canvas.create_oval(1, 1, w - 1, h - 1, outline=state["colour"], width=2)
        else:
            _round_rect(canvas, 1, 1, w - 1, h - 1, radius, state["colour"])
        if image is not None:
            canvas.create_image(w / 2, h / 2, image=image)
        elif state["text"]:
            canvas.create_text(w / 2, h / 2, text=state["text"], fill=text_fill or TEXT, font=font)

    paint(fill)
    canvas.bind("<Configure>", lambda _e: paint())
    if enabled:
        canvas.bind("<Enter>", lambda _e: paint(hover))
        canvas.bind("<Leave>", lambda _e: paint(fill))
        canvas.bind("<Button-1>", lambda _e: command())
    # Keeps the PhotoImage referenced for the canvas's life — Tk drops an
    # image the moment nothing holds it, taking the picture off screen.
    canvas.image = image

    def set_text(new_text: str) -> None:
        state["text"] = new_text
        paint()

    #: For a chip whose label changes after creation — a date, say — rather
    #: than only ever being built once and torn down with its row.
    canvas.set_text = set_text
    return canvas


def confirm(
    parent: tk.Misc,
    message: str,
    on_confirm: Callable[[], None],
    *,
    confirm_label: str = "Delete",
) -> None:
    """A centered confirmation card over a dimmed backdrop, covering ``parent``.

    Nothing else in this app uses a native dialog (``tk.messagebox`` et al.)
    or a second OS window — either would break the single frameless look —
    so this is drawn the same way every other piece of chrome here is: a
    plain ``tk.Frame`` placed over the content rather than a ``Toplevel``.

    The backdrop is solid, not translucent: Tk has no per-widget alpha on
    Windows, only a whole window's (``wm_attributes("-alpha", ...)``), which
    doesn't apply to a frame living inside one already-open window.
    """
    scrim = tk.Frame(parent, bg="#000000")
    scrim.place(relx=0, rely=0, relwidth=1, relheight=1)

    def close() -> None:
        scrim.destroy()

    def confirm_and_close() -> None:
        scrim.destroy()
        on_confirm()

    card = frame(scrim, bg=BG_RAISED)
    card.place(relx=0.5, rely=0.42, anchor="center")

    label(
        card, message, bg=BG_RAISED, fg=TEXT, font=FONT,
        wraplength=240, justify="left",
    ).pack(padx=16, pady=(16, 12))

    row = frame(card, bg=BG_RAISED)
    row.pack(padx=16, pady=(0, 16), fill="x")

    confirm_btn = button(row, confirm_label, confirm_and_close, bg=DANGER, fg=ON_DANGER,
                          hover=_darken(DANGER, 0.16))
    confirm_btn.pack(side="left")
    button(row, "Cancel", close).pack(side="left", padx=(8, 0))

    # Clicking the dimmed backdrop itself (not the card or its buttons) reads
    # the same as Cancel — the usual way to back out of a modal without
    # hunting for the exact button.
    scrim.bind("<Button-1>", lambda _e: close())
    scrim.focus_set()
    scrim.bind("<Escape>", lambda _e: close())
