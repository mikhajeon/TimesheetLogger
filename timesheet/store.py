"""Owns the database: mutations, queries, and persistence.

Deliberately free of any GUI toolkit import — observers are plain callables, so
the same store drives whichever front end is on top of it.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .dates import ISODate, to_iso
from .models import (
    ActiveTimer,
    Client,
    Database,
    Entry,
    Plan,
    Project,
    Task,
    seed,
)
import datetime as dt


@dataclass(frozen=True)
class TaskContext:
    """A task together with the project and client it hangs off."""

    task: Task
    project: Project
    client: Client

    @property
    def heading(self) -> str:
        return f"{self.client.name} · {self.project.name} ({self.project.k_code})"


class Store:
    def __init__(self, data_file: Path) -> None:
        self.data_file = data_file
        self.db: Database = seed()
        self._listeners: list[Callable[[], None]] = []

    # -- plumbing --------------------------------------------------------

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _changed(self) -> None:
        self.save()
        for listener in list(self._listeners):
            listener()

    def load(self) -> None:
        if not self.data_file.exists():
            self.db = seed()
            self.save()
            return
        try:
            self.db = Database.from_dict(json.loads(self.data_file.read_text("utf-8")))
        except Exception as error:  # noqa: BLE001 - any bad file lands here
            # A corrupt file is moved aside rather than silently overwritten, so
            # a week of tracking is never lost to one bad write.
            backup = self.data_file.with_suffix(f".corrupt-{int(time.time())}.json")
            shutil.move(str(self.data_file), str(backup))
            print(f"Could not read {self.data_file} ({error}); kept it at {backup}")
            self.db = seed()
            self.save()

    def save(self) -> None:
        """Writes through a temp file so an interrupted save can't truncate the data."""
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.data_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.db.to_dict(), indent=2), "utf-8")
        os.replace(tmp, self.data_file)

    # -- clients and projects --------------------------------------------

    def add_client(self, name: str, is_internal: bool = False) -> Client:
        client = Client(name=name, is_internal=is_internal)
        self.db.clients.append(client)
        self._changed()
        return client

    def update_client(self, client_id: str, **changes) -> None:
        client = self.db.client(client_id)
        if client is None:
            return
        for key, value in changes.items():
            setattr(client, key, value)
        self._changed()

    def add_project(self, client_id: str, name: str, k_code: str, default_billable: bool) -> Project:
        project = Project(
            client_id=client_id, name=name, k_code=k_code, default_billable=default_billable
        )
        self.db.projects.append(project)
        self._changed()
        return project

    def update_project(self, project_id: str, **changes) -> None:
        project = self.db.project(project_id)
        if project is None:
            return
        for key, value in changes.items():
            setattr(project, key, value)
        self._changed()

    # -- tasks -------------------------------------------------------------

    def add_task(self, project_id: str, title: str, billable: bool) -> Task:
        task = Task(project_id=project_id, title=title, billable=billable)
        self.db.tasks.append(task)
        self._changed()
        return task

    def update_task(self, task_id: str, **changes) -> None:
        task = self.db.task(task_id)
        if task is None:
            return
        for key, value in changes.items():
            setattr(task, key, value)
        if task.archived and self.db.active_timer and self.db.active_timer.task_id == task_id:
            self.db.active_timer = None
        self._changed()

    def toggle_done(self, task_id: str) -> None:
        task = self.db.task(task_id)
        if task is not None:
            task.done = not task.done
            self._changed()

    # -- planning ----------------------------------------------------------

    def set_plan(self, task_id: str, date: ISODate, minutes: int) -> None:
        self.db.plans = [
            p for p in self.db.plans if not (p.task_id == task_id and p.date == date)
        ]
        if minutes > 0:
            self.db.plans.append(Plan(task_id=task_id, date=date, minutes=minutes))
        self._changed()

    # -- time entries -------------------------------------------------------

    def add_entry(self, task_id: str, date: ISODate, minutes: int, note: str = "") -> None:
        if minutes <= 0:
            return
        self.db.entries.append(
            Entry(task_id=task_id, date=date, minutes=minutes, note=note, source="manual")
        )
        self._changed()

    def adjust_entry(self, task_id: str, date: ISODate, delta_minutes: int) -> None:
        """Used by the +/- buttons; tops up one manual entry rather than
        littering the day with a separate entry per click."""
        on_day = [e for e in self.db.entries if e.task_id == task_id and e.date == date]
        existing = next((e for e in on_day if e.source == "manual"), None)
        if existing is None and delta_minutes < 0 and on_day:
            # Subtracting still has to come off something.
            existing = on_day[-1]

        if existing is None:
            if delta_minutes > 0:
                self.add_entry(task_id, date, delta_minutes)
            return

        total = existing.minutes + delta_minutes
        if total <= 0:
            self.db.entries.remove(existing)
        else:
            existing.minutes = total
        self._changed()

    def update_entry(self, entry_id: str, **changes) -> None:
        entry = next((e for e in self.db.entries if e.id == entry_id), None)
        if entry is None:
            return
        for key, value in changes.items():
            setattr(entry, key, value)
        self._changed()

    def delete_entry(self, entry_id: str) -> None:
        self.db.entries = [e for e in self.db.entries if e.id != entry_id]
        self._changed()

    # -- the timer ----------------------------------------------------------

    def start_timer(self, task_id: str) -> None:
        if self.db.active_timer:
            self.stop_timer()
        self.db.active_timer = ActiveTimer(task_id=task_id, started_at=time.time())
        self._changed()

    def set_timer_note(self, note: str) -> None:
        if self.db.active_timer:
            self.db.active_timer.note = note
            self.save()

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
            self.db.entries.append(
                Entry(
                    task_id=timer.task_id,
                    date=to_iso(started),
                    minutes=minutes,
                    note=timer.note,
                    source="timer",
                )
            )
        self._changed()

    def discard_timer(self) -> None:
        self.db.active_timer = None
        self._changed()

    def most_recent_task_id(self) -> str | None:
        if self.db.entries:
            return self.db.entries[-1].task_id
        open_task = next((t for t in self.db.tasks if not t.archived and not t.done), None)
        return open_task.id if open_task else None

    # -- misc ---------------------------------------------------------------

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
        return TaskContext(task=task, project=project, client=client)

    def active_contexts(self) -> list[TaskContext]:
        contexts = [self.context(t) for t in self.db.tasks if not t.archived]
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
        return next(
            (p.minutes for p in self.db.plans if p.task_id == task_id and p.date == date), 0
        )

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
            if task and task.billable:
                billable += entry.minutes
        return total, billable

    def range_totals(self, dates: list[ISODate]) -> tuple[int, int]:
        totals = [self.day_totals(d) for d in dates]
        return sum(t[0] for t in totals), sum(t[1] for t in totals)

    def contexts_for_date(self, date: ISODate) -> list[TaskContext]:
        """Tasks worth showing on a day: planned for it, or already worked on."""
        relevant = {p.task_id for p in self.db.plans if p.date == date}
        relevant |= {e.task_id for e in self.db.entries if e.date == date}
        timer = self.db.active_timer
        if timer and to_iso(dt.date.fromtimestamp(timer.started_at)) == date:
            relevant.add(timer.task_id)
        return [c for c in self.active_contexts() if c.task.id in relevant]

    def live_minutes(self, task_id: str, date: ISODate) -> float:
        """Minutes the running timer has accrued for this task today, if any."""
        timer = self.db.active_timer
        if not timer or timer.task_id != task_id:
            return 0.0
        if to_iso(dt.date.fromtimestamp(timer.started_at)) != date:
            return 0.0
        return max(0.0, (time.time() - timer.started_at) / 60)
