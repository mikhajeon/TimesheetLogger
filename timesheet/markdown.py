"""Builds the Markdown export that Cursor turns into customer timesheet notes.

The export is organised the way a timesheet note is actually written: a table of
bookable lines first, then the narrative grouped per K-code.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

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
class _LineItem:
    title: str
    minutes: int = 0
    notes: list[str] = field(default_factory=list)


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
    items: list[_LineItem] = field(default_factory=list)


def _collect(db: Database, dates: list[ISODate]) -> list[_Booking]:
    in_range = set(dates)
    bookings: dict[tuple, _Booking] = {}

    for entry in db.entries:
        if entry.date not in in_range:
            continue
        task = db.task(entry.task_id)
        if task is None:
            continue
        project = db.project(task.project_id)
        if project is None:
            continue
        client = db.client(project.client_id)

        key = (project.id, task.billable, entry.date)
        booking = bookings.get(key)
        if booking is None:
            booking = _Booking(
                date=entry.date,
                client_name=client.name if client else "Unknown client",
                is_internal=client.is_internal if client else False,
                project_name=project.name,
                k_code=project.k_code,
                billable=task.billable,
            )
            bookings[key] = booking

        booking.exact_minutes += entry.minutes
        item = next((i for i in booking.items if i.title == task.title), None)
        if item is None:
            item = _LineItem(title=task.title)
            booking.items.append(item)
        item.minutes += entry.minutes
        if entry.note.strip():
            item.notes.append(entry.note.strip())

    increment = db.settings.rounding_minutes
    for booking in bookings.values():
        booking.rounded_minutes = round_minutes(booking.exact_minutes, increment)

    # Anything that rounds away to nothing is dropped; the exact-minutes footer
    # at the end of the export is what makes those crumbs visible.
    return sorted(
        (b for b in bookings.values() if b.rounded_minutes > 0),
        key=lambda b: (b.date, b.is_internal, b.client_name, b.k_code),
    )


def build_markdown(db: Database, dates: list[ISODate], title: str) -> str:
    bookings = _collect(db, dates)
    rounding = db.settings.rounding_minutes
    rounding_label = (
        f"rounded to the nearest {rounding} min" if rounding else "exact minutes"
    )

    in_range = set(dates)
    total_exact = sum(e.minutes for e in db.entries if e.date in in_range)
    total_rounded = sum(b.rounded_minutes for b in bookings)
    billable_rounded = sum(b.rounded_minutes for b in bookings if b.billable)

    out: list[str] = [f"# {title}", ""]
    out.append(
        f"_Generated {dt.datetime.now():%d %b %Y %H:%M} · {rounding_label} · "
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
                out.append(f"  - {item.title} ({format_minutes(item.minutes)})")
                for note in item.notes:
                    out.append(f"    - {note}")
        out.append("")

    # -- did the week go to plan? ----------------------------------------
    out.append("## Planned vs actual")
    out.append("")
    out.append("| Day | Planned | Actual | Delta |")
    out.append("| --- | --- | --- | --- |")
    for date in dates:
        planned = sum(p.minutes for p in db.plans if p.date == date)
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

    out.append(f"_Exact tracked time before rounding: {format_minutes(total_exact)}._")
    out.append("")
    return "\n".join(out)
