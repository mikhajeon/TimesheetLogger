"""The Week tab: plan hours per task per day, with actuals underneath."""

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
from .scroll import ScrollFrame


class WeekView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.week_start = start_of_week(today())
        self.frame = theme.frame(parent)
        self._show_all = tk.BooleanVar(value=False)

        self._build_header()
        self.scroll = ScrollFrame(self.frame)
        self.scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _build_header(self) -> None:
        nav = theme.frame(self.frame)
        nav.pack(fill="x", padx=8, pady=(8, 4))
        theme.button(nav, "\u2039", self._previous, bg=theme.BG, padx=7).pack(side="left")
        self.week_label = theme.button(
            nav, "", self._this_week, bg=theme.BG, font=theme.FONT_BOLD
        )
        self.week_label.pack(side="left", fill="x", expand=True)
        theme.button(nav, "\u203a", self._next, bg=theme.BG, padx=7).pack(side="left")

        summary = theme.frame(self.frame)
        summary.pack(fill="x", padx=8, pady=(0, 4))
        self.total_label = theme.label(summary, "", font=theme.FONT_SMALL)
        self.total_label.pack(side="left")

        self._weekend = tk.BooleanVar(value=self.store.db.settings.show_weekend)
        theme.checkbox(
            summary, "Weekend", self._weekend,
            lambda: self.store.patch_settings(show_weekend=self._weekend.get()),
        ).pack(side="right")
        theme.checkbox(
            summary, "All open tasks", self._show_all, self.refresh
        ).pack(side="right", padx=6)

    def _previous(self) -> None:
        self.week_start = add_days(self.week_start, -7)
        self.refresh()

    def _next(self) -> None:
        self.week_start = add_days(self.week_start, 7)
        self.refresh()

    def _this_week(self) -> None:
        self.week_start = start_of_week(today())
        self.refresh()

    # -- rendering ----------------------------------------------------------

    def refresh(self) -> None:
        dates = week_dates(self.week_start, self.store.db.settings.show_weekend)
        self.week_label.config(
            text=f"Week {iso_week_number(self.week_start)} \u00b7 "
            f"{short_date(self.week_start)} \u2013 {short_date(add_days(self.week_start, 6))}"
        )
        total, billable = self.store.range_totals(dates)
        self.total_label.config(
            text=f"{format_minutes(total)} tracked \u00b7 {format_minutes(billable)} billable"
        )

        self.scroll.clear()
        grid = theme.frame(self.scroll.body)
        grid.pack(fill="both", expand=True)

        self._header_row(grid, dates)
        row = 1
        for heading, contexts in self._groups(dates).items():
            theme.label(
                grid, heading, fg=theme.MUTED, font=theme.FONT_TINY
            ).grid(row=row, column=0, columnspan=len(dates) + 2, sticky="w", pady=(7, 1))
            row += 1
            for context in contexts:
                self._task_row(grid, row, context, dates)
                row += 1

        if row == 1:
            theme.label(
                grid,
                'Nothing planned this week. Tick "All open tasks" or add one in Today.',
                fg=theme.MUTED,
                font=theme.FONT_SMALL,
            ).grid(row=1, column=0, columnspan=len(dates) + 2, pady=14)
            return

        self._footer_rows(grid, row, dates)
        grid.columnconfigure(0, weight=1, minsize=120)

    def _groups(self, dates: list[str]) -> dict[str, list]:
        in_week = set(dates)
        touched = {p.task_id for p in self.store.db.plans if p.date in in_week}
        touched |= {e.task_id for e in self.store.db.entries if e.date in in_week}

        groups: dict[str, list] = {}
        for context in self.store.active_contexts():
            if context.task.id not in touched and not (
                self._show_all.get() and not context.task.done
            ):
                continue
            groups.setdefault(context.heading, []).append(context)
        return groups

    def _header_row(self, grid: tk.Misc, dates: list[str]) -> None:
        theme.label(grid, "Task", fg=theme.MUTED, font=theme.FONT_TINY).grid(
            row=0, column=0, sticky="w"
        )
        for index, date in enumerate(dates):
            cell = theme.frame(grid)
            cell.grid(row=0, column=index + 1, padx=1)
            is_today = date == today()
            theme.label(
                cell,
                day_name(date),
                fg=theme.ACCENT if is_today else theme.TEXT,
                font=theme.FONT_TINY,
                anchor="center",
            ).pack()
            theme.label(
                cell, short_date(date), fg=theme.MUTED, font=theme.FONT_TINY, anchor="center"
            ).pack()
        theme.label(
            grid, "Total", fg=theme.MUTED, font=theme.FONT_TINY, anchor="center"
        ).grid(row=0, column=len(dates) + 1, padx=(4, 0))

    def _task_row(self, grid: tk.Misc, row: int, context, dates: list[str]) -> None:
        task = context.task
        name = theme.frame(grid)
        name.grid(row=row, column=0, sticky="w", pady=1)
        theme.dot(name, task.billable).pack(side="left", padx=(0, 4))
        theme.label(
            name,
            task.title,
            fg=theme.MUTED if task.done else theme.TEXT,
            font=theme.FONT_SMALL,
        ).pack(side="left")

        for index, date in enumerate(dates):
            cell = theme.frame(grid)
            cell.grid(row=row, column=index + 1, padx=1)

            planned = self.store.planned_minutes(task.id, date)
            box = theme.entry(cell, width=4, font=theme.FONT_SMALL, justify="center")
            box.insert(0, f"{planned / 60:g}" if planned else "")
            box.pack()
            box.bind(
                "<FocusOut>",
                lambda _e, tid=task.id, d=date, w=box: self._commit_plan(tid, d, w),
            )
            box.bind("<Return>", lambda _e: self.frame.focus_set())

            actual = self.store.logged_minutes(task.id, date)
            theme.label(
                cell,
                format_minutes(actual) if actual else "",
                fg=theme.BILLABLE,
                font=theme.FONT_TINY,
                anchor="center",
            ).pack()

        row_total = sum(self.store.logged_minutes(task.id, d) for d in dates)
        theme.label(
            grid,
            format_minutes(row_total) if row_total else "",
            fg=theme.MUTED,
            font=theme.FONT_TINY,
            anchor="center",
        ).grid(row=row, column=len(dates) + 1, padx=(4, 0))

    def _footer_rows(self, grid: tk.Misc, row: int, dates: list[str]) -> None:
        def totals_row(index: int, title: str, values: list[int], grand: int) -> None:
            theme.label(grid, title, font=theme.FONT_HEAD).grid(
                row=index, column=0, sticky="w", pady=(8, 0)
            )
            for column, value in enumerate(values):
                theme.label(
                    grid,
                    format_minutes(value) if value else "\u2013",
                    fg=theme.MUTED,
                    font=theme.FONT_TINY,
                    anchor="center",
                ).grid(row=index, column=column + 1, pady=(8, 0))
            theme.label(
                grid, format_minutes(grand), font=theme.FONT_TINY, anchor="center"
            ).grid(row=index, column=len(dates) + 1, pady=(8, 0), padx=(4, 0))

        planned = [
            sum(p.minutes for p in self.store.db.plans if p.date == d) for d in dates
        ]
        actual = [
            sum(e.minutes for e in self.store.db.entries if e.date == d) for d in dates
        ]
        totals_row(row, "Planned", planned, sum(planned))
        totals_row(row + 1, "Actual", actual, sum(actual))

    def _commit_plan(self, task_id: str, date: str, widget: tk.Entry) -> None:
        self.store.set_plan(task_id, date, parse_duration(widget.get()) or 0)
