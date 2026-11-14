"""The Month tab: a calendar grid, for a feel of the month at a glance — and,
one step out, the quarter and the year.

Month is always a full 7-day week per row regardless of ``show_weekend`` — a
calendar reads oddly with a weekend missing — and nothing models a monthly
target, so the summary is what was tracked and how much of it was billable
(utilization), not "vs target" the way Day and Week show it.

Quarter (a bar per week) and Year (a bar per month) trade the calendar for a
histogram of tracked hours with the utilization trend laid over it, and a
table of the same figures beneath; clicking a bar drills in (a week opens on
the Week tab, a month opens as the Month view).
"""

from __future__ import annotations

import datetime as dt
import tkinter as tk

from ..dates import (
    add_months,
    format_duration,
    from_iso,
    is_same_month,
    iso_week_number,
    month_label,
    month_weeks,
    short_date,
    start_of_month,
    start_of_week,
    to_iso,
    today,
)
from ..store import Store
from .navrow import NavRow
from . import charts, controls, icons, theme
from .hover import Hover
from .scroll import ScrollFrame

#: Floor so a 5- or 6-row month still reads as a grid, not a sliver, inside
#: the overlay's fixed-height window.
MIN_ROW_HEIGHT = 44

SCALES = ("Month", "Quarter", "Year")
#: Months one step of the prev/next chips moves, per scale.
STEP_MONTHS = {"Month": 1, "Quarter": 3, "Year": 12}


