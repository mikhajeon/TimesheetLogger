"""Owns the database: mutations, queries, and persistence.

Deliberately free of any GUI toolkit import — observers are plain callables, so
the same store drives whichever front end is on top of it.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import bookings, recurrence
from .dates import ISODate, to_iso
from .models import (
    ActiveTimer,
    Client,
    Database,
    Entry,
    Plan,
    Project,
    Recurrence,
    RecurringNote,
    SkippedNote,
    Task,
    seed,
)
import datetime as dt


#: ``Settings.day_grouping`` values the Day tab's view-mode selector offers.
DAY_GROUPINGS = ("manual", "project", "phase", "task")


@dataclass(frozen=True)
class DayRow:
    """One line on the Day tab.

    Row identity is the ``Entry``'s own id once one exists, or ``"task:<id>"``
    for a task that's relevant to the day but hasn't had anything logged
    against it yet — a placeholder standing in for its ``Plan``. This id is
    what Manual grouping's drag order is keyed on.
    """

    id: str
    context: "TaskContext"
    entry: Entry | None
    #: Whether this is the first row rendered for its task on the day, in
    #: the order ``day_rows`` produces them. Every row carries its own copy
    #: of the Day tab's controls now (its own timer selector, its own
    #: Planned Hours), so nothing there singles this one out any more — it
    #: is kept as a plain structural fact about the ordering, for whatever
    #: future use (and the tests already asserting it) wants it.
    is_first_for_task: bool
    plan_note: str = ""
    plan_minutes: int = 0
    #: Set when this is a placeholder for one specific ``RecurringNote``'s
    #: occurrence today, rather than the task's own plan. "" otherwise —
    #: including once the row has become a real ``Entry``, whether or not
    #: that entry started life as one of these (see ``Entry.recurring_note_id``
    #: for that instead).
    recurring_note_id: str = ""
    #: A pending recurring note's own ``internal_note`` template — the
    #: placeholder counterpart to ``plan_note``, for :attr:`internal_note`
    #: below. A bare ``Plan`` placeholder has no equivalent of its own
    #: (``Plan`` carries no internal note), so this stays "" for one.
    plan_internal_note: str = ""

    @property
    def note(self) -> str:
        return self.entry.note if self.entry else self.plan_note

    @property
    def internal_note(self) -> str:
        return self.entry.internal_note if self.entry else self.plan_internal_note

    @property
    def is_placeholder(self) -> bool:
        return self.entry is None


@dataclass(frozen=True)
class TaskContext:
    """A task together with the project and client it hangs off."""

    task: Task
    project: Project
    client: Client
    #: The parent task, if ``task`` is nested one level under a Phase.
    #: ``None`` for a plain top-level task.
    phase: Task | None = None

    @property
    def heading(self) -> str:
        return f"{self.client.name} · {self.project.name} ({self.project.k_code})"

    @property
    def phase_title(self) -> str:
        return self.phase.title if self.phase else ""

    @property
    def display_title(self) -> str:
        """The task's title, prefixed with its Phase's when it's nested."""
        if self.phase:
            return f"{self.phase.title} \u203a {self.task.title}"
        return self.task.title

    @property
    def billable(self) -> bool:
        """Whether this task's time actually counts as billable.

        A project can turn billability off for everything under it, but never
        force it on — ``task.billable`` is never overwritten by this, so
        re-enabling the project silently restores whatever each task's own
        flag already said.
        """
        return self.project.default_billable and self.task.billable


#: How many steps back Undo can reach. In-memory only — never written to
#: disk, and small enough that even a long session's worth costs nothing
#: next to the window it's sitting in.
_HISTORY_LIMIT = 50


def _tracked(method: Callable) -> Callable:
    """Wraps a ``Store`` mutator so Undo can step back to just before it ran.

    A snapshot is taken around the call rather than a hand-written inverse
    kept beside each method: there are getting on for thirty of these, and a
    bespoke undo for every one would be thirty chances to get it wrong or
    forget to update one. Nothing is pushed if the call turns out to have
    been a no-op — a bad id, an empty set of changes, nudging a non-existent
    entry — which several of these return early on, so Undo never lands on a
    step that visibly does nothing.

    Suspended for the span of one mutator calling another (``adjust_entry``
    calling ``add_entry``, ``start_timer`` calling ``stop_timer`` to clear
    the old one first) so a single user action doesn't produce two undo
    steps, the second of which would be a no-op revisiting the first.
    """

    @functools.wraps(method)
    def wrapper(self: "Store", *args, **kwargs):
        if self._tracking_suspended:
            return method(self, *args, **kwargs)
        self._tracking_suspended = True
        try:
            before = self._snapshot()
            result = method(self, *args, **kwargs)
        finally:
            self._tracking_suspended = False
        if self._snapshot() != before:
            self._push_undo(before)
        return result

    return wrapper


