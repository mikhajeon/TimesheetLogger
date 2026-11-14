"""The data model: Client -> Project (K-code) -> Task, plus plans and entries.

Everything serialises to a single JSON file. Minutes are always stored exactly;
rounding is applied only when exporting, so the underlying record stays honest.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

from .dates import ISODate

SCHEMA_VERSION = 1


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Client:
    name: str
    is_internal: bool = False
    archived: bool = False
    id: str = field(default_factory=new_id)


@dataclass
class Project:
    client_id: str
    name: str
    #: The code this project's hours are booked against in the timesheet system.
    k_code: str
    default_billable: bool = True
    archived: bool = False
    #: The K-code's work is wrapped up. Unlike ``archived`` (which just hides
    #: it), closing cascades onto every task under it: each drops out of the
    #: Week tab's "All open tasks" and the Recurring tab's task picker, and
    #: any recurring notes on them stop coming round — see
    #: ``Store.recurring_notes_due``. Nothing already logged is touched, and
    #: it's reversible with no guard, unlike delete.
    closed: bool = False
    id: str = field(default_factory=new_id)


@dataclass
class Recurrence:
    """How often a task comes round, and how long to set aside for it.

    Matched against a date by :mod:`timesheet.recurrence` rather than expanded
    into stored plans, so editing it moves every future occurrence at once.
    """

    #: "daily", "weekly" or "monthly", from ``recurrence.FREQUENCIES``.
    frequency: str = "weekly"
    #: Every N days, weeks or months.
    interval: int = 1
    #: Weekly: which days, 0 = Monday. Empty means the day it started on.
    weekdays: list[int] = field(default_factory=list)
    #: Monthly: which dates, 1-31, or ``recurrence.LAST_DAY``. Empty means the
    #: date it started on.
    month_days: list[int] = field(default_factory=list)
    #: Monthly: which months, 1 = January. Empty means all of them — this is
    #: what lets one rule cover a handful of dates spread across the year.
    months: list[int] = field(default_factory=list)
    #: Planned on each occurrence, unless that day has been typed over.
    minutes: int = 60
    #: First possible occurrence, and what the interval counts from.
    start: ISODate = ""
    #: Last possible occurrence. "" repeats indefinitely.
    until: ISODate = ""


@dataclass
class Task:
    project_id: str
    title: str
    billable: bool = True
    archived: bool = False
    #: "" for a top-level task. A task with this set is itself always a leaf —
    #: nesting caps at one level, so a child never gains children of its own.
    #: A task with no parent that has non-archived children is a "Phase" —
    #: a computed label, not stored here — and stops being loggable itself.
    parent_id: str = ""
    id: str = field(default_factory=new_id)


@dataclass
class Plan:
    """What's intended for a task on a given day: minutes, and optionally a
    note on what that work actually is, ahead of any time being logged."""

    task_id: str
    date: ISODate
    minutes: int
    #: Shown and edited as the day's row for this task before any Entry
    #: exists to carry a note of its own. Once logging starts, each Entry
    #: carries its own note instead — this one is just the head start.
    note: str = ""


@dataclass
class Entry:
    """Minutes actually spent, from the timer or typed in."""

    task_id: str
    date: ISODate
    minutes: int
    note: str = ""
    source: str = "manual"  # "manual" | "timer"
    #: Set when this entry is a specific day's occurrence of a
    #: ``RecurringNote``, so that note's placeholder doesn't show up again
    #: once logged. "" for an entry that never was one.
    recurring_note_id: str = ""
    #: This entry's own Planned Hours — independent of every other entry
    #: logged against the same task on the same day, and of the task's
    #: day-level ``Plan`` (see ``Plan.minutes``), which only ever covers
    #: the day before anything's been logged. 0 ("Unset") by default: a
    #: brand new entry never inherits whatever the day happened to be
    #: planning before it existed.
    plan_minutes: int = 0
    #: Context for whoever reads the raw record later — never put in front
    #: of a customer, unlike ``note``: ``bookings.collect`` (the only path
    #: into an export) reads ``note`` alone, so nothing here ever rolls up
    #: into a timesheet line or its narrative.
    internal_note: str = ""
    id: str = field(default_factory=new_id)


@dataclass
class RecurringNote:
    """A note that comes back on its own schedule, under a shared task.

    The only recurring mechanism in the app — there used to also be a single
    ``Task.recurrence``, tying a whole task to one schedule regardless of
    which entry you'd opened to set it, but real work is rarely that tidy:
    several distinct standing commitments — a daily stand-up, a Monday admin
    slot — often belong under one task for billing purposes (one K-code, one
    line on the timesheet) while each still needs its own schedule and its
    own words. A note is exactly that, scoped to one specific piece of work
    rather than the task as a whole, so a task hosts as many of these as it
    needs, each showing up as its own row on the days it's due until it's
    logged (or skipped), rather than forcing everything under a task to
    share a single plan slot.
    """

    task_id: str
    note: str
    minutes: int = 30
    recurrence: Recurrence | None = None
    archived: bool = False
    #: The template an occurrence's own ``Entry.internal_note`` starts from,
    #: same as ``note`` already does for ``Entry.note`` — see that field.
    internal_note: str = ""
    id: str = field(default_factory=new_id)


@dataclass
class SkippedNote:
    """One occurrence of a ``RecurringNote`` dismissed for a single day.

    A note has no ``Plan`` of its own to pin to zero the way a task's own
    repeat rule does for a single skipped day (see ``Store.planned_minutes``)
    — several notes can be due on the same task the same day, and a skip has
    to name which one it's for, so this is its own record rather than
    reusing ``Plan``.
    """

    note_id: str
    date: str


@dataclass
class ActiveTimer:
    task_id: str
    #: Unix seconds the *current* running segment began — moves forward
    #: every time the timer resumes from a pause, so it's stale while
    #: paused (see ``paused``). What "how much has this accrued" is
    #: computed from directly — see ``elapsed_seconds`` — never which
    #: calendar day the timer's own entry belongs to; that's
    #: ``day_anchor``, below, which never moves.
    started_at: float
    note: str = ""
    #: Set when the timer was started from a ``RecurringNote``'s row, so the
    #: entry it produces on stopping is tagged the same way a typed-in or
    #: nudged one would be.
    recurring_note_id: str = ""
    #: Set when the timer was started on a row that already had its own
    #: logged ``Entry`` — an existing manual or timer entry picked up again
    #: rather than a fresh placeholder. Stopping then tops that same entry's
    #: minutes up instead of appending a brand new one (see
    #: ``Store.stop_timer``), the same way the +/- chips top up rather than
    #: littering the day with a separate entry per click.
    entry_id: str = ""
    #: Seconds already banked from every earlier running segment, before
    #: whichever one is current (or, while paused, before the pause now in
    #: effect) — so pausing and resuming never loses time already run up.
    elapsed_before: float = 0.0
    #: Whether the timer is currently paused — while ``True``,
    #: ``started_at`` is stale and ``elapsed_before`` alone already holds
    #: everything banked up to the pause.
    paused: bool = False
    #: True once idle detection (see ``timesheet.idle``) paused this timer
    #: on its own, rather than the user pressing Pause — only this kind of
    #: pause lifts itself the moment activity is seen again; a pause the
    #: user chose stays a pause until they choose to resume it.
    auto_paused: bool = False
    #: Unix seconds the timer was first started, before any pause — fixed
    #: for the timer's whole life, purely to anchor which calendar day this
    #: timer (and the entry it produces on stopping) belongs to,
    #: independent of whichever segment happens to be current when it's
    #: actually stopped.
    day_anchor: float = 0.0

    @property
    def is_paused(self) -> bool:
        return self.paused

    @property
    def elapsed_seconds(self) -> float:
        """Total time this timer has banked so far, across every
        run/pause/resume segment — what every caller wanting "how long has
        this really been running" should read instead of ``started_at``
        directly, which on its own ignores any pause."""
        if self.paused:
            return self.elapsed_before
        return self.elapsed_before + max(0.0, time.time() - self.started_at)


@dataclass
class Settings:
    always_on_top: bool = True
    #: Whether collapsing to the small badge (see ``Overlay.collapse``) pins
    #: it on top too, even while "Always on top" above is off — on by
    #: default, since a badge that's about to get buried behind whatever
    #: else is open defeats the point of shrinking down to one rather than
    #: just minimising normally. Doesn't touch "Always on top" itself, or
    #: persist a pin past the badge — expanding back to the full view
    #: always drops back to whatever that setting actually says.
    pin_when_collapsed: bool = True
    opacity: float = 1.0
    #: A palette name from ``ui.theme.PALETTES``, or ``"system"`` to track
    #: the OS's own light/dark setting instead of naming one directly.
    theme: str = "dark"
    #: How an hour figure reads on screen — "clock" (``7h 30m``) or "decimal"
    #: (``7.5``). One setting for the whole app, next to Theme in
    #: Preferences, rather than per label; never applied to what gets
    #: exported or copied out, which always stays decimal regardless (see
    #: ``dates.format_duration``).
    hour_format: str = "clock"
    #: How a calendar date reads on screen — "iso" (``2026-11-13``, the
    #: default), "dmy" (``13/11/2026``), "mdy" (``11/13/2026``) or "text"
    #: (``13 Nov 2026``). See ``dates.set_date_format``. Never applied to
    #: stored dates or export filenames, which stay ISO.
    date_format: str = "iso"
    #: Step size (minutes) offered by every duration picker — Actual/Planned
    #: Hours, a recurring note's own minutes, the Daily target, and so on.
    #: Never applied to what's exported; those figures always stay exact.
    entry_increment_minutes: int = 15
    daily_target_minutes: int = 450
    show_weekend: bool = False
    #: How the Day tab groups its rows — "manual" (drag-ordered, the
    #: default), "project", "phase" or "task". One setting for the whole
    #: app rather than per-day, since it's a viewing preference, not data.
    day_grouping: str = "manual"
    #: How the Week tab groups its tasks — "project" (the default) or
    #: "customer". Same kind of setting as ``day_grouping``.
    week_grouping: str = "project"
    #: A corner from ``monitors.CORNERS``; "" leaves the overlay where it is dragged.
    snap_corner: str = ""
    #: Device name of the screen to snap to. "" means whichever is primary.
    snap_screen: str = ""
    #: Put an icon in the notification area. Closing then hides rather than quits.
    tray: bool = True
    #: Auto-pauses a running timer once no mouse or keyboard input has been
    #: seen, system-wide, for ``idle_pause_minutes`` — see
    #: ``timesheet.idle`` and ``Overlay._poll_idle``. On by default: a
    #: timer left running at an away desk otherwise just keeps counting,
    #: same problem Teams' own "Away" status exists to catch.
    idle_auto_pause: bool = True
    #: Minutes of no input before that auto-pause kicks in. Matches
    #: Microsoft Teams' own default "Away" threshold (5 minutes) out of the
    #: box, on the theory that a timer pausing in step with whatever your
    #: own status is already about to say reads as one consistent idea of
    #: "stepped away", not two unrelated ones on different clocks — but
    #: it's independent of Teams entirely (nothing here talks to it), so
    #: changing this never changes what Teams itself decides.
    idle_pause_minutes: int = 5
    #: Global-hotkey overrides, keyed by action name ("collapse", "pin",
    #: "timer"). An action missing here just uses ``overlay.DEFAULT_HOTKEYS``
    #: — this dict only holds what the user has changed away from default,
    #: so "reset to default" is simply removing the entry.
    hotkeys: dict[str, str] = field(default_factory=dict)
    #: A ``#rrggbb`` override for the Day tab's progress bar, replacing
    #: every one of its usual state colours (blue while logging, green at
    #: target, amber for outstanding plan, red over-committed) with this
    #: one colour regardless of state — "" (the default) leaves the bar
    #: showing those colours as it always has. The first of several
    #: planned "customise this component's colour" settings (see
    #: ``timesheet.ui.setup``'s own Preferences tab) — everything else
    #: still reads straight off ``ui.theme``.
    progress_bar_color: str = ""
    #: Same idea for the planned (outstanding) stretch of that bar. "" keeps
    #: its usual amber / red-when-over-committed state colours.
    planned_bar_color: str = ""
    #: Working day as minutes after midnight. The Day tab's progress bar
    #: spans start..end and marks every half hour between them.
    work_start_minutes: int = 9 * 60
    work_end_minutes: int = 17 * 60
    #: A lunch break inside the work day, left out of the progress bar's
    #: timeline (what's logged measures against the working time left).
    lunch_enabled: bool = True
    lunch_start_minutes: int = 12 * 60
    lunch_end_minutes: int = 13 * 60


@dataclass
class Database:
    version: int = SCHEMA_VERSION
    clients: list[Client] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    plans: list[Plan] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    recurring_notes: list[RecurringNote] = field(default_factory=list)
    skipped_notes: list[SkippedNote] = field(default_factory=list)
    #: Free text per day, for context that isn't tied to a single task.
    day_notes: dict[ISODate, str] = field(default_factory=dict)
    #: Manual drag-order for the Day tab, per date: an ordered list of row
    #: ids (an ``Entry`` id, or ``"task:<task_id>"`` for a task with nothing
    #: logged yet). Only consulted in "manual" grouping; a row missing from
    #: it — typically a brand new one — sorts to the end.
    day_order: dict[ISODate, list[str]] = field(default_factory=dict)
    active_timer: ActiveTimer | None = None
    settings: Settings = field(default_factory=Settings)

    # -- lookups ---------------------------------------------------------

    def client(self, client_id: str) -> Client | None:
        return next((c for c in self.clients if c.id == client_id), None)

    def project(self, project_id: str) -> Project | None:
        return next((p for p in self.projects if p.id == project_id), None)

    def task(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def recurring_note(self, note_id: str) -> RecurringNote | None:
        return next((n for n in self.recurring_notes if n.id == note_id), None)

    def project_of_task(self, task_id: str) -> Project | None:
        task = self.task(task_id)
        return self.project(task.project_id) if task else None

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Database":
        def build(cls, rows):
            # Unknown keys from a newer version are dropped rather than crashing.
            allowed = {f for f in cls.__dataclass_fields__}
            return [cls(**{k: v for k, v in row.items() if k in allowed}) for row in rows or []]

        def build_tasks(rows):
            """Also peels off any task-level ``recurrence`` a database saved
            before that was removed in favour of recurring notes, the only
            mechanism left (see ``RecurringNote``). ``build`` already drops
            it silently as an unknown field now that ``Task`` no longer has
            one — this is what stops that from quietly losing a real
            schedule instead: each becomes its own note, named after the
            task since there's no specific entry from back then to scope it
            to any tighter.
            """
            raw_rows = rows or []
            tasks = build(Task, raw_rows)
            migrated: list[RecurringNote] = []
            for raw_row, task in zip(raw_rows, tasks):
                raw_rule = raw_row.get("recurrence")
                if not raw_rule:
                    continue
                rule = build(Recurrence, [raw_rule])[0] if isinstance(raw_rule, dict) else raw_rule
                migrated.append(
                    RecurringNote(
                        task_id=task.id, note=task.title,
                        minutes=max(0, rule.minutes), recurrence=rule,
                    )
                )
            return tasks, migrated

        def build_recurring_notes(rows):
            notes = build(RecurringNote, rows)
            for note in notes:
                if isinstance(note.recurrence, dict):
                    note.recurrence = build(Recurrence, [note.recurrence])[0]
            return notes

        settings_raw = raw.get("settings") or {}
        settings = Settings(
            **{k: v for k, v in settings_raw.items() if k in Settings.__dataclass_fields__}
        )

        timer_raw = raw.get("active_timer")
        if timer_raw and "day_anchor" not in timer_raw:
            # A file saved before pausing existed never had a reason to
            # separate "which day this belongs to" from "when the current
            # segment began" — they were the same field. Carrying the old
            # ``started_at`` over as this timer's fixed day anchor keeps
            # that day the same one it always would have been.
            timer_raw = {**timer_raw, "day_anchor": timer_raw.get("started_at")}
        timer = ActiveTimer(**timer_raw) if timer_raw else None

        tasks, migrated_notes = build_tasks(raw.get("tasks"))

        return Database(
            version=SCHEMA_VERSION,
            clients=build(Client, raw.get("clients")),
            projects=build(Project, raw.get("projects")),
            tasks=tasks,
            plans=build(Plan, raw.get("plans")),
            entries=build(Entry, raw.get("entries")),
            recurring_notes=build_recurring_notes(raw.get("recurring_notes")) + migrated_notes,
            skipped_notes=build(SkippedNote, raw.get("skipped_notes")),
            day_notes=raw.get("day_notes") or {},
            day_order=raw.get("day_order") or {},
            active_timer=timer,
            settings=settings,
        )


def seed() -> Database:
    """A fresh database with the Internal NCC bucket already set up."""
    internal = Client(name="Internal NCC", is_internal=True)
    admin = Project(
        client_id=internal.id,
        name="Admin & internal",
        k_code="NCC",
        default_billable=False,
    )
    return Database(clients=[internal], projects=[admin])
