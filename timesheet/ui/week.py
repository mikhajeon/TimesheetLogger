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
    format_duration,
    iso_week_number,
    short_date,
    start_of_week,
    to_decimal_hours,
    today,
    week_dates,
)
from ..store import Store
from .navrow import NavRow
from . import controls, icons, theme
from .scroll import ScrollFrame

#: Narrowest each column may get, in pixels. Below this the grid stops
#: shrinking; the task title gives way first.
TASK_COLUMN = 120
#: Wide enough for Planned and Actual side by side (see ``_task_row``) rather
#: than one stacked over the other — was 38px when there was only ever one
#: figure per cell.
DAY_COLUMN = 68
#: Same width as a day column, and for the same reason: the row's own total
#: now carries planned and actual side by side too, not just actual alone.
TOTAL_COLUMN = DAY_COLUMN
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


#: The Week tab's Group by choices: ``Settings.week_grouping`` value -> label.
GROUPING_LABELS = {"customer": "Customer", "project": "Project"}
GROUPING_BY_LABEL = {label: mode for mode, label in GROUPING_LABELS.items()}


def hours(minutes: int, blank: str = "") -> str:
    """Decimal hours, the same unit the planning boxes take.

    The grid is a table of numbers in one column-width, so ``2.17`` rather
    than ``2h 10m``: the h/m form is half again as wide and was pushing the
    day columns out until they no longer fit the window.
    """
    return f"{to_decimal_hours(minutes):.2f}" if minutes else blank


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
        self.week_label = NavRow(
            nav, self._previous, self._this_week, self._next, text="",
        )
        self.week_label.pack(fill="x")

        summary = theme.frame(self.frame)
        summary.pack(fill="x", padx=8, pady=(0, 4))
        # Group by on the left, the same place and look as on the Day tab,
        # with the planned / tracked / billable summary beside it.
        theme.label(summary, "Group by:", fg=theme.MUTED, font=theme.FONT_TINY).pack(side="left")
        self._grouping_select = controls.Select(
            summary, list(GROUPING_LABELS.values()),
            GROUPING_LABELS.get(self.store.db.settings.week_grouping, "Project"),
            self._on_grouping_change, bg=theme.BG,
        )
        self._grouping_select.pack(side="left", padx=(6, 10))
        self.total_label = theme.label(summary, "", font=theme.FONT_SMALL)
        self.total_label.pack(side="right")

        # The two view toggles drop to a row of their own: beside Group by
        # and the summary they no longer fit the window's narrowest size.
        options = theme.frame(self.frame)
        options.pack(fill="x", padx=8, pady=(0, 4))
        self._weekend = tk.BooleanVar(value=self.store.db.settings.show_weekend)
        theme.checkbox(
            options, "Weekend", self._weekend,
            lambda: self.store.patch_settings(show_weekend=self._weekend.get()),
        ).pack(side="right")
        theme.checkbox(
            options, "All open tasks", self._show_all, self.refresh
        ).pack(side="right", padx=6)

    def _on_grouping_change(self, label: str) -> None:
        mode = GROUPING_BY_LABEL.get(label)
        if mode:
            from .skeleton import run_behind

            run_behind(
                self.scroll, "Week",
                lambda: self.store.patch_settings(week_grouping=mode),
            )

    @staticmethod
    def _utilization(billable: float, total: float) -> str:
        """Billable time as a share of everything tracked (billable plus
        non-billable), e.g. ``75% utilization``; a dash with nothing tracked."""
        if total <= 0:
            return "\u2013 utilization"
        return f"{round(100 * billable / total)}% utilization"

    def _by_customer(self) -> bool:
        return self.store.db.settings.week_grouping == "customer"

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

    def _fmt(self, minutes: float) -> str:
        """An hour figure the way ``Settings.hour_format`` (Preferences,
        under Theme) says to show it — see ``dates.format_duration``. The
        grid's own cells (see ``hours`` above) stay decimal regardless,
        narrow as they are."""
        return format_duration(minutes, self.store.db.settings.hour_format == "decimal")

    def refresh(self) -> None:
        theme.commit_focused_entry(self.frame)
        dates = week_dates(self.week_start, self.store.db.settings.show_weekend)
        self._grouping_select.set_value(
            GROUPING_LABELS.get(self.store.db.settings.week_grouping, "Project")
        )
        self.week_label.set_highlight(self.week_start == start_of_week(today()))
        self.week_label.set_text(
            f"Week {iso_week_number(self.week_start)} \u00b7 "
            f"{short_date(self.week_start)} \u2013 {short_date(add_days(self.week_start, 6))}"
        )
        # Planned hours here are what's still outstanding: an entry whose
        # actual has matched or passed its plan counts as 0 planned.
        self._outstanding = {d: self.store.unfinished_planned_by_task(d) for d in dates}
        total, billable = self.store.range_totals(dates)
        planned_total = sum(sum(self._outstanding[d].values()) for d in dates)
        self.total_label.config(
            text=f"{self._fmt(planned_total)} planned \u00b7 {self._fmt(total)} tracked "
            f"\u00b7 {self._fmt(billable)} billable "
            f"\u00b7 {self._utilization(billable, total)}"
        )

        self.scroll.clear()
        grid = theme.frame(self.scroll.body)
        grid.pack(fill="both", expand=True)

        limit = self._title_limit(len(dates))
        self._header_row(grid, dates)
        row = 1
        for heading, contexts in self._groups(dates).items():
            theme.heading_label(
                grid, heading, font=theme.FONT_HEAD
            ).grid(row=row, column=0, columnspan=3 * len(dates) + 4, sticky="w", pady=(7, 2))
            row += 1
            # Restarted at each heading, rather than counted straight through
            # every group — a group's own first row is always the lighter
            # stripe, the same as the one above it, rather than carrying
            # on from wherever the previous group's count happened to land.
            for stripe, context in enumerate(contexts):
                self._task_row(grid, row, context, dates, limit, stripe)
                row += 1
            self._project_total_row(grid, row, contexts, dates)
            row += 1

        if row == 1:
            theme.label(
                grid,
                'Nothing planned this week. Tick "All open tasks" or add one in Today.',
                fg=theme.MUTED,
                font=theme.FONT_SMALL,
            ).grid(row=1, column=0, columnspan=3 * len(dates) + 4, pady=14)
            return

        self._footer_rows(grid, row, dates)
        self._size_columns(grid, dates)
        self._day_dividers(grid, dates, row)

    @staticmethod
    def _divider_column(slot: int) -> int:
        """Grid column of the hairline in front of day ``slot`` (the week's
        total is slot ``len(dates)``). Every day owns three columns: the
        divider, then planned, then actual."""
        return 3 * slot + 1

    def _day_dividers(self, grid: tk.Misc, dates: list[str], rows: int) -> None:
        """Hairlines in front of every day and the week's total.

        Each one is a cell of the grid itself, spanning every row in a column
        of its own, rather than a widget floated over the grid at a measured
        position. It therefore can't drift: wherever the grid puts the
        columns — window resized, weekend toggled, a figure growing wider —
        the line is part of that layout and moves with it."""
        for slot in range(len(dates) + 1):
            tk.Frame(grid, bg=theme.LINE, width=1, bd=0, highlightthickness=0).grid(
                row=0, column=self._divider_column(slot), rowspan=rows, sticky="ns"
            )

    def _size_columns(self, grid: tk.Misc, dates: list[str]) -> None:
        """Shares the width out between the task name and the day columns.

        Both get a weight, so widening the window spreads across the grid
        instead of handing every spare pixel to the task names and leaving the
        days as a cramped strip on the right. The uniform group keeps the days
        equal to each other however much there is to go around.
        """
        grid.columnconfigure(0, weight=4, minsize=TASK_COLUMN)
        # Per day: a one-pixel divider column, then planned and actual each
        # in a column of their own (see ``_figure_pair``). One uniform group
        # for every figure column, so the numbers line up from row to row.
        for slot in range(len(dates)):
            grid.columnconfigure(self._divider_column(slot), weight=0, minsize=1)
            for offset in (1, 2):
                grid.columnconfigure(
                    self._divider_column(slot) + offset,
                    weight=1, uniform="figure", minsize=DAY_COLUMN // 2,
                )
        total = self._divider_column(len(dates))
        grid.columnconfigure(total, weight=0, minsize=1)
        for offset in (1, 2):
            grid.columnconfigure(total + offset, uniform="total", minsize=TOTAL_COLUMN // 2)

    @staticmethod
    def _figure_pair(
        grid: tk.Misc, row: int, slot: int, planned: int, actual: int, fill: str,
        font=theme.FONT_TINY, pady=0, last: bool = False,
    ) -> None:
        """One day's (or the week's) planned and actual as two separate
        grid cells, right-aligned in their own columns — ``slot`` is the
        day's index, or ``len(dates)`` for the totals column. Each figure
        being a cell of its own in a fixed-width column is what keeps the
        numbers lined up across rows, instead of drifting with whatever
        the neighbouring figure's text happened to need."""
        column = 3 * slot + 2
        gap = (2, 0)  # clear of the day divider, the same for every cell
        theme.label(
            grid, hours(planned, "\u2013"),
            bg=fill, fg=theme.PLANNED, font=font, anchor="e",
        ).grid(row=row, column=column, padx=gap, pady=pady, sticky="ew")
        theme.label(
            grid, hours(actual, "\u2013"),
            bg=fill, fg=theme.BILLABLE, font=font, anchor="e",
        ).grid(row=row, column=column + 1, padx=(0, 1), pady=pady, sticky="ew")

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
            heading = context.client.name if self._by_customer() else context.heading
            groups.setdefault(heading, []).append(context)
        return groups

    def _header_row(self, grid: tk.Misc, dates: list[str]) -> None:
        # A band of its own behind the whole header row — darker than the
        # row stripes — with today's column picked out in the accent tint.
        band = theme.header_fill()
        theme.frame(grid, bg=band).grid(
            row=0, column=0, columnspan=3 * len(dates) + 4, sticky="nsew"
        )
        theme.label(grid, "Task", bg=band, fg=theme.MUTED, font=theme.FONT_TINY_BOLD).grid(
            row=0, column=0, sticky="nsw", padx=(4, 0)
        )
        for index, date in enumerate(dates):
            is_today = date == today()
            fill = theme.ACCENT_DIM if is_today else band
            cell = theme.frame(grid, bg=fill)
            cell.grid(row=0, column=3 * index + 2, columnspan=2, padx=(2, 0), sticky="nsew")
            theme.label(
                cell,
                day_name(date),
                bg=fill,
                fg=theme.ACCENT if is_today else theme.TEXT,
                font=theme.FONT_TINY_BOLD,
                anchor="center",
            ).pack(pady=(3, 0))
            theme.label(
                cell, short_date(date), bg=fill, fg=theme.MUTED, font=theme.FONT_TINY,
                anchor="center",
            ).pack(pady=(0, 3))
        theme.label(
            grid, "Task Total", bg=band, fg=theme.MUTED, font=theme.FONT_TINY_BOLD,
            anchor="center",
        ).grid(row=0, column=3 * len(dates) + 2, columnspan=2, padx=(2, 0), sticky="nsew")

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

    def _task_row(
        self, grid: tk.Misc, row: int, context, dates: list[str], limit: int, stripe: int
    ) -> None:
        task = context.task
        # Alternating row backgrounds (see ``refresh``'s own ``stripe``
        # counter, restarted at each heading) — every widget across the row
        # shares it, including each label inside a cell, rather than just
        # the cell frames themselves; a ``tk.Label`` paints its own
        # background regardless of its parent's, so leaving one at the
        # default would show as a seam of the wrong shade right where the
        # text sits.
        #
        # ``BG_RAISED`` (white) on a group's own first row, then a grey
        # lighter than the page's own ``BG`` behind it — not ``BG`` itself,
        # which would make the "grey" stripe disappear into the page around
        # the grid rather than read as a deliberate second shade.
        stripe_grey = theme.blend(theme.BG, theme.BG_RAISED, 0.5)
        fill = theme.BG_RAISED if stripe % 2 == 0 else stripe_grey
        name = theme.frame(grid, bg=fill)
        name.grid(row=row, column=0, sticky="ew", pady=1)
        theme.dot(name, context.billable, bg=fill).pack(side="left", padx=(0, 4))
        theme.label(
            name,
            theme.ellipsize(
                # Under a customer, tasks of several projects share the
                # list, so each says which one it belongs to.
                f"[{context.project.k_code}] {context.display_title}"
                if self._by_customer() else context.display_title,
                limit,
            ),
            bg=fill,
            fg=theme.MUTED if context.project.closed else theme.TEXT,
            font=theme.FONT_SMALL,
        ).pack(side="left")

        planned_total = 0
        actual_total = 0
        for index, date in enumerate(dates):
            # Each day's planned figure is the hours still outstanding on
            # it (see ``_figures``), actual alongside — tinted the same way
            # the Day tab's own header bar colours the two (``theme.PLANNED``
            # amber, ``theme.BILLABLE`` green). Decimal-hour labels ("1.50",
            # not "1h 30m") to stay inside the column's own width.
            planned, actual = self._figures(task.id, date)
            planned_total += planned
            actual_total += actual
            self._figure_pair(grid, row, index, planned, actual, fill)

        # The week's subtotal for this task, in the same two-column form.
        self._figure_pair(
            grid, row, len(dates), planned_total, actual_total, fill,
            font=theme.FONT_TINY_BOLD, last=True,
        )

    def _figures(self, task_id: str, date: str) -> tuple[int, int]:
        """``(planned, actual)`` minutes for one task on one day — the
        same figures ``_task_row``'s cells show."""
        planned = self._outstanding.get(date, {}).get(task_id, 0)
        return planned, self.store.logged_minutes(task_id, date)

    def _project_total_row(
        self, grid: tk.Misc, row: int, contexts: list, dates: list[str]
    ) -> None:
        """The project's own total under its tasks: planned and actual for
        each day (summed across the tasks listed above it) and for the
        week, styled like a task row but on the totals' tinted backdrop
        with a quiet, regular-weight label."""
        # A faint blue over the neutral band, so the sums stand apart from the
        # task rows above without shouting.
        fill = theme.blend(theme.blend(theme.BG, theme.BG_RAISED, 0.5), theme.ACCENT, 0.10)
        theme.label(
            grid, "Customer Total" if self._by_customer() else "Project Total", bg=fill, fg=theme.MUTED, font=theme.FONT_SMALL, anchor="w"
        ).grid(row=row, column=0, sticky="ew", pady=(1, 3))

        planned_week = actual_week = 0
        for index, date in enumerate(dates):
            figures = [self._figures(context.task.id, date) for context in contexts]
            planned = sum(p for p, _a in figures)
            actual = sum(a for _p, a in figures)
            planned_week += planned
            actual_week += actual
            self._figure_pair(grid, row, index, planned, actual, fill, pady=(1, 3))
        self._figure_pair(
            grid, row, len(dates), planned_week, actual_week, fill,
            font=theme.FONT_TINY_BOLD, pady=(1, 3), last=True,
        )

    def _footer_rows(self, grid: tk.Misc, row: int, dates: list[str]) -> None:
        """The week's overall totals as one row, planned and actual side by
        side in every cell — the same layout as the task rows and the
        project totals above it."""
        fill = theme.blend(theme.PLANNED_BG, theme.BILLABLE_BG, 0.5)
        theme.label(grid, "Total", bg=fill, font=theme.FONT_HEAD).grid(
            row=row, column=0, sticky="ew", pady=(8, 0)
        )

        planned_week = actual_week = 0
        for index, date in enumerate(dates):
            planned = sum(self._outstanding.get(date, {}).values())
            actual = sum(e.minutes for e in self.store.db.entries if e.date == date)
            planned_week += planned
            actual_week += actual
            self._figure_pair(
                grid, row, index, planned, actual, fill, font=theme.FONT_TINY_BOLD, pady=(8, 0)
            )
        self._figure_pair(
            grid, row, len(dates), planned_week, actual_week, fill,
            font=theme.FONT_TINY_BOLD, pady=(8, 0), last=True,
        )
