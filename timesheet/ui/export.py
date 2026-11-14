"""The Export tab: a click-to-copy table of the figures to key into a timesheet.

Each row is one task on one day, with every entry against it rolled into a
single hours figure and a single note. Clicking either puts it on the
clipboard, so filling in the customer timesheet is click, paste, click, paste.
"""

from __future__ import annotations

import os
import tkinter as tk
import tkinter.font as tkfont

from .. import paths
from ..bookings import Line, by_date
from ..bookings import collect as collect_lines
from ..bookings import setup_order
from ..dates import (
    add_days,
    day_name,
    from_iso,
    iso_week_number,
    pretty_date,
    short_date,
    start_of_week,
    to_decimal_hours,
    today,
    week_dates,
    week_label,
)
from ..store import Store
from . import icons, theme
from .scroll import ScrollFrame

#: Sizing for the week grid's columns, same recipe as the Week tab's own
#: grid (``timesheet/ui/week.py``) — a day column's width is shared equally
#: among every day, with the task info column taking whatever's left.
GRID_TASK_COLUMN = 128
GRID_DAY_COLUMN = 50
GRID_TOTAL_COLUMN = 36
#: Floor for the task info cell's two lines, once the window shrinks enough
#: that measuring would otherwise trim them to nothing useful.
GRID_TASK_CHARS_FLOOR = 10
#: Padding and scrollbar room deducted before measuring how much task-info
#: text fits, plus the billable dot beside the task title — same recipe as
#: the Week tab's own ``GUTTER``/``DOT_COLUMN`` (see ``WeekView._title_limit``).
GRID_GUTTER = 24
GRID_DOT_COLUMN = 15
#: Resizes smaller than this don't change the column widths enough to
#: bother re-measuring and redrawing.
GRID_RESIZE_SLACK = 8

HINT = "Click any figure or note to copy it"

#: What's exported is never rounded — ``Line.rounded`` is still called
#: throughout this view (it also does the float->int conversion), just
#: always with this rather than a user-chosen step.
EXACT = 0


