"""The Today tab: what you planned, what you've actually logged, and the timer."""

from __future__ import annotations

import tkinter as tk

from ..dates import add_days, format_minutes, parse_duration, pretty_date, today
from ..store import Store, TaskContext
from . import theme
from .scroll import ScrollFrame

DEFAULT_PLAN_MINUTES = 30
NUDGE_MINUTES = 15


class TodayView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.date = today()
        self.frame = theme.frame(parent)
        self._expanded: set[str] = set()
        self._adding = False
        self._actual_labels: dict[str, tk.Label] = {}

        self._build_header()
        self.scroll = ScrollFrame(self.frame)
        self.scroll.pack(fill="both", expand=True, padx=8)

    # -- chrome -----------------------------------------------------------

    def _build_header(self) -> None:
        nav = theme.frame(self.frame)
        nav.pack(fill="x", padx=8, pady=(8, 4))

        theme.button(nav, "\u2039", self._previous_day, bg=theme.BG, padx=7).pack(side="left")
        self.date_label = theme.button(
            nav, "", self._jump_to_today, bg=theme.BG, font=theme.FONT_BOLD
        )
        self.date_label.pack(side="left", fill="x", expand=True)
        theme.button(nav, "\u203a", self._next_day, bg=theme.BG, padx=7).pack(side="left")

        self.progress = tk.Canvas(
            self.frame, height=5, bg=theme.BG_INPUT, highlightthickness=0, bd=0
        )
        self.progress.pack(fill="x", padx=8)

        totals = theme.frame(self.frame)
        totals.pack(fill="x", padx=8, pady=(4, 6))
        self.total_label = theme.label(totals, "", font=theme.FONT_SMALL)
        self.total_label.pack(side="left")
        self.billable_label = theme.label(totals, "", fg=theme.MUTED, font=theme.FONT_SMALL)
        self.billable_label.pack(side="right")

    def _previous_day(self) -> None:
        self.date = add_days(self.date, -1)
        self.refresh()

    def _next_day(self) -> None:
        self.date = add_days(self.date, 1)
        self.refresh()

    def _jump_to_today(self) -> None:
        self.date = today()
        self.refresh()

    # -- rendering ---------------------------------------------------------

    def refresh(self) -> None:
        self.date_label.config(text=pretty_date(self.date))

        total, billable = self.store.day_totals(self.date)
        target = self.store.db.settings.daily_target_minutes
        self.total_label.config(text=f"{format_minutes(total)} of {format_minutes(target)}")
        self.billable_label.config(text=f"{format_minutes(billable)} billable")
        self._draw_progress(total, target)

        self.scroll.clear()
        self._actual_labels.clear()
        body = self.scroll.body

        contexts = self.store.contexts_for_date(self.date)
        if not contexts:
            theme.label(
                body,
                "Nothing planned for this day yet.",
                fg=theme.MUTED,
                font=theme.FONT_SMALL,
                anchor="center",
            ).pack(fill="x", pady=16)
        for context in contexts:
            self._task_row(body, context)

        self._add_panel(body)
        self._day_note(body)

    def _draw_progress(self, total: int, target: int) -> None:
        self.progress.delete("all")
        width = max(1, self.progress.winfo_width())
        fraction = min(1.0, total / target) if target else 0.0
        self.progress.create_rectangle(
            0, 0, width * fraction, 5, fill=theme.ACCENT, outline=""
        )

    def _task_row(self, parent: tk.Misc, context: TaskContext) -> None:
        task = context.task
        card = theme.frame(parent, bg=theme.BG_RAISED, highlightthickness=1)
        running = bool(
            self.store.db.active_timer and self.store.db.active_timer.task_id == task.id
        )
        card.config(highlightbackground="#27624a" if running else theme.LINE)
        card.pack(fill="x", pady=2)

        row = theme.frame(card, bg=theme.BG_RAISED)
        row.pack(fill="x", padx=6, pady=5)

        theme.button(
            row,
            "\u25a0" if running else "\u25b6",
            lambda: self._toggle_timer(task.id, running),
            bg=theme.BILLABLE if running else theme.BG_INPUT,
            fg="#06120c" if running else theme.TEXT,
            hover=theme.BILLABLE if running else theme.BG_HOVER,
            font=theme.FONT_TINY,
            padx=5,
            pady=3,
        ).pack(side="left", padx=(0, 7))

        # Right-hand controls are packed before the title so the title is the
        # part that gives way when the window is narrow.
        nudge = theme.frame(row, bg=theme.BG_RAISED)
        nudge.pack(side="right", padx=(5, 0))
        theme.button(
            nudge, "+", lambda: self.store.adjust_entry(task.id, self.date, NUDGE_MINUTES),
            bg=theme.BG_INPUT, fg=theme.MUTED, font=theme.FONT_TINY, padx=4, pady=0,
        ).pack()
        theme.button(
            nudge, "\u2212", lambda: self.store.adjust_entry(task.id, self.date, -NUDGE_MINUTES),
            bg=theme.BG_INPUT, fg=theme.MUTED, font=theme.FONT_TINY, padx=4, pady=0,
        ).pack()

        times = theme.frame(row, bg=theme.BG_RAISED)
        times.pack(side="right")
        logged = self.store.logged_minutes(task.id, self.date) + self.store.live_minutes(
            task.id, self.date
        )
        actual = theme.label(
            times, format_minutes(logged), bg=theme.BG_RAISED, font=theme.FONT_BOLD, anchor="e"
        )
        actual.pack(anchor="e")
        self._actual_labels[task.id] = actual

        planned = self.store.planned_minutes(task.id, self.date)
        if planned:
            theme.label(
                times, f"of {format_minutes(planned)}", bg=theme.BG_RAISED,
                fg=theme.MUTED, font=theme.FONT_TINY, anchor="e",
            ).pack(anchor="e")

        body = theme.frame(row, bg=theme.BG_RAISED)
        body.pack(side="left", fill="x", expand=True)

        title = theme.label(
            body,
            task.title,
            bg=theme.BG_RAISED,
            fg=theme.MUTED if task.done else theme.TEXT,
        )
        title.pack(fill="x")

        meta = theme.frame(body, bg=theme.BG_RAISED)
        meta.pack(fill="x")
        theme.dot(meta, task.billable, bg=theme.BG_RAISED).pack(side="left", pady=1)
        theme.pill(meta, context.project.k_code).pack(side="left", padx=4)
        theme.label(
            meta,
            f"{context.client.name} \u00b7 {context.project.name}",
            bg=theme.BG_RAISED,
            fg=theme.MUTED,
            font=theme.FONT_TINY,
        ).pack(side="left")

        for widget in (body, title, meta):
            widget.bind("<Button-1>", lambda _e, tid=task.id: self._toggle_expanded(tid))
            widget.config(cursor="hand2")

        if task.id in self._expanded:
            self._detail(card, context)

    def _detail(self, parent: tk.Misc, context: TaskContext) -> None:
        task = context.task
        theme.hairline(parent).pack(fill="x")
        box = theme.frame(parent, bg=theme.BG_RAISED)
        box.pack(fill="x", padx=6, pady=5)

        controls = theme.frame(box, bg=theme.BG_RAISED)
        controls.pack(fill="x")

        # Held on the instance because Tk keeps only a weak reference to the
        # variable; a local would be collected and the box would render empty.
        self._done_var = tk.BooleanVar(value=task.done)
        theme.checkbox(
            controls,
            "Done",
            self._done_var,
            lambda: self.store.toggle_done(task.id),
            bg=theme.BG_RAISED,
        ).pack(side="left")

        billable = tk.BooleanVar(value=task.billable)
        theme.checkbox(
            controls,
            "Billable",
            billable,
            lambda: self.store.update_task(task.id, billable=billable.get()),
            bg=theme.BG_RAISED,
        ).pack(side="left", padx=6)

        theme.label(
            controls, "Plan", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(side="left", padx=(6, 3))
        planned = self.store.planned_minutes(task.id, self.date)
        plan_entry = theme.entry(controls, width=6, font=theme.FONT_SMALL)
        plan_entry.insert(0, f"{planned / 60:g}" if planned else "")
        plan_entry.pack(side="left")
        plan_entry.bind(
            "<FocusOut>",
            lambda _e: self.store.set_plan(
                task.id, self.date, parse_duration(plan_entry.get()) or 0
            ),
        )
        plan_entry.bind("<Return>", lambda _e: self.frame.focus_set())

        theme.button(
            controls,
            "Archive",
            lambda: self.store.update_task(task.id, archived=True),
            bg=theme.BG_RAISED,
            fg=theme.MUTED,
            font=theme.FONT_TINY,
        ).pack(side="right")

        for entry in self.store.entries_for(task.id, self.date):
            line = theme.frame(box, bg=theme.BG_RAISED)
            line.pack(fill="x", pady=1)

            minutes_entry = theme.entry(line, width=6, font=theme.FONT_SMALL)
            minutes_entry.insert(0, format_minutes(entry.minutes))
            minutes_entry.pack(side="left")
            minutes_entry.bind(
                "<FocusOut>",
                lambda _e, eid=entry.id, w=minutes_entry: self._commit_minutes(eid, w),
            )

            theme.button(
                line, "\u00d7", lambda eid=entry.id: self.store.delete_entry(eid),
                bg=theme.BG_RAISED, fg=theme.MUTED, hover=theme.DANGER,
                font=theme.FONT_TINY, padx=4, pady=0,
            ).pack(side="right")

            theme.label(
                line,
                "\u23f1" if entry.source == "timer" else "\u270e",
                bg=theme.BG_RAISED,
                fg=theme.MUTED,
                font=theme.FONT_TINY,
            ).pack(side="right", padx=3)

            note_entry = theme.entry(line, font=theme.FONT_SMALL)
            note_entry.insert(0, entry.note)
            note_entry.pack(side="left", fill="x", expand=True, padx=4)
            note_entry.bind(
                "<FocusOut>",
                lambda _e, eid=entry.id, w=note_entry: self.store.update_entry(eid, note=w.get()),
            )

        new_line = theme.frame(box, bg=theme.BG_RAISED)
        new_line.pack(fill="x", pady=(4, 0))
        amount = theme.entry(new_line, width=6, font=theme.FONT_SMALL)
        amount.pack(side="left")
        theme.label(
            new_line, "e.g. 45m, 1h30, 1.5", bg=theme.BG_RAISED, fg=theme.MUTED,
            font=theme.FONT_TINY,
        ).pack(side="left", padx=5)
        theme.button(
            new_line,
            "Log time",
            lambda: self._log_manual(task.id, amount),
            bg=theme.BG_INPUT,
            font=theme.FONT_SMALL,
        ).pack(side="right")

    def _day_note(self, parent: tk.Misc) -> None:
        theme.label(
            parent,
            "Day note \u2014 context that isn't tied to one task",
            fg=theme.MUTED,
            font=theme.FONT_TINY,
        ).pack(fill="x", pady=(10, 2))
        box = theme.text_box(parent, height=3)
        box.insert("1.0", self.store.db.day_notes.get(self.date, ""))
        box.pack(fill="x", pady=(0, 10))
        box.bind("<FocusOut>", lambda _e: self._commit_day_note(box))

    def _add_panel(self, parent: tk.Misc) -> None:
        if not self._adding:
            theme.button(
                parent,
                "+ Add task to this day",
                self._open_add,
                bg=theme.BG,
                fg=theme.MUTED,
                pady=6,
            ).pack(fill="x", pady=(6, 0))
            return

        panel = theme.frame(parent, bg=theme.BG_RAISED)
        panel.pack(fill="x", pady=(6, 0))
        inner = theme.frame(panel, bg=theme.BG_RAISED)
        inner.pack(fill="x", padx=8, pady=8)

        planned_today = {p.task_id for p in self.store.db.plans if p.date == self.date}
        available = [
            c for c in self.store.active_contexts()
            if not c.task.done and c.task.id not in planned_today
        ]
        if available:
            theme.label(
                inner, "Add an existing task", bg=theme.BG_RAISED, fg=theme.MUTED,
                font=theme.FONT_TINY,
            ).pack(fill="x")
            labels = {f"{c.project.k_code} \u00b7 {c.task.title}": c.task.id for c in available}
            choice = tk.StringVar(value="Choose\u2026")
            theme.option_menu(
                inner,
                choice,
                list(labels),
                command=lambda value: self._add_existing(labels.get(value)),
            ).pack(fill="x", pady=(0, 6))

        projects = [p for p in self.store.db.projects if not p.archived]
        if not projects:
            theme.label(
                inner, "Add a project in the Setup tab first.", bg=theme.BG_RAISED,
                fg=theme.MUTED, font=theme.FONT_SMALL,
            ).pack(fill="x")
            return

        theme.label(
            inner, "Or create a new task", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(fill="x")

        project_labels = {}
        for project in projects:
            client = self.store.db.client(project.client_id)
            project_labels[f"{client.name if client else '?'} \u00b7 {project.name} ({project.k_code})"] = project.id
        project_choice = tk.StringVar(value=next(iter(project_labels)))
        theme.option_menu(inner, project_choice, list(project_labels)).pack(fill="x", pady=2)

        title_entry = theme.entry(inner)
        title_entry.pack(fill="x", pady=2)
        title_entry.focus_set()

        row = theme.frame(inner, bg=theme.BG_RAISED)
        row.pack(fill="x", pady=2)
        billable = tk.BooleanVar(value=True)
        theme.checkbox(row, "Billable", billable, lambda: None, bg=theme.BG_RAISED).pack(side="left")
        theme.label(
            row, "Plan", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(side="left", padx=(8, 3))
        plan_entry = theme.entry(row, width=7, font=theme.FONT_SMALL)
        plan_entry.pack(side="left")

        actions = theme.frame(inner, bg=theme.BG_RAISED)
        actions.pack(fill="x", pady=(6, 0))
        theme.button(
            actions, "Cancel", self._close_add, bg=theme.BG_RAISED, fg=theme.MUTED
        ).pack(side="right")

        def create() -> None:
            title = title_entry.get().strip()
            if not title:
                return
            project_id = project_labels[project_choice.get()]
            task = self.store.add_task(project_id, title, billable.get())
            self.store.set_plan(
                task.id, self.date, parse_duration(plan_entry.get()) or DEFAULT_PLAN_MINUTES
            )
            self._adding = False

        theme.button(
            actions, "Add task", create, bg=theme.ACCENT, fg="#0a1020", hover="#6f9bff"
        ).pack(side="right", padx=4)
        title_entry.bind("<Return>", lambda _e: create())

    # -- actions -------------------------------------------------------------

    def _open_add(self) -> None:
        self._adding = True
        self.refresh()

    def _close_add(self) -> None:
        self._adding = False
        self.refresh()

    def _add_existing(self, task_id: str | None) -> None:
        if not task_id:
            return
        self._adding = False
        self.store.set_plan(task_id, self.date, DEFAULT_PLAN_MINUTES)

    def _toggle_expanded(self, task_id: str) -> None:
        self._expanded.symmetric_difference_update({task_id})
        self.refresh()

    def _toggle_timer(self, task_id: str, running: bool) -> None:
        if running:
            self.store.stop_timer()
        else:
            self.store.start_timer(task_id)

    def _log_manual(self, task_id: str, widget: tk.Entry) -> None:
        minutes = parse_duration(widget.get())
        if minutes:
            self.store.add_entry(task_id, self.date, minutes)

    def _commit_minutes(self, entry_id: str, widget: tk.Entry) -> None:
        minutes = parse_duration(widget.get())
        if minutes is not None:
            self.store.update_entry(entry_id, minutes=minutes)

    def _commit_day_note(self, widget: tk.Text) -> None:
        text = widget.get("1.0", "end-1c")
        if text != self.store.db.day_notes.get(self.date, ""):
            self.store.set_day_note(self.date, text)

    def tick(self) -> None:
        """Updates the running task's total without rebuilding the list."""
        timer = self.store.db.active_timer
        if not timer:
            return
        label = self._actual_labels.get(timer.task_id)
        if label is None:
            return
        logged = self.store.logged_minutes(timer.task_id, self.date) + self.store.live_minutes(
            timer.task_id, self.date
        )
        label.config(text=format_minutes(logged))
