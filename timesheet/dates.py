"""Date and duration helpers.

Dates are passed around as ``YYYY-MM-DD`` strings so they drop straight into
JSON. Everything here is local time; nothing in this app cares about UTC.
"""

from __future__ import annotations

import datetime as dt
import re

ISODate = str


def to_iso(value: dt.date) -> ISODate:
    return value.isoformat()


def from_iso(value: ISODate) -> dt.date:
    return dt.date.fromisoformat(value)


def today() -> ISODate:
    return dt.date.today().isoformat()


def add_days(value: ISODate, days: int) -> ISODate:
    return (from_iso(value) + dt.timedelta(days=days)).isoformat()


def start_of_week(value: ISODate) -> ISODate:
    """Monday of the week containing ``value``."""
    date = from_iso(value)
    return (date - dt.timedelta(days=date.weekday())).isoformat()


def week_dates(week_start: ISODate, include_weekend: bool) -> list[ISODate]:
    return [add_days(week_start, i) for i in range(7 if include_weekend else 5)]


def start_of_month(value: ISODate) -> ISODate:
    return from_iso(value).replace(day=1).isoformat()


def add_months(value: ISODate, months: int) -> ISODate:
    """``value``'s month shifted by ``months``, landing on the 1st — the
    Month tab only ever navigates whole months, never a specific day."""
    date = from_iso(value)
    index = date.month - 1 + months
    year = date.year + index // 12
    month = index % 12 + 1
    return date.replace(year=year, month=month, day=1).isoformat()


def month_label(value: ISODate) -> str:
    """``October 2026``."""
    return from_iso(value).strftime("%B %Y")


def is_same_month(value: ISODate, month_start: ISODate) -> bool:
    a, b = from_iso(value), from_iso(month_start)
    return a.year == b.year and a.month == b.month


def month_weeks(value: ISODate) -> list[list[ISODate]]:
    """Full Monday-start weeks covering the month containing ``value``.

    Always seven days wide regardless of ``show_weekend`` — the Month tab is
    a calendar grid, not a work-week one — with the spillover from the month
    before and after filled in so every row is a complete week.
    """
    first = from_iso(start_of_month(value))
    grid_start = first - dt.timedelta(days=first.weekday())
    last_of_month = from_iso(add_months(value, 1)) - dt.timedelta(days=1)
    grid_end = last_of_month + dt.timedelta(days=6 - last_of_month.weekday())

    weeks = []
    cursor = grid_start
    while cursor <= grid_end:
        weeks.append([to_iso(cursor + dt.timedelta(days=i)) for i in range(7)])
        cursor += dt.timedelta(days=7)
    return weeks


def iso_week_number(value: ISODate) -> int:
    return from_iso(value).isocalendar().week


def week_label(week_start: ISODate) -> str:
    """``2026-W43`` — the filename stem used for weekly exports."""
    cal = from_iso(week_start).isocalendar()
    return f"{cal.year}-W{cal.week:02d}"


def day_name(value: ISODate, long: bool = False) -> str:
    return from_iso(value).strftime("%A" if long else "%a")


#: How dates are written on screen (``Settings.date_format``): key ->
#: (full form, short form without the year). Set once by the store whenever
#: the setting loads or changes, so every label formats the same way.
DATE_FORMATS = {
    "iso": ("%Y-%m-%d", "%m-%d"),
    "dmy": ("%d/%m/%Y", "%d/%m"),
    "mdy": ("%m/%d/%Y", "%m/%d"),
    "text": ("%d %b %Y", "%d %b"),
}
DEFAULT_DATE_FORMAT = "iso"
_date_format = DEFAULT_DATE_FORMAT


def set_date_format(key: str) -> None:
    global _date_format
    _date_format = key if key in DATE_FORMATS else DEFAULT_DATE_FORMAT


def format_date(value: ISODate) -> str:
    """``2026-11-13`` / ``13/11/2026`` / ``11/13/2026`` / ``13 Nov 2026``."""
    return from_iso(value).strftime(DATE_FORMATS[_date_format][0])