class ExportView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.mode = "week"
        self.anchor = today()
        self.frame = theme.frame(parent)
        self.lines: list[Line] = []
        self._flash_job: str | None = None
        self._drawn_width = 0

        self._build()
        # Width only, same reasoning as WeekView._on_resize: the task-info
        # column's ellipsis is measured against it, and a redraw mid-layout
        # would restart the very pass that triggered this.
        self.frame.bind("<Configure>", self._on_resize, add="+")

    def _build(self) -> None:
        segmented = theme.frame(self.frame)
        segmented.pack(fill="x", padx=8, pady=(8, 4))
        self.week_btn = theme.button(
            segmented, "Week", lambda: self._set_mode("week"), bg=theme.BG_INPUT
        )
        self.week_btn.pack(side="left", fill="x", expand=True)
        self.day_btn = theme.button(
            segmented, "Day", lambda: self._set_mode("day"), bg=theme.BG_INPUT
        )
        self.day_btn.pack(side="left", fill="x", expand=True)

        nav = theme.frame(self.frame)
        nav.pack(fill="x", padx=8, pady=2)
        # A squared chip of its own, same recipe as the Day tab's date-nav
        # row: real click room on either side of the (longer, expanding)
        # range label in the middle, rather than a glyph with just enough
        # padding to miss.
        icons.chip(
            nav, "prev", self._previous, fallback="\u2039",
            fill=theme.BG_INPUT, text_fill=theme.TEXT,
            size=theme.NAV_CHIP_SIZE, radius=10, font=theme.FONT_ICON,
        ).pack(side="left")
        self.range_label = theme.button(
            nav, "", self._reset, bg=theme.BG, font=theme.FONT_BOLD
        )
        self.range_label.pack(side="left", fill="x", expand=True)
        icons.chip(
            nav, "next", self._next, fallback="\u203a",
            fill=theme.BG_INPUT, text_fill=theme.TEXT,
            size=theme.NAV_CHIP_SIZE, radius=10, font=theme.FONT_ICON,
        ).pack(side="left")

        # One row: the copy hint (and its confirmations) on the left, the
        # hours summary on the right. The summary packs first so it keeps its
        # room; the hint takes what is left. The hint doubles as the status
        # line, so confirming a copy doesn't shunt the table down a row.
        info = theme.frame(self.frame)
        info.pack(fill="x", padx=8, pady=(0, 0))
        self.totals = theme.label(info, "", fg=theme.MUTED, font=theme.FONT_SMALL, anchor="e")
        self.totals.pack(side="right")
        self.status = theme.label(
            info, HINT, fg=theme.MUTED, font=theme.FONT_SMALL, anchor="w"
        )
        self.status.pack(side="left", fill="x", expand=True)
        self.table = ScrollFrame(self.frame)
        self.table.pack(fill="both", expand=True, padx=8, pady=4)

        self.path_label = theme.button(
            self.frame,
            f"Exports: {paths.exports_dir()}",
            self._open_exports,
            bg=theme.BG,
            fg=theme.MUTED,
            font=theme.FONT_TINY,
        )
        self.path_label.pack(fill="x", padx=8, pady=(0, 8))

    # -- range -------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self.refresh()

    def _previous(self) -> None:
        self.anchor = add_days(self.anchor, -7 if self.mode == "week" else -1)
        self.refresh()

    def _next(self) -> None:
        self.anchor = add_days(self.anchor, 7 if self.mode == "week" else 1)
        self.refresh()

    def _reset(self) -> None:
        self.anchor = today()
        self.refresh()

    def _range(self) -> tuple[list[str], str, str]:
        """Returns ``(dates, title, filename)`` for the selected period."""
        if self.mode == "day":
            return [self.anchor], f"Timesheet \u2014 {pretty_date(self.anchor)}", f"{self.anchor}.md"
        start = start_of_week(self.anchor)
        title = (
            f"Timesheet \u2014 Week {iso_week_number(start)} "
            f"({short_date(start)} \u2013 {short_date(add_days(start, 6))})"
        )
        return week_dates(start, True), title, f"{week_label(start)}.md"

    # -- rendering ----------------------------------------------------------

    def _on_resize(self, event: tk.Event) -> None:
        if abs(event.width - self._drawn_width) <= GRID_RESIZE_SLACK:
            return
        self._drawn_width = event.width
        self.frame.after_idle(self._draw)

    def refresh(self) -> None:
        dates, title, filename = self._range()
        self.week_btn.config(fg=theme.TEXT if self.mode == "week" else theme.MUTED)
        self.day_btn.config(fg=theme.TEXT if self.mode == "day" else theme.MUTED)

        if self.mode == "day":
            self.range_label.config(text=pretty_date(self.anchor))
        else:
            start = start_of_week(self.anchor)
            self.range_label.config(
                text=f"Week {iso_week_number(start)} \u00b7 {short_date(start)} "
                f"\u2013 {short_date(add_days(start, 6))}"
            )

        self.dates = dates
        self.title = title
        self.filename = filename
        self.lines = collect_lines(self.store.db, dates)

        rounding = EXACT
        total = sum(line.rounded(rounding) for line in self.lines)
        billable = sum(line.rounded(rounding) for line in self.lines if line.billable)
        self.totals.config(
            text=f"{to_decimal_hours(total):.2f}h to book \u00b7 "
            f"{to_decimal_hours(billable):.2f}h billable \u00b7 "
            f"{len(self.lines)} line{'' if len(self.lines) == 1 else 's'}"
        )

        self._draw()

    _drawing = False
    _redraw = False

    def _draw(self) -> None:
        """Rebuilds the table. Building yields to Tk now and then (see
        ``yielding``), which can deliver a resize and so ask for another draw
        in the middle of this one; two builds clearing and filling the same
        table at once leave it broken. So a request that arrives mid-build
        just notes itself, and the build runs once more when this one ends."""
        if self._drawing:
            self._redraw = True
            return
        self._drawing = True
        try:
            while True:
                self._redraw = False
                self._draw_once()
                if not self._redraw:
                    break
        finally:
            self._drawing = False

    def _draw_once(self) -> None:
        self.table.clear()
        body = self.table.body

        if not self.lines:
            theme.label(
                body, "Nothing logged in this period.", fg=theme.MUTED, font=theme.FONT_SMALL
            ).pack(fill="x", pady=8)
            return

        if self.mode == "week":
            self._draw_week_grid(body)
        else:
            self._draw_day_list(body)

    def _draw_day_list(self, body: tk.Misc) -> None:
        """Day mode: unchanged from before the week grid existed — one
        heading per day (there's only ever the one), one card per entry."""
        rounding = EXACT
        for date, lines in by_date(self.lines).items():
            day_total = sum(line.rounded(rounding) for line in lines)
            head = theme.frame(body)
            head.pack(fill="x", pady=(6, 2))
            theme.label(
                head, f"{day_name(date)} {short_date(date)}", font=theme.FONT_HEAD
            ).pack(side="left")
            theme.label(
                head,
                f"{to_decimal_hours(day_total):.2f}h",
                fg=theme.MUTED,
                font=theme.FONT_SMALL,
                anchor="e",
            ).pack(side="right")

            for line in lines:
                self._row(body, line, rounding)

    # -- week grid ------------------------------------------------------------

    def _day_heading(self, date: str) -> str:
        """"19 Mon" — the day-number-first form a customer timesheet's own
        weekly grid headers its columns with."""
        return f"{from_iso(date).day} {day_name(date)}"

    def _week_rows(self) -> list[dict]:
        """Pivots :attr:`lines` into one row per task touched this week,
        each carrying the day's own ``Line`` (hours and notes together)
        keyed by date, in the order the task was first seen."""
        order: list[str] = []
        rows: dict[str, dict] = {}
        for line in self.lines:
            row = rows.get(line.task_id)
            if row is None:
                order.append(line.task_id)
                row = rows[line.task_id] = {
                    "heading": f"{line.client_name} : {line.k_code} {line.project_name}",
                    "task": line.display_title,
                    "billable": line.billable,
                    "by_date": {},
                }
            row["by_date"][line.date] = line
        # The Setup tab's own order, not the order tasks happened to turn
        # up in (which would depend on which day each was first logged).
        rank = setup_order(self.store.db)
        order.sort(key=lambda task_id: rank.get(task_id, len(rank)))
        return [rows[task_id] for task_id in order]

    def _task_column_limit(self) -> int:
        """How many characters of the heading/task lines fit in the grid's
        first column.

        Measured rather than guessed, same reasoning and recipe as
        ``WeekView._title_limit``: what's left over depends on the window
        width and how many days are shown, and the day columns plus Total
        take their share first since they're the ones a figure has to stay
        legible in, not the free text.
        """
        width = self.frame.winfo_width()
        if width <= 1:  # first draw; the resize handler redraws once laid out
            return GRID_TASK_CHARS_FLOOR
        room = (
            width
            - GRID_GUTTER
            - len(self.dates) * GRID_DAY_COLUMN
            - GRID_TOTAL_COLUMN
            - GRID_DOT_COLUMN
        )
        character = tkfont.Font(font=theme.FONT_SMALL).measure("n") or 6
        return max(GRID_TASK_CHARS_FLOOR, room // character)

    @staticmethod
    def _divider_column(slot: int) -> int:
        """Grid column of the hairline in front of day ``slot`` (the Total
        is slot ``len(dates)``). Every day owns two columns: the divider,
        then its figure."""
        return 2 * slot + 1

    def _draw_week_grid(self, body: tk.Misc) -> None:
        """The week table, built like the Week tab's own grid: every day
        (and the Total) is a divider column plus a figure column, and the
        dividers are cells of the grid itself spanning every row, so they
        stay put wherever the grid places the columns."""
        rounding = EXACT
        rows = self._week_rows()
        task_limit = self._task_column_limit()
        days = len(self.dates)
        total_slot = days

        grid = theme.frame(body)
        grid.pack(fill="both", expand=True)

        # The header strip: its own darker band, today's column in the accent tint.
        band = theme.header_fill()
        theme.frame(grid, bg=band).grid(
            row=0, column=0, columnspan=2 * (days + 1) + 1, sticky="nsew"
        )
        theme.label(
            grid, "Customer : Project / Task", bg=band, fg=theme.MUTED,
            font=theme.FONT_TINY_BOLD,
        ).grid(row=0, column=0, sticky="nsw", padx=(4, 0))
        for index, date in enumerate(self.dates):
            is_today = date == today()
            fill = theme.ACCENT_DIM if is_today else band
            cell = theme.frame(grid, bg=fill)
            cell.grid(
                row=0, column=self._divider_column(index) + 1, padx=(2, 1), sticky="nsew"
            )
            theme.label(
                cell, day_name(date), bg=fill, fg=theme.ACCENT if is_today else theme.TEXT,
                font=theme.FONT_TINY_BOLD, anchor="center",
            ).pack(pady=(3, 0))
            theme.label(
                cell, str(from_iso(date).day), bg=fill, fg=theme.MUTED,
                font=theme.FONT_TINY, anchor="center",
            ).pack(pady=(0, 3))
        theme.label(
            grid, "Total", bg=band, fg=theme.MUTED, font=theme.FONT_TINY_BOLD, anchor="center"
        ).grid(
            row=0, column=self._divider_column(total_slot) + 1, padx=(2, 1), sticky="nsew"
        )

        day_totals = [0] * days
        grand_total = 0
        for position, row in enumerate(rows):
            # Two grid rows per task: the customer/project line on its own,
            # then the task's row — title, figures and note chips together,
            # so the copy buttons sit level with the task label.
            row_index = 2 + 2 * position
            # Same alternating bands as the Week tab: white, then a lighter
            # grey than the page; every widget across both of the task's
            # grid rows shares the fill, or its own background would show
            # as a seam of the wrong shade.
            # A task with nothing logged this period has no figures to
            # frame, so it takes the page grey, like the Week tab's headers.
            has_values = any(
                line.rounded(rounding) for line in row["by_date"].values()
            )
            fill = theme.BG_RAISED if has_values else theme.BG
            # From column 0, so the heading cell is white edge to edge, not
            # only behind its text; the 1px gap under it is the row divider.
            # The customer/project line is its own row on the page grey, shown
            # once per group; the tasks under it sit on their own rows below.
            heading_row = row_index - 1
            new_group = position == 0 or rows[position - 1]['heading'] != row['heading']
            if new_group:
                theme.frame(grid, bg=theme.BG).grid(
                    row=heading_row, column=0, columnspan=2 * (days + 1) + 1,
                    sticky="nsew", pady=(0, 1),
                )
                theme.label(
                    grid, theme.ellipsize(row['heading'], task_limit),
                    bg=theme.BG, fg=theme.TEXT, font=theme.FONT_TINY_BOLD,
                ).grid(row=heading_row, column=0, sticky="w", padx=(4, 0), pady=(4, 2))
                # The group's rollup, faint, in the Total column.
                group_minutes = sum(
                    line.rounded(rounding)
                    for other in rows if other["heading"] == row["heading"]
                    for line in other["by_date"].values()
                )
                theme.label(
                    grid,
                    f"{to_decimal_hours(group_minutes):.2f}" if group_minutes else "\u2013",
                    bg=theme.BG, fg=theme.MUTED, font=theme.FONT_TINY, anchor="center",
                ).grid(
                    row=heading_row, column=self._divider_column(total_slot) + 1,
                    padx=(2, 1), pady=(4, 2), sticky="ew",
                )
            theme.frame(grid, bg=fill).grid(
                row=row_index, column=0, columnspan=2 * (days + 1) + 1,
                sticky="nsew", pady=(0, 1),
            )
            self._task_info_cell(grid, row_index, row, task_limit, fill)
            row_total = 0
            for index, date in enumerate(self.dates):
                line = row["by_date"].get(date)
                cell = theme.frame(grid, bg=fill)
                cell.grid(
                    row=row_index, column=self._divider_column(index) + 1,
                    padx=(2, 1), pady=1, sticky="ew",
                )
                if line is not None:
                    minutes = line.rounded(rounding)
                    row_total += minutes
                    day_totals[index] += minutes
                    self._day_cell(cell, line, rounding, fill)
            grand_total += row_total
            theme.label(
                grid,
                f"{to_decimal_hours(row_total):.2f}" if row_total else "\u2013",
                bg=fill, fg=theme.BILLABLE if row_total else theme.MUTED,
                font=theme.FONT_TINY_BOLD, anchor="center",
            ).grid(
                row=row_index, column=self._divider_column(total_slot) + 1,
                padx=(2, 1), sticky="ew",
            )

        # The totals row on the same tint the Week tab's footer uses.
        footer = 2 * len(rows) + 1
        foot = theme.blend(theme.PLANNED_BG, theme.BILLABLE_BG, 0.5)
        theme.frame(grid, bg=foot).grid(
            row=footer, column=0, columnspan=2 * (days + 1) + 1, sticky="nsew", pady=(8, 0)
        )
        theme.label(grid, "Total", bg=foot, font=theme.FONT_HEAD).grid(
            row=footer, column=0, sticky="w", pady=(8, 0)
        )
        for index, total in enumerate(day_totals):
            theme.label(
                grid,
                f"{to_decimal_hours(total):.2f}" if total else "\u2013",
                bg=foot, fg=theme.BILLABLE if total else theme.MUTED,
                font=theme.FONT_TINY_BOLD, anchor="center",
            ).grid(
                row=footer, column=self._divider_column(index) + 1,
                padx=(2, 1), pady=(8, 0), sticky="ew",
            )
        theme.label(
            grid,
            f"{to_decimal_hours(grand_total):.2f}" if grand_total else "\u2013",
            bg=foot, fg=theme.BILLABLE if grand_total else theme.MUTED,
            font=theme.FONT_TINY_BOLD, anchor="center",
        ).grid(
            row=footer, column=self._divider_column(total_slot) + 1,
            padx=(2, 1), pady=(8, 0), sticky="ew",
        )

        grid.columnconfigure(0, weight=3, minsize=GRID_TASK_COLUMN)
        for slot in range(days):
            column = self._divider_column(slot)
            grid.columnconfigure(column, weight=0, minsize=1)
            grid.columnconfigure(column + 1, weight=1, uniform="day", minsize=GRID_DAY_COLUMN)
        total_column = self._divider_column(total_slot)
        grid.columnconfigure(total_column, weight=0, minsize=1)
        grid.columnconfigure(total_column + 1, minsize=GRID_TOTAL_COLUMN)

        # Last, once the row count is known: one hairline per day and the
        # Total, each a grid cell of its own spanning every row.
        for slot in range(days + 1):
            tk.Frame(grid, bg=theme.LINE, width=1, bd=0, highlightthickness=0).grid(
                row=0, column=self._divider_column(slot), rowspan=footer + 1, sticky="ns"
            )

    @staticmethod
    def _stripe_grey() -> str:
        """The second band: lighter than the page itself, so it reads as a
        deliberate shade rather than vanishing into the background."""
        return theme.blend(theme.BG, theme.BG_RAISED, 0.5)

    def _task_info_cell(
        self, grid: tk.Misc, row_index: int, row: dict, limit: int, fill: str
    ) -> None:
        title_row = theme.frame(grid, bg=fill)
        title_row.grid(row=row_index, column=0, sticky="w", padx=(4, 0), pady=(2, 3))
        theme.dot(title_row, row["billable"], bg=fill).pack(side="left", padx=(0, 4))
        theme.label(
            title_row, theme.ellipsize(row["task"], limit), bg=fill, font=theme.FONT_SMALL,
        ).pack(side="left")

    def _day_cell(self, cell: tk.Misc, line: Line, rounding: int, fill: str) -> None:
        """Hours on the left, a note chip to its right — the chip's own fill
        tells you whether there's anything on it before you even click:
        accented when there's a note to copy, muted when there isn't."""
        hours = line.hours(rounding)
        theme.button(
            cell, hours, lambda: self._copy(hours, f"{hours}h copied"),
            bg=fill, fg=theme.TEXT, hover=theme.BG_INPUT, font=theme.FONT_TINY, padx=3, pady=1,
        ).pack(side="left")

        note = line.note_text()
        icons.chip(
            cell,
            "note-filled" if note else "note",
            (lambda n=note, c=len(line.notes): self._copy(
                n, f"Note copied ({c} entr{'y' if c == 1 else 'ies'})"
            )) if note else (lambda: None),
            fallback="\U0001f4ac",
            fill=theme.ACCENT_DIM if note else fill, bg=fill,
            size=16, radius=5, enabled=bool(note),
        ).pack(side="left", padx=(1, 0))

    def _row(self, parent: tk.Misc, line: Line, rounding: int) -> None:
        card = theme.frame(parent, bg=theme.BG_RAISED, highlightthickness=0)
        theme.round_corners(card, 8, border=theme.LINE)
        card.pack(fill="x", pady=2)

        top = theme.frame(card, bg=theme.BG_RAISED)
        top.pack(fill="x", padx=6, pady=(5, 2))

        hours = line.hours(rounding)
        self._copy_button(
            top, hours, hours, f"{hours}h copied",
            bg=theme.BG_INPUT, fg=theme.TEXT, font=theme.FONT_BOLD,
        ).pack(side="right")
        theme.label(
            top,
            theme.ellipsize(line.display_title, 38),
            bg=theme.BG_RAISED,
            font=theme.FONT_BOLD,
        ).pack(side="left", fill="x", expand=True)

        meta = theme.frame(card, bg=theme.BG_RAISED)
        meta.pack(fill="x", padx=6, pady=(0, 5))
        theme.dot(meta, line.billable, bg=theme.BG_RAISED).pack(side="left", padx=(0, 4))
        theme.pill(meta, line.k_code or "\u2014").pack(side="left")
        theme.label(
            meta,
            theme.ellipsize(f"{line.client_name} \u00b7 {line.project_name}", 34),
            bg=theme.BG_RAISED,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
        ).pack(side="left", padx=4)

        note = line.note_text()
        notes_row = theme.frame(card, bg=theme.BG_RAISED)
        notes_row.pack(fill="x", padx=6, pady=(0, 6))
        if note:
            self._copy_button(
                notes_row,
                theme.ellipsize(note, 52),
                note,
                f"Note copied ({len(line.notes)} entr"
                f"{'y' if len(line.notes) == 1 else 'ies'})",
                bg=theme.BG_INPUT, fg=theme.PILL_TEXT, font=theme.FONT_SMALL,
                anchor="w",
            ).pack(fill="x")
        else:
            theme.label(
                notes_row,
                "No note on this entry",
                bg=theme.BG_RAISED,
                fg=theme.IDLE,
                font=theme.FONT_SMALL,
            ).pack(fill="x")

    def _copy_button(
        self, parent: tk.Misc, text: str, payload: str, message: str, **kw
    ) -> tk.Button:
        """A field you click to put its own contents on the clipboard."""
        widget = theme.button(parent, text, lambda: None, padx=6, pady=2, **kw)
        widget.config(command=lambda: self._copy(payload, message, widget))
        return widget

    # -- actions -------------------------------------------------------------

    def _flash(self, message: str) -> None:
        if self._flash_job is not None:
            self.frame.after_cancel(self._flash_job)
        self.status.config(text=message, fg=theme.BILLABLE)
        self._flash_job = self.frame.after(2500, self._clear_flash)

    def _clear_flash(self) -> None:
        self._flash_job = None
        self.status.config(text=HINT, fg=theme.MUTED)

    def _copy(self, text: str, message: str, source: tk.Widget | None = None) -> None:
        self.frame.clipboard_clear()
        self.frame.clipboard_append(text)
        self._flash(message)
        if source is not None:
            self._mark_copied(source)

    def _mark_copied(self, widget: tk.Widget) -> None:
        """Tints the clicked field, so it's obvious which one is on the clipboard."""
        was = widget.cget("bg")
        widget.config(bg=theme.ACCENT_DIM)

        def restore() -> None:
            # A refresh between the click and here destroys the button.
            if widget.winfo_exists():
                widget.config(bg=was)

        self.frame.after(600, restore)

    def _open_exports(self) -> None:
        directory = paths.exports_dir()
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)  # noqa: S606 - opening Explorer is the intent