class Store:
    #: Everything in ``Database`` that counts as "your data" for Undo/Redo —
    #: everything except ``Settings``, which is a live app preference (theme,
    #: window position, rounding...) rather than something a slip could need
    #: walking back. Undoing a deleted task should never also flip your
    #: theme back because you happened to change it in between.
    _HISTORY_FIELDS = (
        "clients", "projects", "tasks", "plans", "entries",
        "recurring_notes", "skipped_notes", "day_notes", "day_order", "active_timer",
    )

    def __init__(self, data_file: Path) -> None:
        self.data_file = data_file
        self.db: Database = seed()
        self._listeners: list[Callable[[], None]] = []
        #: Session-only history for Undo/Redo — see ``load`` for why it's
        #: reset there rather than carried across a fresh file.
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._tracking_suspended = False

    # -- plumbing --------------------------------------------------------

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _changed(self) -> None:
        self.save()
        for listener in list(self._listeners):
            listener()

    def load(self) -> None:
        # However it goes below, this is a different document from whatever
        # was open a moment ago — Undo reaching back into it would land
        # somewhere the user never asked to go.
        self._undo_stack = []
        self._redo_stack = []
        if not self.data_file.exists():
            self.db = seed()
            self.save()
            return
        try:
            raw = json.loads(self.data_file.read_text("utf-8"))
            self.db = Database.from_dict(raw)
            # A task-level ``recurrence`` saved before it was folded into
            # recurring notes (see ``Database.from_dict``'s ``build_tasks``)
            # only lives in ``self.db`` once migrated — nothing below here
            # writes it back out on its own, so without this, a process that
            # never reaches a save (closed some way other than Quit, or
            # killed outright) would find the very same raw key still on
            # disk next time and migrate it all over again, duplicating it.
            if any(isinstance(t, dict) and t.get("recurrence") for t in raw.get("tasks") or []):
                self.save()
        except Exception as error:  # noqa: BLE001 - any bad file lands here
            # A corrupt file is moved aside rather than silently overwritten, so
            # a week of tracking is never lost to one bad write.
            backup = self.data_file.with_suffix(f".corrupt-{int(time.time())}.json")
            shutil.move(str(self.data_file), str(backup))
            print(f"Could not read {self.data_file} ({error}); kept it at {backup}")
            self.db = seed()
            self.save()

    # -- undo / redo ----------------------------------------------------

    def _snapshot(self) -> dict:
        full = self.db.to_dict()
        return {key: full[key] for key in self._HISTORY_FIELDS}

    def _restore(self, snapshot: dict) -> None:
        live_settings = self.db.settings
        self.db = Database.from_dict(snapshot)
        self.db.settings = live_settings  # untouched by Undo/Redo — see above

    def _push_undo(self, snapshot: dict) -> None:
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > _HISTORY_LIMIT:
            self._undo_stack.pop(0)
        # A fresh edit retires whatever could have been redone — there's no
        # single "future" left once it's been branched from.
        self._redo_stack.clear()

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo(self) -> None:
        if not self._undo_stack:
            return
        current = self._snapshot()
        self._restore(self._undo_stack.pop())
        self._redo_stack.append(current)
        self._changed()

    def redo(self) -> None:
        if not self._redo_stack:
            return
        current = self._snapshot()
        self._restore(self._redo_stack.pop())
        self._undo_stack.append(current)
        self._changed()

    def save(self) -> None:
        """Writes through a temp file so an interrupted save can't truncate the data."""
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.data_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.db.to_dict(), indent=2), "utf-8")
        # Windows occasionally holds the destination open for a moment — a
        # real-time antivirus scan or the search indexer glancing at a file
        # the instant it changes — long enough for os.replace to raise
        # PermissionError on an otherwise uncontended rename. Undo/Redo can
        # fire several saves in a fraction of a second (holding Ctrl+Z down),
        # so a handful of short retries absorbs that instead of losing a save
        # to something that clears itself almost immediately.
        for attempt in range(5):
            try:
                os.replace(tmp, self.data_file)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)

    # -- clients and projects --------------------------------------------

    @_tracked
    def add_client(self, name: str, is_internal: bool = False) -> Client:
        client = Client(name=name, is_internal=is_internal)
        self.db.clients.append(client)
        self._changed()
        return client

    @_tracked
    def update_client(self, client_id: str, **changes) -> None:
        client = self.db.client(client_id)
        if client is None:
            return
        for key, value in changes.items():
            setattr(client, key, value)
        self._changed()

    @_tracked
    def delete_client(self, client_id: str) -> str:
        """Removes a client outright. Returns "" on success, else why not.

        Archiving is the everyday way to retire one; this is for undoing a
        slip, and is refused bottom-up — a client can't disappear out from
        under projects that still exist, archived or not.
        """
        client = self.db.client(client_id)
        if client is None:
            return ""
        if any(p.client_id == client_id for p in self.db.projects):
            return "it still has projects under it"
        self.db.clients.remove(client)
        self._changed()
        return ""

    @_tracked
    def add_project(self, client_id: str, name: str, k_code: str, default_billable: bool) -> Project:
        project = Project(
            client_id=client_id, name=name, k_code=k_code, default_billable=default_billable
        )
        self.db.projects.append(project)
        self._changed()
        return project

    @_tracked
    def update_project(self, project_id: str, **changes) -> None:
        project = self.db.project(project_id)
        if project is None:
            return
        for key, value in changes.items():
            setattr(project, key, value)
        self._changed()

    @_tracked
    def delete_project(self, project_id: str) -> str:
        """Removes a project outright. Returns "" on success, else why not."""
        project = self.db.project(project_id)
        if project is None:
            return ""
        if any(t.project_id == project_id for t in self.db.tasks):
            return "it still has tasks under it"
        self.db.projects.remove(project)
        self._changed()
        return ""

    # -- tasks -------------------------------------------------------------

    @_tracked
    def add_task(self, project_id: str, title: str, billable: bool) -> Task:
        task = Task(project_id=project_id, title=title, billable=billable)
        self.db.tasks.append(task)
        self._changed()
        return task

    def is_phase(self, task: Task) -> bool:
        """Whether ``task`` currently has a non-archived child.

        Purely computed, never stored: a "Phase" is just a task with
        children. Lose the last one and it reads as a plain Task again.
        """
        return any(t.parent_id == task.id and not t.archived for t in self.db.tasks)

    def can_add_subtask(self, task_id: str) -> bool:
        """Whether ``task_id`` is eligible for "+ Add sub-task".

        A child task is never itself eligible — nesting caps at one level.
        That's the only rule now: a task with logged time, a plan, or a
        recurring note of its own still qualifies, since gaining a child
        moves all of that onto the new child in the same step rather than
        stranding it — see :meth:`add_subtask` and :meth:`relocate_entries`.
        """
        task = self.db.task(task_id)
        return task is not None and not task.parent_id

    def task_has_own_history(self, task_id: str) -> bool:
        """Whether ``task_id`` has logged time, a plan, or a recurring note
        directly on it. Used to decide whether the Setup tab's "Move..."
        control has anything worth offering — this used to be exactly what
        gated "+ Add sub-task" outright (see :meth:`can_add_subtask`), before
        gaining a child started relocating that history instead of refusing
        the child over it.
        """
        return (
            any(e.task_id == task_id for e in self.db.entries)
            or any(p.task_id == task_id for p in self.db.plans)
            or any(n.task_id == task_id for n in self.db.recurring_notes)
        )

    @_tracked
    def relocate_entries(self, from_task_id: str, to_task_id: str) -> int:
        """Moves every logged entry, plan and recurring note from one task
        onto another, keyed purely by task id. Returns how many entries moved.

        Used automatically the moment a task gains its first child — a task
        becomes a Phase as soon as it has one, and a Phase is never itself
        loggable (see :meth:`active_contexts`), so leaving history directly
        on it would make it quietly vanish everywhere it's shown rather than
        stay reachable under the child that now carries it — and offered by
        hand from the Setup tab for consolidating two tasks' history, or
        undoing time logged against the wrong one.

        A plan already sitting on ``to_task_id`` for the same day wins the
        slot rather than being silently overwritten: its minutes gain the
        moved plan's on top, and the moved note (if any, and different)
        joins its own rather than replacing it.
        """
        if from_task_id == to_task_id:
            return 0
        moved = 0
        for entry in self.db.entries:
            if entry.task_id == from_task_id:
                entry.task_id = to_task_id
                moved += 1

        kept_plans = []
        for plan in self.db.plans:
            if plan.task_id != from_task_id:
                kept_plans.append(plan)
                continue
            existing = next(
                (p for p in self.db.plans if p.task_id == to_task_id and p.date == plan.date), None
            )
            if existing is None:
                plan.task_id = to_task_id
                kept_plans.append(plan)
            else:
                existing.minutes += plan.minutes
                if plan.note and plan.note != existing.note:
                    existing.note = f"{existing.note} | {plan.note}" if existing.note else plan.note
        self.db.plans = kept_plans

        for note in self.db.recurring_notes:
            if note.task_id == from_task_id:
                note.task_id = to_task_id
        if self.db.active_timer and self.db.active_timer.task_id == from_task_id:
            self.db.active_timer.task_id = to_task_id
        self._changed()
        return moved

    @_tracked
    def add_subtask(self, parent_id: str, title: str, billable: bool) -> tuple[Task | None, str]:
        """Adds a task nested one level under ``parent_id``.

        Returns ``(task, "")`` on success, or ``(None, reason)`` if
        ``parent_id`` isn't eligible — see :meth:`can_add_subtask`. Any
        history already sitting directly on the parent relocates onto the
        new child in the same step (see :meth:`relocate_entries`) — a no-op
        the first time there's none, and never an issue on a later child,
        since the parent became a Phase, and so stopped being loggable
        itself, the moment it gained its first one.
        """
        parent = self.db.task(parent_id)
        if parent is None:
            return None, "the parent task no longer exists"
        if parent.parent_id:
            return None, "nesting only goes one level deep"
        task = Task(project_id=parent.project_id, title=title, billable=billable, parent_id=parent_id)
        self.db.tasks.append(task)
        self.relocate_entries(parent_id, task.id)
        self._changed()
        return task, ""

    @_tracked
    def update_task(self, task_id: str, **changes) -> None:
        task = self.db.task(task_id)
        if task is None:
            return
        for key, value in changes.items():
            setattr(task, key, value)
        if task.archived and self.db.active_timer and self.db.active_timer.task_id == task_id:
            self.db.active_timer = None
        self._changed()

    @_tracked
    def delete_task(self, task_id: str) -> str:
        """Removes a task outright. Returns "" on success, else why not.

        Refused once there's history to protect — logged time, a plan, or a
        recurring note — because that's exactly the record archiving exists
        to keep. A task that's never been touched can just go.
        """
        task = self.db.task(task_id)
        if task is None:
            return ""
        if self.is_phase(task):
            return "it still has sub-tasks under it"
        if self.db.active_timer and self.db.active_timer.task_id == task_id:
            return "its timer is running"
        if any(e.task_id == task_id for e in self.db.entries):
            return "it has logged time against it"
        if any(p.task_id == task_id for p in self.db.plans):
            return "it has a plan set on it"
        if any(n.task_id == task_id for n in self.db.recurring_notes):
            return "it has a recurring note on it"
        self.db.tasks.remove(task)
        self._changed()
        return ""

    # -- planning ----------------------------------------------------------

    @_tracked
    def set_plan(self, task_id: str, date: ISODate, minutes: int) -> None:
        """Pins the minutes for one task on one day.

        A zero is normally just a deletion, but a recurring note's zero is
        kept around too — it's how "not this time" is recorded for a day a
        note would otherwise still plan, and without it the note's own
        placeholder would put the day straight back (see ``planned_minutes``
        and ``day_rows``, which both treat a pinned plan as overriding any
        note due that day). A plan already carrying a note survives a zero
        for the same reason, since there's still something on it worth
        keeping; its note is carried over rather than wiped by this.
        """
        existing = next(
            (p for p in self.db.plans if p.task_id == task_id and p.date == date), None
        )
        note = existing.note if existing else ""
        self.db.plans = [
            p for p in self.db.plans if not (p.task_id == task_id and p.date == date)
        ]
        if minutes > 0 or self.recurring_notes_due(task_id, date) or note:
            self.db.plans.append(
                Plan(task_id=task_id, date=date, minutes=max(0, minutes), note=note)
            )
        self._changed()

    @_tracked
    def set_plan_note(self, task_id: str, date: ISODate, note: str) -> None:
        """Sets the note on a task's plan for a day — the Day row's text
        before any time is logged against it. Creates an empty-minutes plan
        just to hold the note if nothing was planned yet; clears it away
        again if the note is emptied and there was never really a plan.
        """
        existing = next(
            (p for p in self.db.plans if p.task_id == task_id and p.date == date), None
        )
        if existing is not None:
            existing.note = note
            self._changed()
            return
        if note:
            self.db.plans.append(Plan(task_id=task_id, date=date, minutes=0, note=note))
            self._changed()

    @_tracked
    def set_day_order(self, date: ISODate, order: list[str]) -> None:
        """Persists Manual mode's row order for one day."""
        self.db.day_order[date] = list(order)
        self._changed()

    def day_order_for(self, date: ISODate) -> list[str]:
        return self.db.day_order.get(date, [])

    # -- recurring notes ------------------------------------------------------

    @_tracked
    def add_recurring_note(
        self, task_id: str, note: str, minutes: int = 30, rule: Recurrence | None = None
    ) -> RecurringNote:
        """Adds a note with its own schedule under ``task_id``.

        Given no rule, it's created off (``rule=None``) rather than
        defaulted to daily or weekly — there's no single sensible guess,
        and the editor it opens into makes turning it on a single click.
        """
        if rule is not None and not rule.start:
            rule.start = recurrence.today_iso()
        item = RecurringNote(task_id=task_id, note=note, minutes=max(0, minutes), recurrence=rule)
        self.db.recurring_notes.append(item)
        self._changed()
        return item

    @_tracked
    def promote_to_recurring_note(self, row: DayRow, date: ISODate, rule: Recurrence) -> RecurringNote:
        """Turns "Repeat" on for one specific row into its own
        ``RecurringNote``, rather than a rule that would apply to every row
        under the task regardless of which one it was switched on from.

        A bare Plan-backed placeholder has today's ``Plan`` cleared in the
        same step: ``day_rows`` always lets a pinned plan shadow a recurring
        note entirely (see :meth:`planned_minutes`), so the brand-new note's
        own placeholder would stay invisible today, shadowed by the very
        plan it was promoted from, if that plan were left in place. An
        already-logged entry just gets tagged onto the new note instead —
        nothing about it needs to change, including its own minutes.
        """
        if not rule.start:
            rule.start = recurrence.today_iso()
        task_id = row.context.task.id
        item = RecurringNote(
            task_id=task_id, note=row.note or row.context.task.title,
            minutes=max(0, row.plan_minutes), recurrence=rule,
            internal_note=row.internal_note,
        )
        self.db.recurring_notes.append(item)
        if row.entry is not None:
            row.entry.recurring_note_id = item.id
        else:
            self.db.plans = [
                p for p in self.db.plans if not (p.task_id == task_id and p.date == date)
            ]
        self._changed()
        return item

    @_tracked
    def update_recurring_note(self, note_id: str, **changes) -> None:
        item = next((n for n in self.db.recurring_notes if n.id == note_id), None)
        if item is None:
            return
        for key, value in changes.items():
            setattr(item, key, value)
        self._changed()

    @_tracked
    def set_recurring_note_rule(self, note_id: str, rule: Recurrence | None) -> None:
        item = next((n for n in self.db.recurring_notes if n.id == note_id), None)
        if item is None:
            return
        if rule is not None and not rule.start:
            rule.start = recurrence.today_iso()
        item.recurrence = rule
        self._changed()

    @_tracked
    def set_row_note_rule(self, note_id: str, rule: Recurrence | None) -> None:
        """Like :meth:`set_recurring_note_rule`, but also carries the
        rule's own "Plan" figure onto the note's ``minutes`` — what the Day
        tab's row-level Repeat editor calls, since that figure is the only
        one it ever shows for a note, unlike the Recurring tab's own editor,
        which keeps "Usually" as its own separate input the rule's figure
        is never allowed to overwrite (see :meth:`update_recurring_note`).
        """
        item = next((n for n in self.db.recurring_notes if n.id == note_id), None)
        if item is None:
            return
        if rule is not None and not rule.start:
            rule.start = recurrence.today_iso()
        item.recurrence = rule
        if rule is not None:
            item.minutes = max(0, rule.minutes)
        self._changed()

    @_tracked
    def delete_recurring_note(self, note_id: str) -> None:
        """Removes a recurring note outright.

        Unlike a task, nothing is lost by this: every entry it's already
        produced stands on its own (see ``Entry.recurring_note_id``), so
        there's no history here worth a guard or an archive step — only
        future occurrences stop being suggested.
        """
        self.db.recurring_notes = [n for n in self.db.recurring_notes if n.id != note_id]
        self._changed()

    def recurring_notes_for_task(self, task_id: str, *, include_archived: bool = False) -> list[RecurringNote]:
        return [
            n for n in self.db.recurring_notes
            if n.task_id == task_id and (include_archived or not n.archived)
        ]

    def all_recurring_notes(self, *, include_archived: bool = False) -> list[RecurringNote]:
        """Every standing commitment across every task — the Recurring tab's
        whole list, rather than one task's slice of it."""
        return [n for n in self.db.recurring_notes if include_archived or not n.archived]

    def recurring_notes_due(self, task_id: str, date: ISODate) -> list[RecurringNote]:
        """Which of ``task_id``'s recurring notes fall on ``date``, minus
        whichever of those have been dismissed just for this one day (see
        ``skip_recurring_note``). Nothing is due once the host task is
        archived or its project has been closed — either stops every note
        on it coming round, the same as it always stopped a task's own
        repeat rule."""
        task = self.db.task(task_id)
        if task is None or task.archived:
            return []
        project = self.db.project(task.project_id)
        if project is None or project.closed:
            return []
        skipped = self._skipped_note_ids(date)
        return [
            n for n in self.db.recurring_notes
            if n.task_id == task_id and not n.archived and n.id not in skipped
            and recurrence.occurs_on(n.recurrence, date)
        ]

    def tasks_with_notes_due(self, dates: list[ISODate]) -> set[str]:
        """Tasks a recurring note brings into any of ``dates``."""
        return {
            task.id for task in self.db.tasks
            if any(self.recurring_notes_due(task.id, d) for d in dates)
        }

    def _skipped_note_ids(self, date: ISODate) -> set[str]:
        return {s.note_id for s in self.db.skipped_notes if s.date == date}

    @_tracked
    def skip_recurring_note(self, note_id: str, date: ISODate) -> None:
        """Dismisses one occurrence of a recurring note for one day.

        Entirely separate from whatever entry it may have already produced:
        this only stops the placeholder coming back once that entry's gone
        (see ``delete_entry``) or is dismissed before ever being logged —
        it never touches the note's own schedule or any entry logged
        against it, on this day or any other.
        """
        if note_id in self._skipped_note_ids(date):
            return
        self.db.skipped_notes.append(SkippedNote(note_id=note_id, date=date))
        self._changed()

    # -- time entries -------------------------------------------------------

    @_tracked
    def add_entry(
        self, task_id: str, date: ISODate, minutes: int, note: str = "",
        recurring_note_id: str = "", internal_note: str = "",
    ) -> Entry | None:
        """Logs a new entry. ``minutes`` of exactly 0 is a deliberate,
        allowed value — "+ log another entry" starts one that way, left for
        its own Actual Hours picker to fill in rather than guessing a
        duration for it — only negative is rejected outright.

        Returns the new ``Entry`` (``None`` on the rejected-negative no-op)
        so a caller — "+ log another entry" wanting to focus its note field,
        say — can single it out after the refresh it triggers, rather than
        guessing which of possibly several same-task entries just appeared.
        """
        if minutes < 0:
            return None
        entry = Entry(
            task_id=task_id, date=date, minutes=minutes, note=note,
            source="manual", recurring_note_id=recurring_note_id,
            internal_note=internal_note,
        )
        self.db.entries.append(entry)
        self._changed()
        return entry

    @_tracked
    def adjust_entry_minutes(self, entry_id: str, delta_minutes: int) -> None:
        """Nudges one specific entry, used by a Day row's own +/- chips once
        a task has entries of its own to tell apart.

        Floors at zero rather than removing the entry once nudged down that
        far — it stays on the day at 0m, same as any other figure emptied
        out, rather than vanishing outright; an actual deletion is the
        delete chip's job, with its own confirmation."""
        entry = next((e for e in self.db.entries if e.id == entry_id), None)
        if entry is None:
            return
        entry.minutes = max(0, entry.minutes + delta_minutes)
        self._changed()

    @_tracked
    def adjust_entry(
        self, task_id: str, date: ISODate, delta_minutes: int,
        note: str = "", recurring_note_id: str = "", internal_note: str = "",
    ) -> None:
        """Used by the +/- buttons; tops up one manual entry rather than
        littering the day with a separate entry per click.

        Scoped to ``recurring_note_id`` (blank for an ordinary, one-off
        placeholder) so nudging one recurring note due today never tops up
        a different one, or a plain entry, that happens to share the task.

        Floors at zero rather than removing the entry — see
        ``adjust_entry_minutes``, which this mirrors once something exists
        to nudge.
        """
        on_day = [
            e for e in self.db.entries
            if e.task_id == task_id and e.date == date and e.recurring_note_id == recurring_note_id
        ]
        existing = next((e for e in on_day if e.source == "manual"), None)
        if existing is None and delta_minutes < 0 and on_day:
            # Subtracting still has to come off something.
            existing = on_day[-1]

        if existing is None:
            if delta_minutes > 0:
                self.add_entry(task_id, date, delta_minutes, note, recurring_note_id, internal_note)
            return

        existing.minutes = max(0, existing.minutes + delta_minutes)
        self._changed()

    @_tracked
    def update_entry(self, entry_id: str, **changes) -> None:
        entry = next((e for e in self.db.entries if e.id == entry_id), None)
        if entry is None:
            return
        for key, value in changes.items():
            setattr(entry, key, value)
        self._changed()

    @_tracked
    def delete_entry(self, entry_id: str) -> None:
        """Removes a logged entry.

        If it came from a recurring note, deleting it also dismisses that
        one occurrence (see ``skip_recurring_note``) — otherwise the note's
        placeholder would come straight back the moment the entry logged
        against it disappears, undoing the very thing the deletion was for.
        """
        entry = next((e for e in self.db.entries if e.id == entry_id), None)
        if entry is None:
            return
        self.db.entries = [e for e in self.db.entries if e.id != entry_id]
        if entry.recurring_note_id:
            self.skip_recurring_note(entry.recurring_note_id, entry.date)
        self._changed()

    # -- the timer ----------------------------------------------------------

    @_tracked
    def start_timer(self, task_id: str, note: str = "", recurring_note_id: str = "") -> None:
        if self.db.active_timer:
            self.stop_timer()
        self.db.active_timer = ActiveTimer(
            task_id=task_id, started_at=time.time(), note=note, recurring_note_id=recurring_note_id
        )
        self._changed()

    def set_timer_note(self, note: str) -> None:
        if self.db.active_timer:
            self.db.active_timer.note = note
            self.save()

    @_tracked
    def stop_timer(self) -> None:
        timer = self.db.active_timer
        if timer is None:
            return
        minutes = round((time.time() - timer.started_at) / 60)
        self.db.active_timer = None
        if minutes >= 1:
            # The entry belongs to the day the timer started on, not the day it
            # happened to be stopped on.
            started = dt.date.fromtimestamp(timer.started_at)
            template = (
                self.db.recurring_note(timer.recurring_note_id)
                if timer.recurring_note_id else None
            )
            self.db.entries.append(
                Entry(
                    task_id=timer.task_id,
                    date=to_iso(started),
                    minutes=minutes,
                    note=timer.note,
                    source="timer",
                    recurring_note_id=timer.recurring_note_id,
                    internal_note=template.internal_note if template else "",
                )
            )
        self._changed()

    @_tracked
    def discard_timer(self) -> None:
        self.db.active_timer = None
        self._changed()

    def most_recent_task_id(self) -> str | None:
        if self.db.entries:
            return self.db.entries[-1].task_id
        open_task = next(
            (
                t for t in self.db.tasks
                if not t.archived and not self._project_closed(t.project_id)
            ),
            None,
        )
        return open_task.id if open_task else None

    def _project_closed(self, project_id: str) -> bool:
        project = self.db.project(project_id)
        return project is not None and project.closed

    # -- misc ---------------------------------------------------------------

    @_tracked
    def set_day_note(self, date: ISODate, text: str) -> None:
        self.db.day_notes[date] = text
        self._changed()

    def patch_settings(self, **changes) -> None:
        # Only notify on a real change. Tk widgets fire their callback when you
        # set their initial value, which would otherwise rebuild the view that
        # is still being built.
        touched = False
        for key, value in changes.items():
            if getattr(self.db.settings, key) != value:
                setattr(self.db.settings, key, value)
                touched = True
        if touched:
            self._changed()

    # -- queries -------------------------------------------------------------

    def context(self, task: Task) -> TaskContext | None:
        project = self.db.project(task.project_id)
        if project is None:
            return None
        client = self.db.client(project.client_id)
        if client is None:
            return None
        phase = self.db.task(task.parent_id) if task.parent_id else None
        return TaskContext(task=task, project=project, client=client, phase=phase)

    def active_contexts(self) -> list[TaskContext]:
        # A Phase is never its own loggable row — only its leaf children
        # (and ordinary flat tasks) ever show up where time gets picked.
        contexts = [
            self.context(t) for t in self.db.tasks if not t.archived and not self.is_phase(t)
        ]
        found = [c for c in contexts if c is not None]
        return sorted(
            found,
            key=lambda c: (
                c.client.is_internal,
                c.client.name.lower(),
                c.project.name.lower(),
                c.task.title.lower(),
            ),
        )

    def logged_minutes(self, task_id: str, date: ISODate) -> int:
        return sum(
            e.minutes for e in self.db.entries if e.task_id == task_id and e.date == date
        )

    def planned_minutes(self, task_id: str, date: ISODate) -> int:
        """What's set aside for a task on a day.

        A pinned plan wins, including a zero one, which is how a single
        occurrence gets skipped. Otherwise every recurring note due that day
        adds up to fill it in — several standing commitments sharing one
        task is exactly what recurring notes are for, so their minutes are
        meant to combine.
        """
        pinned = next(
            (p for p in self.db.plans if p.task_id == task_id and p.date == date), None
        )
        if pinned is not None:
            return pinned.minutes
        return sum(n.minutes for n in self.recurring_notes_due(task_id, date))

    def planned_minutes_on(self, date: ISODate) -> int:
        """Everything set aside for a day, pinned plans and repeats together."""
        return bookings.planned_on(self.db, date)

    def entries_for(self, task_id: str, date: ISODate) -> list[Entry]:
        return [e for e in self.db.entries if e.task_id == task_id and e.date == date]

    def day_totals(self, date: ISODate) -> tuple[int, int]:
        """Returns ``(total, billable)`` minutes for the day."""
        total = billable = 0
        for entry in self.db.entries:
            if entry.date != date:
                continue
            total += entry.minutes
            task = self.db.task(entry.task_id)
            context = self.context(task) if task else None
            if context and context.billable:
                billable += entry.minutes
        return total, billable

    def range_totals(self, dates: list[ISODate]) -> tuple[int, int]:
        totals = [self.day_totals(d) for d in dates]
        return sum(t[0] for t in totals), sum(t[1] for t in totals)

    def contexts_for_date(self, date: ISODate) -> list[TaskContext]:
        """Tasks worth showing on a day: planned for it, due, or worked on."""
        relevant = {p.task_id for p in self.db.plans if p.date == date}
        relevant |= {e.task_id for e in self.db.entries if e.date == date}
        relevant |= self.tasks_with_notes_due([date])
        timer = self.db.active_timer
        if timer and to_iso(dt.date.fromtimestamp(timer.started_at)) == date:
            relevant.add(timer.task_id)
        return [c for c in self.active_contexts() if c.task.id in relevant]

    def day_rows(self, date: ISODate) -> list[DayRow]:
        """The Day tab's rows for ``date``: one per logged ``Entry``, one
        placeholder per recurring note due and not yet logged, or — failing
        both — one placeholder standing in for the task's own ``Plan``.
        Ungrouped and in each task's natural order — see
        :meth:`grouped_day_rows` for what the current view mode does with
        this.

        A pinned ``Plan`` takes priority over recurring notes entirely, same
        as it already does over a task's own repeat rule in
        :meth:`planned_minutes`: typing a figure or note directly onto the
        task is a deliberate, one-off override for the day.

        Planned Hours is day-level only for the placeholder rows below —
        once an ``Entry`` exists, its row carries that entry's own
        ``plan_minutes`` instead, independent of every other entry logged
        against the same task on the same day.
        """
        rows: list[DayRow] = []
        for context in self.contexts_for_date(date):
            task_id = context.task.id
            plan = next(
                (p for p in self.db.plans if p.task_id == task_id and p.date == date), None
            )
            shared = dict(
                plan_note=plan.note if plan else "",
                plan_minutes=self.planned_minutes(task_id, date),
            )
            entries = self.entries_for(task_id, date)

            pending_notes: list[RecurringNote] = []
            if plan is None:
                logged = {e.recurring_note_id for e in entries if e.recurring_note_id}
                pending_notes = [
                    n for n in self.recurring_notes_due(task_id, date) if n.id not in logged
                ]

            produced = False
            for index, entry in enumerate(entries):
                rows.append(
                    DayRow(
                        id=entry.id, context=context, entry=entry,
                        is_first_for_task=index == 0,
                        # Its own Planned Hours, not the task's shared
                        # day-level ``Plan`` — see ``Entry.plan_minutes``.
                        plan_minutes=entry.plan_minutes,
                    )
                )
                produced = True

            for index, note in enumerate(pending_notes):
                rows.append(
                    DayRow(
                        id=f"note:{note.id}", context=context, entry=None,
                        is_first_for_task=not produced and index == 0,
                        plan_note=note.note, plan_minutes=note.minutes,
                        recurring_note_id=note.id, plan_internal_note=note.internal_note,
                    )
                )
                produced = True

            if not produced:
                rows.append(
                    DayRow(id=f"task:{task_id}", context=context, entry=None,
                           is_first_for_task=True, **shared)
                )
        return rows

    def grouped_day_rows(self, date: ISODate) -> list[tuple[str, list[DayRow]]]:
        """:meth:`day_rows`, arranged per ``Settings.day_grouping``.

        The saved drag order (see ``set_day_order``) is applied first,
        regardless of mode — an id missing from it (typically a brand new
        row) sorts to the end. Manual mode is just that order shown as a
        single, unheaded group; every other mode still groups rows under a
        heading, but each heading's own rows keep whatever relative order
        dragging last gave them, rather than always reverting to each
        task's natural entry order.
        """
        rows = self.day_rows(date)
        order = self.db.day_order.get(date, [])
        position = {row_id: index for index, row_id in enumerate(order)}
        rows = sorted(rows, key=lambda r: position.get(r.id, len(order)))
        mode = self.db.settings.day_grouping

        if mode == "manual":
            return [("", rows)]

        def key_for(row: DayRow) -> str:
            if mode == "project":
                return row.context.heading
            if mode == "phase":
                return f"{row.context.heading} \u2014 {row.context.phase_title or row.context.task.title}"
            return f"{row.context.heading} \u2014 {row.context.display_title}"

        groups: dict[str, list[DayRow]] = {}
        for row in rows:
            groups.setdefault(key_for(row), []).append(row)
        return list(groups.items())

    def live_minutes(self, task_id: str, date: ISODate) -> float:
        """Minutes the running timer has accrued for this task today, if any."""
        timer = self.db.active_timer
        if not timer or timer.task_id != task_id:
            return 0.0
        if to_iso(dt.date.fromtimestamp(timer.started_at)) != date:
            return 0.0
        return max(0.0, (time.time() - timer.started_at) / 60)
