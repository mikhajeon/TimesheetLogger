"""The overlay window: frameless, always-on-top, and draggable by its header."""

from __future__ import annotations

import json
import tkinter as tk

from .. import paths
from ..dates import format_clock
from ..hotkeys import HotkeyListener
from ..store import Store
from . import theme

FULL_SIZE = (470, 700)
WIDE_WIDTH = 1020
PILL_SIZE = (260, 34)

TABS = ["Today", "Week", "Export", "Setup"]


class Overlay:
    def __init__(self) -> None:
        self.store = Store(paths.data_file())
        self.store.load()

        self.root = tk.Tk()
        self.root.title("Timesheet Logger")
        self.root.configure(bg=theme.BG)
        self.root.minsize(380, 260)

        self.collapsed = False
        self.wide = False
        self._drag_origin: tuple[int, int] | None = None
        self._views: dict[str, object] = {}
        self._refresh_pending = False
        self.active_tab = "Today"

        self._build()
        self._restore_geometry()
        self._apply_window_settings()

        self.store.subscribe(self.refresh)
        self._start_hotkeys()

        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.bind("<Escape>", lambda _e: self.collapse(True))

        self.show_tab("Today")
        self.refresh()  # a timer may already be running from a previous session
        self._tick()
        self._pump()

    # -- construction ----------------------------------------------------

    def _build(self) -> None:
        self.shell = theme.frame(self.root)
        self.shell.pack(fill="both", expand=True)

        self._build_titlebar()
        self._build_timerbar()
        self._build_tabs()

        self.content = theme.frame(self.shell)
        self.content.pack(fill="both", expand=True)

        self._build_pill()

    def _build_titlebar(self) -> None:
        bar = theme.frame(self.shell, bg=theme.BG_RAISED)
        bar.pack(fill="x")
        self.titlebar = bar

        title = theme.label(bar, "TIMESHEET", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_HEAD)
        title.pack(side="left", padx=(10, 0), pady=6)

        for widget in (bar, title):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", lambda _e: self._save_geometry())

        self.close_btn = theme.button(
            bar, "\u00d7", self.quit, bg=theme.BG_RAISED, fg=theme.MUTED,
            hover=theme.DANGER, padx=7, pady=1,
        )
        self.close_btn.pack(side="right", padx=(0, 4))

        self.collapse_btn = theme.button(
            bar, "\u2013", lambda: self.collapse(True), bg=theme.BG_RAISED,
            fg=theme.MUTED, padx=7, pady=1,
        )
        self.collapse_btn.pack(side="right")

        self.wide_btn = theme.button(
            bar, "\u21e4", self.toggle_wide, bg=theme.BG_RAISED, fg=theme.MUTED, padx=7, pady=1
        )
        self.wide_btn.pack(side="right")

        self.pin_btn = theme.button(
            bar, "\U0001f4cc", self.toggle_always_on_top, bg=theme.BG_RAISED,
            fg=theme.MUTED, padx=7, pady=1,
        )
        self.pin_btn.pack(side="right")

        theme.hairline(self.shell).pack(fill="x")

    def _build_timerbar(self) -> None:
        self.timerbar = theme.frame(self.shell, bg=theme.BG_RAISED)
        self.timerbar.pack(fill="x")

        self.timer_dot = tk.Canvas(
            self.timerbar, width=10, height=10, bg=theme.BG_RAISED, highlightthickness=0, bd=0
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

        theme.hairline(self.shell).pack(fill="x")

    def _build_tabs(self) -> None:
        bar = theme.frame(self.shell, bg=theme.BG_RAISED)
        bar.pack(fill="x")
        self.tab_buttons: dict[str, tk.Button] = {}
        self.tab_underlines: dict[str, tk.Frame] = {}
        for name in TABS:
            column = theme.frame(bar, bg=theme.BG_RAISED)
            column.pack(side="left")
            btn = theme.button(
                column, name, lambda n=name: self.show_tab(n),
                bg=theme.BG_RAISED, fg=theme.MUTED, padx=11, pady=5,
            )
            btn.pack()
            underline = theme.frame(column, bg=theme.BG_RAISED, height=2)
            underline.pack(fill="x")
            self.tab_buttons[name] = btn
            self.tab_underlines[name] = underline
        theme.hairline(self.shell).pack(fill="x")

    def _build_pill(self) -> None:
        """The collapsed view: a thin always-on-top strip showing the timer."""
        self.pill = theme.frame(self.root, bg=theme.BG_RAISED)

        self.pill_dot = tk.Canvas(
            self.pill, width=10, height=10, bg=theme.BG_RAISED, highlightthickness=0, bd=0
        )
        self.pill_dot.pack(side="left", padx=(9, 6))

        self.pill_label = theme.label(self.pill, "Timesheet", bg=theme.BG_RAISED, fg=theme.MUTED)
        self.pill_label.pack(side="left", fill="x", expand=True)

        self.pill_clock = theme.label(
            self.pill, "", bg=theme.BG_RAISED, fg=theme.TEXT, font=theme.FONT_CLOCK
        )
        self.pill_clock.pack(side="left", padx=4)

        theme.button(
            self.pill, "\u21e2", lambda: self.collapse(False),
            bg=theme.BG_RAISED, fg=theme.MUTED, padx=7, pady=1,
        ).pack(side="right", padx=(0, 4))

        for widget in (self.pill, self.pill_label, self.pill_dot):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<Double-Button-1>", lambda _e: self.collapse(False))

    # -- window behaviour -------------------------------------------------

    def _apply_window_settings(self) -> None:
        settings = self.store.db.settings
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", settings.always_on_top)
        self.root.attributes("-alpha", settings.opacity)
        self.pin_btn.config(fg=theme.ACCENT if settings.always_on_top else theme.MUTED)

    def toggle_always_on_top(self) -> None:
        value = not self.store.db.settings.always_on_top
        self.store.patch_settings(always_on_top=value)
        self.root.attributes("-topmost", value)
        self.pin_btn.config(fg=theme.ACCENT if value else theme.MUTED)

    def set_opacity(self, value: float) -> None:
        # Applied and persisted directly rather than through patch_settings:
        # notifying listeners on every step of a slider drag would rebuild the
        # Setup view out from under the slider being dragged.
        value = max(0.3, min(1.0, value))
        self.store.db.settings.opacity = value
        self.store.save()
        self.root.attributes("-alpha", value)

    def toggle_wide(self) -> None:
        self.wide = not self.wide
        width = WIDE_WIDTH if self.wide else FULL_SIZE[0]
        height = max(self.root.winfo_height(), FULL_SIZE[1] // 2)
        self.root.geometry(f"{width}x{height}")
        self.wide_btn.config(text="\u21e5" if self.wide else "\u21e4")
        self._save_geometry()

    def collapse(self, collapsed: bool) -> None:
        if collapsed == self.collapsed:
            return
        self.collapsed = collapsed
        if collapsed:
            self._save_geometry()
            self.shell.pack_forget()
            self.pill.pack(fill="both", expand=True)
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.root.geometry(f"{PILL_SIZE[0]}x{PILL_SIZE[1]}+{x}+{y}")
        else:
            self.pill.pack_forget()
            self.shell.pack(fill="both", expand=True)
            self._restore_geometry(position_only=False)
        self.refresh()

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

        # Guard against a monitor that is no longer attached.
        if x is None or not (0 <= x < self.root.winfo_screenwidth() - 60):
            x = (self.root.winfo_screenwidth() - width) // 2
        if y is None or not (0 <= y < self.root.winfo_screenheight() - 40):
            y = 80

        self.root.geometry(f"{width}x{height}+{x}+{y}" if not position_only else f"+{x}+{y}")

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
        from .export import ExportView
        from .setup import SetupView

        classes = {"Today": TodayView, "Week": WeekView, "Export": ExportView, "Setup": SetupView}
        return classes[name](self.content, self.store, self)

    def refresh(self) -> None:
        self._refresh_timerbar()
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
        view = self._views.get(self.active_tab)
        if view is not None and not self.collapsed:
            view.refresh()  # type: ignore[attr-defined]

    def _refresh_timerbar(self) -> None:
        timer = self.store.db.active_timer
        running = timer is not None

        for canvas in (self.timer_dot, self.pill_dot):
            canvas.delete("all")
            canvas.create_oval(
                1, 1, 9, 9, fill=theme.BILLABLE if running else "#3a4156", outline=""
            )

        if not running:
            self.timerbar.config(bg=theme.BG_RAISED)
            self.timer_title.config(text="No timer running", fg=theme.MUTED, font=theme.FONT)
            self.timer_note.pack_forget()
            self.timer_stop.pack_forget()
            self.timer_clock.config(text="")
            self.pill_label.config(text="Idle", fg=theme.MUTED)
            self.pill_clock.config(text="")
            return

        task = self.store.db.task(timer.task_id)
        title = task.title if task else "Unknown task"
        self.timer_title.config(text=title, fg=theme.TEXT, font=theme.FONT_BOLD)
        self.pill_label.config(text=title, fg=theme.TEXT)

        if not self.timer_note.winfo_ismapped():
            self.timer_note.pack(fill="x", pady=(2, 6))
        if self.read_timer_note() != timer.note and self.root.focus_get() is not self.timer_note:
            self.timer_note.delete(0, "end")
            self.timer_note.insert(0, timer.note)
            self.timer_note.config(fg=theme.TEXT)
        if not self.timer_stop.winfo_ismapped():
            self.timer_stop.pack(side="right", padx=(0, 8))

    def _commit_timer_note(self) -> None:
        if self.store.db.active_timer:
            self.store.set_timer_note(self.read_timer_note())

    # -- loops --------------------------------------------------------------

    def _tick(self) -> None:
        timer = self.store.db.active_timer
        if timer:
            import time

            text = format_clock(time.time() - timer.started_at)
            self.timer_clock.config(text=text)
            self.pill_clock.config(text=text)
            view = self._views.get(self.active_tab)
            if view is not None and not self.collapsed and hasattr(view, "tick"):
                view.tick()
        self.root.after(1000, self._tick)

    def _pump(self) -> None:
        self.hotkeys.pump()
        self.root.after(100, self._pump)

    def _start_hotkeys(self) -> None:
        self.hotkeys = HotkeyListener()
        self.hotkeys.bind("ctrl+alt+t", self.toggle_visible)
        self.hotkeys.bind("ctrl+alt+a", self.toggle_always_on_top)
        self.hotkeys.bind("ctrl+alt+s", self.toggle_timer)
        self.hotkeys.start()

    # -- lifecycle -----------------------------------------------------------

    def quit(self) -> None:
        self._save_geometry()
        self.store.save()
        self.hotkeys.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
