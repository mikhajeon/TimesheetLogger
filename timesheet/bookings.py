"""Rolls raw time entries up into the lines you actually key into a timesheet.

A customer timesheet takes one figure and one note per task per day, so several
entries against the same task on the same day are a single line here: the
minutes added together and the notes joined into one string.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .dates import ISODate, round_minutes, to_decimal_hours
from .models import Database
from .recurrence import occurs_on

#: What the joined notes are separated by in the copied text. Timesheet note
#: fields are a single line, so this has to be an inline separator.
NOTE_SEPARATOR = " | "


@dataclass
class Line:
    """Every entry for one task on one day, as one bookable row."""

    date: ISODate
    project_id: str
    client_name: str
    is_internal: bool
    project_name: str
    k_code: str
    task_title: str
    billable: bool
    #: The parent task's title, if this task is nested under a Phase.
    #: "" for a plain top-level task.
    phase_title: str = ""
    #: Ties one task's lines together across several days — the Export tab's
    #: week grid groups by this rather than by title, which isn't unique.
    task_id: str = ""
    #: Tracked to the minute. Rounding is a presentation concern, applied by
    #: :meth:`rounded` at the point the figure is shown or copied.
    minutes: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def display_title(self) -> str:
        """``task_title``, prefixed with its Phase's when it's nested."""
        if self.phase_title:
            return f"{self.phase_title} \u203a {self.task_title}"
        return self.task_title

    def rounded(self, increment: int) -> int:
        return round_minutes(self.minutes, increment)

    def hours(self, increment: int) -> str:
        """The decimal figure to paste into the hours field, e.g. ``2.25``."""
        return f"{to_decimal_hours(self.rounded(increment)):.2f}"

    def note_text(self) -> str:
        """The one-line note to paste into the comments field."""
        return NOTE_SEPARATOR.join(self.notes)


def collect(db: Database, dates: list[ISODate]) -> list[Line]:
    """Bookable lines for ``dates``, ordered the way they get keyed in.

    Lines that round away to nothing are kept: the figure is still shown, and
    dropping a row silently would hide time that was genuinely tracked.
    """
    in_range = set(dates)
    lines: dict[tuple[str, ISODate], Line] = {}
    seen_notes: dict[tuple[str, ISODate], set[str]] = {}

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
        phase = db.task(task.parent_id) if task.parent_id else None

        key = (task.id, entry.date)
        line = lines.get(key)
        if line is None:
            line = Line(
                date=entry.date,
                project_id=project.id,
                client_name=client.name if client else "Unknown client",
                is_internal=client.is_internal if client else False,
                project_name=project.name,
                k_code=project.k_code,
                task_title=task.title,
                # A project can turn billability off for everything under it,
                # but never force it on — see ``store.TaskContext.billable``.
                billable=project.default_billable and task.billable,
                phase_title=phase.title if phase else "",
                task_id=task.id,
            )
            lines[key] = line
            seen_notes[key] = set()

        line.minutes += entry.minutes

        # A 0m entry — "+ log another entry"'s own starting state, before
        # its Actual Hours picker is ever touched — has nothing booked
        # against it yet, so whatever's typed in its note meanwhile doesn't
        # roll up into the export either; only an entry with real time
        # against it contributes text here.
        note = entry.note.strip() if entry.minutes else ""
        # The same note twice in a day reads as a mistake once the entries are
        # joined into one string, so repeats are dropped.
        if note and note.casefold() not in seen_notes[key]:
            seen_notes[key].add(note.casefold())
            line.notes.append(note)

    return sorted(
        lines.values(),
        key=lambda l: (l.date, l.is_internal, l.client_name, l.k_code, l.task_title),
    )


def by_date(lines: list[Line]) -> dict[ISODate, list[Line]]:
    """Groups :func:`collect`'s output into day headings, order preserved."""
    days: dict[ISODate, list[Line]] = {}
    for line in lines:
        days.setdefault(line.date, []).append(line)
    return days


def planned_on(db: Database, date: ISODate) -> int:
    """Everything set aside for a day, pinned plans and recurring notes
    together.

    Plans are indexed first, because a pinned one overrides every recurring
    note for that task, and looking each up separately would rescan the
    list per task.
    """
    pinned = {p.task_id: p.minutes for p in db.plans if p.date == date}
    total = sum(pinned.values())
    for note in db.recurring_notes:
        if note.task_id in pinned or note.archived:
            continue
        task = db.task(note.task_id)
        if task is None or task.archived:
            continue
        project = db.project(task.project_id)
        if project is None or project.closed:
            continue
        if occurs_on(note.recurrence, date):
            total += max(0, note.minutes)
    return total
