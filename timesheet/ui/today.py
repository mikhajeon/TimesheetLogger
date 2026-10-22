"""Today: the day's tasks, the timer, and manual time entry."""

from __future__ import annotations

import tkinter as tk

from ..dates import add_days, format_minutes, parse_duration, pretty_date, today
from ..store import Store, TaskContext
from . import theme
from .addtask import AddTaskPanel
from .scroll import ScrollFrame


class TodayView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.date = today()
        self._expanded: set[str] = set()
        self._live_labels: dict[str, tk.Label] = {}
        # Tk only weakly references a widget's variable; locals would be
        # collected and the checkbox would render unchecked.
        self._vars: list[tk.Variable] = []

        self.frame = theme.frame(parent)
        self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        nav = theme.frame(self.frame)
        nav.pack(fill="x", padx=10, pady=(10, 6))

        theme.button(nav, "\u2039", lambda: self._shift(-1), bg=theme.BG, fg=theme.MUTED).pack(side="left")
        self.date_label = theme.button(nav, "", self._go_today, bg=theme.BG, font=theme.FONT_BOLD)
        self.date_label.config(anchor="center")
        self.date_label.pack(side="left", fill="x", expand=True)
        theme.button(nav, "\u203a", lambda: self._shift(1), bg=theme.BG, fg=theme.MUTED).pack(side="left")

        progress = theme.frame(self.frame)
        progress.pack(fill="x", padx=10)
        self.bar = tk.Canvas(progress, height=5, bg=theme.BG_INPUT, highlightthickness=0, bd=0)
        self.bar.pack(fill="x")
        self._bar_values = (0, 1)
        # The canvas has no width until Tk has laid it out, so the first draw
        # would be a no-op without this.
        self.bar.bind("<Configure>", lambda _e: self._draw_bar(*self._bar_values))
        labels = theme.frame(progress)
        labels.pack(fill="x", pady=(4, 0))
        self.total_label = theme.label(labels, "", font=theme.FONT_SMALL)
        self.total_label.pack(side="left")
        self.billable_label = theme.label(labels, "", fg=theme.MUTED, font=theme.FONT_SMALL)
        self.billable_label.pack(side="right")

        self.scroll = ScrollFrame(self.frame)
        self.scroll.pack(fill="both", expand=True, padx=10, pady=(8, 0))

        footer = theme.frame(self.frame)
        footer.pack(fill="x", padx=10, pady=(4, 10))

        self.add_panel = AddTaskPanel(footer, self.store, lambda: self.date)
        self.add_panel.frame.pack(fill="x")

        theme.label(
            footer, "Day note — context that isn't tied to one task",
            fg=theme.MUTED, font=theme.FONT_SMALL,
        ).pack(fill="x", pady=(10, 3))
        self.note = theme.text_box(footer, height=3)
        self.note.pack(fill="x")
        self.note.bind("<FocusOut>", lambda _e: self._commit_note())

    # -- navigation ---------------------------------------------------------

    def _shift(self, days: int) -> None:
        self._commit_note()
        self.date = add_days(self.date, days)
        self._expanded.clear()
        self.refresh()

    def _go_today(self) -> None:
        self._commit_note()
        self.date = today()
        self.refresh()

    def _commit_note(self) -> None:
        text = self.note.get("1.0", "end").strip()
        if text != self.store.db.day_notes.get(self.date, "").strip():
            self.store.set_day_note(self.date, text)

    # -- rendering ----------------------------------------------------------

    def refresh(self) -> None:
        self.date_label.config(text=pretty_date(self.date))

        total, billable = self.store.day_totals(self.date)
        target = self.store.db.settings.daily_target_minutes
        self.total_label.config(text=f"{format_minutes(total)} of {format_minutes(target)}")
        self.billable_label.config(text=f"{format_minutes(billable)} billable")
        self._draw_bar(total, target)

        current = self.note.get("1.0", "end").strip()
        stored = self.store.db.day_notes.get(self.date, "")
        if current != stored.strip():
            self.note.delete("1.0", "end")
            self.note.insert("1.0", stored)

        self.scroll.clear()
        self._live_labels.clear()
        self._vars.clear()
        contexts = self.store.contexts_for_date(self.date)
        if not contexts:
            theme.label(
                self.scroll.body,
                "Nothing planned for this day yet.",
                fg=theme.MUTED, font=theme.FONT_SMALL,
            ).pack(pady=16)
        else:
            for context in contexts:
                self._task_row(context)

        self.add_panel.refresh()

    def _draw_bar(self, total: int, target: int) -> None:
        self._bar_values = (total, target)
        self.bar.delete("all")
        width = self.bar.winfo_width()
        if width <= 1 or target <= 0:
            return
        filled = int(width * min(1.0, total / target))
        self.bar.create_rectangle(0, 0, filled, 5, fill=theme.ACCENT, outline="")

    def _task_row(self, context: TaskContext) -> None:
        task = context.task
        running = (
            self.store.db.active_timer is not None
            and self.store.db.active_timer.task_id == task.id
        )
        border = "#27624a" if running else theme.LINE

        outer = tk.Frame(self.scroll.body, bg=border, padx=1, pady=1)
        outer.pack(fill="x", pady=2)
        card = theme.frame(outer, bg=theme.BG_RAISED)
        card.pack(fill="both", expand=True)

        row = theme.frame(card, bg=theme.BG_RAISED)
        row.pack(fill="x", padx=7, pady=6)

        theme.button(
            row, "\u25a0" if running else "\u25b6",
            (self.store.stop_timer if running else lambda t=task.id: self.store.start_timer(t)),
            bg=theme.BILLABLE if running else theme.BG_INPUT,
            fg="#06120c" if running else theme.TEXT,
            hover=theme.BILLABLE if running else theme.BG_HOVER,
            font=theme.FONT_TINY, padx=6, pady=3,
        ).pack(side="left", padx=(0, 7))

        # Right-hand controls are packed before the stretchy middle so they keep
        # their width when the title is long.
        adjust = theme.frame(row, bg=theme.BG_RAISED)
        adjust.pack(side="right")
        for text, delta in (("+", 15), ("\u2212", -15)):
            theme.button(
                adjust, text,
                lambda d=delta, t=task.id: self.store.adjust_entry(t, self.date, d),
                bg=theme.BG_INPUT, fg=theme.MUTED, font=theme.FONT_TINY, padx=4, pady=0,
            ).pack(fill="x")

        times = theme.frame(row, bg=theme.BG_RAISED)
        times.pack(side="right", padx=(6, 6))
        logged = self.store.logged_minutes(task.id, self.date) + self.store.live_minutes(
            task.id, self.date
        )
        actual = theme.label(
            times, format_minutes(logged), bg=theme.BG_RAISED, font=theme.FONT_BOLD, anchor="e"
        )
        actual.pack(fill="x")
        self._live_labels[task.id] = actual

        planned = self.store.planned_minutes(task.id, self.date)
        if planned:
            theme.label(
                times, f"of {format_minutes(planned)}", bg=theme.BG_RAISED,
                fg=theme.MUTED, font=theme.FONT_TINY, anchor="e",
            ).pack(fill="x")

        middle = theme.frame(row, bg=theme.BG_RAISED)
        middle.pack(side="left", fill="x", expand=True)

        title = theme.label(
            middle, task.title, bg=theme.BG_RAISED,
            fg=theme.MUTED if task.done else theme.TEXT,
        )
        title.pack(fill="x")

        meta = theme.frame(middle, bg=theme.BG_RAISED)
        meta.pack(fill="x")
        theme.dot(meta, task.billable, bg=theme.BG_RAISED).pack(side="left", pady=2)
        theme.pill(meta, context.project.k_code).pack(side="left", padx=4)
        theme.label(
            meta, f"{context.client.name} · {context.project.name}",
            bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
        ).pack(side="left")

        for widget in (middle, title, meta):
            widget.bind("<Button-1>", lambda _e, t=task.id: self._toggle_expand(t))
            widget.config(cursor="hand2")

        if task.id in self._expanded:
            self._detail(card, context)

    def _toggle_expand(self, task_id: str) -> None:
        if task_id in self._expanded:
            self._expanded.discard(task_id)
        else:
            self._expanded.add(task_id)
        self.refresh()

    def _detail(self, card: tk.Frame, context: TaskContext) -> None:
        task = context.task
        theme.hairline(card).pack(fill="x")

        actions = theme.frame(card, bg=theme.BG_RAISED)
        actions.pack(fill="x", padx=7, pady=5)

        done_var = tk.BooleanVar(value=task.done)
        self._vars.append(done_var)
        done = theme.checkbox(
            actions, "Done", done_var, lambda t=task.id: self.store.toggle_done(t)
        )
        done.config(bg=theme.BG_RAISED, activebackground=theme.BG_RAISED)
        done.pack(side="left")

        billable_var = tk.BooleanVar(value=task.billable)
        self._vars.append(billable_var)
        billable = theme.checkbox(
            actions, "Billable", billable_var,
            lambda t=task.id, v=billable_var: self.store.update_task(t, billable=v.get()),
        )
        billable.config(bg=theme.BG_RAISED, activebackground=theme.BG_RAISED)
        billable.pack(side="left", padx=(10, 0))

        theme.button(
            actions, "Archive",
            lambda t=task.id: self.store.update_task(t, archived=True),
            bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_SMALL,
        ).pack(side="right")

        theme.label(
            actions, "Plan", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_SMALL
        ).pack(side="right", padx=(0, 4))
        plan_entry = theme.entry(actions, width=6)
        planned = self.store.planned_minutes(task.id, self.date)
        plan_entry.insert(0, f"{planned / 60:g}" if planned else "")
        plan_entry.pack(side="right", padx=(0, 6))
        plan_entry.bind(
            "<FocusOut>",
            lambda _e, t=task.id, w=plan_entry: self.store.set_plan(
                t, self.date, parse_duration(w.get()) or 0
            ),
        )

        for entry in self.store.entries_for(task.id, self.date):
            line = theme.frame(card, bg=theme.BG_RAISED)
            line.pack(fill="x", padx=7, pady=(0, 4))

            minutes = theme.entry(line, width=6)
            minutes.insert(0, format_minutes(entry.minutes))
            minutes.pack(side="left")
            minutes.bind(
                "<FocusOut>",
                lambda _e, i=entry.id, w=minutes: self._commit_minutes(i, w.get()),
            )

            theme.button(
                line, "\u00d7", lambda i=entry.id: self.store.delete_entry(i),
                bg=theme.BG_RAISED, fg=theme.MUTED, hover=theme.DANGER, padx=5, pady=0,
            ).pack(side="right")

            theme.label(
                line, "\u23f1" if entry.source == "timer" else "\u270e",
                bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
            ).pack(side="right", padx=3)

            note = theme.entry(line)
            note.insert(0, entry.note)
            note.pack(side="left", fill="x", expand=True, padx=5)
            note.bind(
                "<FocusOut>",
                lambda _e, i=entry.id, w=note: self.store.update_entry(i, note=w.get()),
            )

        new_row = theme.frame(card, bg=theme.BG_RAISED)
        new_row.pack(fill="x", padx=7, pady=(0, 7))
        draft = theme.entry(new_row, width=6)
        draft.pack(side="left")
        theme.button(
            new_row, "Log time", lambda t=task.id, w=draft: self._log(t, w),
            bg=theme.BG_INPUT, fg=theme.TEXT, font=theme.FONT_SMALL,
        ).pack(side="left", padx=5)
        draft.bind("<Return>", lambda _e, t=task.id, w=draft: self._log(t, w))

    def _commit_minutes(self, entry_id: str, text: str) -> None:
        minutes = parse_duration(text)
        if minutes is not None:
            self.store.update_entry(entry_id, minutes=minutes)

    def _log(self, task_id: str, widget: tk.Entry) -> None:
        minutes = parse_duration(widget.get())
        if minutes:
            self.store.add_entry(task_id, self.date, minutes)

    def tick(self) -> None:
        """Updates the running task's figure without rebuilding the list."""
        timer = self.store.db.active_timer
        if timer is None:
            return
        label = self._live_labels.get(timer.task_id)
        if label is not None:
            total = self.store.logged_minutes(timer.task_id, self.date) + self.store.live_minutes(
                timer.task_id, self.date
            )
            label.config(text=format_minutes(total))
