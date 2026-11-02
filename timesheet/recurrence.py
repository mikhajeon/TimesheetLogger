"""When a repeating task is due — the Outlook-style rules, without the dialog.

A rule is matched against a date rather than expanded into stored plans. That
way editing one updates every future day at once, past weeks are left exactly
as they were booked, and the data file doesn't fill with generated rows.

What a rule produces is an intention. Typing a different figure over one of
its days keeps that figure, and the override is the only thing written down;
see ``Store.set_plan``.
"""

from __future__ import annotations

import calendar
import datetime as dt

from .dates import ISODate, add_days, from_iso, to_iso

DAILY = "daily"
WEEKLY = "weekly"
MONTHLY = "monthly"
FREQUENCIES = (DAILY, WEEKLY, MONTHLY)

#: Stands in for "whatever the last day of this month turns out to be", so a
#: month-end task doesn't skip February.
LAST_DAY = -1

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def occurs_on(rule, date: ISODate) -> bool:
    """Whether ``rule`` falls on ``date``."""
    if rule is None or not rule.start:
        return False
    day, start = from_iso(date), from_iso(rule.start)
    if day < start or (rule.until and day > from_iso(rule.until)):
        return False

    interval = max(1, rule.interval)
    if rule.frequency == DAILY:
        return (day - start).days % interval == 0
    if rule.frequency == WEEKLY:
        return _weekly(rule, day, start, interval)
    if rule.frequency == MONTHLY:
        return _monthly(rule, day, start, interval)
    return False


def _weekly(rule, day: dt.date, start: dt.date, interval: int) -> bool:
    # No day ticked means the one the rule started on, so a new rule is
    # already valid before anything has been chosen.
    if day.weekday() not in (rule.weekdays or [start.weekday()]):
        return False
    # Counted in whole weeks between the Mondays, so "every 2 weeks on Mon and
    # Thu" keeps both days in the same week rather than alternating them.
    weeks = (_monday(day) - _monday(start)).days // 7
    return weeks % interval == 0


def _monthly(rule, day: dt.date, start: dt.date, interval: int) -> bool:
    if rule.months and day.month not in rule.months:
        return False
    if ((day.year - start.year) * 12 + day.month - start.month) % interval:
        return False

    wanted = rule.month_days or [start.day]
    if day.day in wanted:
        return True
    return LAST_DAY in wanted and day.day == last_day_of(day.year, day.month)


def _monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def last_day_of(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def dates_between(rule, start: ISODate, end: ISODate) -> list[ISODate]:
    """Every date in ``[start, end]`` the rule falls on, earliest first."""
    found, date = [], start
    while date <= end:
        if occurs_on(rule, date):
            found.append(date)
        date = add_days(date, 1)
    return found


def next_after(rule, date: ISODate, horizon_days: int = 400) -> ISODate | None:
    """The first occurrence strictly after ``date``, or None within the horizon.

    Bounded so an unsatisfiable rule — the 31st of February only — can't spin.
    """
    for _ in range(horizon_days):
        date = add_days(date, 1)
        if occurs_on(rule, date):
            return date
    return None


def describe(rule) -> str:
    """The rule in words, for the line shown above the editor."""
    if rule is None:
        return "Does not repeat"
    every = max(1, rule.interval)

    if rule.frequency == DAILY:
        return "Every day" if every == 1 else f"Every {every} days"

    if rule.frequency == WEEKLY:
        days = sorted(rule.weekdays) if rule.weekdays else [from_iso(rule.start).weekday()]
        named = ", ".join(WEEKDAY_NAMES[d] for d in days)
        if days == [0, 1, 2, 3, 4] and every == 1:
            return "Every weekday"
        lead = "Every week" if every == 1 else f"Every {every} weeks"
        return f"{lead} on {named}"

    if rule.frequency == MONTHLY:
        when = _ordinals(rule.month_days or [from_iso(rule.start).day])
        lead = "Every month" if every == 1 else f"Every {every} months"
        if rule.months:
            months = ", ".join(MONTH_NAMES[m - 1] for m in sorted(rule.months))
            # "Every month ... in Jan, Jul" would contradict itself.
            lead = "The" if every == 1 else f"Every {every} months, the"
            return f"{lead} {when} of {months}"
        return f"{lead} on the {when}"

    return "Does not repeat"


def _ordinals(days: list[int]) -> str:
    # LAST_DAY is negative, so sorting on the raw value would read "last day,
    # 1st and 15th" when it belongs at the end of the list.
    order = sorted(days, key=lambda d: 32 if d == LAST_DAY else d)
    names = ["last day" if d == LAST_DAY else _ordinal(d) for d in order]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _ordinal(day: int) -> str:
    # 11th, 12th and 13th break the pattern the other teens follow.
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def today_iso() -> ISODate:
    return to_iso(dt.date.today())
