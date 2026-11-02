"""The Week tab: a read-only look at planned hours per task per day,
with actuals underneath — editing a figure happens on the Day tab now,
either on the task's day-level placeholder or on one specific entry (see
``timesheet/ui/today.py`` and ``Entry.plan_minutes``)."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from ..dates import (
    add_days,
    day_name,
    format_minutes,
    iso_week_number,
    short_date,
    start_of_week,
    to_decimal_hours,
    today,
    week_dates,
)
from ..store import Store
from . import icons, theme
from .scroll import ScrollFrame

#: Narrowest each column may get, in pixels. Below this the grid stops
#: shrinking; the task title gives way first.
TASK_COLUMN = 120
DAY_COLUMN = 38
TOTAL_COLUMN = 40
#: The billable dot and its padding, which sit beside the title.
DOT_COLUMN = 15
#: Padding either side of the grid, room for the scrollbar — always deducted,
#: so the title doesn't change length when one appears — and a few pixels of
#: slack, since the column figures above are nominal rather than measured.
GUTTER = 44

#: Floor for the trimmed task title. Below this the name says nothing useful,
#: so the grid is allowed to overflow instead.
MIN_TITLE = 12

#: Resizes smaller than this don't change any column, so they don't redraw.
RESIZE_SLACK = 8


def hours(minutes: int, blank: str = "") -> str:
    """Decimal hours, the same unit the planning boxes take.

    The grid is a table of numbers in one column-width, so ``2.17`` rather
    than ``2h 10m``: the h/m form is half again as wide and was pushing the
    day columns out until they no longer fit the window.
    """
    return f"{to_decimal_hours(minutes):g}" if minutes else blank


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

        self._drawn_width = 0
        # Bound on the tab frame rather than the scrolling body: the body's
        # width drops by the scrollbar's when one appears, and redrawing on
        # that oscillates, since the redraw is what decides whether it does.
        self.frame.bind("<Configure>", self._on_resize, add="+")

    def _on_resize(self, event: tk.Event) -> None:
        """Redraws when the width changes, since the columns are sized to it.

        Deferred to the idle queue: this fires part-way through the layout
        pass that a redraw would restart. Only width matters — the frame also
        grows taller, which must not trigger anything.
        """
        if abs(event.width - self._drawn_width) <= RESIZE_SLACK:
            return
        self._drawn_width = event.width
        self.frame.after_idle(self.refresh)

    def _build_header(self) -> None:
        nav = theme.frame(self.frame)
        nav.pack(fill="x", padx=8, pady=(8, 4))
        # A squared chip of its own, same recipe as the Day tab's date-nav
        # row: real click room on either side of the (longer, expanding)
        # week label in the middle, rather than a glyph with just enough
        # padding to miss.
        icons.chip(
            nav, "prev", self._previous, fallback="\u2039",
            fill=theme.BG_INPUT, text_fill=theme.TEXT,
            size=theme.NAV_CHIP_SIZE, radius=10, font=theme.FONT_ICON,
        ).pack(side="left")
        self.week_label = theme.button(
            nav, "", self._this_week, bg=theme.BG, font=theme.FONT_BOLD
        )
        self.week_label.pack(side="left", fill="x", expand=True)
        icons.chip(
            nav, "next", self._next, fallback="\u203a",
            fill=theme.BG_INPUT, text_fill=theme.TEXT,
            size=theme.NAV_CHIP_SIZE, radius=10, font=theme.FONT_ICON,
        ).pack(side="left")

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
        theme.commit_focused_entry(self.frame)
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

        limit = self._title_limit(len(dates))
        self._header_row(grid, dates)
        row = 1
        for heading, contexts in self._groups(dates).items():
            theme.label(
                grid, heading, font=theme.FONT_HEAD
            ).grid(row=row, column=0, columnspan=len(dates) + 2, sticky="w", pady=(7, 2))
            row += 1
            for context in contexts:
                self._task_row(grid, row, context, dates, limit)
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
        self._size_columns(grid, dates)

    def _size_columns(self, grid: tk.Misc, dates: list[str]) -> None:
        """Shares the width out between the task name and the day columns.

        Both get a weight, so widening the window spreads across the grid
        instead of handing every spare pixel to the task names and leaving the
        days as a cramped strip on the right. The uniform group keeps the days
        equal to each other however much there is to go around.
        """
        grid.columnconfigure(0, weight=4, minsize=TASK_COLUMN)
        for index in range(len(dates)):
            grid.columnconfigure(index + 1, weight=1, uniform="day", minsize=DAY_COLUMN)
        grid.columnconfigure(len(dates) + 1, minsize=TOTAL_COLUMN)

    def _groups(self, dates: list[str]) -> dict[str, list]:
        in_week = set(dates)
        touched = {p.task_id for p in self.store.db.plans if p.date in in_week}
        touched |= {e.task_id for e in self.store.db.entries if e.date in in_week}
        touched |= self.store.tasks_with_notes_due(dates)

        groups: dict[str, list] = {}
        for context in self.store.active_contexts():
            if context.task.id not in touched and not (
                self._show_all.get() and not context.project.closed
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
            cell.grid(row=0, column=index + 1, padx=1, sticky="ew")
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
        ).grid(row=0, column=len(dates) + 1, padx=(4, 0), sticky="ew")

    def _title_limit(self, days: int) -> int:
        """How many characters of task title fit beside the day columns.

        Measured rather than guessed, because what's left over depends on the
        window width, whether the weekend is showing, and the font.
        """
        width = self.frame.winfo_width()
        if width <= 1:  # first draw; the resize handler redraws once laid out
            return MIN_TITLE
        room = width - GUTTER - days * DAY_COLUMN - TOTAL_COLUMN - DOT_COLUMN
        character = tkfont.Font(font=theme.FONT_SMALL).measure("n") or 6
        return max(MIN_TITLE, room // character)

    def _task_row(self, grid: tk.Misc, row: int, context, dates: list[str], limit: int) -> None:
        task = context.task
        name = theme.frame(grid)
        name.grid(row=row, column=0, sticky="w", pady=1)
        theme.dot(name, context.billable).pack(side="left", padx=(0, 4))
        theme.label(
            name,
            theme.ellipsize(context.display_title, limit),
            fg=theme.MUTED if context.project.closed else theme.TEXT,
            font=theme.FONT_SMALL,
        ).pack(side="left")

        for index, date in enumerate(dates):
            cell = theme.frame(grid)
            cell.grid(row=row, column=index + 1, padx=1, sticky="ew")

            # Once anything's logged, each of the day's entries carries its
            # own Planned Hours now (see ``Entry.plan_minutes``) rather than
            # one figure shared across them — so this cell sums whatever
            # they've each been given. Before that, there's no entry yet
            # for a figure to belong to, so it falls back to the task's own
            # day-level ``Plan`` the same as ever, read-only here too:
            # editing it now only happens from the Day tab, on whichever
            # row (the placeholder, or a specific entry) the figure
            # actually belongs to.
            entries = self.store.entries_for(task.id, date)
            planned = (
                sum(e.plan_minutes for e in entries) if entries
                else self.store.planned_minutes(task.id, date)
            )
            # Decimal-hour labels ("1.5", not "1h 30m"), trimmed down to no
            # padding to stay inside a 38px day column — ``format_minutes``'s
            # usual labels would force every column wider than the grid's
            # been measured and tested for.
            theme.label(
                cell, f"{planned / 60:g}" if planned else "\u2013",
                fg=theme.TEXT, font=theme.FONT_SMALL, anchor="center",
            ).pack(fill="x")

            actual = self.store.logged_minutes(task.id, date)
            theme.label(
                cell,
                hours(actual),
                fg=theme.BILLABLE,
                font=theme.FONT_TINY,
                anchor="center",
            ).pack()

        row_total = sum(self.store.logged_minutes(task.id, d) for d in dates)
        theme.label(
            grid,
            hours(row_total),
            fg=theme.MUTED,
            font=theme.FONT_TINY,
            anchor="center",
        ).grid(row=row, column=len(dates) + 1, padx=(4, 0), sticky="ew")

    def _footer_rows(self, grid: tk.Misc, row: int, dates: list[str]) -> None:
        def totals_row(index: int, title: str, values: list[int], grand: int) -> None:
            theme.label(grid, title, font=theme.FONT_HEAD).grid(
                row=index, column=0, sticky="w", pady=(8, 0)
            )
            for column, value in enumerate(values):
                theme.label(
                    grid,
                    hours(value, "\u2013"),
                    fg=theme.MUTED,
                    font=theme.FONT_TINY,
                    anchor="center",
                ).grid(row=index, column=column + 1, pady=(8, 0), sticky="ew")
            theme.label(
                grid, hours(grand, "\u2013"), font=theme.FONT_TINY, anchor="center"
            ).grid(row=index, column=len(dates) + 1, pady=(8, 0), padx=(4, 0), sticky="ew")

        planned = [self.store.planned_minutes_on(d) for d in dates]
        actual = [
            sum(e.minutes for e in self.store.db.entries if e.date == d) for d in dates
        ]
        totals_row(row, "Planned", planned, sum(planned))
        totals_row(row + 1, "Actual", actual, sum(actual))
