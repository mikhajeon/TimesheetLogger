"""Collapsible panel for putting a task on a day, shared by Today and Week."""

from __future__ import annotations

import tkinter as tk

from ..dates import ISODate, parse_duration
from ..store import Store
from . import theme

DEFAULT_PLAN_MINUTES = 30


class AddTaskPanel:
    def __init__(self, parent: tk.Misc, store: Store, date_getter, label: str = "this day") -> None:
        self.store = store
        self._date_getter = date_getter
        self._label = label
        self.frame = theme.frame(parent)
        self._open = False

        self._toggle = theme.button(
            self.frame, f"+  Add task to {label}", self.toggle,
            bg=theme.BG, fg=theme.MUTED, pady=6,
        )
        self._toggle.pack(fill="x")

        self.panel = theme.frame(self.frame, bg=theme.BG_RAISED)
        self._build_panel()

    @property
    def date(self) -> ISODate:
        return self._date_getter()

    def toggle(self) -> None:
        self._open = not self._open
        if self._open:
            self.refresh()
            self.panel.pack(fill="x", pady=(4, 0))
            self._toggle.config(text="\u2212  Close")
            self.title_entry.focus_set()
        else:
            self.panel.pack_forget()
            self._toggle.config(text=f"+  Add task to {self._label}")

    def _build_panel(self) -> None:
        pad = {"padx": 9}

        theme.label(
            self.panel, "Add an existing task", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_SMALL
        ).pack(fill="x", pady=(8, 2), **pad)

        self.existing_var = tk.StringVar(value="Choose\u2026")
        self.existing_menu = theme.option_menu(self.panel, self.existing_var, ["Choose\u2026"], self._add_existing)
        self.existing_menu.pack(fill="x", **pad)

        theme.label(
            self.panel, "or create a new one", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_SMALL
        ).pack(fill="x", pady=(10, 2), **pad)

        self.project_var = tk.StringVar()
        self.project_menu = theme.option_menu(self.panel, self.project_var, [""])
        self.project_menu.pack(fill="x", **pad)

        self.title_entry = theme.entry(self.panel)
        self.title_entry.pack(fill="x", pady=(5, 0), **pad)
        self.title_entry.bind("<Return>", lambda _e: self._create())

        row = theme.frame(self.panel, bg=theme.BG_RAISED)
        row.pack(fill="x", pady=6, **pad)

        self.billable_var = tk.BooleanVar(value=True)
        billable = theme.checkbox(row, "Billable", self.billable_var, lambda: None)
        billable.config(bg=theme.BG_RAISED, activebackground=theme.BG_RAISED)
        billable.pack(side="left")

        theme.label(row, "Plan", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_SMALL).pack(
            side="left", padx=(12, 4)
        )
        self.plan_entry = theme.entry(row, width=7)
        self.plan_entry.pack(side="left")

        theme.button(row, "Add", self._create, bg=theme.ACCENT, fg="#0a1020", hover="#7aa2ff").pack(
            side="right"
        )

    def refresh(self) -> None:
        """Repopulates both dropdowns from the current database."""
        planned = {p.task_id for p in self.store.db.plans if p.date == self.date}
        self._existing = [
            c for c in self.store.active_contexts() if not c.task.done and c.task.id not in planned
        ]
        labels = ["Choose\u2026"] + [
            f"{c.client.name} · {c.project.k_code} · {c.task.title}" for c in self._existing
        ]
        self._set_menu(self.existing_menu, self.existing_var, labels, self._add_existing)
        self.existing_var.set("Choose\u2026")

        self._projects = [p for p in self.store.db.projects if not p.archived]
        project_labels = self._project_labels()
        self._set_menu(self.project_menu, self.project_var, project_labels or [""], self._on_project)
        if project_labels:
            self.project_var.set(project_labels[0])
            self._on_project(project_labels[0])

    @staticmethod
    def _set_menu(widget: tk.OptionMenu, variable: tk.StringVar, values: list[str], command) -> None:
        menu = widget["menu"]
        menu.delete(0, "end")
        for value in values:
            menu.add_command(label=value, command=lambda v=value: (variable.set(v), command(v)))

    def _on_project(self, _label: str) -> None:
        project = self._selected_project()
        if project is not None:
            self.billable_var.set(project.default_billable)

    def _selected_project(self):
        label = self.project_var.get()
        for project, candidate in zip(self._projects, self._project_labels()):
            if candidate == label:
                return project
        return self._projects[0] if self._projects else None

    def _project_labels(self) -> list[str]:
        labels = []
        for project in self._projects:
            client = self.store.db.client(project.client_id)
            labels.append(f"{client.name if client else '?'} · {project.name} ({project.k_code})")
        return labels

    def _add_existing(self, label: str) -> None:
        if label == "Choose\u2026":
            return
        for context, candidate in zip(
            self._existing,
            [f"{c.client.name} · {c.project.k_code} · {c.task.title}" for c in self._existing],
        ):
            if candidate == label:
                self.store.set_plan(context.task.id, self.date, DEFAULT_PLAN_MINUTES)
                self.toggle()
                return

    def _create(self) -> None:
        title = self.title_entry.get().strip()
        project = self._selected_project()
        if not title or project is None:
            return
        task = self.store.add_task(project.id, title, self.billable_var.get())
        minutes = parse_duration(self.plan_entry.get()) or DEFAULT_PLAN_MINUTES
        self.store.set_plan(task.id, self.date, minutes)
        self.title_entry.delete(0, "end")
        self.plan_entry.delete(0, "end")
        self.toggle()
