"""The overlay window: frameless, always-on-top, and draggable by its header."""

from __future__ import annotations

import colorsys
import ctypes
import json
import math
import os
import re
import time
import tkinter as tk
from ctypes import wintypes
from typing import Callable

from .. import anim, dwm, idle, launcher, monitors, paths, winfx
from ..dates import format_clock, format_decimal_hours
from ..hotkeys import HotkeyListener
from ..tray import TrayIcon
from ..store import Store
from . import charts, icons, theme, yielding

#: Default accelerator for each global hotkey, keyed by the stable action
#: name the Setup tab uses to let the user change or reset it. Overrides
#: live in ``Settings.hotkeys``; an action missing there just uses this.
DEFAULT_HOTKEYS = {
    "collapse": "ctrl+alt+t",
    "pin": "ctrl+alt+a",
    "timer": "ctrl+alt+s",
}

#: Tall enough for the Today and Week tabs without scrolling. Export and Setup
#: are longer than any sensible overlay, so they scroll regardless. This is
#: also "small", the first stop of the titlebar's size cycle, and the one the
#: window always reopens at regardless of which it was left on.
FULL_SIZE = (565, 500)
#: Smallest the expanded window may get. Relaxed while collapsed, or Tk would
#: refuse to shrink the window down to the badge. Wide enough that all seven
#: tabs stay on screen — ``pack(side="left")`` doesn't wrap, so a narrower
#: floor would let the last tab or two scroll off the right edge with no way
#: back to them short of widening the window again — and wide enough for the
#: Export tab's week grid (seven day columns plus task info and a total) to
#: draw without clipping, which needs more room than the tab bar does.
MIN_SIZE = (560, 260)

#: The titlebar's size button steps through these in order, wrapping back to
#: "small" after the last one. All three are a
#: fraction of whichever screen the window is currently on — width and height
#: independently, since neither "quarter" nor "half" means a quarter or half
#: of the screen's width *and* height together. An "eighth" mode (a quarter
#: width, half height) sat between them at first, but on a typical monitor it
#: lands within a few pixels of "small" — two stops that looked the same —
#: so it was dropped rather than kept as a second, redundant "small".
#: Titlebar size button's face, the same for every mode.
SIZE_GLYPH = "\u2750"
SIZE_MODES = ("small", "quarter", "half", "tall")
SIZE_FRACTIONS = {
    "small": (1 / 3, 0.5),   # a third of the width, half the height
    "quarter": (0.5, 0.5),   # half the width, half the height
    "half": (0.5, 1.0),      # half the width, the full height
    "tall": (1 / 3, 1.0),    # a third of the width, the full height
}

#: The collapsed badge: square, and small enough to park in a screen corner.
ICON_SIZE = (46, 46)
#: GetAncestor(GA_ROOT) — Tk's window id is a child of the real top-level one,
#: and that's the one the window manager knows about.
GA_ROOT = 2
#: Start/Pause/Stop's own square, clickable area — see ``_build_timerbar``.
#: Drawn with ``theme.chip`` rather than ``theme.button``: a native
#: ``tk.Button`` sized to fit one glyph still pads its own clickable box
#: well past the glyph actually drawn inside it, built as it is for a
#: word label, not a single icon — ``chip`` is the one widget in this app
#: already built the other way around, its own box sized first and the
#: glyph scaled to fill it, not the other way around.
TIMER_BUTTON_SIZE = 32
#: Sized to actually fill ``TIMER_BUTTON_SIZE``'s own box — ``theme.chip``'s
#: own default (``FONT_ICON_SMALL``) is tuned for the much smaller hour
#: nudges, and would leave a disproportionately small glyph floating in
#: the middle of a box this size.
TIMER_BUTTON_FONT = ("Segoe UI", 16)
#: Gap left between the collapsed badge and the screen edge when snapped.
#: The expanded window docks flush (see ``monitors.MARGIN``) since a corner
#: is for getting it out of the way, but the badge is small and square
#: enough to read as a free-floating icon — like a desktop shortcut — rather
#: than chrome, so it sits a little off the edge instead of flush to it.
BADGE_MARGIN = 10
#: Stands in for the badge illustration if the PNGs haven't been generated. A
#: clock face from the Geometric Shapes block; the obvious candidates — the
#: stopwatch and alarm clock — are colour emoji on Windows and so ignore the
#: foreground colour, leaving a dark glyph on the dark palette.
ICON_GLYPH = "\u25f7"

#: How often to look for a monitor being plugged in, unplugged, or rearranged.
SCREEN_POLL_MS = 3000

#: Internal keys stay put even where the label shown on the tab doesn't —
#: "Today" always reads as "Day", and "Setup" was repointed at the new
#: clients/projects view when the old Setup content became "Preferences" —
#: so every other reference to a tab (``_make_view``, ``_watch_screens``,
#: the smoke tests) never has to care which label is on screen.
TABS = ["Today", "Week", "Month", "Projects", "Recurring", "Setup", "Export"]
TAB_LABELS = {
    "Today": "Day",
    "Week": "Week",
    "Month": "Utilization",
    "Projects": "Setup",
    "Recurring": "Recurring",
    "Setup": "Preferences",
    "Export": "Export",
}
#: A vertical rule goes after this tab, setting the logging views (Day, Week,
#: Month) apart from the configuration ones that follow them.
DIVIDER_AFTER = "Month"


