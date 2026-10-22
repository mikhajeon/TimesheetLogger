"""Week: plan hours per task per day, with actuals underneath each cell."""

from __future__ import annotations

import tkinter as tk

from ..dates import (
    add_days,
    day_name,
    format_minutes,
    iso_week_number,
    parse_duration,
    short_date,
    start_of_week,
    today,
    week_dates,
)
from ..store import Store
from . import theme
from .addtask import AddTaskPanel
from .scroll import ScrollFrame


class WeekView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.week_start = start_of_week(today())
        self.show_all = tk.BooleanVar(value=False)

        self.frame = theme.frame(parent)
        self._build()

    def _build(self) -> None:
        nav = theme.frame(self.frame)
        nav.pack(fill="x", padx=10, pady=(10, 6))
        theme.button(nav, "\u2039", lambda: self._shift(-7), bg=theme.BG, fg=theme.MUTED).pack(side="left")
        self.week_label = theme.button(nav, "", self._go_current, bg=theme.BG, font=theme.FONT_BOLD)
        self.week_label.config(anchor="center")
        self.week_label.pack(side="left", fill="x", expand=True)
        theme.button(nav, "\u203a", lambda: self._shift(7), bg=theme.BG, fg=theme.MUTED).pack(side="left")

        summary = theme.frame(self.frame)
        summary.pack(fill="x", padx=10)
        self.summary_label = theme.label(summary, "", font=theme.FONT_SMALL)
        self.summary_label.pack(side="left")

        weekend_var = tk.BooleanVar(value=self.store.db.settings.show_weekend)
        self.weekend_var = weekend_var
        theme.checkbox(
            summary, "Weekend", weekend_var,
            lambda: self.store.patch_settings(show_weekend=weekend_var.get()),
        ).pack(side="right")
        theme.checkbox(summary, "All open tasks", self.show_all, self.refresh).pack(side="right")

        self.scroll = ScrollFrame(self.frame)
        self.scroll.pack(fill="both", expand=True, padx=10, pady=(6, 0))

        footer = theme.frame(self.frame)
        footer.pack(fill="x", padx=10, pady=(4, 10))
        self.add_panel = AddTaskPanel(
            footer, self.store, lambda: self.week_start, label="Monday of this week"
        )
        self.add_panel.frame.pack(fill="x")

    def _shift(self, days: int) -> None:
        self.week_start = add_days(self.week_start, days)
        self.refresh()

    def _go_current(self) -> None:
        self.week_start = start_of_week(today())
        self.refresh()

    # -- rendering ----------------------------------------------------------

    def refresh(self) -> None:
        dates = week_dates(self.week_start, self.store.db.settings.show_weekend)
        self.weekend_var.set(self.store.db.settings.show_weekend)

        self.week_label.config(
            text=f"Week {iso_week_number(self.week_start)} · "
            f"{short_date(self.week_start)} – {short_date(add_days(self.week_start, 6))}"
        )
        total, billable = self.store.range_totals(dates)
        self.summary_label.config(
            text=f"{format_minutes(total)} tracked · {format_minutes(billable)} billable"
        )

        self.scroll.clear()
        grid = theme.frame(self.scroll.body)
        grid.pack(fill="both", expand=True)

        self._header(grid, dates)

        touched = {p.task_id for p in self.store.db.plans if p.date in set(dates)}
        touched |= {e.task_id for e in self.store.db.entries if e.date in set(dates)}
        contexts = [
            c
            for c in self.store.active_contexts()
            if c.task.id in touched or (self.show_all.get() and not c.task.done)
        ]

        if not contexts:
            theme.label(
                self.scroll.body,
                "Nothing planned this week. Tick \u201cAll open tasks\u201d or add one below.",
                fg=theme.MUTED, font=theme.FONT_SMALL, wraplength=320, justify="center",
            ).pack(pady=16)
            self.add_panel.refresh()
            return

        row = 1
        last_heading = None
        for context in contexts:
            if context.heading != last_heading:
                last_heading = context.heading
                theme.label(
                    grid, context.heading, fg=theme.MUTED, font=theme.FONT_TINY
                ).grid(row=row, column=0, columnspan=len(dates) + 2, sticky="w", pady=(8, 2))
                row += 1
            self._task_row(grid, context, dates, row)
            row += 1

        self._totals(grid, dates, row)
        self.add_panel.refresh()

    def _header(self, grid: tk.Frame, dates: list[str]) -> None:
        theme.label(grid, "Task", fg=theme.MUTED, font=theme.FONT_TINY).grid(
            row=0, column=0, sticky="w"
        )
        for index, date in enumerate(dates):
            is_today = date == today()
            cell = theme.frame(grid)
            cell.grid(row=0, column=index + 1, padx=2)
            theme.label(
                cell, day_name(date),
                fg=theme.ACCENT if is_today else theme.TEXT, font=theme.FONT_TINY, anchor="center",
            ).pack()
            theme.label(
                cell, short_date(date), fg=theme.MUTED, font=theme.FONT_TINY, anchor="center"
            ).pack()
        theme.label(grid, "Total", fg=theme.MUTED, font=theme.FONT_TINY, anchor="center").grid(
            row=0, column=len(dates) + 1, padx=2
        )
        grid.columnconfigure(0, weight=1, minsize=120)

    def _task_row(self, grid: tk.Frame, context, dates: list[str], row: int) -> None:
        task = context.task

        name = theme.frame(grid)
        name.grid(row=row, column=0, sticky="w", pady=1)
        theme.dot(name, task.billable).pack(side="left", padx=(0, 4))
        theme.label(
            name, theme.ellipsize(task.title, 26),
            fg=theme.MUTED if task.done else theme.TEXT, font=theme.FONT_SMALL,
        ).pack(side="left")

        week_total = 0
        for index, date in enumerate(dates):
            planned = self.store.planned_minutes(task.id, date)
            actual = self.store.logged_minutes(task.id, date)
            week_total += actual

            cell = theme.frame(grid)
            cell.grid(row=row, column=index + 1, padx=2, pady=1)

            box = theme.entry(cell, width=4, font=theme.FONT_SMALL)
            box.config(justify="center")
            if planned:
                box.insert(0, f"{planned / 60:g}")
            box.pack()
            box.bind(
                "<FocusOut>",
                lambda _e, t=task.id, d=date, w=box: self.store.set_plan(
                    t, d, parse_duration(w.get()) or 0
                ),
            )
            box.bind("<Return>", lambda _e, w=box: w.tk_focusNext().focus_set())

            theme.label(
                cell, format_minutes(actual) if actual else " ",
                fg=theme.BILLABLE, font=theme.FONT_TINY, anchor="center",
            ).pack()

        theme.label(
            grid, format_minutes(week_total), fg=theme.MUTED, font=theme.FONT_SMALL, anchor="center"
        ).grid(row=row, column=len(dates) + 1, padx=2)

    def _totals(self, grid: tk.Frame, dates: list[str], row: int) -> None:
        theme.hairline(grid).grid(row=row, column=0, columnspan=len(dates) + 2, sticky="ew", pady=5)

        for offset, (caption, getter) in enumerate(
            (
                ("Planned", lambda d: sum(p.minutes for p in self.store.db.plans if p.date == d)),
                ("Actual", lambda d: self.store.day_totals(d)[0]),
            )
        ):
            line = row + 1 + offset
            theme.label(grid, caption, font=theme.FONT_BOLD, fg=theme.MUTED).grid(
                row=line, column=0, sticky="w"
            )
            running = 0
            for index, date in enumerate(dates):
                value = getter(date)
                running += value
                theme.label(
                    grid, format_minutes(value), fg=theme.MUTED,
                    font=theme.FONT_TINY, anchor="center",
                ).grid(row=line, column=index + 1, padx=2)
            theme.label(
                grid, format_minutes(running), font=theme.FONT_BOLD, anchor="center"
            ).grid(row=line, column=len(dates) + 1, padx=2)