class MonthView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        #: The first of the month being looked at; the quarter and year
        #: views are the ones containing it.
        self.month_start = start_of_month(today())
        self.scale = "Month"
        self.frame = theme.frame(parent)

        #: ``{date: {task_id: minutes}}`` and ``{date: (total, billable)}``
        #: for the month on screen, read once per refresh.
        self._tasks: dict = {}
        self._totals: dict = {}
        self._hovers: list[Hover] = []
        self._day_clients: dict = {}
        self._client_colour: dict = {}

        self._build_header()
        self.grid = theme.frame(self.frame)
        self.share = theme.frame(self.frame)
        self.report = ScrollFrame(self.frame)
        self._bind_keys()

    def _bind_keys(self) -> None:
        """\u2190 / \u2192 step the period, Home returns to today's — only while
        this tab is the one showing and no text field has the keyboard."""
        top = self.frame.winfo_toplevel()

        def guarded(action):
            def handler(_event) -> None:
                try:
                    if not self.frame.winfo_ismapped():
                        return
                    if isinstance(top.focus_get(), (tk.Entry, tk.Text)):
                        return
                except (tk.TclError, KeyError):
                    return
                action()
            return handler

        top.bind("<Left>", guarded(self._previous), add="+")
        top.bind("<Right>", guarded(self._next), add="+")
        top.bind("<Home>", guarded(self._this_month), add="+")

    # -- chrome -------------------------------------------------------------

    def _build_header(self) -> None:
        nav = theme.frame(self.frame)
        nav.pack(fill="x", padx=8, pady=(8, 4))
        self.month_label = NavRow(
            nav, self._previous, self._this_month, self._next, text="",
        )
        self.month_label.pack(fill="x")

        summary = theme.frame(self.frame)
        summary.pack(fill="x", padx=8, pady=(0, 3))
        self.total_label = theme.label(summary, "", font=theme.FONT_SMALL)
        self.total_label.pack(side="right")
        self._gauge_holder = theme.frame(self.frame)
        self._gauge_holder.pack(fill="x", padx=8, pady=(0, 6))
        #: Month only: tracked against the hours the month calls for.
        self.pace = theme.frame(self.frame)

    def _update_title(self) -> None:
        first, last = self._range()
        self.month_label.set_text(self._title())
        self.month_label.set_highlight(first <= today() <= last)

    def _set_scale(self, scale: str) -> None:
        self.scale = scale
        self.refresh()

    def _previous(self) -> None:
        self.month_start = add_months(self.month_start, -STEP_MONTHS[self.scale])
        self.refresh()

    def _next(self) -> None:
        self.month_start = add_months(self.month_start, STEP_MONTHS[self.scale])
        self.refresh()

    def _this_month(self) -> None:
        self.month_start = start_of_month(today())
        self.refresh()

    # -- ranges ---------------------------------------------------------------

    def _quarter_start(self) -> str:
        month = from_iso(self.month_start).month
        return add_months(self.month_start, -((month - 1) % 3))

    def _range(self) -> tuple[str, str]:
        """First and last day (inclusive) of the period on screen."""
        if self.scale == "Quarter":
            first = self._quarter_start()
            span = 3
        elif self.scale == "Year":
            first = from_iso(self.month_start).replace(month=1, day=1).isoformat()
            span = 12
        else:
            first, span = self.month_start, 1
        last = to_iso(from_iso(add_months(first, span)) - dt.timedelta(days=1))
        return first, last

    def _title(self) -> str:
        if self.scale == "Quarter":
            first = from_iso(self._quarter_start())
            end = from_iso(add_months(self._quarter_start(), 2))
            return (
                f"Q{(first.month - 1) // 3 + 1} {first.year} \u00b7 "
                f"{first.strftime('%b')} \u2013 {end.strftime('%b')}"
            )
        if self.scale == "Year":
            return str(from_iso(self.month_start).year)
        return month_label(self.month_start)

    def _buckets(self, by_day: dict) -> list[charts.Bucket]:
        """The bars of the quarter (a week each) or the year (a month each)."""
        first, last = self._range()
        now = today()
        if self.scale == "Quarter":
            monday0 = from_iso(start_of_week(first))
            count = (from_iso(last) - monday0).days // 7 + 1
            buckets = []
            for index in range(count):
                monday = monday0 + dt.timedelta(days=7 * index)
                start = max(monday, from_iso(first))
                end = min(monday + dt.timedelta(days=6), from_iso(last))
                buckets.append(charts.Bucket(
                    label=f"W{iso_week_number(to_iso(monday))}", total=0, billable=0,
                    key=to_iso(monday), current=start <= from_iso(now) <= end,
                ))
            lookup = lambda date: (from_iso(date) - monday0).days // 7  # noqa: E731
        else:
            year = from_iso(first).year
            buckets = [
                charts.Bucket(
                    label=dt.date(year, month, 1).strftime("%b"), total=0, billable=0,
                    key=dt.date(year, month, 1).isoformat(),
                    current=from_iso(now).year == year and from_iso(now).month == month,
                )
                for month in range(1, 13)
            ]
            lookup = lambda date: from_iso(date).month - 1  # noqa: E731
        for date, (total, billable) in by_day.items():
            bucket = buckets[lookup(date)]
            bucket.total += total
            bucket.billable += billable
        return buckets

    # -- rendering ------------------------------------------------------------

    def _fmt(self, minutes: float) -> str:
        """An hour figure the way ``Settings.hour_format`` (Preferences,
        under Theme) says to show it — see ``dates.format_duration``."""
        return format_duration(minutes, self.store.db.settings.hour_format == "decimal")

    def _summary(self, total: int, billable: int) -> None:
        """The headline: tracked, billable, and utilization — billable as a
        share of everything tracked — rolled up over whatever is on screen."""
        self.total_label.config(
            text=f"{self._fmt(total)} tracked \u00b7 {self._fmt(billable)} billable "
            f"\u00b7 {charts.percent(billable, total)} utilization"
        )
        for child in self._gauge_holder.winfo_children():
            child.destroy()
        charts.gauge(self._gauge_holder, billable / total if total else None).pack(fill="x")

    def refresh(self) -> None:
        self._update_title()
        # Set from the Utilization tab's flyout (``Overlay.open_utilization``).
        pending = getattr(self.overlay, "_utilization_scale", None)
        if pending in SCALES:
            self.scale = pending
        self.overlay._utilization_scale = None
        self._update_title()
        for part in (self.pace, self.grid, self.share, self.report):
            part.pack_forget()
        self._hovers.clear()
        if self.scale == "Month":
            self._refresh_month()
        else:
            self._refresh_report()

    # -- month: calendar, pace, weekly totals, customers -----------------------

    def _working_days(self) -> tuple[int, int]:
        """Mon-Fri days in the month, and how many of them are up to today."""
        first = from_iso(self.month_start)
        last = from_iso(add_months(self.month_start, 1)) - dt.timedelta(days=1)
        now = from_iso(today())
        full = so_far = 0
        day = first
        while day <= last:
            if day.weekday() < 5:
                full += 1
                if day <= now:
                    so_far += 1
            day += dt.timedelta(days=1)
        return full, so_far

    def _refresh_month(self) -> None:
        weeks = month_weeks(self.month_start)
        grid_first, grid_last = weeks[0][0], weeks[-1][-1]
        self._totals = self.store.totals_by_day(grid_first, grid_last)
        self._tasks = self.store.minutes_by_task(grid_first, grid_last)
        in_month = [d for week in weeks for d in week if is_same_month(d, self.month_start)]
        total = sum(self._totals.get(d, (0, 0))[0] for d in in_month)
        billable = sum(self._totals.get(d, (0, 0))[1] for d in in_month)
        self._summary(total, billable)
        self._pace(total)

        self.pace.pack(fill="x", padx=8, pady=(0, 6))
        self.grid.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.share.pack(fill="x", padx=8, pady=(0, 8))

        for child in self.grid.winfo_children():
            child.destroy()

        for column, date in enumerate(weeks[0]):
            theme.label(
                self.grid, from_iso(date).strftime("%a"), fg=theme.MUTED,
                font=theme.FONT_TINY, anchor="center",
            ).grid(row=0, column=column, sticky="ew", pady=(0, 3))
        theme.label(
            self.grid, "Week", fg=theme.MUTED, font=theme.FONT_TINY, anchor="center",
        ).grid(row=0, column=7, sticky="ew", pady=(0, 3))

        # Who each day went to — the cells list it — coloured by the same
        # month-wide ranking the customer bar below uses, so a colour means
        # the same customer in both places.
        per_client: dict[str, int] = {}
        self._day_clients = {}
        for date in in_month:
            by_client: dict[str, int] = {}
            for task_id, minutes in self._tasks.get(date, {}).items():
                name = self._client_name(task_id)
                by_client[name] = by_client.get(name, 0) + minutes
                per_client[name] = per_client.get(name, 0) + minutes
            self._day_clients[date] = sorted(by_client.items(), key=lambda kv: -kv[1])
        ranked = sorted(per_client.items(), key=lambda kv: -kv[1])
        self._client_colour = {
            name: charts.SHARE_COLOURS[i] if i < len(charts.SHARE_COLOURS) else theme.IDLE
            for i, (name, _m) in enumerate(ranked)
        }

        for row, week in enumerate(weeks, start=1):
            for column, date in enumerate(week):
                self._day_cell(row, column, date)
            self._week_cell(row, week)

        for column in range(7):
            self.grid.columnconfigure(column, weight=1, uniform="day")
        self.grid.columnconfigure(7, weight=0, minsize=66)
        for row in range(1, len(weeks) + 1):
            self.grid.rowconfigure(row, weight=1, minsize=MIN_ROW_HEIGHT)

        self._customers(in_month)

    def _pace(self, total: int) -> None:
        """Tracked against the working days' worth of the daily target."""
        for child in self.pace.winfo_children():
            child.destroy()
        target = self.store.db.settings.daily_target_minutes
        days, so_far_days = self._working_days()
        expected_total = days * target
        expected_so_far = so_far_days * target
        diff = total - expected_so_far
        if expected_so_far == 0:
            verdict = "month not started"
        elif abs(diff) < max(15, target // 30):
            verdict = "on pace"
        else:
            verdict = f"{self._fmt(abs(diff))} {'ahead' if diff > 0 else 'behind'}"
        theme.label(
            self.pace,
            f"Pace \u00b7 {self._fmt(total)} of {self._fmt(expected_so_far)} expected by now "
            f"\u00b7 {verdict} \u00b7 month goal {self._fmt(expected_total)}",
            fg=theme.MUTED, font=theme.FONT_TINY,
        ).pack(anchor="w", pady=(0, 2))
        charts.pace_bar(self.pace, total, expected_so_far, expected_total).pack(fill="x")

    def _client_name(self, task_id: str) -> str:
        task = self.store.db.task(task_id)
        context = self.store.context(task) if task else None
        return context.client.name if context else "Unknown client"

    def _task_name(self, task_id: str) -> str:
        task = self.store.db.task(task_id)
        context = self.store.context(task) if task else None
        if not context:
            return "Unknown task"
        return f"{context.client.name} \u00b7 {context.display_title}"

    def _customers(self, dates: list[str]) -> None:
        """Where the month's hours went: a stacked bar and a key beside it,
        biggest customer first, the tail folded into "Other"."""
        for child in self.share.winfo_children():
            child.destroy()
        per_client: dict[str, int] = {}
        for date in dates:
            for task_id, minutes in self._tasks.get(date, {}).items():
                name = self._client_name(task_id)
                per_client[name] = per_client.get(name, 0) + minutes
        if not per_client:
            return
        ranked = sorted(per_client.items(), key=lambda kv: -kv[1])
        shown = ranked[: len(charts.SHARE_COLOURS)]
        rest = sum(m for _n, m in ranked[len(shown):])
        parts = [(charts.SHARE_COLOURS[i], m) for i, (_n, m) in enumerate(shown)]
        names = [(charts.SHARE_COLOURS[i], n, m) for i, (n, m) in enumerate(shown)]
        if rest:
            parts.append((theme.IDLE, rest))
            names.append((theme.IDLE, "Other", rest))
        charts.share_bar(self.share, parts).pack(fill="x", pady=(0, 3))
        key = theme.frame(self.share)
        key.pack(fill="x")
        grand = sum(per_client.values())
        # The key wraps: a handful of customers don't fit one 560px row.
        row = None
        used = 0
        for colour, name, minutes in names:
            text = f"{theme.ellipsize(name, 18)} {self._fmt(minutes)} ({round(100 * minutes / grand)}%)"
            width = 22 + 6 * len(text)
            if row is None or used + width > 520:
                row = theme.frame(key)
                row.pack(fill="x", anchor="w")
                used = 0
            charts.swatch(row, colour).pack(side="left", padx=(0, 3))
            theme.label(row, text, fg=theme.MUTED, font=theme.FONT_TINY).pack(
                side="left", padx=(0, 10)
            )
            used += width

    def _week_cell(self, row: int, week: list[str]) -> None:
        """The week's in-month total and utilization; click opens the week."""
        dates = [d for d in week if is_same_month(d, self.month_start)]
        total = sum(self._totals.get(d, (0, 0))[0] for d in dates)
        billable = sum(self._totals.get(d, (0, 0))[1] for d in dates)
        bg = theme.header_fill()
        cell = theme.frame(self.grid, bg=bg)
        cell.grid(row=row, column=7, sticky="nsew", padx=(5, 1), pady=1)
        number = theme.label(
            cell, f"W{iso_week_number(week[0])}", bg=bg, fg=theme.MUTED, font=theme.FONT_TINY,
        )
        number.pack(anchor="w", padx=4, pady=(3, 0))
        widgets = [cell, number]
        if total:
            hours = theme.label(cell, self._fmt(total), bg=bg, fg=theme.TEXT, font=theme.FONT_SMALL)
            hours.pack(anchor="w", padx=4)
            share = theme.label(
                cell, f"{charts.percent(billable, total)} billable", bg=bg, fg=theme.BILLABLE,
                font=theme.FONT_TINY,
            )
            share.pack(anchor="w", padx=4, pady=(0, 3))
            widgets += [hours, share]
        monday = start_of_week(week[0])

        def open_week(_e=None, key=monday) -> None:
            self.overlay.show_tab("Week")
            view = self.overlay._views["Week"]
            view.week_start = key
            view.refresh()

        def tip() -> str:
            if not total:
                return f"Week {iso_week_number(week[0])}\nNothing tracked"
            return (
                f"Week {iso_week_number(week[0])} (this month's days)\n"
                f"{self._fmt(total)} tracked \u00b7 {self._fmt(billable)} billable\n"
                f"{charts.percent(billable, total)} utilization \u00b7 click to open"
            )

        for widget in widgets:
            widget.bind("<Button-1>", open_week)
            widget.config(cursor="hand2")
        hover = Hover(tip)
        hover.attach(*widgets)
        self._hovers.append(hover)

    def _refresh_report(self) -> None:
        self.report.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        first, last = self._range()
        by_day = self.store.totals_by_day(first, last)
        buckets = self._buckets(by_day)
        total = sum(b.total for b in buckets)
        billable = sum(b.billable for b in buckets)
        self._summary(total, billable)

        self.report.clear()
        body = self.report.body
        charts.legend(body).pack(anchor="w", pady=(0, 4))
        charts.Histogram(body, buckets, self._drill).pack(fill="x")
        theme.label(
            body,
            "Click a week to open it" if self.scale == "Quarter" else "Click a month to open it",
            fg=theme.MUTED, font=theme.FONT_TINY,
        ).pack(anchor="w", pady=(2, 8))
        self._table(body, buckets, total, billable)

    def _table(self, body: tk.Misc, buckets: list, total: int, billable: int) -> None:
        """The figures behind the bars: tracked, billable and a utilization
        meter per week/month, banded like the Week tab's grid."""
        grid = theme.frame(body)
        grid.pack(fill="x")
        head = theme.header_fill()
        titles = ("Week" if self.scale == "Quarter" else "Month", "Tracked", "Billable", "Utilization")
        for column, text in enumerate(titles):
            theme.label(
                grid, text, bg=head, fg=theme.MUTED, font=theme.FONT_TINY_BOLD,
                anchor="w" if column in (0, 3) else "e", padx=6,
            ).grid(row=0, column=column, sticky="nsew")

        stripe_grey = theme.blend(theme.BG, theme.BG_RAISED, 0.5)
        for row, bucket in enumerate(buckets, start=1):
            fill = theme.BG_RAISED if row % 2 else stripe_grey
            name = bucket.label
            if self.scale == "Quarter":
                name = f"{bucket.label} \u00b7 {short_date(bucket.key)}"
            else:
                name = from_iso(bucket.key).strftime("%B")
            cells = (
                (name, theme.ACCENT if bucket.current else theme.TEXT, "w"),
                (self._fmt(bucket.total) if bucket.total else "\u2013", theme.TEXT, "e"),
                (self._fmt(bucket.billable) if bucket.billable else "\u2013", theme.BILLABLE, "e"),
            )
            for column, (text, colour, anchor) in enumerate(cells):
                label = theme.label(
                    grid, text, bg=fill, fg=colour if text != "\u2013" else theme.MUTED,
                    font=theme.FONT_SMALL, anchor=anchor, padx=6,
                )
                label.grid(row=row, column=column, sticky="nsew", pady=1)
                label.bind("<Button-1>", lambda _e, k=bucket.key: self._drill(k))
                label.config(cursor="hand2")
            charts.meter_cell(grid, bucket.utilization, fill).grid(
                row=row, column=3, sticky="nsew", pady=1, padx=(6, 0)
            )

        foot = theme.blend(theme.PLANNED_BG, theme.BILLABLE_BG, 0.5)
        row = len(buckets) + 1
        for column, (text, anchor) in enumerate((
            ("Total", "w"), (self._fmt(total), "e"), (self._fmt(billable), "e"),
        )):
            theme.label(
                grid, text, bg=foot, font=theme.FONT_BOLD, anchor=anchor, padx=6,
            ).grid(row=row, column=column, sticky="nsew", pady=(6, 0))
        charts.meter_cell(grid, billable / total if total else None, foot).grid(
            row=row, column=3, sticky="nsew", pady=(6, 0), padx=(6, 0)
        )
        for column in (0, 1, 2):
            grid.columnconfigure(column, weight=1 if column == 0 else 0, minsize=70)
        grid.columnconfigure(3, weight=0, minsize=130)

    def _drill(self, key: str) -> None:
        """A bar or row was clicked: a week opens on the Week tab, a month
        opens as the Month view."""
        if self.scale == "Quarter":
            self.overlay.show_tab("Week")
            view = self.overlay._views["Week"]
            view.week_start = key
            view.refresh()
            return
        self.month_start = key
        self.scale = "Month"
        self.refresh()

    def _day_tip(self, date: str) -> str:
        total, billable = self._totals.get(date, (0, 0))
        head = f"{from_iso(date).strftime('%a')} {short_date(date)}"
        if not total:
            return f"{head}\nNothing tracked"
        target = self.store.db.settings.daily_target_minutes
        lines = [
            head,
            f"{self._fmt(total)} tracked (target {self._fmt(target)}) \u00b7 "
            f"{self._fmt(billable)} billable \u00b7 {charts.percent(billable, total)}",
        ]
        tasks = sorted(self._tasks.get(date, {}).items(), key=lambda kv: -kv[1])
        for task_id, minutes in tasks[:5]:
            lines.append(f"  {theme.ellipsize(self._task_name(task_id), 44)}   {self._fmt(minutes)}")
        if len(tasks) > 5:
            lines.append(f"  + {len(tasks) - 5} more")
        return "\n".join(lines)

    def _day_cell(self, row: int, column: int, date: str) -> None:
        """One day with a progress bar of its own along the bottom edge: it
        fills left to right in proportion to the hours logged against the
        daily target (0% empty, 100% full), with the day number, the hours
        and a billable dot above it."""
        in_month = is_same_month(date, self.month_start)
        is_today = date == today()
        total, billable = self._totals.get(date, (0, 0))
        target = max(1, self.store.db.settings.daily_target_minutes)
        fraction = min(total / target, 1.0) if in_month else 0.0
        bg = theme.BG_RAISED if in_month else theme.BG
        # Blue (the actual-progress colour) until the day reaches its
        # target, green from then on.
        blue = self.store.db.settings.progress_bar_color or theme.ACCENT
        fill = theme.BILLABLE if total >= target else blue
        hours = lambda m: f"{round(m / 60, 2):g}"  # noqa: E731

        cell = tk.Canvas(
            self.grid, bg=bg, bd=0, highlightthickness=2 if is_today else 1,
            highlightbackground=theme.ACCENT if is_today else theme.LINE,
            cursor="hand2",
        )
        cell.grid(row=row, column=column, sticky="nsew", padx=1, pady=1)

        def draw(_event=None) -> None:
            width, height = cell.winfo_width(), cell.winfo_height()
            if width < 4 or height < 4:
                return
            cell.delete("all")
            # The bar is a short strip along the bottom edge (10% of the
            # cell) on a white track, filling left to right.
            strip = max(4, round(height * 0.10))
            if in_month:
                cell.create_rectangle(
                    0, height - strip, width, height, fill="#ffffff", outline="",
                )
            if fraction > 0:
                cell.create_rectangle(
                    0, height - strip, width * fraction, height, fill=fill, outline="",
                )
            cell.create_text(
                5, 4, anchor="nw", text=str(from_iso(date).day),
                fill=theme.TEXT if in_month else theme.IDLE,
                font=theme.FONT_BOLD if is_today else theme.FONT_SMALL,
            )
            # Who the day went to: one line per customer, biggest first — a
            # coloured square (the customer bar's own colour), the name and
            # the hours — as many as the cell has room for, then "+N more".
            clients = self._day_clients.get(date, []) if in_month else []
            if clients:
                top = 19
                room = max(0, (height - strip - 2 - top) // 12)
                if room <= 1:
                    # Only one line fits: the day's biggest customer, with a
                    # "+N" on it when there were others.
                    shown = clients[:1]
                    extra = f" +{len(clients) - 1}" if len(clients) > 1 else ""
                else:
                    shown = clients if len(clients) <= room else clients[: room - 1]
                    extra = ""
                chars = max(3, (width - 24) // 6)
                y = top
                for name, minutes in shown:
                    cell.create_rectangle(
                        5, y + 3, 10, y + 8, fill=self._client_colour.get(name, theme.IDLE),
                        outline="",
                    )
                    hrs = f" {hours(minutes)}"
                    tail = hrs + extra
                    cell.create_text(
                        13, y, anchor="nw", fill=theme.MUTED, font=theme.FONT_TINY,
                        text=theme.ellipsize(name, max(2, chars - len(tail))) + tail,
                    )
                    y += 12
                if len(shown) < len(clients) and room > 1:
                    cell.create_text(
                        13, y, anchor="nw", fill=theme.IDLE, font=theme.FONT_TINY,
                        text=f"+{len(clients) - len(shown)} more",
                    )
            if total:
                # "x/x h": logged over target, on a white patch so it reads
                # over both the track and the fill.
                label = cell.create_text(
                    width / 2, 0, anchor="center", text=f"{hours(total)}/{hours(target)}h",
                    fill="#1f2937", font=theme.FONT_TINY,
                )
                x0, y0, x1, y1 = cell.bbox(label)
                shift = (height - strip / 2) - (y0 + y1) / 2
                shift = min(shift, height - 1 - y1)
                cell.move(label, 0, shift)
                x0, y0, x1, y1 = cell.bbox(label)
                patch = cell.create_rectangle(
                    max(0, x0 - 3), y0 - 1, min(width, x1 + 3), min(height, y1 + 1),
                    fill="#ffffff", outline="",
                )
                cell.tag_lower(patch, label)
                cell.create_oval(
                    width - 15, 5, width - 6, 14, outline="",
                    fill=theme.BILLABLE if billable * 2 >= total else theme.IDLE,
                )

        cell.bind("<Configure>", draw)
        cell.bind("<Button-1>", lambda _e, d=date: self._jump(d))
        if in_month:
            hover = Hover(lambda d=date: self._day_tip(d))
            hover.attach(cell)
            self._hovers.append(hover)

    def _jump(self, date: str) -> None:
        """Clicking a day jumps into it on the Day tab — a calendar is for
        finding a day, not for working inside one cell of it."""
        self.overlay.show_tab("Today")
        view = self.overlay._views["Today"]
        view.date = date
        view.refresh()
