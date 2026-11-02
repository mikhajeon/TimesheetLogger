"""The repeat editor: the panel inside an expanded task that sets its rule.

Laid out as a sentence that grows — pick a frequency and only the parts that
frequency needs appear. A full Outlook recurrence dialog would not fit a 470px
overlay, and most of its controls would be blank most of the time.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from ..models import Recurrence
from ..recurrence import (
    DAILY,
    LAST_DAY,
    MONTH_NAMES,
    MONTHLY,
    WEEKDAY_NAMES,
    WEEKLY,
    describe,
    next_after,
    today_iso,
)
from . import theme

FREQUENCY_LABELS = {"Days": DAILY, "Weeks": WEEKLY, "Months": MONTHLY}

#: Dates run 1-28 on four rows of seven, then a short row for the ones not
#: every month has, so the awkward ones are visibly set apart.
FIRST_DATES = list(range(1, 29))
LATE_DATES = [29, 30, 31]

#: Roughly what fits the summary line at the overlay's narrowest.
SUMMARY_CHARS = 60


class RepeatEditor:
    """Builds itself into ``parent``; call :meth:`build` again to redraw."""

    def __init__(
        self,
        parent: tk.Misc,
        rule: Recurrence | None,
        on_change: Callable[[Recurrence | None], None],
        bg: str | None = None,
        header_trailing: Callable[[tk.Misc], None] | None = None,
        step: int = 15,
    ) -> None:
        self.parent = parent
        self.on_change = on_change
        self.bg = bg or theme.BG_RAISED
        #: The "Plan" duration picker's own step — ``Settings.entry_increment_minutes``,
        #: threaded in by whichever tab built this editor rather than read
        #: from the store directly, since this editor doesn't hold one.
        self.step = step
        # Edited in place and handed back on every change, so the panel can
        # redraw from it without the caller having to round-trip the store.
        self.rule = rule
        # Called with the summary row's own frame, if given, so a caller can
        # pack something in beside the Repeat checkbox — a row's own delete
        # control, say — without this editor needing to know what it is.
        self.header_trailing = header_trailing
        self.frame = theme.frame(parent, bg=self.bg)
        self.frame.pack(fill="x")
        # Tk holds only a weak reference to a variable, so they live here.
        self._vars: list[tk.Variable] = []
        self.build()

    # -- building -----------------------------------------------------------

    def build(self) -> None:
        for child in self.frame.winfo_children():
            child.destroy()
        self._vars.clear()

        self._summary()
        if self.rule is None:
            return
        self._every()
        if self.rule.frequency == WEEKLY:
            self._weekdays()
        elif self.rule.frequency == MONTHLY:
            self._month_dates()
            self._months()
        self._duration_and_dates()

    def _summary(self) -> None:
        row = theme.frame(self.frame, bg=self.bg)
        row.pack(fill="x")
        # Packed first, with side="right", so it claims the row's own
        # rightmost edge before "Repeat" and its description below fill in
        # from the left.
        if self.header_trailing is not None:
            self.header_trailing(row)
        theme.label(
            row, "Repeat", bg=self.bg, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(side="left")

        on = tk.BooleanVar(value=self.rule is not None)
        self._vars.append(on)
        theme.checkbox(row, "", on, lambda: self._toggle(on.get()), bg=self.bg).pack(side="left")

        # Nothing to summarise while it's off — "Does not repeat" next to an
        # unticked "Repeat" checkbox only says the checkbox's own state back.
        if self.rule is not None:
            theme.label(
                row,
                theme.ellipsize(describe(self.rule), SUMMARY_CHARS),
                bg=self.bg,
                fg=theme.TEXT,
                font=theme.FONT_SMALL,
            ).pack(side="left", fill="x", expand=True)

    def _every(self) -> None:
        row = theme.frame(self.frame, bg=self.bg)
        row.pack(fill="x", pady=(4, 0))
        theme.label(
            row, "Every", bg=self.bg, fg=theme.MUTED, font=theme.FONT_SMALL
        ).pack(side="left")

        interval = theme.entry(row, width=3, font=theme.FONT_SMALL, justify="center")
        interval.insert(0, str(max(1, self.rule.interval)))
        interval.pack(side="left", padx=4)
        interval.bind("<FocusOut>", lambda _e: self._set_interval(interval.get()))
        interval.bind("<Return>", lambda _e: self._set_interval(interval.get()))

        current = next(k for k, v in FREQUENCY_LABELS.items() if v == self.rule.frequency)
        choice = tk.StringVar(value=current)
        self._vars.append(choice)
        theme.option_menu(
            row, choice, list(FREQUENCY_LABELS), command=self._set_frequency
        ).pack(side="left")

        upcoming = next_after(self.rule, today_iso())
        theme.label(
            row,
            f"next {upcoming}" if upcoming else "never happens again",
            bg=self.bg,
            fg=theme.MUTED if upcoming else theme.DANGER,
            font=theme.FONT_TINY,
            anchor="e",
        ).pack(side="right")

    def _weekdays(self) -> None:
        self._toggle_row(
            "On",
            [(WEEKDAY_NAMES[day], day) for day in range(7)],
            self.rule.weekdays,
            "weekdays",
            columns=7,
        )

    def _month_dates(self) -> None:
        self._toggle_row(
            "On the",
            [(str(date), date) for date in FIRST_DATES],
            self.rule.month_days,
            "month_days",
            columns=7,
        )
        self._toggle_row(
            "",
            [(str(d), d) for d in LATE_DATES] + [("Last", LAST_DAY)],
            self.rule.month_days,
            "month_days",
            columns=7,
            hint="not every month has a 29th, 30th or 31st \u2014 those months are skipped",
        )

    def _months(self) -> None:
        self._toggle_row(
            "Of",
            [(MONTH_NAMES[month - 1], month) for month in range(1, 13)],
            self.rule.months,
            "months",
            columns=6,
            hint="none ticked means every month",
        )

    def _toggle_row(
        self,
        caption: str,
        options: list[tuple[str, int]],
        selected: list[int],
        field: str,
        columns: int,
        hint: str = "",
    ) -> None:
        """A caption over a wrapped grid of on/off chips."""
        if caption:
            theme.label(
                self.frame, caption, bg=self.bg, fg=theme.MUTED, font=theme.FONT_TINY
            ).pack(fill="x", pady=(4, 1))

        grid = theme.frame(self.frame, bg=self.bg)
        grid.pack(fill="x")
        for index in range(columns):
            grid.columnconfigure(index, weight=1, uniform="chip")

        for position, (label, value) in enumerate(options):
            on = value in selected
            theme.button(
                grid,
                label,
                lambda f=field, v=value: self._flip(f, v),
                bg=theme.ACCENT if on else theme.BG_INPUT,
                fg=theme.ON_ACCENT if on else theme.MUTED,
                hover=theme.ACCENT_HOVER if on else theme.BG_HOVER,
                font=theme.FONT_TINY,
                padx=2,
                pady=2,
            ).grid(row=position // columns, column=position % columns, sticky="ew", padx=1, pady=1)

        if hint:
            theme.label(
                self.frame, hint, bg=self.bg, fg=theme.MUTED, font=theme.FONT_TINY
            ).pack(fill="x")

    def _duration_and_dates(self) -> None:
        row = theme.frame(self.frame, bg=self.bg)
        row.pack(fill="x", pady=(5, 0))

        theme.label(
            row, "Plan", bg=self.bg, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(side="left")
        theme.duration_picker(
            row, self.rule.minutes, self._set_minutes, width=6, font=theme.FONT_SMALL,
            step=self.step,
        ).pack(side="left", padx=(3, 8))

        theme.label(
            row, "each time, from", bg=self.bg, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(side="left")
        theme.date_picker(
            row, self.rule.start or today_iso(), lambda d: self._set_date("start", d),
            width=10,
        ).pack(side="left", padx=3)

        theme.label(
            row, "until", bg=self.bg, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(side="left")
        theme.date_picker(
            row, self.rule.until, lambda d: self._set_date("until", d),
            allow_clear=True, clear_label="Forever", placeholder="forever", width=10,
        ).pack(side="left", padx=3)

    # -- editing ------------------------------------------------------------

    def _toggle(self, wanted: bool) -> None:
        self.rule = Recurrence(start=today_iso()) if wanted else None
        self._changed()

    def _set_frequency(self, label: str) -> None:
        frequency = FREQUENCY_LABELS[label]
        if frequency == self.rule.frequency:
            return
        self.rule.frequency = frequency
        self._changed()

    def _set_interval(self, text: str) -> None:
        try:
            value = max(1, int(text.strip()))
        except ValueError:
            value = 1
        if value != self.rule.interval:
            self.rule.interval = value
            self._changed()

    def _set_minutes(self, minutes: int) -> None:
        if minutes and minutes != self.rule.minutes:
            self.rule.minutes = minutes
            self._changed()

    def _set_date(self, field: str, value: str) -> None:
        if value == getattr(self.rule, field):
            return
        # An empty start would make the rule match nothing at all — the
        # calendar popup can still clear "until" to "forever", but "start"
        # always keeps a real date, the same floor the old typed field had.
        setattr(self.rule, field, value or (today_iso() if field == "start" else ""))
        self._changed()

    def _flip(self, field: str, value: int) -> None:
        chosen = list(getattr(self.rule, field))
        chosen.remove(value) if value in chosen else chosen.append(value)
        setattr(self.rule, field, sorted(chosen))
        self._changed()

    def _changed(self) -> None:
        self.on_change(self.rule)
        # Saving usually makes the view around us redraw, and that replaces
        # this editor wholesale. Only redraw ourselves if we outlived it.
        if self.frame.winfo_exists():
            self.build()
