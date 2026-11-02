"""The Month tab: a calendar grid, for a feel of the month at a glance.

Always a full 7-day week per row regardless of ``show_weekend`` — a calendar
reads oddly with a weekend missing — and nothing models a monthly target, so
the summary is just what was tracked, not "vs target" the way Day and Week
show it.
"""

from __future__ import annotations

import tkinter as tk

from ..dates import (
    add_months,
    format_minutes,
    from_iso,
    is_same_month,
    month_label,
    month_weeks,
    start_of_month,
    today,
)
from ..store import Store
from . import icons, theme

#: Floor so a 5- or 6-row month still reads as a grid, not a sliver, inside
#: the overlay's fixed-height window.
MIN_ROW_HEIGHT = 44


class MonthView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.month_start = start_of_month(today())
        self.frame = theme.frame(parent)

        self._build_header()
        self.grid = theme.frame(self.frame)
        self.grid.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    # -- chrome -------------------------------------------------------------

    def _build_header(self) -> None:
        nav = theme.frame(self.frame)
        nav.pack(fill="x", padx=8, pady=(8, 4))
        # A squared chip of its own, same recipe as the Day tab's date-nav
        # row: real click room on either side of the (longer, expanding)
        # month label in the middle, rather than a glyph with just enough
        # padding to miss.
        icons.chip(
            nav, "prev", self._previous, fallback="\u2039",
            fill=theme.BG_INPUT, text_fill=theme.TEXT,
            size=theme.NAV_CHIP_SIZE, radius=10, font=theme.FONT_ICON,
        ).pack(side="left")
        self.month_label = theme.button(
            nav, "", self._this_month, bg=theme.BG, font=theme.FONT_BOLD
        )
        self.month_label.pack(side="left", fill="x", expand=True)
        icons.chip(
            nav, "next", self._next, fallback="\u203a",
            fill=theme.BG_INPUT, text_fill=theme.TEXT,
            size=theme.NAV_CHIP_SIZE, radius=10, font=theme.FONT_ICON,
        ).pack(side="left")

        summary = theme.frame(self.frame)
        summary.pack(fill="x", padx=8, pady=(0, 6))
        self.total_label = theme.label(summary, "", font=theme.FONT_SMALL)
        self.total_label.pack(side="left")

    def _previous(self) -> None:
        self.month_start = add_months(self.month_start, -1)
        self.refresh()

    def _next(self) -> None:
        self.month_start = add_months(self.month_start, 1)
        self.refresh()

    def _this_month(self) -> None:
        self.month_start = start_of_month(today())
        self.refresh()

    # -- rendering ------------------------------------------------------------

    def refresh(self) -> None:
        self.month_label.config(text=month_label(self.month_start))

        weeks = month_weeks(self.month_start)
        total = billable = 0
        for week in weeks:
            for date in week:
                if not is_same_month(date, self.month_start):
                    continue
                day_total, day_billable = self.store.day_totals(date)
                total += day_total
                billable += day_billable
        self.total_label.config(
            text=f"{format_minutes(total)} tracked \u00b7 {format_minutes(billable)} billable"
        )

        for child in self.grid.winfo_children():
            child.destroy()

        for column, date in enumerate(weeks[0]):
            theme.label(
                self.grid, from_iso(date).strftime("%a"), fg=theme.MUTED,
                font=theme.FONT_TINY, anchor="center",
            ).grid(row=0, column=column, sticky="ew", pady=(0, 3))

        for row, week in enumerate(weeks, start=1):
            for column, date in enumerate(week):
                self._day_cell(row, column, date)

        for column in range(7):
            self.grid.columnconfigure(column, weight=1, uniform="day")
        for row in range(1, len(weeks) + 1):
            self.grid.rowconfigure(row, weight=1, minsize=MIN_ROW_HEIGHT)

    def _day_cell(self, row: int, column: int, date: str) -> None:
        in_month = is_same_month(date, self.month_start)
        is_today = date == today()
        bg = theme.BG_RAISED if in_month else theme.BG

        cell = theme.frame(self.grid, bg=bg, highlightthickness=2 if is_today else 1)
        cell.config(highlightbackground=theme.ACCENT if is_today else theme.LINE)
        cell.grid(row=row, column=column, sticky="nsew", padx=1, pady=1)

        top = theme.frame(cell, bg=bg)
        top.pack(fill="x", padx=4, pady=(3, 0))
        number = theme.label(
            top,
            str(from_iso(date).day),
            bg=bg,
            fg=theme.TEXT if in_month else theme.IDLE,
            font=theme.FONT_BOLD if is_today else theme.FONT_SMALL,
        )
        number.pack(side="left")

        total, billable = self.store.day_totals(date)
        clickable = [cell, top, number]
        if total:
            # Green once at least half the day's tracked time is billable,
            # the same threshold a single dot can sensibly stand in for.
            theme.dot(top, billable * 2 >= total, bg=bg).pack(side="right", pady=2)
            minutes = theme.label(
                cell, format_minutes(total), bg=bg,
                fg=theme.TEXT if in_month else theme.MUTED, font=theme.FONT_TINY,
            )
            minutes.pack(anchor="w", padx=4, pady=(1, 3))
            clickable.append(minutes)

        for widget in clickable:
            widget.bind("<Button-1>", lambda _e, d=date: self._jump(d))
            widget.config(cursor="hand2")

    def _jump(self, date: str) -> None:
        """Clicking a day jumps into it on the Day tab — a calendar is for
        finding a day, not for working inside one cell of it."""
        self.overlay.show_tab("Today")
        view = self.overlay._views["Today"]
        view.date = date
        view.refresh()