def pretty_date(value: ISODate) -> str:
    """The weekday and the full date, in the chosen format."""
    return f"{day_name(value, long=True)} {format_date(value)}"


def short_date(value: ISODate) -> str:
    """The date without its year: ``11-13`` / ``13/11`` / ``11/13`` / ``13 Nov``."""
    return from_iso(value).strftime(DATE_FORMATS[_date_format][1])


def format_minutes(minutes: float) -> str:
    """``7h 30m``, or ``45m`` when under an hour."""
    sign = "-" if minutes < 0 else ""
    total = abs(round(minutes))
    hours, mins = divmod(total, 60)
    if hours == 0:
        return f"{sign}{mins}m"
    if mins == 0:
        return f"{sign}{hours}h"
    return f"{sign}{hours}h {mins:02d}m"


def to_decimal_hours(minutes: float) -> float:
    """Hours as most timesheet systems want them, e.g. 2.25."""
    return round(minutes / 60, 2)


def format_decimal_hours(minutes: float, places: int = 1) -> str:
    """Fixed-decimal hours — ``"1.5"``, ``"0.3"``, ``"2.0"`` at the default
    one place — for the few spots tight enough that even
    ``format_duration``'s own "7h 30m" or a two-decimal ``to_decimal_hours``
    costs more width than they can spare: the collapsed badge's own timer,
    and the Stop preview above the timer bar's clock. Deliberately not
    tied to ``Settings.hour_format`` the way ``format_duration`` is — a
    glance at a badge the size of a desktop icon calls for the same
    compact shape either way, not a choice between two.

    ``places`` goes past that default of one for a timer that's actually
    ticking (see ``Overlay._tick``/``_refresh_badge_timer``): one decimal
    place only moves roughly every six minutes, which over the short
    stretch anyone's actually watching it reads as frozen even though it's
    being recomputed every second same as the clock right beside it.
    """
    return f"{minutes / 60:.{places}f}"


def format_duration(minutes: float, decimal: bool = False) -> str:
    """An hour figure the way the app is set to show it (``Settings.hour_format``,
    Preferences \u2192 under Theme): ``format_minutes`` (``7h 30m``) normally, or
    plain decimal hours (``7.5``) when ``decimal`` is set.

    Only for what the app displays live — what gets exported or copied out
    (see ``bookings.py``, ``timesheet/ui/export.py``) always stays
    ``to_decimal_hours`` regardless of this, since that figure has to read
    the same way no matter how the user likes their own screen formatted.
    """
    if decimal:
        return f"{to_decimal_hours(minutes):g}"
    return format_minutes(minutes)


def format_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    mins, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


_HOURS_AND_MINUTES = re.compile(r"^(\d+)\s*h(?:ours?)?\s*(\d+)?\s*m?(?:ins?|inutes?)?$")
_MINUTES_ONLY = re.compile(r"^(\d+)\s*m(?:ins?|inutes?)?$")
_CLOCK = re.compile(r"^(\d*):(\d{1,2})$")
_DECIMAL = re.compile(r"^(\d+(?:\.\d+)?)$")


def parse_duration(text: str) -> int | None:
    """Accepts ``1.5``, ``1h30``, ``1h 30m``, ``90m``, ``:45`` and ``1:30``."""
    value = text.strip().lower()
    if not value:
        return None

    match = _HOURS_AND_MINUTES.match(value)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2) or 0)

    match = _MINUTES_ONLY.match(value)
    if match:
        return int(match.group(1))

    match = _CLOCK.match(value)
    if match:
        return int(match.group(1) or 0) * 60 + int(match.group(2))

    match = _DECIMAL.match(value)
    if match:
        return round(float(match.group(1)) * 60)

    return None


def round_minutes(minutes: float, increment: int) -> int:
    if not increment:
        return round(minutes)
    return round(minutes / increment) * increment
