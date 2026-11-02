"""The overlay window: frameless, always-on-top, and draggable by its header."""

from __future__ import annotations

import ctypes
import json
import tkinter as tk
from typing import Callable

from .. import dwm, launcher, monitors, paths
from ..dates import format_clock
from ..hotkeys import HotkeyListener
from ..tray import TrayIcon
from ..store import Store
from . import icons, theme

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
#: "small" (``FULL_SIZE``, fixed) after the last one. The other two are a
#: fraction of whichever screen the window is currently on — width and height
#: independently, since neither "quarter" nor "half" means a quarter or half
#: of the screen's width *and* height together. An "eighth" mode (a quarter
#: width, half height) sat between them at first, but on a typical monitor it
#: lands within a few pixels of "small" — two stops that looked the same —
#: so it was dropped rather than kept as a second, redundant "small".
SIZE_MODES = ("small", "quarter", "half")
SIZE_FRACTIONS = {
    "quarter": (0.5, 0.5),   # half the width, half the height
    "half": (0.5, 1.0),      # half the width, the full height
}

#: The collapsed badge: square, and small enough to park in a screen corner.
ICON_SIZE = (46, 46)
#: GetAncestor(GA_ROOT) — Tk's window id is a child of the real top-level one,
#: and that's the one the window manager knows about.
GA_ROOT = 2
#: Diameter of the lamp in its corner that shows a timer is running.
RUNNING_LAMP = 9
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
    "Month": "Month",
    "Projects": "Setup",
    "Recurring": "Recurring",
    "Setup": "Preferences",
    "Export": "Export",
}
#: A vertical rule goes after this tab, setting the logging views (Day, Week,
#: Month) apart from the configuration ones that follow them.
DIVIDER_AFTER = "Month"


