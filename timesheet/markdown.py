"""Builds the Markdown file saved to ``exports/`` as a record of the period.

The file is organised the way a timesheet note is actually written: a table of
bookable lines first, then the narrative grouped per K-code. Day-to-day keying
in happens off the Export tab's table rather than this.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .bookings import Line
from .bookings import collect as bookings_for
from .bookings import planned_on
from .dates import (
    ISODate,
    format_minutes,
    pretty_date,
    round_minutes,
    short_date,
    to_decimal_hours,
)
from .models import Database


@dataclass
class _Booking:
    """One bookable line: a K-code, on a day, at one billable status."""

    date: ISODate
    client_name: str
    is_internal: bool
    project_name: str
    k_code: str
    billable: bool
    exact_minutes: int = 0
    rounded_minutes: int = 0
    items: list[Line] = field(default_factory=list)


def _collect(db: Database, dates: list[ISODate]) -> list[_Booking]:
    """Gathers the per-task lines into the K-code lines the export is written in.

    Rounding happens on the booking rather than on its tasks, so the figures
    here add up to what gets keyed in even when the parts wouldn't.
    """
    bookings: dict[tuple, _Booking] = {}

    for line in bookings_for(db, dates):
        key = (line.project_id, line.billable, line.date)
        booking = bookings.get(key)
        if booking is None:
            booking = _Booking(
                date=line.date,
                client_name=line.client_name,
                is_internal=line.is_internal,
                project_name=line.project_name,
                k_code=line.k_code,
                billable=line.billable,
            )
            bookings[key] = booking

        booking.exact_minutes += line.minutes
        booking.items.append(line)

    # Always exact — the export never rounds what's keyed in, only the Day
    # tab's duration pickers (``Settings.entry_increment_minutes``) offer a
    # step other than one minute. ``rounded_minutes`` is kept as the field
    # name used below rather than renamed, but it's exact minutes now.
    for booking in bookings.values():
        booking.rounded_minutes = round_minutes(booking.exact_minutes, 0)

    # A booking with nothing on it is dropped rather than shown as 0h.
    return sorted(
        (b for b in bookings.values() if b.rounded_minutes > 0),
        key=lambda b: (b.date, b.is_internal, b.client_name, b.k_code),
    )


def build_markdown(db: Database, dates: list[ISODate], title: str) -> str:
    bookings = _collect(db, dates)

    total_rounded = sum(b.rounded_minutes for b in bookings)
    billable_rounded = sum(b.rounded_minutes for b in bookings if b.billable)

    out: list[str] = [f"# {title}", ""]
    out.append(
        f"_Generated {dt.datetime.now():%d %b %Y %H:%M} · "
        f"{format_minutes(total_rounded)} total, {format_minutes(billable_rounded)} billable_"
    )
    out.append("")

    if not bookings:
        out.append("> No time logged in this period yet.")
        out.append("")
        return "\n".join(out)

    # -- the numbers to key in -------------------------------------------
    out.append("## Timesheet lines")
    out.append("")
    out.append("| Date | Client | Project | K-code | Billable | Hours |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    for b in bookings:
        out.append(
            f"| {short_date(b.date)} | {b.client_name} | {b.project_name} | "
            f"`{b.k_code}` | {'Yes' if b.billable else 'No'} | "
            f"{to_decimal_hours(b.rounded_minutes):.2f} |"
        )
    out.append("")

    # -- narrative source, grouped the way a note is written --------------
    groups: dict[str, list[_Booking]] = {}
    for b in bookings:
        groups.setdefault(f"{b.client_name} — {b.project_name} ({b.k_code})", []).append(b)

    out.append("## Work carried out, by project")
    out.append("")
    for heading, group in groups.items():
        group_total = sum(b.rounded_minutes for b in group)
        billable = any(b.billable for b in group)
        out.append(f"### {heading}")
        out.append("")
        out.append(
            f"{format_minutes(group_total)} ({to_decimal_hours(group_total):.2f}h) · "
            f"{'billable' if billable else 'non-billable'}"
        )
        out.append("")
        for b in group:
            out.append(f"- **{pretty_date(b.date)}** — {format_minutes(b.rounded_minutes)}")
            for item in sorted(b.items, key=lambda i: -i.minutes):
                out.append(f"  - {item.display_title} ({format_minutes(item.minutes)})")
                for note in item.notes:
                    out.append(f"    - {note}")
        out.append("")

    # -- did the week go to plan? ----------------------------------------
    out.append("## Planned vs actual")
    out.append("")
    out.append("| Day | Planned | Actual | Delta |")
    out.append("| --- | --- | --- | --- |")
    for date in dates:
        planned = planned_on(db, date)
        actual = sum(e.minutes for e in db.entries if e.date == date)
        if not planned and not actual:
            continue
        delta = actual - planned
        sign = "+" if delta > 0 else ""
        out.append(
            f"| {short_date(date)} | {format_minutes(planned)} | "
            f"{format_minutes(actual)} | {sign}{format_minutes(delta)} |"
        )
    out.append("")

    # -- context that isn't attached to a task ---------------------------
    noted = [d for d in dates if db.day_notes.get(d, "").strip()]
    if noted:
        out.append("## Day notes")
        out.append("")
        for date in noted:
            out.append(f"- **{pretty_date(date)}**: {db.day_notes[date].strip()}")
        out.append("")

    return "\n".join(out)
