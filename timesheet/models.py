"""The data model: Client -> Project (K-code) -> Task, plus plans and entries.

Everything serialises to a single JSON file. Minutes are always stored exactly;
rounding is applied only when exporting, so the underlying record stays honest.
"""

from __future__ import annotations

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
    id: str = field(default_factory=new_id)


@dataclass
class Task:
    project_id: str
    title: str
    billable: bool = True
    done: bool = False
    archived: bool = False
    id: str = field(default_factory=new_id)


@dataclass
class Plan:
    """Minutes you intend to spend on a task on a given day."""

    task_id: str
    date: ISODate
    minutes: int


@dataclass
class Entry:
    """Minutes actually spent, from the timer or typed in."""

    task_id: str
    date: ISODate
    minutes: int
    note: str = ""
    source: str = "manual"  # "manual" | "timer"
    id: str = field(default_factory=new_id)


@dataclass
class ActiveTimer:
    task_id: str
    #: Unix seconds, so it survives a restart mid-task.
    started_at: float
    note: str = ""


@dataclass
class Settings:
    always_on_top: bool = True
    opacity: float = 1.0
    #: Increment used when exporting. 0 reports exact minutes.
    rounding_minutes: int = 15
    daily_target_minutes: int = 450
    show_weekend: bool = False


@dataclass
class Database:
    version: int = SCHEMA_VERSION
    clients: list[Client] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    plans: list[Plan] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    #: Free text per day, for context that isn't tied to a single task.
    day_notes: dict[ISODate, str] = field(default_factory=dict)
    active_timer: ActiveTimer | None = None
    settings: Settings = field(default_factory=Settings)

    # -- lookups ---------------------------------------------------------

    def client(self, client_id: str) -> Client | None:
        return next((c for c in self.clients if c.id == client_id), None)

    def project(self, project_id: str) -> Project | None:
        return next((p for p in self.projects if p.id == project_id), None)

    def task(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

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

        settings_raw = raw.get("settings") or {}
        settings = Settings(
            **{k: v for k, v in settings_raw.items() if k in Settings.__dataclass_fields__}
        )

        timer_raw = raw.get("active_timer")
        timer = ActiveTimer(**timer_raw) if timer_raw else None

        return Database(
            version=SCHEMA_VERSION,
            clients=build(Client, raw.get("clients")),
            projects=build(Project, raw.get("projects")),
            tasks=build(Task, raw.get("tasks")),
            plans=build(Plan, raw.get("plans")),
            entries=build(Entry, raw.get("entries")),
            day_notes=raw.get("day_notes") or {},
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