class Overlay:
    def __init__(self) -> None:
        self.store = Store(paths.data_file())
        self.store.load()

        theme.use(self.store.db.settings.theme)

        self.root = tk.Tk()
        self.root.title("Timesheet Logger")
        self.root.configure(bg=theme.BG)
        self.root.minsize(*MIN_SIZE)
        self._set_window_icon()

        self.collapsed = False
        #: Where the titlebar's size button is in ``SIZE_MODES``. Reset to
        #: "small" on every launch regardless of the geometry actually
        #: restored below — there's no saved record of which mode produced
        #: it, and starting the cycle fresh is no worse a guess than any other.
        self._size_mode = "small"
        #: Tracked rather than read back from Tk: ``winfo_width`` reports 1
        #: until the window is mapped, and the stale size until a resize has
        #: been processed, both of which would snap to the wrong coordinate.
        self._size = FULL_SIZE
        self._drag_origin: tuple[int, int] | None = None
        self._views: dict[str, object] = {}
        self._refresh_pending = False
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
        self._bind_undo_redo_keys()

        self.show_tab("Today")
        self.refresh()  # a timer may already be running from a previous session
        self._tick()
        self._pump()
        self._watch_screens()
        self._watch_theme()

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
        title.pack(side="left", padx=(10, 6), pady=6)

        for widget in (bar, title):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", lambda _e: self._save_geometry())

        chrome = dict(bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_ICON, padx=6, pady=3)
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

        self.size_btn = icons.button(
            bar, "narrow" if self._size_mode == "half" else "wide", self.cycle_size,
            fallback="\u21e4", **chrome,
        )
        self.size_btn.pack(side="right", padx=GAP)

        self.pin_btn = icons.button(
            bar, "pin", self.toggle_always_on_top, fallback="\U0001f4cc", **chrome
        )
        self.pin_btn.pack(side="right", padx=GAP)

        theme.hairline(self.shell).pack(fill="x")

    def _build_timerbar(self) -> None:
        self.timerbar = theme.frame(self.shell, bg=theme.BG_RAISED)
        self.timerbar.pack(fill="x")

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

        self.timer_note = theme.entry(middle)
        self.read_timer_note = theme.add_placeholder(
            self.timer_note, "What are you doing? This becomes the customer note."
        )
        self.timer_note.bind("<FocusOut>", lambda _e: self._commit_timer_note(), add="+")
        self.timer_note.bind("<Return>", lambda _e: self._commit_timer_note())

        self.timer_clock = theme.label(
            self.timerbar, "", bg=theme.BG_RAISED, fg=theme.TEXT, font=theme.FONT_CLOCK
        )
        self.timer_clock.pack(side="right", padx=(0, 8))

        self.timer_stop = theme.button(
            self.timerbar, "Stop", self.store.stop_timer,
            bg=theme.BG_RAISED, fg=theme.BILLABLE, hover=theme.BILLABLE_BG,
        )
        self.timer_start = theme.button(
            self.timerbar, "Start", self.start_selected_timer,
            bg=theme.BG_RAISED, fg=theme.ACCENT, hover=theme.ACCENT_DIM,
        )

        #: Kept so ``_refresh_timerbar`` can hide the bar entirely (nothing
        #: running, nothing armed) and bring both it and this divider back
        #: in the right spot later, rather than one reappearing without the
        #: other.
        self.timerbar_hairline = theme.hairline(self.shell)
        self.timerbar_hairline.pack(fill="x")

    def _build_tabs(self) -> None:
        bar = theme.frame(self.shell, bg=theme.BG_RAISED)
        bar.pack(fill="x")
        #: The anchor ``_refresh_timerbar`` re-inserts the timer bar and its
        #: hairline ahead of, once hidden — plain ``pack`` would otherwise
        #: drop them at the end of ``shell``'s packing order, behind
        #: ``content``.
        self.tabbar = bar
        self.tab_buttons: dict[str, tk.Button] = {}
        self.tab_underlines: dict[str, tk.Frame] = {}
        #: The three logging views — "Day", "Week", "Month" — read as one
        #: group of equal-weight choices, so each gets the widest one's
        #: width rather than "Day" sitting narrower than its neighbours
        #: just because it's the shortest word.
        EQUAL_WIDTH = {"Today", "Week", "Month"}
        #: The configuration tabs — Setup, Recurring, Preferences, Export —
        #: pack to the bar's own right edge instead of trailing straight
        #: after Month, so the two groups (what you log vs. how it's set up)
        #: read as distinct clusters with the window's own slack between
        #: them, not just one run-on row of seven.
        RIGHT_ALIGNED = {"Projects", "Recurring", "Setup", "Export"}
        columns: dict[str, tk.Frame] = {}

        def build_column(name: str) -> tk.Frame:
            column = theme.frame(bar, bg=theme.BG_RAISED)
            btn = theme.button(
                column, TAB_LABELS.get(name, name), lambda n=name: self.show_tab(n),
                bg=theme.BG_RAISED, fg=theme.MUTED, padx=11, pady=5,
            )
            btn.pack(fill="x")
            underline = theme.frame(column, bg=theme.BG_RAISED, height=2)
            underline.pack(fill="x")
            self.tab_buttons[name] = btn
            self.tab_underlines[name] = underline
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

        self.icon_glyph = icons.label(
            self.icon,
            "badge",
            fallback=ICON_GLYPH,
            bg=theme.BG_RAISED,
            fg=theme.MUTED,
            font=theme.FONT_ICON_LARGE,
        )
        self.icon_glyph.pack(fill="both", expand=True)

        # The badge icon is a full-colour illustration, so unlike the rest of
        # the chrome it can't be tinted to show the timer running. A lamp in
        # the corner says it instead, and reads at a glance besides.
        self.icon_lamp = tk.Canvas(
            self.icon, width=RUNNING_LAMP, height=RUNNING_LAMP,
            bg=theme.BG_RAISED, highlightthickness=0, bd=0,
        )
        self.icon_lamp.create_oval(
            0, 0, RUNNING_LAMP, RUNNING_LAMP, fill=theme.BILLABLE, outline=theme.RUNNING_LINE
        )

        for widget in (self.icon, self.icon_glyph, self.icon_lamp):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<Double-Button-1>", lambda _e: self.collapse(False))

    # -- window behaviour -------------------------------------------------

    def _apply_window_settings(self) -> None:
        settings = self.store.db.settings
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", settings.always_on_top)
        self.root.attributes("-alpha", settings.opacity)
        self._show_pin_state(settings.always_on_top)

    def _show_pin_state(self, pinned: bool) -> None:
        # The icon carries the state in its colour, so it is a different file;
        # fg still matters for the text fallback.
        icons.swap(self.pin_btn, "pin-on" if pinned else "pin", fallback="\U0001f4cc")
        self.pin_btn.config(fg=theme.ACCENT if pinned else theme.MUTED)

    def toggle_always_on_top(self) -> None:
        value = not self.store.db.settings.always_on_top
        self.store.patch_settings(always_on_top=value)
        self.root.attributes("-topmost", value)
        self._show_pin_state(value)

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
        settings = self.store.db.settings
        if settings.snap_corner not in monitors.CORNERS.values():
            return False
        screen = monitors.find(self.screens(), settings.snap_screen)
        margin = BADGE_MARGIN if self.collapsed else monitors.MARGIN
        x, y = monitors.corner_position(screen, settings.snap_corner, *self._size, margin=margin)
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
        index = (SIZE_MODES.index(self._size_mode) + 1) % len(SIZE_MODES)
        self._size_mode = SIZE_MODES[index]
        width, height = self._size_for_mode(self._size_mode)
        self.root.geometry(f"{width}x{height}")
        self._size = (width, height)
        icons.swap(
            self.size_btn, "narrow" if self._size_mode == "half" else "wide",
            fallback="\u21e5" if self._size_mode == "half" else "\u21e4",
        )
        self.apply_snap()
        self._save_geometry()
        # Views lay out to the width they have — the Week grid most of all —
        # so the new one has to be redrawn into, not just resized.
        self._rebuild_active_view()

    def _size_for_mode(self, mode: str) -> tuple[int, int]:
        """The window size ``mode`` resolves to right now.

        "small" is fixed; the other two are a fraction of whichever screen
        the window is currently snapped to (or the primary one, if free) —
        resolved fresh on every call rather than cached, so unplugging a
        monitor or moving between ones of different sizes is reflected the
        next time the button is pressed, same as snapping already does.
        """
        if mode == "small":
            return FULL_SIZE
        screen = monitors.find(self.screens(), self.store.db.settings.snap_screen)
        width_fraction, height_fraction = SIZE_FRACTIONS[mode]
        width = max(MIN_SIZE[0], int(screen.width * width_fraction))
        height = max(MIN_SIZE[1], int(screen.height * height_fraction))
        return width, height

    def collapse(self, collapsed: bool) -> None:
        if collapsed == self.collapsed:
            return
        self.collapsed = collapsed
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
            dwm.corners(self._hwnd(), dwm.SQUARE)
            dwm.border(self._hwnd(), dwm.NO_BORDER)
            self.icon.pack_forget()
            self.shell.pack(fill="both", expand=True)
            self.root.minsize(*MIN_SIZE)
            self._restore_geometry(position_only=False)
        self.refresh()

    def _hwnd(self) -> int:
        return ctypes.windll.user32.GetAncestor(self.root.winfo_id(), GA_ROOT)

    def _round_badge(self) -> None:
        """Rounds the collapsed window's corners, if Windows will.

        Tk's own border is a rectangle and would be left hanging at the arcs,
        so where the rounding takes, the border is handed to DWM as well —
        which is also what tints it while a timer runs.
        """
        self._rounded = dwm.corners(self._hwnd(), dwm.ROUNDED)
        self.icon.config(highlightthickness=0 if self._rounded else 1)
        self._paint_badge_border()

    def _paint_badge_border(self) -> None:
        running = self.store.db.active_timer is not None
        colour = theme.RUNNING_LINE if running else theme.LINE
        if self._rounded:
            dwm.border(self._hwnd(), colour)
        else:
            self.icon.config(highlightbackground=colour)

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
        try:
            saved = json.loads(paths.window_state_file().read_text("utf-8"))
            width, height = saved.get("width", width), saved.get("height", height)
            x, y = saved.get("x"), saved.get("y")
        except Exception:  # noqa: BLE001 - a missing or stale file is fine
            pass

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

        self.root.geometry(f"+{x}+{y}")

    def _save_geometry(self) -> None:
        if self.collapsed:
            return
        try:
            state = {
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

    def show_tab(self, name: str) -> None:
        # Switching tabs never runs the view being left behind's own
        # ``refresh()`` (only the one being shown gets that call, below), so
        # this is the one spot that view's own self-flush can't cover itself.
        theme.commit_focused_entry(self.root)
        self.active_tab = name
        for tab, btn in self.tab_buttons.items():
            active = tab == name
            btn.config(fg=theme.TEXT if active else theme.MUTED)
            self.tab_underlines[tab].config(bg=theme.ACCENT if active else theme.BG_RAISED)

        for view in self._views.values():
            view.frame.pack_forget()  # type: ignore[attr-defined]

        view = self._views.get(name)
        if view is None:
            view = self._make_view(name)
            self._views[name] = view
        view.frame.pack(fill="both", expand=True)  # type: ignore[attr-defined]
        view.refresh()  # type: ignore[attr-defined]

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
        theme.commit_focused_entry(self.root)
        view = self._views.get(self.active_tab)
        if view is not None and not self.collapsed:
            # Every view rebuilds wholesale on every store change (see
            # ``ScrollFrame.clear``) — a nudge click, a note losing focus,
            # a drag landing. That's dozens of widgets torn down and
            # recreated for what's usually a one-line change, and Windows
            # paints each step of it unless told not to; hence the freeze
            # bracketing it, not just around the heavier tabs.
            hwnd = self._hwnd()
            dwm.freeze(hwnd)
            try:
                view.refresh()  # type: ignore[attr-defined]
            finally:
                dwm.thaw(hwnd)

    def _refresh_undo_redo(self) -> None:
        self.undo_btn.config(state="normal" if self.store.can_undo() else "disabled")
        self.redo_btn.config(state="normal" if self.store.can_redo() else "disabled")

    def _refresh_timerbar(self) -> None:
        timer = self.store.db.active_timer
        running = timer is not None

        self.timer_dot.delete("all")
        self.timer_dot.create_oval(
            1, 1, 12, 12, fill=theme.BILLABLE if running else theme.IDLE, outline=""
        )

        # Collapsed, the badge is the only indicator left, so it has to carry
        # the running state too.
        if self.collapsed:
            self._paint_badge_border()
        if running:
            self.icon_lamp.place(relx=1.0, rely=1.0, anchor="se", x=-3, y=-3)
        else:
            self.icon_lamp.place_forget()

        if not running:
            self.timerbar.config(bg=theme.BG_RAISED)
            self.timer_note.pack_forget()
            self.timer_stop.pack_forget()
            self.timer_clock.config(text="")

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
                self.timerbar.pack(fill="x", before=self.tabbar)
                self.timerbar_hairline.pack(fill="x", before=self.tabbar)
            self.timer_title.config(
                text=f"Ready to start: {target.context.display_title}",
                fg=theme.TEXT, font=theme.FONT_BOLD,
            )
            if not self.timer_start.winfo_ismapped():
                self.timer_start.pack(side="right", padx=(0, 8))
            return

        if not self.timerbar.winfo_ismapped():
            self.timerbar.pack(fill="x", before=self.tabbar)
            self.timerbar_hairline.pack(fill="x", before=self.tabbar)
        self.timer_start.pack_forget()
        self.timer_title.config(
            text=self._running_title(), fg=theme.TEXT, font=theme.FONT_BOLD
        )

        if not self.timer_note.winfo_ismapped():
            self.timer_note.pack(fill="x", pady=(2, 6))
        if self.read_timer_note() != timer.note and self.root.focus_get() is not self.timer_note:
            self.timer_note.delete(0, "end")
            self.timer_note.insert(0, timer.note)
            self.timer_note.config(fg=theme.TEXT)
        if not self.timer_stop.winfo_ismapped():
            self.timer_stop.pack(side="right", padx=(0, 8))

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
        self.store.start_timer(
            target.context.task.id, note=target.plan_note,
            recurring_note_id=target.recurring_note_id,
        )

    def _commit_timer_note(self) -> None:
        if self.store.db.active_timer:
            self.store.set_timer_note(self.read_timer_note())

    # -- loops --------------------------------------------------------------

    def _tick(self) -> None:
        timer = self.store.db.active_timer
        if timer:
            import time

            elapsed = format_clock(time.time() - timer.started_at)
            self.timer_clock.config(text=elapsed)
            view = self._views.get(self.active_tab)
            if view is not None and not self.collapsed and hasattr(view, "tick"):
                view.tick()
            if self.tray is not None:
                # Hovering the tray is the one way to read the clock while
                # the overlay is hidden, so it counts up with it.
                self.tray.set_tooltip(f"{self._running_title()} \u2014 {elapsed}")
        self.root.after(1000, self._tick)

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
        return task.title if task else "Unknown task"

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
        self.root.mainloop()
