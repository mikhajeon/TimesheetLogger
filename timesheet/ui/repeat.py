"""The repeat editor: the panel inside an expanded task that sets its rule.

Laid out as a sentence that grows — pick a frequency and only the parts that
frequency needs appear. A full Outlook recurrence dialog would not fit a 470px
overlay, and most of its controls would be blank most of the time.
"""

from __future__ import annotations

import calendar
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
from ..dates import from_iso
from . import modern, theme

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
        resize: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self.parent = parent
        #: Runs a change that alters the editor's height (the toggle, the
        #: Days/Weeks/Months switch) so the owner can animate the resize;
        #: ``None`` just runs it.
        self.resize = resize
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
        #: Which month's days the grid shows. Only a view: the rule itself
        #: repeats every month on the days ticked, whichever month is shown.
        self._view_month: int | None = None
        #: The live widgets a change has to touch, so most edits update them
        #: in place instead of redrawing the editor (let alone the modal).
        self._chips: dict[str, dict[int, modern.ToggleChip]] = {}
        self._month_chips: dict[int, modern.ToggleChip] = {}
        self._summary_label: tk.Label | None = None
        self._next_label: tk.Label | None = None
        self._days_frame: tk.Frame | None = None
        self.build()

    # -- building -----------------------------------------------------------

    def build(self) -> None:
        for child in self.frame.winfo_children():
            child.destroy()
        self._vars.clear()
        self._chips = {}
        self._month_chips = {}
        self._summary_label = self._next_label = self._days_frame = None

        if self.rule is not None and self.rule.frequency == MONTHLY and self.rule.months:
            # Months are no longer something a rule is limited to — it
            # repeats every month — so any left over from before are dropped.
            self.rule.months = []
        self._summary()
        if self.rule is None:
            return
        self._every()
        if self.rule.frequency == WEEKLY:
            self._weekdays()
        elif self.rule.frequency == MONTHLY:
            # Pick the month to look at, then tick days in it.
            self._months()
            self._month_dates()
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

        modern.Toggle(
            row, self.rule is not None, self._toggle, bg=self.bg
        ).pack(side="left", padx=(6, 6))

        # Nothing to summarise while it's off — "Does not repeat" next to an
        # unticked "Repeat" checkbox only says the checkbox's own state back.
        if self.rule is not None:
            self._summary_label = theme.label(
                row,
                theme.ellipsize(describe(self.rule), SUMMARY_CHARS),
                bg=self.bg,
                fg=theme.TEXT,
                font=theme.FONT_SMALL,
            )
            self._summary_label.pack(side="left", fill="x", expand=True)

    def _every(self) -> None:
        row = theme.frame(self.frame, bg=self.bg)
        row.pack(fill="x", pady=(4, 0))
        theme.label(
            row, "Every", bg=self.bg, fg=theme.MUTED, font=theme.FONT_SMALL
        ).pack(side="left")

        field = modern.Field(
            row, bg=self.bg, font=theme.FONT_SMALL, radius=8, width=3, justify="center"
        )
        interval = field.entry
        interval.insert(0, str(max(1, self.rule.interval)))
        field.configure(width=interval.winfo_reqwidth() + 2 * field.PAD + 2)
        field.pack(side="left", padx=6)
        interval.bind("<FocusOut>", lambda _e: self._set_interval(interval.get()))
        interval.bind("<Return>", lambda _e: self._set_interval(interval.get()))

        current = next(k for k, v in FREQUENCY_LABELS.items() if v == self.rule.frequency)
        modern.Segmented(
            row, list(FREQUENCY_LABELS), current, self._set_frequency, bg=self.bg,
            font=theme.FONT_SMALL,
        ).pack(side="left")

        upcoming = next_after(self.rule, today_iso())
        self._next_label = theme.label(
            row,
            f"next {upcoming}" if upcoming else "never happens again",
            bg=self.bg,
            fg=theme.MUTED if upcoming else theme.DANGER,
            font=theme.FONT_TINY,
            anchor="e",
        )
        self._next_label.pack(side="right")

    def _weekdays(self) -> None:
        self._toggle_row(
            "On",
            [(WEEKDAY_NAMES[day], day) for day in range(7)],
            self._effective("weekdays"),
            "weekdays",
            columns=7,
        )

    def _month_dates(self) -> None:
        """The days grid for the month being looked at (see ``_months``)."""
        self._days_frame = theme.frame(self.frame, bg=self.bg)
        self._days_frame.pack(fill="x")
        self._fill_days()

    def _fill_days(self) -> None:
        """(Re)draws just the days grid, for the month in view. February
        offers 29 so a leap-year day can be picked; days a month does not
        have are simply skipped when it comes round."""
        frame = self._days_frame
        for child in frame.winfo_children():
            child.destroy()
        self._chips["month_days"] = {}
        month = self._viewed_month()
        length = calendar.monthrange(2024, month)[1]
        selected = self._effective("month_days")
        self._toggle_row(
            f"On these days of {MONTH_NAMES[month - 1]}",
            [(str(date), date) for date in FIRST_DATES],
            selected, "month_days", columns=7, parent=frame,
        )
        self._toggle_row(
            "",
            [(str(d), d) for d in range(29, length + 1)] + [("Last", LAST_DAY)],
            selected, "month_days", columns=7, parent=frame,
        )
        self._extra_hint = theme.label(
            frame, "", bg=self.bg, fg=theme.MUTED, font=theme.FONT_TINY
        )
        self._extra_hint.pack(fill="x")
        self._sync_hint()

    def _sync_hint(self) -> None:
        """Says so when days ticked for other months are not in this one's
        grid, and what repeating monthly means."""
        month = self._viewed_month()
        length = calendar.monthrange(2024, month)[1]
        hidden = sorted(d for d in self._effective("month_days") if d != LAST_DAY and d > length)
        text = "Repeats every month on the days ticked"
        if hidden:
            text += f" \u2014 also on the {', '.join(map(str, hidden))} of months that have them"
        if hasattr(self, "_extra_hint") and self._extra_hint.winfo_exists():
            self._extra_hint.config(text=theme.ellipsize(text, 90))

    def _viewed_month(self) -> int:
        if self._view_month is None:
            try:
                self._view_month = from_iso(self.rule.start or today_iso()).month
            except ValueError:
                self._view_month = from_iso(today_iso()).month
        return self._view_month

    def _months(self) -> None:
        """Which month's days to show: one at a time, and only a view — it
        does not limit the rule to that month."""
        theme.label(
            self.frame, "Month", bg=self.bg, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(fill="x", pady=(4, 1))
        grid = theme.frame(self.frame, bg=self.bg)
        grid.pack(fill="x")
        for index in range(6):
            grid.columnconfigure(index, weight=1, uniform="chip")
        viewed = self._viewed_month()
        for month in range(1, 13):
            chip = modern.ToggleChip(
                grid, MONTH_NAMES[month - 1], month == viewed,
                lambda m=month: self._view(m), bg=self.bg, font=theme.FONT_TINY, pady=2,
            )
            chip.grid(row=(month - 1) // 6, column=(month - 1) % 6, sticky="ew", padx=2, pady=2)
            self._month_chips[month] = chip

    def _view(self, month: int) -> None:
        """Looks at another month: moves the highlight and redraws only the
        days grid. Nothing is saved — it is not part of the rule."""
        if month == self._view_month:
            return
        self._view_month = month
        for m, chip in self._month_chips.items():
            if chip.winfo_exists():
                chip.set_on(m == month)
        if self._days_frame is not None and self._days_frame.winfo_exists():
            self._fill_days()

    def _toggle_row(
        self,
        caption: str,
        options: list[tuple[str, int]],
        selected: list[int],
        field: str,
        columns: int,
        hint: str = "",
        parent: tk.Misc | None = None,
    ) -> None:
        """A caption over a wrapped grid of on/off chips."""
        parent = parent or self.frame
        if caption:
            theme.label(
                parent, caption, bg=self.bg, fg=theme.MUTED, font=theme.FONT_TINY
            ).pack(fill="x", pady=(4, 1))

        grid = theme.frame(parent, bg=self.bg)
        grid.pack(fill="x")
        for index in range(columns):
            grid.columnconfigure(index, weight=1, uniform="chip")

        registry = self._chips.setdefault(field, {})
        for position, (label, value) in enumerate(options):
            on = value in selected
            chip = modern.ToggleChip(
                grid, label, on, lambda f=field, v=value: self._flip(f, v),
                bg=self.bg, font=theme.FONT_TINY, pady=2,
            )
            chip.grid(row=position // columns, column=position % columns, sticky="ew", padx=2, pady=2)
            registry[value] = chip

        if hint:
            theme.label(
                parent, hint, bg=self.bg, fg=theme.MUTED, font=theme.FONT_TINY
            ).pack(fill="x")

    def _duration_and_dates(self) -> None:
        row = theme.frame(self.frame, bg=self.bg)
        row.pack(fill="x", pady=(5, 0))

        theme.label(
            row, "Plan", bg=self.bg, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(side="left")
        theme.duration_picker(
            row, self.rule.minutes, self._set_minutes, width=6, font=theme.FONT_SMALL,
            step=self.step, open_upward=True,
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
        self._changed()  # a different set of controls: the editor redraws

    def _set_interval(self, text: str) -> None:
        try:
            value = max(1, int(text.strip()))
        except ValueError:
            value = 1
        if value != self.rule.interval:
            self.rule.interval = value
            self._changed(rebuild=False)

    def _set_minutes(self, minutes: int) -> None:
        if minutes and minutes != self.rule.minutes:
            self.rule.minutes = minutes
            self._changed(rebuild=False)

    def _set_date(self, field: str, value: str) -> None:
        if value == getattr(self.rule, field):
            return
        # An empty start would make the rule match nothing at all — the
        # calendar popup can still clear "until" to "forever", but "start"
        # always keeps a real date, the same floor the old typed field had.
        setattr(self.rule, field, value or (today_iso() if field == "start" else ""))
        self._changed(rebuild=False)

    def _effective(self, field: str) -> list[int]:
        """What the rule actually uses for `field`. A rule with none picked
        falls back to the start date's own weekday / day of the month (see
        `recurrence`), which is also what its summary says — so the chips
        must show that choice as selected, not look empty."""
        chosen = list(getattr(self.rule, field))
        if chosen:
            return chosen
        try:
            start = from_iso(self.rule.start or today_iso())
        except ValueError:
            return chosen
        if field == "weekdays":
            return [start.weekday()]
        if field == "month_days":
            return [start.day]
        return chosen

    def _flip(self, field: str, value: int) -> None:
        chosen = self._effective(field)
        chosen.remove(value) if value in chosen else chosen.append(value)
        setattr(self.rule, field, sorted(chosen))
        self._changed(rebuild=False)

    def _sync(self) -> None:
        """Brings what is on screen in line with the rule, in place: chip
        states, the summary line and the "next" date. No widget is made or
        destroyed, so nothing moves or flickers."""
        for field, chips in self._chips.items():
            selected = self._effective(field)
            for value, chip in chips.items():
                if chip.winfo_exists():
                    chip.set_on(value in selected)
        if self.rule is not None:
            if self._summary_label is not None and self._summary_label.winfo_exists():
                self._summary_label.config(
                    text=theme.ellipsize(describe(self.rule), SUMMARY_CHARS)
                )
            if self._next_label is not None and self._next_label.winfo_exists():
                upcoming = next_after(self.rule, today_iso())
                self._next_label.config(
                    text=f"next {upcoming}" if upcoming else "never happens again",
                    fg=theme.MUTED if upcoming else theme.DANGER,
                )
            if self.rule.frequency == MONTHLY:
                self._sync_hint()

    def _changed(self, rebuild: bool = True) -> None:
        def apply() -> None:
            self.on_change(self.rule)
            # Saving usually makes the view around us redraw, and that
            # replaces this editor wholesale. Only touch ourselves if we
            # outlived it.
            if not self.frame.winfo_exists():
                return
            if rebuild:
                self.build()
            else:
                self._sync()

        if rebuild and self.resize is not None:
            self.resize(apply)
        else:
            apply()