class Overlay:
    #: How long after the last store-triggered rebuild finished a
    #: foreground mismatch is still treated as possible fallout from it,
    #: rather than something to start confirming towards a real collapse
    #: — see ``_collapse_if_focus_left``.
    _REBUILD_GRACE = 0.05

    def __init__(self) -> None:
        # Before any window exists: Windows only reads this once, and uses
        # it for everything from here on that would otherwise describe the
        # process as "Python" — Task Manager's app list chief among them,
        # since the overlay's toolwindow styling gives it no ordinary
        # taskbar window to read a title and icon off instead.
        launcher.claim_app_identity()

        self.store = Store(paths.data_file())
        self.store.load()

        theme.use(self.store.db.settings.theme)

        self.root = tk.Tk()
        self.root.title("Timesheet Logger")
        self.root.configure(bg=theme.BG)
        self.root.minsize(*MIN_SIZE)
        self._set_window_icon()

        self.collapsed = False
        #: Where the titlebar's size button is in ``SIZE_MODES``. Saved with
        #: the window state and resolved against the current screen on launch
        #: (see ``_restore_geometry``), so each mode is the right proportion
        #: from the start rather than only after being cycled through.
        self._size_mode = "small"
        self._resize_queued = False
        #: Tracked rather than read back from Tk: ``winfo_width`` reports 1
        #: until the window is mapped, and the stale size until a resize has
        #: been processed, both of which would snap to the wrong coordinate.
        self._size = FULL_SIZE
        self._drag_origin: tuple[int, int] | None = None
        self._views: dict[str, object] = {}
        self._refresh_pending = False
        #: Whether a store-triggered rebuild (``_rebuild_active_view``) is
        #: on the stack right now, and when the last one finished — see
        #: ``_collapse_if_focus_left`` for why either keeps that check from
        #: trusting a foreground reading yet.
        self._rebuilding = False
        self._last_rebuild_at = 0.0
        self._screen_layout: tuple = ()
        self.active_tab = "Today"

        self._build()
        self._restore_geometry()
        self._apply_window_settings()

        self.store.subscribe(self.refresh)
        self._start_hotkeys()
        self._start_tray()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Escape>", lambda _e: self.collapse(True))
        self.root.bind_all("<FocusOut>", self._on_focus_out, add="+")
        # Every press inside any of this app's own windows, timestamped:
        # ``_collapse_if_focus_left`` never reads a focus change as "clicked
        # outside" while a press of ours is this recent (see there).
        self._last_press_at = 0.0
        self.root.bind_all("<ButtonPress>", self._note_press, add="+")
        self._bind_undo_redo_keys()

        self.show_tab("Today")
        self.refresh()  # a timer may already be running from a previous session
        self._tick()
        self._pump()
        yielding.install(self._checkpoint)
        self._watch_screens()
        self._watch_theme()
        self._watch_foreground()

    def _set_window_icon(self) -> None:
        """Gives the real window the same logo as the tray and the desktop
        shortcut — ``launcher.icon()``, the one ``scripts/build_icons.py``
        packs — rather than Tk's own feather, which is what Alt-Tab and the
        taskbar thumbnail would otherwise show instead.

        Best-effort, same as the tray icon falling back to the stock one: a
        window missing its icon is a cosmetic slip, not a reason to crash.
        """
        art = launcher.icon()
        if art.exists():
            try:
                self.root.iconbitmap(default=str(art))
            except tk.TclError:
                pass

    def _bind_undo_redo_keys(self) -> None:
        """Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y, the usual trio, work anywhere in
        the window rather than needing a particular tab or widget focused.

        Skipped while an Entry or Text has focus: neither has its own undo
        wired up, so there's nothing for this to collide with mid-edit, but
        jumping the whole day's data back while someone is mid-sentence in a
        note would be a strange thing for a keystroke they typed into that
        note to do.
        """

        def guarded(action) -> Callable[[tk.Event], str | None]:
            def handler(_event: tk.Event) -> str | None:
                if isinstance(self.root.focus_get(), (tk.Entry, tk.Text)):
                    return None
                action()
                return "break"

            return handler

        self.root.bind_all("<Control-z>", guarded(self.store.undo))
        self.root.bind_all("<Control-y>", guarded(self.store.redo))
        self.root.bind_all("<Control-Shift-z>", guarded(self.store.redo))

    # -- construction ----------------------------------------------------

    def _build(self) -> None:
        self.shell = theme.frame(self.root)
        self.shell.pack(fill="both", expand=True)

        self._build_titlebar()
        self._build_timerbar()
        self._build_tabs()

        self.content = theme.frame(self.shell)
        self.content.pack(fill="both", expand=True)

        self._build_icon()

    def _build_titlebar(self) -> None:
        bar = theme.frame(self.shell, bg=theme.BG_RAISED)
        bar.pack(fill="x")
        self.titlebar = bar

        title = theme.label(bar, "TIMESHEET", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_HEAD)
        title.pack(side="left", padx=(10, 6), pady=3)

        for widget in (bar, title):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", lambda _e: self._save_geometry())

        # Kept small on purpose: the glyph buttons (size, pin) set the bar's
        # height, since their font's line height is what the bar grows to.
        chrome = dict(bg=theme.BG_RAISED, fg=theme.MUTED, font=("Segoe UI", 10), padx=6, pady=0)
        #: Even gap between every titlebar button, including the first one in
        #: from the edge — packed side="right", they'd otherwise sit flush
        #: against each other with nothing to tell one from the next.
        GAP = (4, 4)

        # Left of the label rather than crowding the window-chrome cluster on
        # the right: there's slack here even at the narrowest width, and
        # Undo/Redo are acted on far less often than pin/size/collapse.
        small_chrome = dict(chrome, font=theme.FONT_ICON_SMALL)
        self.undo_btn = icons.button(
            bar, "undo", self.store.undo, fallback="\u21b6", **small_chrome
        )
        self.undo_btn.pack(side="left", padx=(8, 0))

        self.redo_btn = icons.button(
            bar, "redo", self.store.redo, fallback="\u21b7", **small_chrome
        )
        self.redo_btn.pack(side="left", padx=(2, 0))

        self.collapse_btn = icons.button(
            bar, "collapse", lambda: self.collapse(True), fallback="\u2013", **chrome
        )
        self.collapse_btn.pack(side="right", padx=(4, 8))

        # The same glyph at every size: it only says "change the size", and
        # the window itself shows which size it's now at.
        self.size_btn = theme.button(bar, SIZE_GLYPH, self.cycle_size, **chrome)
        self.size_btn.pack(side="right", padx=GAP)

        # A plain glyph, not ``icons.button``'s generated PNG — this icon
        # set has no thumbtack of its own (see ``scripts/build_icons.py``'s
        # own note on "pin"/"pin-on", an anchor standing in for one), and
        # the pin emoji reads as "pinned" more directly than that anchor
        # ever did.
        self.pin_btn = theme.button(
            bar, "\U0001f4cc", self.toggle_always_on_top, **chrome
        )
        self.pin_btn.pack(side="right", padx=GAP)

        theme.hairline(self.shell).pack(fill="x")

    def _build_timerbar(self) -> None:
        self.timerbar = theme.frame(self.shell, bg=theme.BG_RAISED)
        # A footer: packed from the bottom, ahead of ``content``, so the
        # content gets whatever is left between the tabs and this bar.
        self.timerbar.pack(side="bottom", fill="x")

        self.timer_dot = tk.Canvas(
            self.timerbar, width=13, height=13, bg=theme.BG_RAISED, highlightthickness=0, bd=0
        )
        self.timer_dot.pack(side="left", padx=(10, 7), pady=8)

        middle = theme.frame(self.timerbar, bg=theme.BG_RAISED)
        middle.pack(side="left", fill="x", expand=True)

        self.timer_title = theme.label(
            middle, "No timer running", bg=theme.BG_RAISED, fg=theme.MUTED
        )
        self.timer_title.pack(fill="x")

        # Used to be an editable note field here too — a second, duplicate
        # place to set the same note a row's own Day tab card already edits
        # (see ``today.py``'s own note field, which promotes a placeholder
        # straight to a real ``Entry`` on commit same as this would have),
        # and now that the title line above shows that same note once
        # there is one (``_row_timer_label``/``_running_title``), a second
        # editable copy of it here was one box too many rather than
        # anything this bar still needed of its own.

        #: Groups the Stop preview above the running clock below it — one
        #: ``side="right"`` slot at the ``timerbar`` level, so the pair
        #: reads as one piece beside Pause/Stop rather than the preview
        #: shifting the clock and buttons further left of where they've
        #: always sat.
        clock_box = theme.frame(self.timerbar, bg=theme.BG_RAISED)
        clock_box.pack(side="right", padx=(0, 8))

        #: ``timer_preview`` (this row's exact elapsed time) and
        #: ``timer_total`` (its own running total, base included) sit side
        #: by side in their own row, rather than ``timer_total`` stacked
        #: below everything else — the Day tab's own Actual Hours slot
        #: used to show this same live total ticking inside the entry
        #: card itself (see ``today.py``'s own ``_day_row``); it's locked
        #: there now, showing just the last-committed figure, and this is
        #: where that live comparison lives instead.
        preview_row = theme.frame(clock_box, bg=theme.BG_RAISED)
        preview_row.pack()

        #: The exact elapsed time in decimal hours — bare, same shape as
        #: the collapsed badge's own readout (``icon_timer_hours``), not a
        #: sentence; the two are the same timer, so they read as one.
        self.timer_preview = theme.label(
            preview_row, "", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
        )
        self.timer_preview.pack(side="left")

        #: This row's own running total — its last-committed figure plus
        #: the elapsed time just above, exact and unrounded the same way
        #: (see ``Store.timer_live_minutes``) — not what Stop would
        #: actually bank once rounded (``timer_add_preview``, below).
        self.timer_total = theme.label(
            preview_row, "", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
        )
        self.timer_total.pack(side="left")

        self.timer_clock = theme.label(
            clock_box, "", bg=theme.BG_RAISED, fg=theme.TEXT, font=theme.FONT_CLOCK
        )
        self.timer_clock.pack()

        #: What Stop would actually bank right now — rounded to the
        #: duration pickers' own step (``Settings.entry_increment_minutes``),
        #: same rounding ``Store.stop_timer`` itself applies — and what the
        #: row's own total becomes once it does. A preview, not a
        #: commitment; nothing is saved until Stop is actually pressed.
        self.timer_add_preview = theme.label(
            clock_box, "", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
        )
        self.timer_add_preview.pack()

        #: ``theme.chip`` (see ``TIMER_BUTTON_SIZE``), not ``theme.button``
        #: — a square clickable area sized to the glyph instead of padded
        #: generously around a word that isn't there any more. ``fill``
        #: matches the bar's own background so it reads as flat until
        #: hovered (``chip`` darkens it automatically then); the colour
        #: that actually says what each one does lives on ``text_fill``
        #: (the glyph itself) instead.
        self.timer_stop = theme.chip(
            self.timerbar, self.store.stop_timer,
            fill=theme.BG_RAISED, bg=theme.BG_RAISED, text="\u23f9", text_fill=theme.BILLABLE,
            size=TIMER_BUTTON_SIZE, font=TIMER_BUTTON_FONT,
        )

        #: Glyph (and its colour) swap between \u23f8/``MUTED`` (pause) and
        #: \u25b6/``ACCENT`` (resume) in ``_refresh_timerbar`` — see
        #: ``theme.chip``'s own ``set_text``/``set_enabled`` — rather than
        #: two separate chips, only one of which is ever the right action
        #: for whichever state the timer's actually in.
        self.timer_pause = theme.chip(
            self.timerbar, self._toggle_timer_pause,
            fill=theme.BG_RAISED, bg=theme.BG_RAISED, text="\u23f8", text_fill=theme.MUTED,
            size=TIMER_BUTTON_SIZE, font=TIMER_BUTTON_FONT,
        )

        self.timer_start = theme.chip(
            self.timerbar, self.start_selected_timer,
            fill=theme.BG_RAISED, bg=theme.BG_RAISED, text="\u25b6", text_fill=theme.ACCENT,
            size=TIMER_BUTTON_SIZE, font=TIMER_BUTTON_FONT,
        )

        #: Kept so ``_refresh_timerbar`` can hide the bar entirely (nothing
        #: running, nothing armed) and bring both it and this divider back
        #: in the right spot later, rather than one reappearing without the
        #: other.
        self.timerbar_hairline = theme.hairline(self.shell)
        self.timerbar_hairline.pack(side="bottom", fill="x")  # above the footer

    def _build_tabs(self) -> None:
        bar = theme.frame(self.shell, bg=theme.BG_RAISED)
        bar.pack(fill="x")
        #: The anchor ``_refresh_timerbar`` re-inserts the timer bar and its
        #: hairline ahead of, once hidden — plain ``pack`` would otherwise
        #: drop them at the end of ``shell``'s packing order, behind
        #: ``content``.
        self.tabbar = bar
        self.tab_buttons: dict[str, tk.Button] = {}
        #: The three logging views — "Day", "Week", "Month" — read as one
        #: group of equal-weight choices, so each gets the widest one's
        #: width rather than "Day" sitting narrower than its neighbours
        #: just because it's the shortest word.
        EQUAL_WIDTH = {"Today", "Week"}
        #: The configuration tabs — Setup, Recurring, Preferences, Export —
        #: pack to the bar's own right edge instead of trailing straight
        #: after Month, so the two groups (what you log vs. how it's set up)
        #: read as distinct clusters with the window's own slack between
        #: them, not just one run-on row of seven.
        RIGHT_ALIGNED = {"Projects", "Recurring", "Setup", "Export"}
        columns: dict[str, tk.Frame] = {}
        self._tab_columns = columns

        def build_column(name: str) -> tk.Frame:
            column = theme.frame(bar, bg=theme.BG_RAISED)
            btn = theme.button(
                column, TAB_LABELS.get(name, name), lambda n=name: self.navigate(n),
                bg=theme.BG_RAISED, fg=theme.MUTED, padx=11, pady=3,
                hover=theme.BG_RAISED,
            )
            # Hovering lights up the label only; the button's own
            # background never changes.
            btn.bind("<Enter>", lambda _e, n=name, b=btn: b.config(fg=theme.TEXT))
            btn.bind(
                "<Leave>",
                lambda _e, n=name, b=btn: b.config(
                    fg=theme.TEXT if self.active_tab == n else theme.MUTED
                ),
            )
            btn.pack(fill="x")
            self.tab_buttons[name] = btn
            columns[name] = column
            return column

        for name in TABS:
            if name in RIGHT_ALIGNED:
                continue
            build_column(name).pack(side="left")
            if name == DIVIDER_AFTER:
                theme.vrule(bar).pack(side="left", fill="y", padx=6, pady=6)

        # Packed in reverse so the group still reads left-to-right (Setup,
        # Recurring, Preferences, Export) ending flush with the bar's right
        # edge — the first ``side="right"`` pack claims that outer edge, and
        # each one after it lands just inside the last, not past it.
        for name in reversed([n for n in TABS if n in RIGHT_ALIGNED]):
            build_column(name).pack(side="right")

        theme.hairline(self.shell).pack(fill="x")

        # Measured after every button exists, not guessed from the text
        # alone — padding, font metrics and the pixel grid all factor into
        # ``winfo_reqwidth`` in a way a character count wouldn't match.
        bar.update_idletasks()
        width = max(columns[name].winfo_reqwidth() for name in EQUAL_WIDTH)
        height = max(columns[name].winfo_reqheight() for name in EQUAL_WIDTH)
        for name in EQUAL_WIDTH:
            columns[name].pack_propagate(False)
            columns[name].config(width=width, height=height)

        self._build_pill(bar)
        self._attach_flyout(columns["Month"])

    # -- the sliding tab pill ----------------------------------------------

    PILL_MS = 190
    PILL_INSET = 3
    PILL_HEIGHT = 28
    #: Pixels the label inside the pill is lifted, so it lines up with the
    #: unselected tab labels (a Tk button sets its text a touch higher than
    #: a canvas centres it).
    PILL_TEXT_RISE = 2
    #: How long the label takes to fade back in once the pill has landed.
    PILL_FADE_MS = 150

    def _build_pill(self, bar: tk.Misc) -> None:
        """The selected tab's pill. One widget laid over the tab row that
        glides to whichever tab is chosen. Re-aimed from wherever it is, so
        clicking tabs in quick succession turns it round mid-way rather than
        waiting or jumping. It carries the label of the tab it is currently
        over (the buttons beneath are opaque), so the text changes hands as
        it passes."""
        pill = tk.Canvas(bar, bg=theme.BG_RAISED, highlightthickness=0, bd=0, cursor="hand2")
        self._pill = pill
        self._pill_images: dict = {}
        self._pill_state = {
            "x": None, "w": None, "from": None, "to": None,
            "start": 0.0, "job": None, "size": None, "text": None,
            #: The label's current colour: the pill's own colour while it
            #: is moving (invisible), fading to ``ON_ACCENT`` on arrival.
            "fill": theme.ON_ACCENT, "fade": None,
        }
        pill.bind("<Button-1>", lambda _e: self._pill_click())
        # The bar's own <Configure> arrives before pack has moved the tabs
        # (the right-hand group shifts whenever the window changes width), so
        # the pill also follows each tab's own move and resize.
        bar.bind("<Configure>", lambda _e: self.root.after_idle(self._pill_refit), add="+")
        for column in self._tab_columns.values():
            column.bind("<Configure>", lambda _e: self._pill_refit(), add="+")
        self.root.after_idle(self._pill_refit)

    def _pill_click(self) -> None:
        name = self._pill_tab_at(self._pill_state["x"], self._pill_state["w"])
        if name and name != self.active_tab:
            self.navigate(name)

    def _tab_rect(self, name: str) -> tuple[float, float] | None:
        column = self._tab_columns.get(name)
        if column is None or column.winfo_width() <= 1:
            return None
        inset = self.PILL_INSET
        return column.winfo_x() + inset, max(8, column.winfo_width() - 2 * inset)

    def _pill_tab_at(self, x, w) -> str | None:
        """The tab whose centre is nearest the pill's."""
        if x is None:
            return None
        centre = x + w / 2
        best, best_gap = None, 1e9
        for name, column in self._tab_columns.items():
            gap = abs(column.winfo_x() + column.winfo_width() / 2 - centre)
            if gap < best_gap:
                best, best_gap = name, gap
        return best

    def _pill_paint(self, x: float, w: float) -> None:
        state = self._pill_state
        width = max(8, round(w))
        bar_height = self.tabbar.winfo_height()
        # Same parity as the bar, so the pill sits on its exact middle.
        height = self.PILL_HEIGHT + ((bar_height - self.PILL_HEIGHT) % 2 if bar_height > 1 else 0)
        top = max(0, (bar_height - height) // 2) if bar_height > 1 else self.PILL_INSET
        self._pill.place(x=round(x), y=top, width=width, height=height)
        tk.Misc.lift(self._pill)  # Canvas.lift would raise a canvas item
        name = self._pill_tab_at(x, w) or self.active_tab
        text = TAB_LABELS.get(name, name)
        if state["size"] != (width, height):
            key = (width, height, theme.ACCENT)
            image = self._pill_images.get(key)
            if image is None:
                raster = charts.Raster(width, height, theme.BG_RAISED)
                raster.rrect(0, 0, width, height, theme.ACCENT, height / 2, height / 2)
                image = raster.photo(self._pill)
                if len(self._pill_images) > 80:
                    self._pill_images.clear()
                self._pill_images[key] = image
            self._pill.delete("all")
            self._pill.create_image(0, 0, anchor="nw", image=image)
            self._pill.create_text(
                width / 2, height / 2 - self.PILL_TEXT_RISE, text=text, fill=state["fill"],
                font=theme.FONT, tags="label",
            )
            state["size"], state["text"] = (width, height), text
        elif state["text"] != text:
            self._pill.itemconfigure("label", text=text)
            state["text"] = text
        state["x"], state["w"] = x, w

    def _pill_set_fill(self, colour: str) -> None:
        self._pill_state["fill"] = colour
        self._pill.itemconfigure("label", fill=colour)

    def _pill_fade_in(self) -> None:
        """Brings the label back from the pill's colour to its own."""
        state = self._pill_state
        state["fade"] = time.perf_counter()

        def step() -> None:
            started = state["fade"]
            if started is None:
                return
            t = min(1.0, (time.perf_counter() - started) * 1000 / self.PILL_FADE_MS)
            self._pill_set_fill(theme.blend(theme.ACCENT, theme.ON_ACCENT, anim.smoothstep(t)))
            if t < 1.0:
                state["fade_job"] = self.root.after(16, step)
            else:
                state["fade"] = state["fade_job"] = None

        step()

    def _pill_stop_fade(self) -> None:
        state = self._pill_state
        state["fade"] = None
        job = state.get("fade_job")
        if job:
            self.root.after_cancel(job)
            state["fade_job"] = None

    def _pill_refit(self, tries: int = 0) -> None:
        """Snaps (or, mid-slide, re-aims) the pill at the active tab after
        the bar has been laid out or resized."""
        if not hasattr(self, "_pill_state"):
            return
        rect = self._tab_rect(self.active_tab)
        if rect is None:
            # Not laid out yet (the window is still being mapped): try again
            # shortly, for a second or so.
            if tries < 20:
                self.root.after(50, lambda: self._pill_refit(tries + 1))
            return
        state = self._pill_state
        if state["job"] is not None:
            state["to"] = rect
            return
        self._pill_paint(*rect)

    def _slide_pill(self, name: str) -> None:
        """Aims the pill at ``name``, starting from wherever it is now."""
        if not hasattr(self, "_pill_state"):
            return
        rect = self._tab_rect(name)
        if rect is None:
            return
        state = self._pill_state
        if state["x"] is None or self.collapsed:
            self._pill_paint(*rect)
            return
        if state["job"] is not None:
            self.root.after_cancel(state["job"])
            state["job"] = None
        if abs(rect[0] - state["x"]) < 0.5 and abs(rect[1] - state["w"]) < 0.5:
            if state["fill"] != theme.ON_ACCENT:
                self._pill_fade_in()
            return  # already there
        self._pill_stop_fade()
        self._pill_set_fill(theme.ACCENT)  # label hidden while it travels
        state["from"] = (state["x"], state["w"])
        state["to"] = rect
        state["start"] = time.perf_counter()
        self._pill_step()

    def _pill_step(self) -> None:
        state = self._pill_state
        t = min(1.0, (time.perf_counter() - state["start"]) * 1000 / self.PILL_MS)
        e = anim.ease_out_cubic(t)
        (x0, w0), (x1, w1) = state["from"], state["to"]
        self._pill_paint(anim.lerp(x0, x1, e), anim.lerp(w0, w1, e))
        if t < 1.0:
            state["job"] = self.root.after(8, self._pill_step)
        else:
            state["job"] = None
            self._pill_fade_in()

    #: The Utilization tab's flyout: ``(scale, label)`` for each way of
    #: looking at it.
    FLYOUT_ITEMS = (("Month", "Month"), ("Quarter", "Quarter"), ("Year", "Year"))

    def _attach_flyout(self, column: tk.Misc) -> None:
        """Hovering the Utilization tab drops a small menu beneath it with
        Month, Quarter and Year; it goes again once the pointer is off both
        the tab and the menu."""
        self._flyout: tk.Toplevel | None = None
        self._flyout_job: str | None = None
        self._flyout_column = column

        def inside(widget, ancestor) -> bool:
            while widget is not None:
                if widget is ancestor:
                    return True
                widget = getattr(widget, "master", None)
            return False

        def pointer_inside() -> bool:
            try:
                under = self.root.winfo_containing(*self.root.winfo_pointerxy())
            except (tk.TclError, KeyError):
                return False
            return inside(under, column) or (
                under is self._pill and self.active_tab == "Month"
            ) or (
                self._flyout is not None and inside(under, self._flyout)
            )

        def check() -> None:
            self._flyout_job = None
            if not pointer_inside():
                self._close_flyout()

        def leave(_event=None) -> None:
            if self._flyout_job:
                self.root.after_cancel(self._flyout_job)
            self._flyout_job = self.root.after(160, check)

        def enter(_event=None) -> None:
            if self._flyout_job:
                self.root.after_cancel(self._flyout_job)
                self._flyout_job = None
            if self._flyout is None and not self.collapsed and not getattr(self, "_morphing", False):
                self._open_flyout(leave)

        for widget in (column, *column.winfo_children()):
            widget.bind("<Enter>", enter, add="+")
            widget.bind("<Leave>", leave, add="+")
        # The pill covers the active tab, so hovering it counts as hovering
        # Utilization whenever that is the tab it is on.
        self._pill.bind(
            "<Enter>", lambda e: enter(e) if self.active_tab == "Month" else None, add="+"
        )
        self._pill.bind("<Leave>", leave, add="+")

    def _open_flyout(self, leave) -> None:
        column = self._flyout_column
        fly = tk.Toplevel(self.root)
        fly.withdraw()
        fly.overrideredirect(True)
        fly.attributes("-topmost", True)
        fly.config(bg=theme.LINE)
        current = getattr(self._views.get("Month"), "scale", "Month")
        active = self.active_tab == "Month"
        inner = tk.Frame(fly, bg=theme.BG_RAISED)
        inner.pack(padx=1, pady=1)
        for scale, label in self.FLYOUT_ITEMS:
            chosen = active and scale == current
            item = tk.Label(
                inner, text=label, anchor="w", padx=14, pady=6, cursor="hand2",
                bg=theme.BG_RAISED, fg=theme.ACCENT if chosen else theme.TEXT,
                font=theme.FONT_BOLD if chosen else theme.FONT_SMALL,
            )
            item.pack(fill="x")
            item.bind("<Enter>", lambda _e, w=item: w.config(bg=theme.BG_INPUT))
            item.bind("<Leave>", lambda _e, w=item: w.config(bg=theme.BG_RAISED))
            item.bind("<Button-1>", lambda _e, s=scale: self.open_utilization(s))
        for widget in (fly, inner, *inner.winfo_children()):
            widget.bind("<Enter>", lambda _e: self._flyout_job and (
                self.root.after_cancel(self._flyout_job), setattr(self, "_flyout_job", None)
            ), add="+")
            widget.bind("<Leave>", leave, add="+")
        fly.update_idletasks()
        x = column.winfo_rootx()
        y = column.winfo_rooty() + column.winfo_height()
        width = max(fly.winfo_reqwidth(), column.winfo_width())
        fly.geometry(f"{width}x{fly.winfo_reqheight()}+{x}+{y}")
        fly.deiconify()
        self._flyout = fly

    def _close_flyout(self) -> None:
        fly, self._flyout = getattr(self, "_flyout", None), None
        if fly is not None:
            try:
                fly.destroy()
            except tk.TclError:
                pass

    def open_utilization(self, scale: str) -> None:
        """Opens the Utilization tab on Month, Quarter or Year."""
        self._close_flyout()
        self._utilization_scale = scale
        self.navigate("Month")

    def _build_icon(self) -> None:
        """The collapsed view: a single badge, standing in for a tray icon.

        Too small for a close or expand button, so the whole badge is the
        control — double-click to expand, drag to move.
        """
        self.icon = theme.frame(self.root, bg=theme.BG_RAISED, highlightthickness=1)
        self.icon.config(highlightbackground=theme.LINE)
        #: Whether Windows rounded the badge off, which decides who draws the
        #: border. Settled on the first collapse.
        self._rounded = False

        #: The live readout beside the icon while a timer runs — see
        #: ``_refresh_badge_timer``. Decimal hours on top, the running
        #: mm:ss clock underneath it, the same pairing the expanded timer
        #: bar itself shows (``timer_preview`` above ``timer_clock``), so
        #: the collapsed badge reads as the same timer continuing, not a
        #: different, cut-down one of its own. Packed (claiming its own
        #: column on the right) only while actually showing something;
        #: packed *before* the glyph below so that column is reserved
        #: first, leaving the glyph the rest of the frame to fill rather
        #: than the other way around.
        self.icon_timer_box = theme.frame(self.icon, bg=theme.BG_RAISED)
        self.icon_timer_hours = theme.label(
            self.icon_timer_box, "", bg=theme.BG_RAISED, fg=theme.TEXT, font=theme.FONT_TINY,
        )
        self.icon_timer_hours.pack()
        self.icon_timer_clock = theme.label(
            self.icon_timer_box, "", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
        )
        self.icon_timer_clock.pack()

        self.icon_glyph = icons.label(
            self.icon,
            "badge",
            fallback=ICON_GLYPH,
            bg=theme.BG_RAISED,
            fg=theme.MUTED,
            font=theme.FONT_ICON_LARGE,
        )
        self.icon_glyph.pack(side="left", fill="both", expand=True)

        # The badge icon is a full-colour illustration, so unlike the rest of
        # the chrome it can't be tinted to show the timer running. A stroke
        # traced around the badge itself says it instead — see
        # ``_paint_badge_border`` — rather than a dot sitting in its corner;
        # Windows 11's own DWM border follows the badge's rounded corners
        # exactly the way a dot drawn on a square canvas never could, so the
        # colour reads as part of the badge's own outline rather than a
        # separate thing glued on top of it.
        for widget in (
            self.icon, self.icon_glyph,
            self.icon_timer_box, self.icon_timer_hours, self.icon_timer_clock,
        ):
            widget.bind("<Button-1>", self._badge_press)
            widget.bind("<B1-Motion>", self._badge_motion)
            widget.bind("<ButtonRelease-1>", self._badge_release)
            widget.bind("<Enter>", self._dock_enter, add="+")

    #: How far (pixels) the pointer may wander during a click on the badge
    #: before it counts as a drag rather than a click.
    BADGE_CLICK_SLOP = 4

    def _badge_press(self, event: tk.Event) -> None:
        self._badge_press_at = (event.x_root, event.y_root)
        self._badge_dragged = False
        self._drag_start(event)

    def _badge_motion(self, event: tk.Event) -> None:
        px, py = getattr(self, "_badge_press_at", (event.x_root, event.y_root))
        if abs(event.x_root - px) > self.BADGE_CLICK_SLOP or abs(event.y_root - py) > self.BADGE_CLICK_SLOP:
            self._badge_dragged = True
        self._drag_move(event)

    def _badge_release(self, _event: tk.Event) -> None:
        """A single click on the badge expands it; a drag just moves it."""
        if not getattr(self, "_badge_dragged", False) and getattr(self, "_badge_press_at", None):
            self._badge_press_at = None
            self.collapse(False)

    # -- edge docking -----------------------------------------------------
    #
    # Left alone for ``DOCK_DELAY_S`` the collapsed badge slides off the right
    # edge of its screen, leaving ``DOCK_PEEK`` pixels showing; hovering the
    # sliver slides it back to where it was. Only the window's position moves
    # (no resize, no re-layout), so a plain per-frame ``SetWindowPos`` is
    # smooth; the ring window rides along with it.

    DOCK_DELAY_S = 3.0
    DOCK_PEEK = 14
    DOCK_MS = 280
    DOCK_POLL_MS = 400

    _docked = False
    _dock_home: tuple | None = None
    _dock_anim = None
    _dock_cur = 0.0
    _dock_y = 0
    _dock_xy: tuple | None = None
    _dock_xy_until = 0.0
    _last_activity = 0.0

    def _badge_xy(self) -> tuple:
        """Where the badge is, as far as the ring should be concerned: the
        position we last set while docking (Tk's own idea of it lags a
        frame or two behind a ``SetWindowPos``)."""
        override = self._dock_xy
        if override and (self._docked or self._dock_anim is not None
                         or time.monotonic() < self._dock_xy_until):
            return override
        return self.root.winfo_x(), self.root.winfo_y()

    def _dock_enter(self, _event: tk.Event | None = None) -> None:
        self._last_activity = time.monotonic()
        if self._docked:
            self._dock_return()

    def _dock_watch(self) -> None:
        try:
            self._dock_check()
        except Exception:  # noqa: BLE001 - a docking hiccup must never stop the poll
            pass
        self.root.after(self.DOCK_POLL_MS, self._dock_watch)

    def _dock_check(self) -> None:
        now = time.monotonic()
        eligible = (
            self.collapsed and not getattr(self, "_morphing", False)
            and not self._docked and self._dock_anim is None
            and self.root.state() != "withdrawn"
            and getattr(self, "_badge_press_at", None) is None
        )
        if not eligible or self._pointer_in_window():
            self._last_activity = now
            return
        if now - self._last_activity >= self.DOCK_DELAY_S:
            self._dock()

    def _dock_target(self) -> tuple | None:
        x, y = self.root.winfo_x(), self.root.winfo_y()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        screens = self.screens()
        cx, cy = x + w // 2, y + h // 2
        screen = next(
            (s for s in screens if s.x <= cx < s.x + s.width and s.y <= cy < s.y + s.height),
            None,
        ) or monitors.find(screens, self.store.db.settings.snap_screen)
        edge = screen.x + screen.width
        scale = self._dpi_scale()
        # Plus the clip's safety margin (see ``_dock_clip``), so the sliver
        # that shows is still ``DOCK_PEEK`` wide.
        target = edge - max(6, round(self.DOCK_PEEK * scale)) - max(2, round(2 * scale))
        if x >= target:
            return None
        self._dock_edge = edge
        return x, y, target

    def _dock(self) -> None:
        found = self._dock_target()
        if found is None:
            self._last_activity = time.monotonic()
            return
        x, y, target = found
        self._dock_home = (x, y)
        self._dock_y = y
        self._dock_cur = float(x)
        self._docked = True
        self._dock_glide(target)

    def _dock_return(self) -> None:
        if self._dock_home is None:
            self._docked = False
            return
        self._docked = False
        self._dock_glide(self._dock_home[0])

    def _undock_now(self) -> None:
        """Back home at once, no animation — before anything that reads or
        sets the badge's position (expanding, re-snapping, resizing)."""
        job = self._dock_anim
        if job is not None:
            self.root.after_cancel(job)
            self._dock_anim = None
        if self._dock_home is not None:
            hx, hy = self._dock_home
            self._dock_place(hx, hy)
            self.root.geometry(f"+{hx}+{hy}")
            self._dock_home = None
            self.root.update()  # so ``winfo_x`` reports home before anyone reads it
        self._docked = False
        self._dock_xy = None
        self._last_activity = time.monotonic()

    def _dock_place(self, x: int, y: int) -> None:
        self._dock_xy = (x, y)
        self._dock_cur = float(x)
        flags = 0x0001 | 0x0004 | 0x0010  # NOSIZE | NOZORDER | NOACTIVATE
        user32 = ctypes.windll.user32
        user32.SetWindowPos(self._hwnd(), 0, x, y, 0, 0, flags)
        ring = getattr(self, "_ring", None)
        if ring is not None and ring.hwnd:
            user32.SetWindowPos(ring.hwnd, 0, x, y, 0, 0, flags)
        self._dock_clip(x)

    _dock_edge = 0
    _dock_clip_key: tuple | None = None

    def _dock_clip(self, x: int) -> None:
        """Shapes the badge (and its ring) to the part still on its own
        screen, so it is cut off cleanly at the screen's edge instead of
        reaching onto the next monitor; whole again once it is back home."""
        width, height = self.root.winfo_width(), self.root.winfo_height()
        # A couple of pixels short of the edge itself: with mixed-DPI screens
        # the edge is rounded in the conversion, and one stray pixel shows.
        margin = max(2, round(2 * self._dpi_scale()))
        visible = self._dock_edge - x - margin
        ring = getattr(self, "_ring", None)
        ring_hwnd = ring.hwnd if ring is not None else None
        key = (min(visible, width), ring_hwnd, width, height)
        if key == self._dock_clip_key:
            return
        self._dock_clip_key = key
        scale = self._dpi_scale()
        try:
            if visible >= width:  # (home: the margin only matters at the edge)
                self._badge_clip = None
                self._apply_badge_region()
                if ring_hwnd:
                    winfx.clear_region(ring_hwnd)
                return
            winfx.clip_visible(
                self._hwnd(), width, height, visible,
                round(self.BADGE_CORNER_RADIUS * scale), max(1, round(scale)),
            )
            if ring_hwnd:
                winfx.clip_visible(ring_hwnd, width, height, visible)
        except OSError:
            self._dock_clip_key = None

    def _dock_glide(self, target: int) -> None:
        """Slides to ``target`` x from wherever it is now (so it can be turned
        round mid-way), at a time proportional to the distance left."""
        if self._dock_anim is not None:
            self.root.after_cancel(self._dock_anim)
            self._dock_anim = None
        start = self._dock_cur
        span = abs(target - start)
        if span < 1:
            self._dock_done(int(target))
            return
        full = abs((self._dock_home or (0,))[0] - target) or span
        ms = max(90.0, self.DOCK_MS * min(1.0, span / max(full, 1)))
        self._dock_step(start, float(target), time.perf_counter(), ms)

    def _dock_step(self, start: float, target: float, t0: float, ms: float) -> None:
        t = min(1.0, (time.perf_counter() - t0) * 1000 / ms)
        e = t * t * (3 - 2 * t)
        x = round(start + (target - start) * e)
        self._dock_place(x, self._dock_y)
        if t < 1.0:
            self._dock_anim = self.root.after(
                8, lambda: self._dock_step(start, target, t0, ms)
            )
            return
        self._dock_anim = None
        self._dock_done(int(target))

    def _dock_done(self, x: int) -> None:
        # Let Tk know where the window really is.
        self.root.geometry(f"+{x}+{self._dock_y}")
        self._dock_xy_until = time.monotonic() + 0.4
        self._last_activity = time.monotonic()
        if not self._docked:
            self._dock_home = None

    # -- window behaviour -------------------------------------------------

    def _apply_window_settings(self) -> None:
        settings = self.store.db.settings
        self.root.overrideredirect(True)
        # Windows still gives an ``overrideredirect`` window its own taskbar
        # button by default — nothing about removing the titlebar/border
        # also hides it there. "Toolwindow" is the one ``wm_attributes`` flag
        # Tk exposes for this on Windows; the tray icon is this app's only
        # intended presence outside its own overlay and collapsed badge.
        self.root.attributes("-toolwindow", True)
        self._sync_topmost()
        self.root.attributes("-alpha", settings.opacity)
        self._show_pin_state(settings.always_on_top)
        if not self.collapsed:
            self._round_panel(self._hwnd())

    def _show_pin_state(self, pinned: bool) -> None:
        self.pin_btn.config(fg=theme.ACCENT if pinned else theme.MUTED)

    def toggle_always_on_top(self) -> None:
        # Pure window chrome — the pin button already updates itself and
        # ``_sync_topmost`` already applies the new state, so there's nothing
        # for a tab rebuild to add. Notifying anyway was why clicking the pin
        # looked like the whole app refreshing.
        value = not self.store.db.settings.always_on_top
        self.store.patch_settings(always_on_top=value, notify=False)
        self._sync_topmost()
        self._show_pin_state(value)

    def set_pin_when_collapsed(self, enabled: bool) -> None:
        self.store.patch_settings(pin_when_collapsed=enabled, notify=False)
        self._sync_topmost()

    def set_theme(self, name: str) -> None:
        self.store.patch_settings(theme=theme.use(name))
        # Deferred: this is called from a widget inside the tree it destroys.
        self.root.after_idle(self._rebuild_chrome)

    def _rebuild_chrome(self) -> None:
        """Rebuilds every widget against the current palette.

        Tk writes colours onto a widget when it is created and never looks at
        them again, so there is no way to repaint one in place.
        """
        self._views.clear()
        self.shell.destroy()
        self.icon.destroy()
        self.root.configure(bg=theme.BG)

        self._build()
        if self.collapsed:
            self.shell.pack_forget()
            self.icon.pack(fill="both", expand=True)
            self._round_badge()  # the badge it was applied to has been destroyed

        self._apply_window_settings()
        self.show_tab(self.active_tab)
        self.refresh()

    def set_opacity(self, value: float) -> None:
        # Applied and persisted directly rather than through patch_settings:
        # notifying listeners on every step of a slider drag would rebuild the
        # Setup view out from under the slider being dragged.
        value = max(0.3, min(1.0, value))
        self.store.db.settings.opacity = value
        self.store.save()
        self.root.attributes("-alpha", value)

    # -- screen snapping ---------------------------------------------------

    def screens(self) -> list[monitors.Monitor]:
        """Connected screens, primary first, never empty.

        Falls back to the one screen Tk can describe if Windows won't answer,
        so the rest of the snapping code never has to handle an empty list.
        """
        found = monitors.detect()
        return found or [
            monitors.Monitor(
                name="",
                x=0,
                y=0,
                width=self.root.winfo_screenwidth(),
                height=self.root.winfo_screenheight(),
                primary=True,
            )
        ]

    def set_snap(self, *, corner: str | None = None, screen: str | None = None) -> None:
        changes = {}
        if corner is not None:
            changes["snap_corner"] = corner
        if screen is not None:
            changes["snap_screen"] = screen
        self.store.patch_settings(**changes)
        self.apply_snap()

    def apply_snap(self) -> bool:
        """Moves the window to its chosen corner. False when set to free.

        Flush against the edge while expanded, a few pixels off it while
        collapsed to the badge — see ``BADGE_MARGIN``.
        """
        self._undock_now()
        settings = self.store.db.settings
        if settings.snap_corner not in monitors.CORNERS.values():
            return False
        screen = monitors.find(self.screens(), settings.snap_screen)
        margin = BADGE_MARGIN if self.collapsed else monitors.MARGIN
        x, y = monitors.corner_position(screen, settings.snap_corner, *self._size, margin=margin)
        self._planned_pos = (x, y)
        self.root.geometry(f"+{x}+{y}")
        return True

    def _watch_screens(self) -> None:
        """Re-snaps after a monitor is plugged in, unplugged, or rearranged."""
        layout = tuple((s.name, s.x, s.y, s.width, s.height) for s in self.screens())
        if layout != self._screen_layout:
            self._screen_layout = layout
            self.apply_snap()
            if self.active_tab == "Setup":
                self.refresh()  # the screen picker is now out of date
        self.root.after(SCREEN_POLL_MS, self._watch_screens)

    def _watch_theme(self) -> None:
        """Re-resolves "System" mode against Windows' own setting.

        Only ``theme.use`` itself knows to re-check the registry; nothing
        else calls it again on a running app, so a mode of "system" would
        otherwise stay on whichever palette happened to be live at launch
        even after the OS setting changed underneath it.
        """
        if theme.MODE == theme.SYSTEM and theme.system_palette() != theme.ACTIVE:
            theme.use(theme.MODE)
            self._rebuild_chrome()
        self.root.after(SCREEN_POLL_MS, self._watch_theme)

    # -- size ---------------------------------------------------------------

    def cycle_size(self) -> None:
        """Steps the window through ``SIZE_MODES``, wrapping back to small."""
        if getattr(self, "_morphing", False) or self._resize_queued or self.collapsed:
            return  # mid-animation: the press would fight the frame loop
        index = (SIZE_MODES.index(self._size_mode) + 1) % len(SIZE_MODES)
        self._size_mode = SIZE_MODES[index]
        width, height = self._size_for_mode(self._size_mode)
        # Not animated from inside the button's own command: that runs in the
        # middle of the click (the release is still being processed), and a
        # nested event loop there leaves Tk and Windows believing the mouse
        # button is still held — after which no click registers anywhere.
        # Run once the click has fully finished instead.
        self._resize_queued = True

        def go() -> None:
            self._resize_queued = False
            if not self.collapsed:
                self._resize_to(width, height)

        self.root.after(30, go)

    #: How long the window takes to glide to a new size.
    RESIZE_MS = 260

    def _resize_glide(self, src: tuple, dst: tuple) -> bool:
        """The resize animation, built the same way as expand/collapse
        (see ``_ghost_morph``): a layered ghost window — moved and scaled by
        the compositor, no Tk work and no repaint per frame — stands in for
        the window. It starts as a snapshot of the screen, dissolves into a
        plain panel rectangle, which then eases to the new rect while the
        real window waits, invisible and untouched, at its old size. Only
        once the rectangle has landed is the real window resized and
        laid out — once, under the ghost — and revealed; the ghost then
        fades away. A shield window swallows presses for the duration.
        Returns False, having changed nothing, if the ghost can't be made."""
        hwnd = self._hwnd()
        opacity = self.store.db.settings.opacity
        ghost = snap = panel = None
        try:
            snap = winfx.capture(hwnd, src[2], src[3])
            ghost = winfx.Ghost(
                max(src[2], dst[2]), max(src[3], dst[3]), click_through=False
            )
            panel = winfx.solid(theme.BG)
            border = self._panel_border()
            ghost.draw(src, snap, snap, 0.0, opacity, border=border)
        except OSError:
            for item in (ghost, snap, panel):
                if item is not None:
                    item.close()
            return False

        def vsync() -> None:
            try:
                ctypes.windll.dwmapi.DwmFlush()
            except (OSError, AttributeError):
                time.sleep(0.008)

        union_x, union_y = min(src[0], dst[0]), min(src[1], dst[1])
        self._raise_shield((
            union_x, union_y,
            max(src[0] + src[2], dst[0] + dst[2]) - union_x,
            max(src[1] + src[3], dst[1] + dst[3]) - union_y,
        ))
        self._morphing = True
        try:
            vsync()  # the ghost is on screen before the real window goes
            self.root.attributes("-alpha", 0.0)
            hold = 0.2  # contents dissolve into the plain panel, in place

            def frame(t: float) -> None:
                if t < hold:
                    ghost.draw(src, snap, panel, t / hold, opacity, border=border)
                    return
                e = anim.smoothstep((t - hold) / (1 - hold))
                ghost.draw(anim.lerp_rect(src, dst, e), panel, panel, 0.0, opacity, border=border)

            anim.run(self.RESIZE_MS * 1.25, frame, easing=lambda t: t)

            # Landed: the real window changes size and lays out, unseen.
            x, y, width, height = dst
            ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, width, height, 0x0004 | 0x0010)
            dwm.freeze(hwnd)
            try:
                self.root.geometry(f"{width}x{height}+{x}+{y}")
                self.root.minsize(*MIN_SIZE)
                self.apply_snap()
            finally:
                dwm.thaw(hwnd)
            self.root.update_idletasks()
            self._rebuild_active_view()
            self.root.update()
            vsync()
            self.root.attributes("-alpha", opacity)
            self.root.update_idletasks()
            vsync()
            self._drop_shield()
            ghost.set_click_through(True)
            fade_start = time.perf_counter()
            while True:
                t = (time.perf_counter() - fade_start) * 1000 / 120
                if t >= 1:
                    break
                ghost.draw(dst, panel, panel, 0.0, opacity * (1 - t), border=border)
                vsync()
        finally:
            for item in (ghost, snap, panel):
                item.close()
            self._drop_shield()
            self._morphing = False
        return True

    def _resize_to(self, width: int, height: int) -> None:
        """Glides the window to ``width`` x ``height``, staying in its corner.

        Like ``_morph``, nothing is re-laid-out per frame: the shell is
        laid out once, at the *final* size, and pinned there while the window
        itself is eased frame by frame with one ``SetWindowPos`` each, paced
        to the display. The window just reveals or clips that fixed layout,
        and the one real relayout happens as the animation lands.
        """
        hwnd = self._hwnd()
        user32 = ctypes.windll.user32
        src = (
            self.root.winfo_x(), self.root.winfo_y(),
            self.root.winfo_width(), self.root.winfo_height(),
        )
        settings = self.store.db.settings
        x, y = src[0], src[1]
        if settings.snap_corner in monitors.CORNERS.values():
            screen = monitors.find(self.screens(), settings.snap_screen)
            x, y = monitors.corner_position(
                screen, settings.snap_corner, width, height, margin=monitors.MARGIN
            )
        else:
            # Free-floating: a bigger window mustn't grow off the screen it's on.
            here = next(
                (s for s in self.screens()
                 if s.x <= x + src[2] // 2 < s.x + s.width and s.y <= y + src[3] // 2 < s.y + s.height),
                monitors.find(self.screens(), settings.snap_screen),
            )
            x = max(here.x, min(x, here.x + here.width - width))
            y = max(here.y, min(y, here.y + here.height - height))
        dst = (x, y, width, height)
        self._size = (width, height)
        if src == dst:
            self._rebuild_active_view()
            return

        if self._resize_glide(src, dst):
            self._save_geometry()
            self._finish_resize()
            return

        self._morphing = True
        self.root.minsize(1, 1)
        try:
            # Lay the content out for the destination up front (the Week grid
            # and friends size their columns to the width they have), then
            # hold it there for the whole animation.
            self.shell.pack_propagate(False)
            self.shell.configure(width=width, height=height)
            self.shell.pack_configure(fill="none", expand=False, anchor="nw")
            self.root.update()
            flags = 0x0004 | 0x0010  # SWP_NOZORDER | SWP_NOACTIVATE

            def put(px: int, py: int, pw: int, ph: int) -> None:
                user32.SetWindowPos(hwnd, 0, px, py, max(pw, 1), max(ph, 1), flags)
                self.root.update()

            anim.run_rect(src, dst, self.RESIZE_MS, put)
            # SetWindowPos went behind Tk's back: place for real first, or
            # ``geometry`` would treat the same string as a no-op.
            user32.SetWindowPos(hwnd, 0, x, y, width, height, flags)
            dwm.freeze(hwnd)
            try:
                self.root.geometry(f"{width}x{height}+{x}+{y}")
                self.shell.pack_configure(fill="both", expand=True, anchor="center")
                self.shell.pack_propagate(True)
                self.root.minsize(*MIN_SIZE)
                self.apply_snap()
            finally:
                dwm.thaw(hwnd)
            # Only once repainting is back on: flushing Tk's pending geometry
            # work while the window is frozen made Tk unmap its own window
            # (state "withdrawn"), leaving it visible but deaf to the mouse.
            self.root.update_idletasks()
        finally:
            self._morphing = False
        self._save_geometry()
        # The final layout at the settled size.
        self._rebuild_active_view()
        self._finish_resize()

    def _finish_resize(self) -> None:
        """Hands the window back to the user after a resize animation."""
        hwnd = self._hwnd()
        user32 = ctypes.windll.user32
        # The animation (SWP_NOACTIVATE moves, the repaint freeze, the
        # snapshot cover) leaves the window on screen but no longer the
        # active one, so clicks only activated it instead of landing, and a
        # click elsewhere had no focus loss to react to. Hand it back.
        self._last_press_at = time.monotonic()
        try:
            user32.ReleaseCapture()  # never leave the mouse captured by the button
            user32.SetForegroundWindow(hwnd)
        except OSError:
            pass
        self.root.focus_force()
        # Re-apply the "Always on top" choice from the saved setting, so a
        # window that isn't meant to be pinned is really not topmost again
        # after the resize (and one that is, still is) — and start watching
        # for a click outside afresh.
        self._sync_topmost()
        settings = self.store.db.settings
        pinned = settings.always_on_top
        self._show_pin_state(pinned)
        try:
            # Tk skips ``-topmost`` when it thinks nothing changed, so also
            # set the real z-order band directly (HWND_TOPMOST / NOTOPMOST,
            # no move, no resize, no activation).
            user32.SetWindowPos(hwnd, -1 if pinned else -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except OSError:
            pass
        self._seen_foreground = True

    def _size_for_mode(self, mode: str) -> tuple[int, int]:
        """The window size ``mode`` resolves to right now.

        Each mode is a fraction of whichever screen
        the window is currently snapped to (or the primary one, if free) —
        resolved fresh on every call rather than cached, so unplugging a
        monitor or moving between ones of different sizes is reflected the
        next time the button is pressed, same as snapping already does.
        """
        screen = monitors.find(self.screens(), self.store.db.settings.snap_screen)
        width_fraction, height_fraction = SIZE_FRACTIONS[mode]
        width = max(MIN_SIZE[0], int(screen.width * width_fraction))
        height = max(MIN_SIZE[1], int(screen.height * height_fraction))
        return width, height

    #: How long after a press inside the app a focus change is still put
    #: down to that press (a rebuild, a popup opening) rather than the user
    #: clicking away.
    _PRESS_GRACE = 0.8

    def _note_press(self, event: tk.Event) -> None:
        # Only a press that really landed on this app counts as "inside".
        # If Tk is still holding the mouse capture from an earlier click,
        # every click anywhere on screen is delivered to it too — and
        # counting those as presses of ours kept the grace period alive
        # forever, so a click outside never read as one.
        try:
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            inside = (
                x <= event.x_root < x + self.root.winfo_width()
                and y <= event.y_root < y + self.root.winfo_height()
            )
        except tk.TclError:
            inside = True
        if inside:
            self._last_press_at = time.monotonic()

    def _pointer_over_window(self) -> bool:
        """Whether the mouse pointer is within this window's rectangle."""
        point = wintypes.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return False
        rects = [(
            self.root.winfo_rootx(), self.root.winfo_rooty(),
            self.root.winfo_width(), self.root.winfo_height(),
        )]
        if time.monotonic() - getattr(self, "_expanded_at", -99.0) < 2.5 and not self.collapsed:
            # Just opened: Tk may not have caught up with the window's real
            # size yet (the panel is laid out after the animation), so the
            # rectangle it is opening to counts as well.
            rects.append((*self._planned_pos, *self._size))
        return any(x <= point.x < x + w and y <= point.y < y + h for x, y, w, h in rects)

    def _pointer_in_window(self) -> bool:
        """Whether the mouse pointer is over this window right now, with a
        button held — a click that is landing inside the app."""
        user32 = ctypes.windll.user32
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return False
        held = bool(user32.GetAsyncKeyState(0x01) & 0x8000) or bool(
            user32.GetAsyncKeyState(0x02) & 0x8000
        )
        x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        inside = (
            x <= point.x < x + self.root.winfo_width()
            and y <= point.y < y + self.root.winfo_height()
        )
        return held and inside

    #: How often the foreground window is checked while the panel is open.
    FOREGROUND_POLL_MS = 60

    def _watch_foreground(self) -> None:
        """A second, independent way to notice "clicked outside the app".

        ``<FocusOut>`` is the quick path, but it depends on Tk still holding
        keyboard focus somewhere, and after the window has been moved or
        resized behind Tk's back that can silently stop being true — then no
        event ever fires and the panel just sits there. The OS's own idea of
        the foreground window doesn't have that failure mode, so it is polled
        too: once the panel has been seen in front, any other program's
        window taking over is treated exactly as a focus-out would be.
        """
        try:
            if self.collapsed or getattr(self, "_morphing", False):
                self._seen_foreground = False
            else:
                user32 = ctypes.windll.user32
                foreground = user32.GetForegroundWindow()
                pid = ctypes.c_ulong(0)
                if foreground:
                    user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
                if foreground and pid.value == os.getpid():
                    self._seen_foreground = True
                elif foreground and getattr(self, "_seen_foreground", False):
                    # Stays armed: a check that is put off (a recent press,
                    # a rebuild settling) is simply tried again next tick.
                    self._collapse_if_focus_left(confirmed=False)
        except Exception:  # noqa: BLE001 - a poll must never die
            pass
        self.root.after(self.FOREGROUND_POLL_MS, self._watch_foreground)

    def _dbg(self, message: str) -> None:
        """Temporary diagnostics for the click-outside collapse: appends a
        line to ``collapse-debug.log`` next to the window state file."""
        try:
            file = paths.window_state_file().with_name("collapse-debug.log")
            with open(file, "a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%H:%M:%S')} {message}\n")
        except OSError:
            pass

    def _on_focus_out(self, _event: tk.Event) -> None:
        self._dbg(f"FocusOut widget={_event.widget}")
        """Clicking away from the app collapses it to the badge, same as
        Escape — ``bind_all``, not just ``self.root``'s own binding,
        because the widget that actually loses focus is whichever one had
        it, never the toplevel itself.

        Checked a beat later, not inline, and against the OS's own
        foreground window (see ``_collapse_if_focus_left``) rather than
        Tk's own ``focus_get()`` — every focus change anywhere fires this
        first, including entirely within the app (a popup opening, Tab
        between fields, or just clicking one of this app's own drawn
        ``Canvas`` chips, none of which take Tk keyboard focus the way an
        ``Entry`` does), and ``focus_get()`` would read every one of those
        as focus having left the app entirely, not landed on a control
        that just doesn't hold it.
        """
        self.root.after(1, lambda: self._collapse_if_focus_left(confirmed=False))

    def _collapse_if_focus_left(self, confirmed: bool) -> None:
        """Collapses to the badge once the OS's own foreground window
        belongs to a different process entirely — "clicked outside the
        app" — rather than anything read off Tk's own focus state (see
        ``_on_focus_out``). Every window this app owns, including every
        popup it draws itself (duration pickers, dropdowns), is still this
        same process, so none of them trip this; only another program's
        window taking the foreground does.

        Skipped while "Always on top" is on: the whole point of pinning it
        there is to keep working with other windows in front without the
        overlay getting out of the way on its own. Escape still collapses
        it either way — that's a separate binding this doesn't touch.

        A mismatch isn't acted on the first time it's seen (``confirmed``):
        clicking the +/- nudges fast enough rebuilds several rows' worth of
        widgets in quick succession (see ``today.py``'s ``_begin_light_
        refresh`` and its full-rebuild fallback), each one firing its own
        ``<FocusOut>`` as the focused widget is torn down — and Windows can
        hand the foreground to something else of its own accord for a
        moment under that much churn, with nothing the user actually did
        switching away. Checked again a beat later and only collapsed if
        it's still true then — long enough for a real switch to another
        window to hold, short enough that it's no more noticeable a delay
        than the original 50ms was.

        That fixed 200ms (50 then +150) window held for hammering one row
        repeatedly, but not for going back and forth across *several*
        rows: each one lands on a different widget, so a rebuild for row A
        can still be settling down right as row B's own rebuild starts
        tearing its focused widget down too — more total churn than one
        row clicked the same number of times, with no guarantee it's
        finished settling by a count of milliseconds picked for the
        single-row case. Rather than guess a bigger fixed number, this
        checks the one thing that actually matters — whether
        ``_rebuild_active_view`` is on the stack right now, or finished
        recently enough that another is plausibly still coming — and keeps
        deferring, unconfirmed, for as long as that's true, however many
        rebuilds that ends up being. Only once the app's own churn has
        actually gone quiet does a foreground mismatch get to start
        counting towards a real collapse.
        """
        if self.collapsed or self.store.db.settings.always_on_top:
            self._dbg(f"skip: collapsed={self.collapsed} always_on_top={self.store.db.settings.always_on_top}")
            return
        if getattr(self, "_morphing", False) or getattr(self, "_resize_queued", False):
            # Mid-animation the window is hidden or in motion, so any focus
            # change is the animation's own. Never queued as a collapse: it
            # would run the moment the expand landed, collapsing a window
            # the user had only just opened.
            self._dbg("skip: animating")
            return
        if self._rebuilding or time.monotonic() - self._last_rebuild_at < self._REBUILD_GRACE:
            self._dbg("defer: rebuilding/grace")
            self.root.after(50, lambda: self._collapse_if_focus_left(confirmed=False))
            return
        user32 = ctypes.windll.user32
        foreground = user32.GetForegroundWindow()
        if not foreground:
            # No window currently owns the foreground at all — a momentary
            # gap, not a deliberate switch to another one.
            self._dbg("skip: no foreground window")
            return
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
        if pid.value == os.getpid():
            self._dbg("skip: foreground is this app")
            return
        # Focus moved to another program's window, but that's no proof the
        # user clicked outside: a click *inside* can do it too — the window
        # is momentarily without the foreground while a rebuild or cover
        # swaps things under it — and that read as "clicked away", collapsing
        # the app on a click that never left it. Not while a press of ours
        # is recent, nor while a button is down over the window.
        # Clicking away means clicking *outside* the screen. While the pointer
        # is over the screen itself, a focus change is never one — it is the
        # window re-activating around a click that landed on it (a rebuild,
        # an expand just finishing) — so it never collapses it. Disarmed
        # until the app has been in front again, so the poll doesn't collapse
        # it later, the moment the pointer wanders off.
        if self._pointer_over_window():
            self._dbg("skip: pointer is over the screen")
            self._seen_foreground = False
            return
        if not confirmed:
            self.root.after(20, lambda: self._collapse_if_focus_left(confirmed=True))
            return
        self._dbg(f"COLLAPSE morphing={getattr(self, '_morphing', None)}")
        self.collapse(True)

    def collapse(self, collapsed: bool) -> None:
        self._finish_reveal()
        self._undock_now()
        if getattr(self, "_morphing", False):
            # Asked for mid-animation: remembered rather than dropped. The
            # opposite request turns a collapse around from wherever it has
            # got to (see ``_morph``) or, for an expand, runs right after
            # it lands; repeating the current direction cancels it.
            self._pending = collapsed if collapsed != self.collapsed else None
            return
        if collapsed == self.collapsed:
            return
        if collapsed and self._collapse_live():
            return
        old_rect = (
            self.root.winfo_x(), self.root.winfo_y(),
            self.root.winfo_width(), self.root.winfo_height(),
        )
        self._early_ghost = self._early_snap = None
        if not collapsed:
            self._expanded_at = time.monotonic()
        if not collapsed and old_rect[2] > 1 and old_rect[3] > 1:
            # The icon stays exactly as it is — now as a ghost laid over it,
            # border and all — until the expanding itself begins; only then
            # does the real window go and the ghost's border start to grow.
            self._cover_badge(old_rect)
        self.collapsed = collapsed
        self._stop_ring()
        # The swap below calls `_sync_ring` indirectly while the window is
        # still its old (large) size; the ring is only started at the end.
        self._collapsing = True
        # Before any positioning below, not after: toggling ``-topmost`` on
        # Windows can itself nudge an ``overrideredirect`` window's position
        # (collapsing used to never actually change this value unless
        # "Always on top" was already on, so this side effect had nothing
        # to clobber — now that collapsing alone can flip it, whichever of
        # ``apply_snap``/``_restore_geometry`` runs below needs to be the
        # last word on where the window ends up, not this).
        self._sync_topmost()
        # Swapping the badge for the shell (or back) is a corner-style change,
        # a repack, and a geometry jump all at once — unfrozen, Windows paints
        # each of those separately, and the gap between "badge gone" and
        # "shell sized and placed" shows up as a flash of bare, unstyled
        # window background (what reads as a plain white box).
        hwnd = self._hwnd()
        self.root.attributes("-alpha", 0.0)
        dwm.freeze(hwnd)
        try:
            if collapsed:
                self._save_geometry()
                self.shell.pack_forget()
                self.icon.pack(fill="both", expand=True)
                self.root.minsize(*ICON_SIZE)
                x, y = self.root.winfo_x(), self.root.winfo_y()
                self.root.geometry(f"{ICON_SIZE[0]}x{ICON_SIZE[1]}+{x}+{y}")
                self._size = ICON_SIZE
                self.apply_snap()  # the corner is measured off the new size
                self._round_badge()
            else:
                self._round_panel(hwnd)
                dwm.border(hwnd, dwm.NO_BORDER)
                self.icon.pack_forget()
                # The panel's contents are packed (and so laid out and
                # painted — a couple of hundred milliseconds of work) only
                # *after* the expand animation has run: the animation is a
                # ghost window that needs just the target rectangle, so the
                # click gets an immediate response instead of a pause.
                self._shell_pending = True
                self.root.minsize(*MIN_SIZE)
                self._restore_geometry(position_only=False)
        finally:
            dwm.thaw(hwnd)
        # Settled at its final size/position. Measured *after* the thaw (an
        # idle flush while frozen leaves Tk's geometry unapplied). The
        # window is then taken back to its old rect and eased to the new
        # one; the alpha dip hides the single frame of the final rect
        # in between.
        if collapsed:
            self._refresh_badge_timer()  # the badge's width depends on it
        if collapsed:
            self.root.update_idletasks()
            target = (
                self.root.winfo_x(), self.root.winfo_y(),
                self.root.winfo_width(), self.root.winfo_height(),
            )
        else:
            # The rectangle is taken from what was just asked for (see
            # ``_planned_pos``) rather than by flushing the window's resize
            # first — real work that would only delay the animation, and
            # that happens later, hidden, anyway.
            x, y = self._planned_pos
            target = (x, y, *self._size)
        if not collapsed:
            # Measure against the real screen first: a saved size from a
            # bigger monitor (or a different scaling) must not make the
            # panel grow past the work area, so the animation lands on the
            # rect the panel will actually occupy.
            fitted = self._fit_to_screen(target)
            if fitted != target:
                x, y, w, h = fitted
                self.root.geometry(f"{w}x{h}+{x}+{y}")
                self._size = (w, h)
                self.root.update_idletasks()
                target = fitted
        if not collapsed:
            self._raise_shield(target)
        if old_rect != target and old_rect[2] > 1 and old_rect[3] > 1:
            self._morphing = True
            try:
                if collapsed:
                    # The real window shrinks (its content clipped, not
                    # redrawn); the cover only hid the swap above.
                    self._morph(old_rect, target, collapsed)
                elif not self._ghost_morph(old_rect, target, collapsed):
                    self._pack_shell()  # the live animation needs it laid out
                    self._morph(old_rect, target, collapsed)
            finally:
                self._morphing = False
        self._pack_shell()  # no animation ran (or it didn't take it)
        if self._early_ghost is not None:  # no animation ran to take it over
            self._early_ghost.close()
            self._early_ghost = None
        if self._early_snap is not None:
            self._early_snap.close()
            self._early_snap = None

        self.root.attributes("-alpha", self.store.db.settings.opacity)
        self.root.update_idletasks()
        self._drop_shield()
        self._collapsing = False
        if not collapsed:
            self._expanded_at = time.monotonic()
        turning_back = getattr(self, "_pending", None) is not None
        if collapsed and not turning_back:
            self._apply_badge_region()
        if not turning_back:  # mid-size window: no ring until it settles
            self._sync_ring()
        self.refresh()
        pending, self._pending = getattr(self, "_pending", None), None
        if pending is not None and pending != self.collapsed:
            self.root.after(1, lambda: self.collapse(pending))

    MORPH_MS = 160

    def _pack_shell(self) -> None:
        """Puts the panel's contents in the window, if an expand left that
        until after its animation (see ``collapse``)."""
        if getattr(self, "_shell_pending", False):
            self._shell_pending = False
            self.shell.pack(fill="both", expand=True)

    # -- animated borders ---------------------------------------------------

    def _badge_border(self) -> tuple:
        """The badge's own stroke as ``((r, g, b), thickness, radius)``:
        the status colour at ring thickness while a timer exists, the
        neutral hairline otherwise (see ``_paint_badge_border``)."""
        timer = self.store.db.active_timer
        scale = self._dpi_scale()
        if timer is None:
            colour, thickness = theme.LINE, max(1, round(scale))
        else:
            colour = theme.PLANNED if timer.is_paused else theme.BILLABLE
            thickness = max(2, round(self.RING_THICKNESS * scale))
        rgb = tuple(c / 257 for c in self.root.winfo_rgb(colour))
        return rgb, thickness, self.BADGE_CORNER_RADIUS * scale

    def _panel_border(self) -> tuple:
        """The expanded panel's own stroke: the 1px neutral line."""
        rgb = tuple(c / 257 for c in self.root.winfo_rgb(theme.LINE))
        return rgb, max(1, round(self._dpi_scale())), self.PANEL_RADIUS * self._dpi_scale()

    @staticmethod
    def _lerp_border(a: tuple, b: tuple, t: float) -> tuple:
        """Colour, thickness and corner radius, all eased from ``a`` to ``b``."""
        colour = tuple(x + (y - x) * t for x, y in zip(a[0], b[0]))
        return colour, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t

    def _raise_shield(self, rect: tuple) -> None:
        """Puts an all-but-invisible, click-swallowing window over ``rect``
        for the length of an animation. The real window is fully
        transparent while it runs, and Windows lets clicks fall straight
        through a fully transparent window — so without this, a press on
        any part of the screen the animation hasn't reached yet (or has
        already left) lands on whatever app is behind it and brings that
        app to the front."""
        self._drop_shield()
        try:
            ghost = winfx.Ghost(rect[2], rect[3], click_through=False)
        except OSError:
            return
        try:
            ghost.draw(rect, winfx.solid("#000000"), winfx.solid("#000000"), 0.0, 1 / 255)
        except OSError:
            ghost.close()
            return
        self._shield = ghost
        # Never left behind, whatever goes wrong in the animation.
        self._shield_timer = self.root.after(4000, self._drop_shield)

    def _drop_shield(self) -> None:
        timer, self._shield_timer = getattr(self, "_shield_timer", None), None
        if timer is not None:
            try:
                self.root.after_cancel(timer)
            except tk.TclError:
                pass
        shield, self._shield = getattr(self, "_shield", None), None
        if shield is not None:
            shield.close()

    def _cover_badge(self, rect: tuple) -> None:
        """Lays a ghost — a snapshot of the badge with its border drawn
        around it — exactly over the collapsed icon, and waits for it to be
        on screen. ``_ghost_morph`` takes it over to expand it. Does nothing
        (leaving the old behaviour) if Windows won't make it."""
        snap = ghost = None
        try:
            snap = winfx.capture(self._hwnd(), rect[2], rect[3])
        except OSError:
            snap = winfx.solid(theme.BG_RAISED)
        try:
            screens = self.screens()
            ghost = winfx.Ghost(
                max(rect[2], *(s.width for s in screens)),
                max(rect[3], *(s.height for s in screens)),
                click_through=False,
            )
            ghost.draw(
                rect, snap, snap, 0.0, self.store.db.settings.opacity,
                border=self._badge_border(),
            )
            try:
                ctypes.windll.dwmapi.DwmFlush()  # on screen before the icon goes
            except (OSError, AttributeError):
                pass
        except OSError:
            for item in (ghost, snap):
                if item is not None:
                    item.close()
            return
        self._early_ghost, self._early_snap = ghost, snap

    def _collapse_live(self) -> bool:
        """Collapses to the badge in two clean stages.

        1. The moment it's asked for, every on-screen element is gone: a
           bare, panel-coloured rectangle (a layered ``winfx.Ghost``, so the
           compositor moves and scales it with no repaint, no resize
           messages and no Tk work per frame) takes the window's place and
           shrinks to the badge's rect, drifting to the badge's colour on
           the way. The real window just waits, invisible, at its old size —
           nothing in it is clipped or re-laid-out while this runs.
        2. Only once the rectangle has landed is the real window swapped to
           the badge, in one frozen step, and revealed under it.

        Returns False, having changed nothing, if it can't run (the caller
        then swaps without the animation).
        """
        if self.root.state() == "withdrawn":
            return False
        old = (
            self.root.winfo_x(), self.root.winfo_y(),
            self.root.winfo_width(), self.root.winfo_height(),
        )
        if old[2] <= ICON_SIZE[0] or old[3] <= ICON_SIZE[1]:
            return False
        settings = self.store.db.settings
        opacity = settings.opacity
        ghost = panel = badge = snap = None
        try:
            # A snapshot of the screen as it is, so the ghost starts as an
            # exact copy of it: the contents then dissolve into the plain
            # background in place rather than vanishing in one frame.
            snap = winfx.capture(self._hwnd(), old[2], old[3])
            ghost = winfx.Ghost(old[2], old[3], click_through=False)
            panel, badge = winfx.solid(theme.BG), winfx.solid(theme.BG_RAISED)
            border_a, border_b = self._panel_border(), self._badge_border()
            ghost.draw(old, snap, snap, 0.0, opacity, border=border_a)
        except OSError:
            for item in (ghost, panel, badge, snap):
                if item is not None:
                    item.close()
            return False

        hwnd = self._hwnd()
        self._save_geometry()  # while it still is the panel
        self.collapsed = True
        self._stop_ring()
        self._collapsing = True
        self._morphing = True
        # Where the badge will land: the spot the swap's own ``apply_snap``
        # would pick, worked out up front.
        dst = (old[0], old[1], *ICON_SIZE)
        if settings.snap_corner in monitors.CORNERS.values():
            screen = monitors.find(self.screens(), settings.snap_screen)
            bx, by = monitors.corner_position(
                screen, settings.snap_corner, *ICON_SIZE, margin=BADGE_MARGIN
            )
            dst = (bx, by, *ICON_SIZE)

        def vsync() -> None:
            try:
                ctypes.windll.dwmapi.DwmFlush()
            except (OSError, AttributeError):
                time.sleep(0.008)

        self._raise_shield(old)
        vsync()  # the rectangle is on screen before the real window goes
        self.root.attributes("-alpha", 0.0)
        self._sync_topmost()
        duration = self.MORPH_MS * 1.25
        #: The first stretch only dissolves the contents into the plain
        #: background, in place; the background then shrinks to the badge.
        hold = 0.2

        def paint(progress: float) -> None:
            if progress < hold:
                ghost.draw(old, snap, panel, progress / hold, opacity, border=border_a)
                return
            e = anim.smoothstep((progress - hold) / (1 - hold))
            ghost.draw(
                anim.lerp_rect(old, dst, e), panel, badge, e, opacity,
                border=self._lerp_border(border_a, border_b, e),
            )

        start = time.perf_counter()
        progress = 0.0
        interrupted = False
        try:
            while True:
                if getattr(self, "_pending", None) is False:
                    interrupted = True  # asked to expand again: reverse it
                    break
                progress = (time.perf_counter() - start) * 1000 / duration
                if progress >= 1:
                    progress = 1.0
                    break
                paint(progress)
                self.root.update()  # input (and so interruption) stays live
                vsync()
            if interrupted:
                # Back the way it came, over a time in proportion to how far
                # it had got — the real window never moved, so this lands
                # exactly on it.
                span = max(progress * duration, 1.0)
                reverse = time.perf_counter()
                while True:
                    back = (time.perf_counter() - reverse) * 1000 / span
                    if back >= 1:
                        break
                    paint(progress * (1 - back))
                    vsync()
                self.collapsed = False
                self._collapsing = False
                self._morphing = False
                self._sync_topmost()
                self.root.attributes("-alpha", opacity)
                self.root.update_idletasks()
                vsync()
                self._pending = None
                return True

            # Landed: swap the real window to the badge while it's invisible.
            self.shell.pack_forget()
            dwm.freeze(hwnd)
            try:
                self.icon.pack(fill="both", expand=True)
                self.root.minsize(*ICON_SIZE)
                self.root.geometry(f"{dst[2]}x{dst[3]}+{dst[0]}+{dst[1]}")
                self._size = ICON_SIZE
                self._refresh_badge_timer()  # the badge's width depends on it
                self.apply_snap()
                self._round_badge()
            finally:
                dwm.thaw(hwnd)
            self.root.update_idletasks()
            self._morphing = False
            self._collapsing = False
            self._apply_badge_region()
            # A full update, not just idle tasks: only it handles the expose
            # events that repaint the resized window, which otherwise still
            # holds pieces of the old panel when it is revealed.
            self.root.update()
            vsync()
            self.root.attributes("-alpha", opacity)
            self.root.update()
            vsync()
            # The shrunken background *is* the badge's own: lift it away
            # gradually so the icon comes up on it, rather than the
            # rectangle being dropped and the icon popping in.
            self._drop_shield()
            ghost.set_click_through(True)  # the badge is clickable again
            fade_start = time.perf_counter()
            while True:
                t = (time.perf_counter() - fade_start) * 1000 / 160
                if t >= 1:
                    break
                ghost.draw(dst, panel, badge, 1.0, opacity * (1 - t), border=border_b)
                vsync()
        finally:
            for item in (ghost, panel, badge, snap):
                if item is not None:
                    item.close()
            self._drop_shield()
            self._morphing = False
            self._collapsing = False
        self._sync_ring()
        self.refresh()
        pending, self._pending = getattr(self, "_pending", None), None
        if pending is not None and pending != self.collapsed:
            self.root.after(1, lambda: self.collapse(pending))
        return True

    def _ghost_morph(self, src: tuple, dst: tuple, collapsed: bool) -> bool:
        """The expand/collapse animation: a click-through ghost window shows
        a blank panel-coloured rectangle growing from the badge to the panel
        (or shrinking back), shifting between the badge's and the panel's
        colour on the way. No Tk widget is touched while it runs, and
        nothing of either screen is shown scaled; the real window (still
        transparent) is only revealed at the end. Returns False — having
        changed nothing — if the ghost can't be made, so the caller falls
        back to the live animation."""
        ghost = panel = badge = snap = None
        try:
            if collapsed:
                self.root.update()  # paint the new state (invisible at alpha 0)
            panel, badge = winfx.solid(theme.BG), winfx.solid(theme.BG_RAISED)
            start, end = (panel, badge) if collapsed else (badge, panel)
            ghost, self._early_ghost = self._early_ghost, None
            snap, self._early_snap = self._early_snap, None
            if ghost is None:
                ghost = winfx.Ghost(
                    max(src[2], dst[2]), max(src[3], dst[3]), click_through=False
                )
            opacity = self.store.db.settings.opacity
            panel_border, badge_border = self._panel_border(), self._badge_border()
            border_a, border_b = (
                (panel_border, badge_border) if collapsed else (badge_border, panel_border)
            )
            # With a snapshot of the outgoing look (the icon, when expanding),
            # the first stretch of the animation just fades it into the plain
            # rectangle in place (unscaled); the border then grows with the
            # rectangle — colour, width and curve all easing to the card's.
            hold = 0.22 if snap is not None else 0.0
            first = snap if snap is not None else start
            ghost.draw(src, first, first, 0.0, opacity, border=border_a)

            def frame(t: float) -> None:
                if t < hold:
                    ghost.draw(src, snap, start, t / hold, opacity, border=border_a)
                    return
                u = (t - hold) / (1 - hold)
                ghost.draw(
                    anim.lerp_rect(src, dst, u), start, end, u, opacity,
                    border=self._lerp_border(border_a, border_b, u),
                )

            anim.run(self.MORPH_MS, frame)
            if not collapsed:
                # Only now is the panel's content laid out and painted —
                # still invisible, under the ghost that is holding the
                # final rectangle — so the animation didn't wait for it.
                self._pack_shell()
                self.root.update()
                try:
                    ctypes.windll.dwmapi.DwmFlush()
                except OSError:
                    pass
            # Reveal the real window under the ghost, then drop the ghost.
            self.root.attributes("-alpha", opacity)
            self.root.update_idletasks()
            return True
        except OSError:
            return False
        finally:
            for item in (ghost, panel, badge, snap):
                if item is not None:
                    item.close()
    def _fit_to_screen(self, rect: tuple) -> tuple:
        """``rect`` shrunk and shifted to sit inside the work area of the
        screen it mostly overlaps (the nearest one if it overlaps none)."""
        x, y, w, h = rect
        screens = self.screens()
        if not screens:
            return rect
        cx, cy = x + w // 2, y + h // 2

        def distance(s) -> int:
            dx = max(s.x - cx, 0, cx - (s.x + s.width))
            dy = max(s.y - cy, 0, cy - (s.y + s.height))
            return dx * dx + dy * dy

        screen = min(screens, key=distance)
        w, h = min(w, screen.width), min(h, screen.height)
        x = max(screen.x, min(x, screen.x + screen.width - w))
        y = max(screen.y, min(y, screen.y + screen.height - h))
        return (x, y, w, h)

    def _morph(self, src: tuple, dst: tuple, collapsed: bool) -> None:
        """Eases the window between the badge and panel rects.

        Smoothness comes from three things: the panel is *not* re-laid-out
        each frame (it's placed at one fixed size and the window just
        reveals or clips it), the window is moved with a single
        ``SetWindowPos`` call per frame rather than Tk's asynchronous
        ``geometry``, and each frame waits for the compositor
        (``DwmFlush``) instead of a coarse ``after`` timer.
        """
        hwnd = self._hwnd()
        user32 = ctypes.windll.user32
        shell_w, shell_h = (src[2], src[3]) if collapsed else (dst[2], dst[3])
        self.root.minsize(1, 1)
        if collapsed:
            # The shell was unpacked by the swap; show it again at its old,
            # fixed size and let the shrinking window clip it.
            self.icon.pack_forget()
            self.shell.place(x=0, y=0, width=shell_w, height=shell_h)
        else:
            # The shell is already laid out at its final size; pin it there
            # (no fill) so the growing window reveals it instead of
            # re-laying-out every child on every frame.
            self.shell.pack_propagate(False)
            self.shell.configure(width=shell_w, height=shell_h)
            self.shell.pack_configure(fill="none", expand=False, anchor="nw")
        flags = 0x0004 | 0x0010  # SWP_NOZORDER | SWP_NOACTIVATE

        def put(x: int, y: int, w: int, h: int) -> None:
            user32.SetWindowPos(hwnd, 0, x, y, max(w, 1), max(h, 1), flags)

        if collapsed:
            # The badge's clip and square corners don't fit a panel-sized
            # window; it shrinks with the panel's rounded look and is
            # clipped to the badge's shape again once it lands.
            winfx.clear_region(hwnd)
            self._badge_clip = None
            dwm.corners(hwnd, dwm.ROUNDED)
        put(*src)
        # Laid out and painted while still invisible: revealed any sooner,
        # the window would show the badge stretched to panel size (what the
        # swap left in it) for a frame before the panel is put back.
        self.root.update()
        try:
            ctypes.windll.dwmapi.DwmFlush()
        except OSError:
            pass
        self.root.attributes("-alpha", self.store.db.settings.opacity)
        self.root.update_idletasks()
        cover, self._early_ghost = self._early_ghost, None
        if cover is not None:
            # The real window is back under the (identical) snapshot cover.
            try:
                ctypes.windll.dwmapi.DwmFlush()
            except OSError:
                pass
            cover.close()
        start = time.perf_counter()
        current = src
        interrupted = False
        while True:
            if collapsed and getattr(self, "_pending", None) is False:
                # Asked to expand again mid-collapse: stop where the window
                # is and let ``collapse`` grow it back from there.
                interrupted = True
                dst = current
                break
            t = (time.perf_counter() - start) * 1000 / self.MORPH_MS
            if t >= 1:
                break
            e = t * t * (3 - 2 * t)  # smoothstep: gentle at both ends
            current = tuple(round(a + (b - a) * e) for a, b in zip(src, dst))
            put(*current)
            self.root.update()
            try:
                ctypes.windll.dwmapi.DwmFlush()
            except OSError:
                time.sleep(0.008)
        # The window was moved with SetWindowPos behind Tk's back, so Tk's own
        # idea of its position is the old one and ``geometry`` would treat the
        # same string as a no-op — leaving the window wherever the last frame
        # put it (what shifted the badge). Placed for real first.
        put(*dst)
        x, y, w, h = dst
        dwm.freeze(hwnd)  # shell -> badge swap lands as one step
        try:
            if collapsed:
                self.shell.place_forget()
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            if collapsed:
                self.icon.pack(fill="both", expand=True)
                self.root.minsize(*ICON_SIZE)
                if not interrupted:  # (it would snap the window to the badge's width)
                    self._refresh_badge_timer()
                self._round_badge()
            else:
                self.shell.pack_configure(fill="both", expand=True, anchor="center")
                self.shell.pack_propagate(True)
                self.root.minsize(*MIN_SIZE)
            if not interrupted:
                self.apply_snap()
        finally:
            dwm.thaw(hwnd)

    def _sync_topmost(self) -> None:
        """Keeps ``-topmost`` matching what it actually should be right now:
        "Always on top" if that's on, otherwise still pinned while
        collapsed to the badge, as long as ``Settings.pin_when_collapsed``
        (on by default) hasn't been turned off — a badge that's about to
        disappear behind whatever else is open defeats the point of
        shrinking down to one in the first place.

        Called on every collapse/expand, every "Always on top" toggle, and
        every change to ``pin_when_collapsed`` itself — rather than the
        badge remembering whether *it* turned the pin on, which would have
        to unwind in exactly the right order against the user flipping
        "Always on top" by hand mid-collapse.
        """
        settings = self.store.db.settings
        pinned = settings.always_on_top or (self.collapsed and settings.pin_when_collapsed)
        self.root.attributes("-topmost", pinned)

    def _reassert_topmost(self) -> None:
        """Raises the window back to the top of the topmost band.

        Another topmost window (a fullscreen app, a screen-share bar, the
        taskbar after a click) can land above this one while Tk still
        believes ``-topmost`` is set, so just setting the attribute again
        is a no-op. Re-issuing ``SetWindowPos(HWND_TOPMOST)`` without
        moving, resizing or activating it puts it back on top.
        """
        settings = self.store.db.settings
        if not (settings.always_on_top or (self.collapsed and settings.pin_when_collapsed)):
            return
        if self.root.state() == "withdrawn":
            return
        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW-less
        flags = 0x0001 | 0x0002 | 0x0010
        try:
            ctypes.windll.user32.SetWindowPos(self._hwnd(), -1, 0, 0, 0, 0, flags)
        except Exception:
            pass

    def _hwnd(self) -> int:
        return ctypes.windll.user32.GetAncestor(self.root.winfo_id(), GA_ROOT)

    PANEL_RADIUS = 10

    def _round_panel(self, hwnd: int) -> None:
        """Rounds the expanded window: DWM's own corners where Windows 11
        supplies them, otherwise a clipping region (see ``winfx``)."""
        winfx.clear_region(hwnd)  # the badge's clip doesn't fit the panel
        self._badge_clip = None
        self._panel_region = not dwm.corners(hwnd, dwm.ROUNDED)
        if self._panel_region and not getattr(self, "_clip_bound", False):
            self._clip_bound = True
            self.root.bind("<Configure>", self._clip_panel, add="+")

    def _clip_panel(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        if self.collapsed or not getattr(self, "_panel_region", False):
            return
        size = (self.root.winfo_width(), self.root.winfo_height())
        if getattr(self, "_panel_clip", None) == size:
            return  # unchanged: re-clipping would fire another <Configure>
        self._panel_clip = size
        try:
            winfx.round_hwnd(
                self._hwnd(), self.root.winfo_width(), self.root.winfo_height(),
                self.PANEL_RADIUS,
            )
        except OSError:
            pass

    def _round_badge(self) -> None:
        """Rounds the collapsed window's corners, if Windows will.

        Tk's own border is a rectangle and would be left hanging at the arcs,
        so where the rounding takes, the border is handed to DWM as well —
        which is also what tints it while a timer runs.
        """
        if getattr(self, "_panel_region", False):
            winfx.clear_region(self._hwnd())  # the panel's clip doesn't fit the badge
        if not getattr(self, "_badge_bound", False):
            self._badge_bound = True
            self.root.bind("<Configure>", self._clip_badge, add="+")
        # The badge's shape is the window region (``_apply_badge_region``) and
        # its stroke is the ring window, both at ``BADGE_CORNER_RADIUS`` — so
        # DWM's own (smaller-radius) corners and border are switched off,
        # otherwise its border pokes out past the clipped corners.
        dwm.corners(self._hwnd(), dwm.SQUARE)
        dwm.border(self._hwnd(), dwm.NO_BORDER)
        self._rounded = True
        self.icon.config(highlightthickness=0)
        self._paint_badge_border()

    def _paint_badge_border(self) -> None:
        """The badge's own stroke — ``theme.LINE`` (its everyday, neutral
        outline) with nothing running, ``theme.BILLABLE`` while a timer's
        actually ticking, ``theme.PLANNED`` (amber) while one's paused —
        the same statuses the expanded timer bar's own dot shows, just
        traced around the badge itself (see ``_build_icon``) rather than a
        dot in its corner, since the badge icon's own artwork can't be
        tinted to say so any other way.
        """
        timer = self.store.db.active_timer
        if timer is None:
            colour = theme.LINE
        elif timer.is_paused:
            colour = theme.PLANNED
        else:
            colour = theme.BILLABLE
        # The ring window (``_sync_ring``) draws the stroke either way: thick
        # and animated while a timer exists, a 1px neutral line otherwise.
        self._sync_ring()

    # -- animated status ring ---------------------------------------------

    RING_THICKNESS = 6
    RING_FRAME_MS = 40
    RING_PERIOD_S = 2.4
    #: How far the hue swings either side of the status colour (fraction of
    #: the colour wheel; 0.08 is about 29 degrees) — a slight gradient, not
    #: a rainbow.
    RING_HUE_SWING = 0.08
    #: The badge's corner radius at 100% scaling: the same as the
    #: expanded panel (`PANEL_RADIUS`), so both share one rounded-square style. Big enough that the ring's
    #: concentric inner edge (this minus ``RING_THICKNESS``) still visibly
    #: curves; the badge window is clipped to the same radius so the ring's
    #: outer edge and the window's edge coincide.
    BADGE_CORNER_RADIUS = 10

    def _dpi_scale(self) -> float:
        try:
            return (ctypes.windll.user32.GetDpiForWindow(self._hwnd()) or 96) / 96
        except (OSError, AttributeError):
            return 1.0

    def _apply_badge_region(self) -> None:
        """Clips the collapsed window to the badge's rounded shape."""
        width, height = self.root.winfo_width(), self.root.winfo_height()
        if width < 2 or height < 2 or getattr(self, "_badge_clip", None) == (width, height):
            return  # unchanged: re-clipping would fire another <Configure>
        self._badge_clip = (width, height)
        try:
            # Pulled in by the width of the ring's soft outer pixel: the
            # window's own clip is a hard, un-smoothed edge, so it is kept
            # under the ring's opaque band (``_ring_tick``) rather than at
            # its very edge, where it showed through as a stair-stepped rim.
            scale = self._dpi_scale()
            winfx.round_hwnd(
                self._hwnd(), width, height, round(self.BADGE_CORNER_RADIUS * scale),
                inset=max(1, round(scale)),
            )
        except OSError:
            self._badge_clip = None

    def _clip_badge(self, event: tk.Event) -> None:
        if event.widget is self.root and self.collapsed and not getattr(self, "_morphing", False):
            self._apply_badge_region()

    def _sync_ring(self) -> None:
        """Shows, animates or hides the ring around the collapsed badge to
        match the timer: an anti-aliased rounded ring (``winfx.RingWindow``)
        whose colour is a moving hue gradient on the status colour — green
        running, amber paused, the latter drifting slower. Nothing when
        there's no timer or the window is expanded."""
        if getattr(self, "_collapsing", False):
            return
        if not self.collapsed:
            self._stop_ring()
            return
        if getattr(self, "_ring_job", None) is None:
            self._ring_tick()

    def _stop_ring(self) -> None:
        job = getattr(self, "_ring_job", None)
        if job is not None:
            self.root.after_cancel(job)
            self._ring_job = None
        ring = getattr(self, "_ring", None)
        if ring is not None:
            ring.close()
            self._ring = None

    def _ring_tick(self) -> None:
        self._ring_job = None
        timer = self.store.db.active_timer
        if not self.collapsed or getattr(self, "_morphing", False):
            if not getattr(self, "_morphing", False):
                self._stop_ring()
            return
        if self._dock_anim is not None:
            # Gliding: the ring is moved with the window each frame.
            self._ring_job = self.root.after(self.RING_FRAME_MS, self._ring_tick)
            return
        x, y = self._badge_xy()
        width, height = self.root.winfo_width(), self.root.winfo_height()
        if width < 2 or height < 2:
            self._ring_job = self.root.after(self.RING_FRAME_MS, self._ring_tick)
            return
        ring = getattr(self, "_ring", None)
        scale = self._dpi_scale()
        thickness = max(2, round(self.RING_THICKNESS * scale)) if timer else max(2, round(2 * scale))
        if ring is None or (ring.width, ring.height, ring.thickness) != (width, height, thickness):
            if ring is not None:
                ring.close()
            try:
                ring = self._ring = winfx.RingWindow(
                    width, height, self.BADGE_CORNER_RADIUS * scale, thickness
                )
            except OSError:
                self._ring = None
                return  # no ring is better than a broken one
        if self._docked:
            self._dock_clip(x)  # a rebuilt ring has to be cut to the screen too
        if timer is None:
            # Nothing running: a still, neutral hairline, drawn once.
            # Re-drawn only when it has moved, resized or changed opacity.
            opacity = self.store.db.settings.opacity
            key = (x, y, width, height, opacity, thickness, ring.hwnd)
            if getattr(self, "_static_key", None) != key:
                line = tuple(c / 257 for c in self.root.winfo_rgb(theme.LINE))
                try:
                    ring.draw(x, y, [line] * winfx.RING_BUCKETS, opacity, above=self._hwnd())
                except OSError:
                    self._stop_ring()
                    return
                self._static_key = key
            self._ring_job = self.root.after(self.RING_FRAME_MS, self._ring_tick)
            return
        self._static_key = None
        paused = timer.is_paused
        r, g, b = (c / 65535 for c in self.root.winfo_rgb(theme.PLANNED if paused else theme.BILLABLE))
        hue, sat, val = colorsys.rgb_to_hsv(r, g, b)
        period = self.RING_PERIOD_S * (2.5 if paused else 1.0)
        phase = (time.monotonic() % period) / period
        buckets = winfx.RING_BUCKETS
        palette = []
        for k in range(buckets):
            wave = math.sin(2 * math.pi * (k / buckets - phase))
            # Slightly brighter at the crest of the wave, so the movement
            # reads even where the hue shift alone is subtle.
            rr, gg, bb = colorsys.hsv_to_rgb(
                (hue + self.RING_HUE_SWING * wave) % 1.0, sat,
                min(1.0, val * (0.85 + 0.15 * (wave + 1) / 2) + 0.05),
            )
            palette.append((rr * 255, gg * 255, bb * 255))
        try:
            ring.draw(x, y, palette, self.store.db.settings.opacity, above=self._hwnd())
        except OSError:
            self._stop_ring()
            return
        self._ring_job = self.root.after(self.RING_FRAME_MS, self._ring_tick)
    def _refresh_badge_timer(self) -> None:
        """Shows, hides, and keeps current the live readout beside the
        collapsed badge (``icon_timer_box`` — decimal hours over the
        running mm:ss clock) — called on every tick (so it keeps ticking)
        and on every store-driven ``refresh`` (so starting, stopping or
        pausing shows up the moment it happens, not up to a second late).

        Idempotent rather than edge-triggered on "just started"/"just
        stopped": it always leaves the labels and the badge's own width
        matching whatever the timer's doing right now, so there's no
        separate state of its own that could drift out of step with
        ``Store.db.active_timer`` — the one thing collapsing and expanding
        again, or this running before the badge has ever been shown once,
        would otherwise risk.

        The badge's width is measured off what's actually been packed
        (``winfo_reqwidth``), not a fixed guess — the clock's own text
        grows an extra digit once an hour passes (``format_clock``), and a
        guess sized for "1.5h" over "12:34" would start clipping the
        second line the moment a timer ran that long.
        """
        if not self.collapsed:
            return
        timer = self.store.db.active_timer
        if timer is None:
            self.icon_timer_box.pack_forget()
            width = ICON_SIZE[0]
        else:
            fg = theme.MUTED if timer.is_paused else theme.TEXT
            # "+ 0.25 h": what Stop would add to the entry right now, rounded
            # the same way the entry itself will be (see
            # ``Store.timer_preview``). Until the elapsed time rounds up to
            # something that would actually be added there's nothing to
            # show, so that line is left off and only the clock remains.
            preview = self.store.timer_preview()
            add_minutes = preview[0] if preview else 0
            if add_minutes > 0:
                hours_text = f"+ {format_decimal_hours(add_minutes, places=2)} h"
                self.icon_timer_hours.config(text=hours_text, fg=fg)
                if not self.icon_timer_hours.winfo_manager():
                    self.icon_timer_hours.pack(before=self.icon_timer_clock)
            else:
                self.icon_timer_hours.pack_forget()
            self.icon_timer_clock.config(text=format_clock(timer.elapsed_seconds), fg=fg)
            self.icon_timer_box.pack(side="right", padx=(0, 3))
            self.icon.update_idletasks()
            width = ICON_SIZE[0] + self.icon_timer_box.winfo_reqwidth() + 6
        if self._size != (width, ICON_SIZE[1]):
            self._size = (width, ICON_SIZE[1])
            self.root.minsize(*self._size)
            self._undock_now()
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.root.geometry(f"{width}x{ICON_SIZE[1]}+{x}+{y}")
            self.apply_snap()  # the corner is measured off the new size

    def toggle_visible(self) -> None:
        if self.root.state() == "withdrawn":
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        else:
            self.root.withdraw()

    def toggle_timer(self) -> None:
        if self.store.db.active_timer:
            self.store.stop_timer()
            return
        task_id = self.store.most_recent_task_id()
        if task_id:
            self.store.start_timer(task_id)
        else:
            self.toggle_visible()

    def _drag_start(self, event: tk.Event) -> None:
        if self.store.db.settings.snap_corner:
            # Pinned to a corner, so the header moves nothing. Cleared rather
            # than just returning: a stale origin from an earlier drag would
            # otherwise let the next motion event move the window anyway.
            self._drag_origin = None
            return
        self._drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        dx, dy = self._drag_origin
        self.root.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    # -- geometry persistence ---------------------------------------------

    def _restore_geometry(self, position_only: bool = False) -> None:
        width, height = FULL_SIZE
        x = y = None
        mode = "small"
        try:
            saved = json.loads(paths.window_state_file().read_text("utf-8"))
            width, height = saved.get("width", width), saved.get("height", height)
            x, y = saved.get("x"), saved.get("y")
            if "size_mode" in saved:
                mode = saved["size_mode"]  # None: resized by hand
            else:
                mode = None  # a file from before modes were saved: keep its size
        except Exception:  # noqa: BLE001 - a missing or stale file is fine
            pass

        # A size mode is a proportion of the screen, so work it out afresh
        # for the screen the window is on now instead of trusting pixels
        # saved on whatever screen it was last on.
        if mode in SIZE_MODES:
            self._size_mode = mode
            width, height = self._size_for_mode(mode)
        if not position_only:
            self.root.geometry(f"{width}x{height}")
            self._size = (width, height)
        if self.apply_snap():
            return

        # Guard against a monitor that is no longer attached. Checked against
        # every screen, not Tk's primary-only view, so a window parked on a
        # second monitor is left where the user put it.
        screens = self.screens()
        if x is None or y is None or not monitors.is_on_screen(x, y, width, height, screens):
            primary = screens[0]
            x = primary.x + (primary.width - width) // 2
            y = primary.y + 80

        self._planned_pos = (x, y)
        self.root.geometry(f"+{x}+{y}")

    def _current_size_mode(self) -> str | None:
        """The size mode the window is still at, or ``None`` once it has
        been dragged to some other size by hand."""
        try:
            target = self._size_for_mode(self._size_mode)
            if (
                abs(self.root.winfo_width() - target[0]) <= 2
                and abs(self.root.winfo_height() - target[1]) <= 2
            ):
                return self._size_mode
        except Exception:  # noqa: BLE001
            pass
        return None

    def _save_geometry(self) -> None:
        if self.collapsed:
            return
        try:
            state = {
                "size_mode": self._current_size_mode(),
                "width": self.root.winfo_width(),
                "height": self.root.winfo_height(),
                "x": self.root.winfo_x(),
                "y": self.root.winfo_y(),
            }
            file = paths.window_state_file()
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(state), "utf-8")
        except OSError:
            pass  # window position is a nicety, never worth an error for

    # -- tabs and refresh --------------------------------------------------

    #: The tab whose view is being built right now behind its skeleton (and
    #: so while the real window is still up and clickable) — see
    #: ``show_tab`` and ``_checkpoint``. ``None`` otherwise.
    _build_name: str | None = None
    _in_checkpoint = False
    _last_checkpoint = 0.0
    _refresh_again = False

    def _checkpoint(self) -> None:
        """Called from deep inside a view build (``yielding.checkpoint``):
        lets Tk handle pending clicks and animation for a moment, so the nav
        bar stays live however long the view takes. If that input chose
        another tab, the build is abandoned."""
        name = self._build_name
        if name is None or self._in_checkpoint:
            return
        if time.perf_counter() - self._last_checkpoint < 0.025:
            return
        self._in_checkpoint = True
        try:
            self.root.update()
        finally:
            self._in_checkpoint = False
            self._last_checkpoint = time.perf_counter()
        if self._build_name != name or self.active_tab != name or self.collapsed:
            raise yielding.Abandoned()

    def show_tab(self, name: str, fade: bool = False) -> None:
        """``fade`` is for a view replacing its own skeleton: the skeleton
        stays on screen, lifted over the view while it is built beneath it
        (the window stays visible and clickable, and the build keeps
        yielding to input — see ``_checkpoint``); once built, the skeleton
        is snapshotted and that snapshot dissolves away over the built view
        rather than being swapped out in one step."""
        # Switching tabs never runs the view being left behind's own
        # ``refresh()`` (only the one being shown gets that call, below), so
        # this is the one spot that view's own self-flush can't cover itself.
        theme.commit_focused_entry(self.root)
        self._close_flyout()
        self._finish_reveal()
        behind = fade and getattr(self, "_skeleton", None) is not None
        cover = None
        if not behind:
            # Taken while the skeleton is still up, so it is what fades out.
            cover = self._cover_start() if fade else None
            fade = fade and cover is not None
            self._clear_skeleton()
        self._mark_tab(name)

        for view in self._views.values():
            view.frame.pack_forget()  # type: ignore[attr-defined]

        if cover is None and not behind:
            cover = self._cover_start()
        before = set(self.content.winfo_children())
        try:
            if behind:
                self._build_name = name
            view = self._views.get(name)
            if view is None:
                view = self._make_view(name)
                self._views[name] = view
            view.frame.pack(fill="both", expand=True)  # type: ignore[attr-defined]
            if behind:
                self._skeleton.frame.lift()  # over the view, whichever was made last
            view.refresh()  # type: ignore[attr-defined]
            if behind:
                self._build_name = None
                cover = self._cover_start()
                fade = cover is not None
                self._clear_skeleton()
        except yielding.Abandoned:
            # Another tab was chosen while this one was still building: what
            # it made is dropped (a view only half-made is destroyed; one
            # that already existed is just refreshed again next time).
            keep = getattr(getattr(self, "_skeleton", None), "frame", None)
            if name not in self._views:
                for child in self.content.winfo_children():
                    if child not in before and child is not keep:
                        child.destroy()
        finally:
            self._build_name = None
            if cover is not None:
                self._cover_end(cover, fade=fade)
        if self._refresh_again:
            self._refresh_again = False
            self.refresh()

    def _mark_tab(self, name: str) -> None:
        self.active_tab = name
        for tab, btn in self.tab_buttons.items():
            active = tab == name
            btn.config(fg=theme.TEXT if active else theme.MUTED)
        self._slide_pill(name)

    def navigate(self, name: str) -> None:
        """Switching tabs from the nav bar, optimistically: the tab is
        marked and a skeleton of the screen is on screen straight away, and
        the real view is built right after, replacing it — so a click is
        answered at once however long the view takes to build."""
        if self.collapsed or getattr(self, "_morphing", False):
            self.show_tab(name)
            return
        if self._build_name == name:
            return  # that is the very tab being built right now
        theme.commit_focused_entry(self.root)
        self._finish_reveal()
        self._clear_skeleton()
        self._mark_tab(name)
        for view in self._views.values():
            view.frame.pack_forget()  # type: ignore[attr-defined]
        from .skeleton import Skeleton

        skeleton = Skeleton(self.content, name)
        skeleton.show_over(self.content)  # over the view built beneath it
        self._skeleton = skeleton
        self.root.update_idletasks()  # painted before the build starts

        def build() -> None:
            if getattr(skeleton, "taken", False):
                return  # the watchdog below is loading it
            # A later click (or a direct ``show_tab``) has taken over.
            if self._build_name is not None:
                # Another view is mid-build and is about to give way to this
                # one (or finish); start once it has unwound.
                self.root.after(12, build)
                return
            if self._skeleton is skeleton and self.active_tab == name:
                # Building the view blocks Tk for a while, which would
                # freeze the tab pill mid-glide; so it waits for the pill to
                # land and its label to fade in (the skeleton is already on screen meanwhile).
                pill = getattr(self, "_pill_state", None)
                if pill is not None and (pill["job"] is not None or pill["fade"] is not None):
                    self.root.after(12, build)
                    return
                self.show_tab(name, fade=True)

        self.root.after(12, build)

        def watchdog() -> None:
            """Still on the skeleton two seconds on: the view never loaded
            (something kept ``build`` waiting), so load it now."""
            if self._skeleton is not skeleton or self.active_tab != name:
                return  # it loaded, or another tab took over
            if self._build_name is not None:
                # Something is mid-build: give it one more stretch, then
                # treat it as dead (a build that died without clearing this
                # would otherwise block every later load for good).
                if not skeleton.__dict__.setdefault("waited", False):
                    skeleton.waited = True
                    self.root.after(2000, watchdog)
                    return
                self._build_name = None
                self._in_checkpoint = False
            skeleton.taken = True  # the ordinary build stands down
            try:
                self.show_tab(name, fade=True)
            except tk.TclError:
                pass

        self.root.after(2000, watchdog)

    def _clear_skeleton(self) -> None:
        skeleton, self._skeleton = getattr(self, "_skeleton", None), None
        if skeleton is not None:
            skeleton.dispose()

    def _make_view(self, name: str):
        from .today import TodayView
        from .week import WeekView
        from .month import MonthView
        from .export import ExportView
        from .projects import ProjectsView
        from .recurring import RecurringView
        from .setup import SetupView

        classes = {
            "Today": TodayView,
            "Week": WeekView,
            "Month": MonthView,
            "Projects": ProjectsView,
            "Recurring": RecurringView,
            "Setup": SetupView,
            "Export": ExportView,
        }
        return classes[name](self.content, self.store, self)

    def refresh(self) -> None:
        self._refresh_timerbar()
        self._refresh_tray()
        self._refresh_undo_redo()
        if self.collapsed or self._refresh_pending:
            return
        # Deferred to the idle queue: a store change is usually triggered from a
        # widget callback, and rebuilding the view would destroy the very widget
        # whose handler is still on the stack. Also collapses bursts of changes
        # into one rebuild.
        self._refresh_pending = True
        self.root.after_idle(self._rebuild_active_view)

    def _rebuild_active_view(self) -> None:
        self._refresh_pending = False
        if self._build_name is not None:
            # A view is mid-build (and yielding to input): rebuilding now
            # would re-enter it. It runs once, right after the build ends.
            self._refresh_again = True
            return
        theme.commit_focused_entry(self.root)
        view = self._views.get(self.active_tab)
        if view is not None and not self.collapsed:
            # Every view rebuilds wholesale on every store change (see
            # ``ScrollFrame.clear``) — a nudge click, a note losing focus,
            # a drag landing. That's dozens of widgets torn down and
            # recreated for what's usually a one-line change, and Windows
            # paints each step of it unless told not to; hence the freeze
            # bracketing it, not just around the heavier tabs.
            # The cover (a snapshot of the window, held on top while the
            # widgets are torn down and rebuilt underneath) hides the whole
            # rebuild; only if Windows won't take a snapshot does it fall
            # back to just suspending repaints.
            hwnd = self._hwnd()
            # A view that refreshes block by block (``incremental``) only
            # redraws the blocks that changed, so there's nothing to hide:
            # no window-wide cover, no repaint freeze.
            incremental = getattr(view, "incremental", False)
            cover = None if incremental else self._cover_start()
            if cover is None and not incremental:
                dwm.freeze(hwnd)
            self._rebuilding = True
            try:
                view.refresh()  # type: ignore[attr-defined]
            finally:
                self._rebuilding = False
                self._last_rebuild_at = time.monotonic()
                if incremental:
                    pass
                elif cover is None:
                    dwm.thaw(hwnd)
                else:
                    self._cover_end(cover)

    def _cover_start(self):
        """Freezes what's on screen: snapshots the window, shows the
        snapshot in a click-through ghost exactly over it, then makes the
        real window transparent so whatever is rebuilt under the ghost can
        never be seen mid-way. ``None`` when that isn't possible (collapsed,
        hidden, mid-animation, or Windows refused the snapshot)."""
        self._finish_reveal()
        if (
            self.collapsed
            or getattr(self, "_morphing", False)
            or getattr(self, "_cover", None) is not None
            or self.root.state() == "withdrawn"
        ):
            return None
        rect = (
            self.root.winfo_x(), self.root.winfo_y(),
            self.root.winfo_width(), self.root.winfo_height(),
        )
        if rect[2] < 2 or rect[3] < 2:
            return None
        snap = ghost = None
        try:
            snap = winfx.capture(self._hwnd(), rect[2], rect[3])
            ghost = winfx.Ghost(rect[2], rect[3])
            dwm.corners(ghost.hwnd, dwm.ROUNDED)
            ghost.draw(rect, snap, snap, 0.0, self.store.db.settings.opacity)
            self.root.attributes("-alpha", 0.0)
        except OSError:
            for item in (ghost, snap):
                if item is not None:
                    item.close()
            return None
        self._cover = (ghost, snap)
        return self._cover

    #: How long the skeleton's snapshot takes to dissolve over a built view.
    REVEAL_FADE_MS = 320

    def _cover_end(self, cover, fade: bool = False) -> None:
        """Paints the rebuilt window while it's still hidden, reveals it, and
        only then lifts the snapshot — so the swap is a single step."""
        ghost, snap = cover
        handed_off = False  # the fade, once begun, owns (and closes) the snapshot
        try:
            self.root.update()
            opacity = self.store.db.settings.opacity
            self.root.attributes("-alpha", opacity)
            self.root.update_idletasks()
            if fade:
                # The snapshot is still over the live window (click-through):
                # dissolve it, so the real content fades in beneath. Driven
                # from the event loop, not a blocking loop, so a click on
                # another tab is handled at once and cuts the fade short.
                self._cover = None
                handed_off = True
                self._begin_reveal(ghost, snap, opacity)
                return
        finally:
            self._cover = None
            if not handed_off:
                ghost.close()
                snap.close()

    def _begin_reveal(self, ghost, snap, opacity: float) -> None:
        self._finish_reveal()
        rect = (
            self.root.winfo_x(), self.root.winfo_y(),
            self.root.winfo_width(), self.root.winfo_height(),
        )
        state = {"ghost": ghost, "snap": snap, "start": time.perf_counter(), "job": None}
        self._reveal = state

        def step() -> None:
            if self._reveal is not state:
                return
            t = min(1.0, (time.perf_counter() - state["start"]) * 1000 / self.REVEAL_FADE_MS)
            try:
                ghost.draw(rect, snap, snap, 0.0, opacity * (1 - anim.smoothstep(t)))
            except OSError:
                t = 1.0
            if t < 1.0:
                state["job"] = self.root.after(8, step)
            else:
                self._finish_reveal()

        step()

    def _finish_reveal(self) -> None:
        """Ends a running reveal at once (it finished, or something else —
        another tab, a collapse, a rebuild — needs the window now)."""
        state, self._reveal = getattr(self, "_reveal", None), None
        if state is None:
            return
        if state["job"] is not None:
            try:
                self.root.after_cancel(state["job"])
            except tk.TclError:
                pass
        state["ghost"].close()
        state["snap"].close()

    def _refresh_undo_redo(self) -> None:
        self.undo_btn.config(state="normal" if self.store.can_undo() else "disabled")
        self.redo_btn.config(state="normal" if self.store.can_redo() else "disabled")

    def _refresh_timerbar(self) -> None:
        timer = self.store.db.active_timer
        running = timer is not None
        paused = running and timer.is_paused

        self.timer_dot.delete("all")
        dot_fill = theme.IDLE
        if running:
            dot_fill = theme.MUTED if paused else theme.BILLABLE
        self.timer_dot.create_oval(1, 1, 12, 12, fill=dot_fill, outline="")

        # Collapsed, the badge is the only indicator left, so it has to carry
        # the running state too.
        if self.collapsed:
            self._paint_badge_border()
            self._refresh_badge_timer()

        if not running:
            self.timerbar.config(bg=theme.BG_RAISED)
            self.timer_stop.pack_forget()
            self.timer_pause.pack_forget()
            self.timer_clock.config(text="")
            self.timer_preview.config(text="")
            self.timer_total.config(text="")
            self.timer_add_preview.config(text="")

            target = self._selected_timer_target()
            if target is None:
                # Nothing running and nothing armed to start next — the bar
                # would only ever say "No timer running", which the rest of
                # the screen already implies, so it steps aside rather than
                # sit there as a permanent fixture saying nothing useful.
                self.timerbar.pack_forget()
                self.timerbar_hairline.pack_forget()
                return

            if not self.timerbar.winfo_ismapped():
                self.timerbar.pack(side="bottom", fill="x", before=self.content)
                self.timerbar_hairline.pack(side="bottom", fill="x", before=self.content)
            self.timer_title.config(
                text=f"Ready to start: {self._row_timer_label(target)}",
                fg=theme.TEXT, font=theme.FONT_BOLD,
            )
            if not self.timer_start.winfo_ismapped():
                self.timer_start.pack(side="right", padx=(0, 8))
            return

        if not self.timerbar.winfo_ismapped():
            self.timerbar.pack(side="bottom", fill="x", before=self.content)
            self.timerbar_hairline.pack(side="bottom", fill="x", before=self.content)
        self.timer_start.pack_forget()
        title = self._running_title()
        self.timer_title.config(
            text=f"{title} \u2014 paused" if paused else title,
            fg=theme.MUTED if paused else theme.TEXT, font=theme.FONT_BOLD,
        )

        if not self.timer_stop.winfo_ismapped():
            self.timer_stop.pack(side="right", padx=(0, 8))
        self.timer_pause.set_text("\u25b6" if paused else "\u23f8")
        self.timer_pause.set_enabled(True, text_fill=theme.ACCENT if paused else theme.MUTED)
        if not self.timer_pause.winfo_ismapped():
            self.timer_pause.pack(side="right", padx=(0, 4))

    @staticmethod
    def _row_timer_label(row) -> str:
        """What the timer bar should actually call this row — its own
        entry's note when it's logged one, since that's what tells one
        entry apart from however many others sit against the same task
        today; the task's own name (``context.display_title``, which
        folds in its Phase's own title when nested) only as a fallback —
        a placeholder picked to start a fresh entry, or a logged one left
        with a blank note, has nothing of its own to show instead.
        """
        if row.entry is not None and row.entry.note.strip():
            return row.entry.note
        return row.context.display_title

    def _selected_timer_target(self):
        """The Day tab's row armed to start next, if any — ``None`` before
        the Today view has ever been built, same as if nothing were armed.
        """
        view = self._views.get("Today")
        if view is None or not hasattr(view, "selected_timer_target"):
            return None
        return view.selected_timer_target()

    def start_selected_timer(self) -> None:
        target = self._selected_timer_target()
        if target is None:
            return
        # ``target.note`` (not ``plan_note``) so a row that already has its
        # own logged ``Entry`` carries that entry's real note into the timer
        # bar instead of showing blank and reading like a fresh one's being
        # started from scratch. ``entry_id`` is what actually tells
        # ``stop_timer`` to top that same entry up rather than create a new
        # one alongside it — see ``ActiveTimer.entry_id``.
        self.store.start_timer(
            target.context.task.id, note=target.note,
            recurring_note_id=target.recurring_note_id,
            entry_id=target.entry.id if target.entry else "",
        )

    def _toggle_timer_pause(self) -> None:
        timer = self.store.db.active_timer
        if timer is None:
            return
        if timer.is_paused:
            self.store.resume_timer()
        else:
            self.store.pause_timer()

    # -- loops --------------------------------------------------------------

    def _tick(self) -> None:
        self._poll_idle()
        self._reassert_topmost()
        if self.collapsed:
            self._refresh_badge_timer()
        timer = self.store.db.active_timer
        if timer:
            elapsed = format_clock(timer.elapsed_seconds)
            self.timer_clock.config(text=elapsed)
            # 3 decimal places, not the usual 1 (see
            # ``dates.format_decimal_hours``) — a live readout that barely
            # moves for minutes at a stretch reads as stuck, even though
            # it's being recomputed every second same as ``elapsed`` right
            # below it.
            preview_text = f"{format_decimal_hours(timer.elapsed_seconds / 60, places=3)}h"
            plan_minutes = self.store.timer_plan_minutes()
            if plan_minutes:
                # Only when this row actually has one set — the bare
                # elapsed figure alone otherwise, same as before there was
                # anything to compare it against.
                preview_text += f" / {format_decimal_hours(plan_minutes, places=2)}h"
            self.timer_preview.config(text=preview_text)
            live_total = self.store.timer_live_minutes()
            if live_total is not None:
                self.timer_total.config(
                    text=f" \u00b7 total {format_decimal_hours(live_total, places=3)}h"
                )
            preview = self.store.timer_preview()
            if preview is not None:
                add, total = preview
                # 2 decimal places, not the usual 1 — the standard
                # increment this is built from (``Store.timer_preview``)
                # is as fine as 15-minute steps by default, and 0.25h
                # rounds to "0.2" at one decimal place (Python's own
                # round-half-to-even), silently disagreeing with an
                # entry that's actually sitting at exactly 0.25.
                self.timer_add_preview.config(
                    text=(
                        f"+{format_decimal_hours(add, places=2)}h \u2192 "
                        f"{format_decimal_hours(total, places=2)}h"
                    )
                )
            view = self._views.get(self.active_tab)
            if view is not None and not self.collapsed and hasattr(view, "tick"):
                view.tick()
            if self.tray is not None:
                # Hovering the tray is the one way to read the clock while
                # the overlay is hidden, so it counts up with it.
                self.tray.set_tooltip(f"{self._running_title()} \u2014 {elapsed}")
        self.root.after(1000, self._tick)

    def _poll_idle(self) -> None:
        """Auto-pauses (and un-pauses) a running timer against system-wide
        idle time — see ``timesheet.idle`` and
        ``Settings.idle_auto_pause``/``idle_pause_minutes``.

        Only ever resumes a pause it caused itself
        (``ActiveTimer.auto_paused``) — one the user chose by pressing
        Pause stays paused regardless of activity, same as
        ``Store.pause_timer``'s own docstring says."""
        timer = self.store.db.active_timer
        if timer is None:
            return
        settings = self.store.db.settings
        threshold = settings.idle_pause_minutes * 60
        if not settings.idle_auto_pause or threshold <= 0:
            return
        idle_seconds = idle.seconds_since_last_input()
        if not timer.is_paused and idle_seconds >= threshold:
            self.store.pause_timer(auto=True)
        elif timer.is_paused and timer.auto_paused and idle_seconds < threshold:
            self.store.resume_timer()

    def _pump(self) -> None:
        self.hotkeys.pump()
        if self.tray is not None:
            self.tray.pump()
        self.root.after(100, self._pump)

    # -- notification area ----------------------------------------------------

    def _start_tray(self) -> None:
        self.tray = None
        if not self.store.db.settings.tray:
            return
        self.tray = TrayIcon("Timesheet", launcher.icon())
        self.tray.on(TrayIcon.CLICK, self.toggle_visible)
        self.tray.on("show", self.show_window)
        self.tray.on("timer", self.toggle_timer)
        self.tray.on("quit", self.quit)
        self.tray.start()
        self._refresh_tray()

    def set_tray(self, enabled: bool) -> None:
        self.store.patch_settings(tray=enabled)
        if self.tray is not None:
            self.tray.stop()
            self.tray = None
        if enabled:
            self._start_tray()

    def _refresh_tray(self) -> None:
        """Keeps the tooltip and menu in step with the timer.

        Both are pushed from here rather than read by the tray thread when
        it needs them, so nothing off the UI thread touches the store.
        """
        if self.tray is None:
            return
        running = self._running_title()
        self.tray.set_menu(
            [
                ("Show the overlay", "show"),
                (f"Stop \u2014 {running}" if running else "Start the last task", "timer"),
                (None, None),
                ("Quit", "quit"),
            ]
        )
        if not running:
            self.tray.set_tooltip("Timesheet \u2014 no timer running")

    def _running_title(self) -> str:
        timer = self.store.db.active_timer
        if timer is None:
            return ""
        task = self.store.db.task(timer.task_id)
        if task is None:
            return "Unknown task"
        entry = None
        if timer.entry_id:
            entry = next((e for e in self.store.db.entries if e.id == timer.entry_id), None)
        if entry is not None and entry.note.strip():
            return entry.note
        context = self.store.context(task)
        return context.display_title if context else task.title

    def show_window(self) -> None:
        if self.root.state() == "withdrawn":
            self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_window(self) -> None:
        self.root.withdraw()

    def close(self) -> None:
        """What an OS-level close request (Alt+F4, "Close window" from the
        taskbar) does — there's no in-app close button; quitting for good is
        the tray icon's job alone, via its right-click menu.

        With a tray icon there's a way back, so closing hides; without one it
        would put the app somewhere the user can't reach it, so it quits.
        """
        if self.tray is not None and not self.tray.failed:
            self.hide_window()
        else:
            self.quit()

    def accelerator_for(self, name: str) -> str:
        """The effective accelerator for a hotkey action — the user's
        override if they've set one, else ``DEFAULT_HOTKEYS[name]``."""
        return self.store.db.settings.hotkeys.get(name) or DEFAULT_HOTKEYS[name]

    def set_hotkey(self, name: str, accelerator: str | None) -> None:
        """Changes ``name``'s accelerator, or resets it to default if
        ``accelerator`` is ``None``, and re-registers it immediately."""
        overrides = dict(self.store.db.settings.hotkeys)
        if accelerator is None:
            overrides.pop(name, None)
        else:
            overrides[name] = accelerator
        self.store.patch_settings(hotkeys=overrides)
        self.hotkeys.rebind(name, self.accelerator_for(name))

    def _start_hotkeys(self) -> None:
        self.hotkeys = HotkeyListener()
        self.hotkeys.bind("collapse", self.accelerator_for("collapse"), lambda: self.collapse(True))
        self.hotkeys.bind("pin", self.accelerator_for("pin"), self.toggle_always_on_top)
        self.hotkeys.bind("timer", self.accelerator_for("timer"), self.toggle_timer)
        self.hotkeys.start()

    # -- lifecycle -----------------------------------------------------------

    def quit(self) -> None:
        self._save_geometry()
        self.store.save()
        self.hotkeys.stop()
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()

    def run(self) -> None:
        self._last_activity = time.monotonic()
        self.root.after(self.DOCK_POLL_MS, self._dock_watch)
        self.root.mainloop()
