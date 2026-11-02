"""The Export tab: a click-to-copy table of the figures to key into a timesheet.

Each row is one task on one day, with every entry against it rolled into a
single hours figure and a single note. Clicking either puts it on the
clipboard, so filling in the customer timesheet is click, paste, click, paste.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import os
import tkinter as tk
import tkinter.font as tkfont

from .. import paths
from ..bookings import Line, by_date
from ..bookings import collect as collect_lines
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
from ..markdown import build_markdown
from ..store import Store
from . import icons, theme
from .scroll import ScrollFrame

#: Header of Day mode's whole-table copy — one row per entry. Tabs, so it
#: lands in Excel as a real grid.
DAY_COLUMNS = ["Date", "Client", "Project", "K-code", "Task", "Hours", "Notes"]

#: Week mode instead pivots to one row per task, hours filed under the day
#: they were logged — the shape a customer timesheet's own weekly grid
#: wants, so the whole table pastes straight into one rather than needing a
#: pivot built around Day mode's per-entry rows first.
WEEK_LABEL_COLUMNS = ["Customer : Project", "Task", "Time type"]

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

        self.totals = theme.label(self.frame, "", fg=theme.MUTED, font=theme.FONT_SMALL)
        self.totals.pack(fill="x", padx=8)

        # Doubles as the hint, so confirming a copy doesn't shunt the table
        # down by a row every time.
        self.status = theme.label(self.frame, HINT, fg=theme.MUTED, font=theme.FONT_SMALL)
        self.status.pack(fill="x", padx=8, pady=(2, 0))

        self.table = ScrollFrame(self.frame)
        self.table.pack(fill="both", expand=True, padx=8, pady=4)

        actions = theme.frame(self.frame)
        actions.pack(fill="x", padx=8, pady=(0, 4))
        theme.button(
            actions,
            "Copy whole table",
            self._copy_table,
            bg=theme.ACCENT,
            fg=theme.ON_ACCENT,
            hover=theme.ACCENT_HOVER,
        ).pack(side="left")
        theme.button(actions, "Save to exports/", self._save).pack(side="left", padx=4)
        theme.button(actions, "Open as CSV", self._open_csv).pack(side="left")

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

    def _draw(self) -> None:
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

    def _draw_week_grid(self, body: tk.Misc) -> None:
        rounding = EXACT
        rows = self._week_rows()
        task_limit = self._task_column_limit()

        grid = theme.frame(body)
        grid.pack(fill="both", expand=True)

        theme.label(grid, "Customer : Project / Task", fg=theme.MUTED, font=theme.FONT_TINY).grid(
            row=0, column=0, sticky="w"
        )
        for index, date in enumerate(self.dates):
            cell = theme.frame(grid)
            cell.grid(row=0, column=index + 1, padx=1, sticky="ew")
            is_today = date == today()
            theme.label(
                cell, day_name(date), fg=theme.ACCENT if is_today else theme.TEXT,
                font=theme.FONT_TINY, anchor="center",
            ).pack()
            theme.label(
                cell, str(from_iso(date).day), fg=theme.MUTED, font=theme.FONT_TINY, anchor="center",
            ).pack()
        theme.label(
            grid, "Total", fg=theme.MUTED, font=theme.FONT_TINY, anchor="center"
        ).grid(row=0, column=len(self.dates) + 1, padx=(4, 0), sticky="ew")

        day_totals = [0] * len(self.dates)
        grand_total = 0
        for row_index, row in enumerate(rows, start=1):
            self._task_info_cell(grid, row_index, row, task_limit)
            row_total = 0
            for col_index, date in enumerate(self.dates):
                line = row["by_date"].get(date)
                cell = theme.frame(grid)
                cell.grid(row=row_index, column=col_index + 1, padx=1, pady=1, sticky="ew")
                if line is not None:
                    minutes = line.rounded(rounding)
                    row_total += minutes
                    day_totals[col_index] += minutes
                    self._day_cell(cell, line, rounding)
            grand_total += row_total
            theme.label(
                grid,
                f"{to_decimal_hours(row_total):.2f}" if row_total else "\u2013",
                fg=theme.MUTED, font=theme.FONT_TINY, anchor="center",
            ).grid(row=row_index, column=len(self.dates) + 1, padx=(4, 0), sticky="ew")

        footer = len(rows) + 1
        theme.label(grid, "Total", font=theme.FONT_HEAD).grid(
            row=footer, column=0, sticky="w", pady=(8, 0)
        )
        for col_index, total in enumerate(day_totals):
            theme.label(
                grid,
                f"{to_decimal_hours(total):.2f}" if total else "\u2013",
                fg=theme.MUTED, font=theme.FONT_TINY, anchor="center",
            ).grid(row=footer, column=col_index + 1, pady=(8, 0), sticky="ew")
        theme.label(
            grid,
            f"{to_decimal_hours(grand_total):.2f}" if grand_total else "\u2013",
            font=theme.FONT_TINY, anchor="center",
        ).grid(row=footer, column=len(self.dates) + 1, pady=(8, 0), padx=(4, 0), sticky="ew")

        grid.columnconfigure(0, weight=3, minsize=GRID_TASK_COLUMN)
        for index in range(len(self.dates)):
            grid.columnconfigure(index + 1, weight=1, uniform="day", minsize=GRID_DAY_COLUMN)
        grid.columnconfigure(len(self.dates) + 1, minsize=GRID_TOTAL_COLUMN)

    def _task_info_cell(self, grid: tk.Misc, row_index: int, row: dict, limit: int) -> None:
        cell = theme.frame(grid)
        cell.grid(row=row_index, column=0, sticky="w", pady=2)
        theme.label(
            cell, theme.ellipsize(row["heading"], limit),
            fg=theme.MUTED, font=theme.FONT_TINY,
        ).pack(anchor="w")
        title_row = theme.frame(cell)
        title_row.pack(anchor="w")
        theme.dot(title_row, row["billable"]).pack(side="left", padx=(0, 4))
        theme.label(
            title_row, theme.ellipsize(row["task"], limit), font=theme.FONT_SMALL,
        ).pack(side="left")

    def _day_cell(self, cell: tk.Misc, line: Line, rounding: int) -> None:
        """Hours on the left, a note chip to its right — the chip's own fill
        tells you whether there's anything on it before you even click:
        accented when there's a note to copy, muted when there isn't."""
        hours = line.hours(rounding)
        theme.button(
            cell, hours, lambda: self._copy(hours, f"{hours}h copied"),
            bg=theme.BG_INPUT, fg=theme.TEXT, font=theme.FONT_TINY, padx=3, pady=1,
        ).pack(side="left")

        note = line.note_text()
        icons.chip(
            cell,
            "note-filled" if note else "note",
            (lambda n=note, c=len(line.notes): self._copy(
                n, f"Note copied ({c} entr{'y' if c == 1 else 'ies'})"
            )) if note else (lambda: None),
            fallback="\U0001f4ac",
            fill=theme.ACCENT_DIM if note else theme.BG,
            size=16, radius=5, enabled=bool(note),
        ).pack(side="left", padx=(1, 0))

    def _row(self, parent: tk.Misc, line: Line, rounding: int) -> None:
        card = theme.frame(parent, bg=theme.BG_RAISED, highlightthickness=1)
        card.config(highlightbackground=theme.LINE)
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

    def _week_table(self) -> list[list[str]]:
        """The week grid as plain rows and columns — what both the TSV and
        CSV forms are built from, and what the on-screen grid itself draws.
        Blank cells are blank here too, same as a day nothing was logged.
        """
        rounding = EXACT
        header = (
            WEEK_LABEL_COLUMNS + [self._day_heading(d) for d in self.dates] + ["Total"]
        )
        table = [header]

        day_totals = [0] * len(self.dates)
        grand_total = 0
        for row in self._week_rows():
            cells = [row["heading"], row["task"], "Billable" if row["billable"] else "Non-billable"]
            row_total = 0
            for index, date in enumerate(self.dates):
                line = row["by_date"].get(date)
                if line is None:
                    cells.append("")
                    continue
                minutes = line.rounded(rounding)
                row_total += minutes
                day_totals[index] += minutes
                cells.append(line.hours(rounding))
            grand_total += row_total
            cells.append(f"{to_decimal_hours(row_total):.2f}" if row_total else "")
            table.append(cells)

        table.append(
            ["Total", "", ""]
            + [f"{to_decimal_hours(t):.2f}" if t else "" for t in day_totals]
            + [f"{to_decimal_hours(grand_total):.2f}" if grand_total else ""]
        )
        return table

    def _as_tsv(self) -> str:
        if self.mode == "week":
            return "\n".join("\t".join(row) for row in self._week_table())
        rounding = EXACT
        rows = ["\t".join(DAY_COLUMNS)]
        for line in self.lines:
            rows.append(
                "\t".join(
                    [
                        line.date,
                        line.client_name,
                        line.project_name,
                        line.k_code,
                        line.display_title,
                        line.hours(rounding),
                        line.note_text(),
                    ]
                )
            )
        return "\n".join(rows)

    def _copy_table(self) -> None:
        if not self.lines:
            self._flash("Nothing to copy")
            return
        count = len(self._week_rows()) if self.mode == "week" else len(self.lines)
        noun = "task" if self.mode == "week" else "row"
        self._copy(
            self._as_tsv(),
            f"{count} {noun}{'' if count == 1 else 's'} copied \u2014 paste into a sheet",
        )

    def _save(self) -> None:
        directory = paths.exports_dir()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / self.filename
        target.write_text(build_markdown(self.store.db, self.dates, self.title), "utf-8")
        self._flash(f"Saved {self.filename}")

    def _as_csv(self) -> str:
        """Same rows as :meth:`_as_tsv`, but through the ``csv`` module.

        A note with a comma or a line break in it would break a naive
        join-on-comma; ``csv.writer`` quotes whatever needs it so the file
        still opens as one column per field.
        """
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        if self.mode == "week":
            for row in self._week_table():
                writer.writerow(row)
            return buffer.getvalue()

        rounding = EXACT
        writer.writerow(DAY_COLUMNS)
        for line in self.lines:
            writer.writerow(
                [
                    line.date,
                    line.client_name,
                    line.project_name,
                    line.k_code,
                    line.display_title,
                    line.hours(rounding),
                    line.note_text(),
                ]
            )
        return buffer.getvalue()

    def _open_csv(self) -> None:
        """Writes the table to ``exports/`` as a ``.csv`` and opens it.

        Opening it rather than just the folder (``_open_exports``) is the
        point: Explorer hands a ``.csv`` straight to Excel or whatever else
        is registered for it, so the table is already on screen.

        Stamped with the time, down to the second, rather than reusing
        ``self.filename``'s plain day/week name: Excel refuses to open a
        second file that shares a name with one it already has open, so a
        repeat click — exporting again after logging more time — would
        otherwise hit a wall instead of a fresh table. A second click inside
        the same second is still possible, so a numeric suffix backs the
        timestamp up rather than trusting it alone.
        """
        if not self.lines:
            self._flash("Nothing to export")
            return
        directory = paths.exports_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = f"{self.filename.removesuffix('.md')}-{stamp}"
        target = directory / f"{stem}.csv"
        suffix = 1
        while target.exists():
            suffix += 1
            target = directory / f"{stem}-{suffix}.csv"
        # newline="" so csv's own \r\n survives instead of becoming \r\r\n.
        target.write_text(self._as_csv(), "utf-8", newline="")
        os.startfile(target)  # noqa: S606 - opening it is the intent
        self._flash(f"Opened {target.name}")

    def _open_exports(self) -> None:
        directory = paths.exports_dir()
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)  # noqa: S606 - opening Explorer is the intent
