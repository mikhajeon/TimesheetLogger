"""The Setup tab: clients, projects, K-codes, and the task/phase tree.

Split out of what used to be the Setup tab (now Preferences) because this list
is the one meant to grow — a handful of clients can easily carry a dozen
projects between them, and that no longer belongs beside window chrome and
shortcut toggles.

Tasks live here too, now that the Day tab no longer creates them on the fly —
see ``timesheet/ui/today.py``'s module docstring for why. A top-level task
that gains a child becomes a "Phase" — a computed label, never stored — and
nesting stops there: a child never gets its own "+ Add sub-task".

"+ Add sub-task" is offered on every top-level task regardless of whether it
already has logged time, a plan, or a recurring note directly on it — see
``Store.add_subtask``, which relocates all of that onto the new child in the
same step rather than refusing the child over it (a task becomes a Phase,
and stops being loggable itself, the instant it gains one — see
``Store.active_contexts`` — so leaving history where it was would make it
quietly vanish instead). "Move..." on any task row does the same relocation
by hand, for consolidating two tasks' history or fixing time logged against
the wrong one.

A leaf task's own recurring notes — standing commitments on their own
schedule, sharing the task's billing bucket — are managed on the Recurring
tab instead; see ``timesheet/ui/recurring.py`` and ``models.RecurringNote``.
"""

from __future__ import annotations

import tkinter as tk

from ..models import Client, Project, Task
from ..store import Store
from . import icons, theme
from .scroll import ScrollFrame


class ProjectsView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.frame = theme.frame(parent)
        self._show_archived = tk.BooleanVar(value=False)
        #: Client and project ids armed for delete, waiting on a second
        #: click. One set for both, since their ids never collide.
        self._confirm_delete: set[str] = set()
        #: Why a delete was refused, by id, shown until the next attempt.
        self._delete_errors: dict[str, str] = {}
        #: The one task, if any, whose "Move..." picker is currently open.
        self._moving_task_id: str | None = None
        #: The destination last chosen in that picker, held across the
        #: rebuild its own dropdown triggers — same recipe as the Day tab's.
        self._move_target_id: str | None = None

        self.scroll = ScrollFrame(self.frame)
        self.scroll.pack(fill="both", expand=True, padx=8, pady=8)

    def refresh(self) -> None:
        theme.commit_focused_entry(self.frame)
        self.scroll.clear()
        self._clients(self.scroll.body)

    # -- sections -----------------------------------------------------------

    def _section(self, parent: tk.Misc, title: str) -> tk.Frame:
        theme.label(parent, title.upper(), fg=theme.MUTED, font=theme.FONT_HEAD).pack(
            fill="x", pady=(12, 4)
        )
        box = theme.frame(parent)
        box.pack(fill="x")
        return box

    def _clients(self, parent: tk.Misc) -> None:
        box = self._section(parent, "Clients & projects")

        for client in self.store.db.clients:
            if client.archived and not self._show_archived.get():
                continue
            self._client_row(box, client)
            self._client_projects(box, client)

        row = theme.frame(box)
        row.pack(fill="x", pady=(8, 0))
        new_name = theme.entry(row, font=theme.FONT_SMALL)
        read_name = theme.add_placeholder(new_name, "New client")
        new_name.pack(side="left", fill="x", expand=True)

        def add() -> None:
            if read_name().strip():
                self.store.add_client(read_name().strip())
                new_name.delete(0, "end")

        icons.chip(
            row, "confirm", add, fallback="\u2713",
            fill=theme.BG, text_fill=theme.BILLABLE, size=24, radius=6,
        ).pack(side="right", padx=(4, 0))
        new_name.bind("<Return>", lambda _e: add())
        theme.bind_tree(row, "<Control-Return>", lambda _e: add())

        theme.checkbox(
            box, "Show archived", self._show_archived, self.refresh
        ).pack(fill="x", pady=(4, 0))

    def _client_row(self, parent: tk.Misc, client: Client) -> None:
        row = theme.frame(parent)
        row.pack(fill="x", pady=(6, 1))

        name = theme.entry(row, font=theme.FONT_SMALL)
        name.insert(0, client.name)
        name.pack(side="left", fill="x", expand=True)
        name.bind(
            "<FocusOut>",
            lambda _e, cid=client.id, w=name: self.store.update_client(cid, name=w.get()),
        )

        internal = tk.BooleanVar(value=client.is_internal)
        theme.checkbox(
            row,
            "Internal",
            internal,
            lambda cid=client.id, v=internal: self.store.update_client(cid, is_internal=v.get()),
        ).pack(side="left", padx=4)

        self._delete_chip(row, client.id, self.store.delete_client).pack(side="right")
        theme.button(
            row,
            "Restore" if client.archived else "Archive",
            lambda cid=client.id, a=client.archived: self.store.update_client(cid, archived=not a),
            bg=theme.BG,
            fg=theme.MUTED,
            font=theme.FONT_TINY,
        ).pack(side="right", padx=(0, 4))

        error = self._delete_errors.get(client.id)
        if error:
            theme.label(
                parent, f"Can't delete \u2014 {error}. Archive it instead.",
                fg=theme.DANGER, font=theme.FONT_TINY,
            ).pack(fill="x", pady=(0, 2))

    def _client_projects(self, parent: tk.Misc, client: Client) -> None:
        """This client's projects and its own add row, indented beneath it."""
        wrapper = theme.frame(parent)
        wrapper.pack(fill="x", padx=(6, 0))
        theme.vrule(wrapper).pack(side="left", fill="y")

        box = theme.frame(wrapper)
        box.pack(side="left", fill="x", expand=True, padx=(7, 0))

        for project in self.store.db.projects:
            if project.client_id != client.id:
                continue
            if project.archived and not self._show_archived.get():
                continue
            self._project_row(box, project)
            self._project_tasks(box, project)

        self._add_project_row(box, client)

    def _project_row(self, parent: tk.Misc, project: Project) -> None:
        row = theme.frame(parent)
        row.pack(fill="x", pady=1)

        code = theme.entry(row, width=8, font=theme.FONT_MONO)
        code.insert(0, project.k_code)
        code.pack(side="left")
        code.bind(
            "<FocusOut>",
            lambda _e, pid=project.id, w=code: self.store.update_project(pid, k_code=w.get()),
        )

        name = theme.entry(row, font=theme.FONT_SMALL)
        name.insert(0, project.name)
        name.pack(side="left", fill="x", expand=True, padx=3)
        name.bind(
            "<FocusOut>",
            lambda _e, pid=project.id, w=name: self.store.update_project(pid, name=w.get()),
        )

        billable = tk.BooleanVar(value=project.default_billable)
        theme.checkbox(
            row,
            "Billable",
            billable,
            lambda pid=project.id, v=billable: self.store.update_project(
                pid, default_billable=v.get()
            ),
        ).pack(side="left")

        self._delete_chip(row, project.id, self.store.delete_project).pack(side="right")
        theme.button(
            row,
            "Restore" if project.archived else "Archive",
            lambda pid=project.id, a=project.archived: self.store.update_project(
                pid, archived=not a
            ),
            bg=theme.BG,
            fg=theme.MUTED,
            font=theme.FONT_TINY,
        ).pack(side="right", padx=(0, 4))
        theme.button(
            row,
            "Reopen" if project.closed else "Close",
            lambda p=project: self._toggle_closed(p),
            bg=theme.BG,
            fg=theme.MUTED,
            font=theme.FONT_TINY,
        ).pack(side="right", padx=(0, 4))

        error = self._delete_errors.get(project.id)
        if error:
            theme.label(
                parent, f"Can't delete \u2014 {error}. Archive it instead.",
                fg=theme.DANGER, font=theme.FONT_TINY,
            ).pack(fill="x", pady=(0, 2))

    def _toggle_closed(self, project: Project) -> None:
        """Reopening is a plain, one-click toggle — closing gets a
        confirmation modal first, since it cascades onto every task under
        the K-code (see ``Project.closed``), where reopening just undoes
        that and so needs no guard."""
        if project.closed:
            self.store.update_project(project.id, closed=False)
            return
        theme.confirm(
            self.overlay.shell,
            f"Close {project.k_code} \u2014 {project.name}? Its tasks drop out of "
            "\u201cAll open tasks\u201d and the recurring-note picker, and stop generating "
            "new recurring notes. Nothing already logged is touched, and it can be "
            "reopened any time.",
            lambda pid=project.id: self.store.update_project(pid, closed=True),
            confirm_label="Close",
        )

    def _project_tasks(self, parent: tk.Misc, project: Project) -> None:
        """This project's top-level tasks, indented beneath it like the
        project itself sits beneath its client."""
        wrapper = theme.frame(parent)
        wrapper.pack(fill="x", padx=(6, 0))
        theme.vrule(wrapper).pack(side="left", fill="y")

        box = theme.frame(wrapper)
        box.pack(side="left", fill="x", expand=True, padx=(7, 0))

        for task in self.store.db.tasks:
            if task.project_id != project.id or task.parent_id:
                continue
            if task.archived and not self._show_archived.get():
                continue
            self._task_row(box, task, indent=0)

        self._add_task_row(box, project)

    def _task_row(self, parent: tk.Misc, task: Task, *, indent: int) -> None:
        """One task row. A top-level task (``indent=0``) also renders its own
        children beneath it and, while eligible, a "+ Add sub-task" row —
        a child task never gets either, since nesting stops at one level."""
        phase = self.store.is_phase(task)
        host = parent
        if indent:
            wrapper = theme.frame(parent)
            wrapper.pack(fill="x", padx=(6, 0))
            theme.vrule(wrapper).pack(side="left", fill="y")
            host = theme.frame(wrapper)
            host.pack(side="left", fill="x", expand=True, padx=(7, 0))

        row = theme.frame(host)
        row.pack(fill="x", pady=1)

        if phase:
            theme.label(row, "PHASE", fg=theme.ACCENT, font=theme.FONT_TINY).pack(
                side="left", padx=(0, 4)
            )

        name = theme.entry(row, font=theme.FONT_SMALL)
        name.insert(0, task.title)
        name.pack(side="left", fill="x", expand=True)
        name.bind(
            "<FocusOut>",
            lambda _e, tid=task.id, w=name: self.store.update_task(tid, title=w.get()),
        )

        if phase:
            # Never loggable itself once it has children — see the module
            # docstring — so its own Billable flag has nothing left to gate.
            theme.label(
                row, "billed via its tasks", fg=theme.MUTED, font=theme.FONT_TINY
            ).pack(side="left", padx=4)
        else:
            billable = tk.BooleanVar(value=task.billable)
            theme.checkbox(
                row,
                "Billable",
                billable,
                lambda tid=task.id, v=billable: self.store.update_task(tid, billable=v.get()),
            ).pack(side="left", padx=4)

        self._delete_chip(row, task.id, self.store.delete_task).pack(side="right")
        theme.button(
            row,
            "Restore" if task.archived else "Archive",
            lambda tid=task.id, a=task.archived: self.store.update_task(tid, archived=not a),
            bg=theme.BG,
            fg=theme.MUTED,
            font=theme.FONT_TINY,
        ).pack(side="right", padx=(0, 4))

        if self.store.task_has_own_history(task.id):
            theme.button(
                row,
                "Move\u2026",
                lambda tid=task.id: self._toggle_move(tid),
                bg=theme.BG,
                fg=theme.MUTED,
                font=theme.FONT_TINY,
            ).pack(side="right", padx=(0, 4))

        error = self._delete_errors.get(task.id)
        if error:
            theme.label(
                host, f"Can't delete \u2014 {error}. Archive it instead.",
                fg=theme.DANGER, font=theme.FONT_TINY,
            ).pack(fill="x", pady=(0, 2))

        if self._moving_task_id == task.id:
            self._move_entries_row(host, task)

        if indent == 0:
            for child in self.store.db.tasks:
                if child.parent_id != task.id:
                    continue
                if child.archived and not self._show_archived.get():
                    continue
                self._task_row(host, child, indent=1)
            if self.store.can_add_subtask(task.id):
                self._add_subtask_row(host, task)

    def _add_task_row(self, parent: tk.Misc, project: Project) -> None:
        row = theme.frame(parent)
        row.pack(fill="x", pady=(2, 4))

        name = theme.entry(row, font=theme.FONT_SMALL)
        read_name = theme.add_placeholder(name, "New task")
        name.pack(side="left", fill="x", expand=True)

        billable = tk.BooleanVar(value=project.default_billable)
        if project.default_billable:
            theme.checkbox(row, "Billable", billable, lambda: None).pack(side="left", padx=4)

        def add() -> None:
            title = read_name().strip()
            if not title:
                return
            self.store.add_task(project.id, title, billable.get() and project.default_billable)
            name.delete(0, "end")

        icons.chip(
            row, "confirm", add, fallback="\u2713",
            fill=theme.BG, text_fill=theme.BILLABLE, size=24, radius=6,
        ).pack(side="right", padx=(4, 0))
        name.bind("<Return>", lambda _e: add())
        theme.bind_tree(row, "<Control-Return>", lambda _e: add())

    def _add_subtask_row(self, parent: tk.Misc, task: Task) -> None:
        row = theme.frame(parent)
        row.pack(fill="x", pady=(2, 4))

        name = theme.entry(row, font=theme.FONT_SMALL)
        read_name = theme.add_placeholder(name, "+ Add sub-task")
        name.pack(side="left", fill="x", expand=True)

        billable = tk.BooleanVar(value=task.billable)
        theme.checkbox(row, "Billable", billable, lambda: None).pack(side="left", padx=4)

        def add() -> None:
            title = read_name().strip()
            if not title:
                return
            _child, problem = self.store.add_subtask(task.id, title, billable.get())
            if not problem:
                name.delete(0, "end")

        icons.chip(
            row, "confirm", add, fallback="\u2713",
            fill=theme.BG, text_fill=theme.BILLABLE, size=22, radius=6,
        ).pack(side="right", padx=(4, 0))
        name.bind("<Return>", lambda _e: add())
        theme.bind_tree(row, "<Control-Return>", lambda _e: add())

    def _toggle_move(self, task_id: str) -> None:
        self._moving_task_id = None if self._moving_task_id == task_id else task_id
        self.refresh()

    def _move_entries_row(self, parent: tk.Misc, task: Task) -> None:
        """Lets this task's logged time, plans and recurring notes move onto
        another task in one step — see ``Store.relocate_entries``. Offered
        by hand here for consolidating two tasks' history, or fixing time
        logged against the wrong one; ``add_subtask`` does the same thing
        automatically for a brand new child.
        """
        targets = [c for c in self.store.active_contexts() if c.task.id != task.id]
        row = theme.frame(parent)
        row.pack(fill="x", pady=(0, 4))
        if not targets:
            theme.label(
                row, "No other task to move it onto yet.", fg=theme.MUTED, font=theme.FONT_TINY,
            ).pack(side="left")
            icons.chip(
                row, "deny", lambda: self._toggle_move(task.id), fallback="\u00d7",
                fill=theme.BG, text_fill=theme.DANGER, size=22, radius=6,
            ).pack(side="right")
            return

        theme.label(
            row, "Move its history to", fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(side="left", padx=(0, 4))

        labels = {
            f"{c.project.k_code} \u00b7 {c.project.name} \u00b7 {c.display_title}": c.task.id
            for c in targets
        }
        by_id = {tid: label for label, tid in labels.items()}
        if self._move_target_id not in by_id:
            self._move_target_id = targets[0].task.id
        choice = tk.StringVar(value=by_id[self._move_target_id])
        theme.option_menu(
            row, choice, list(labels),
            command=lambda value: setattr(
                self, "_move_target_id", labels.get(value, self._move_target_id)
            ),
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        icons.chip(
            row, "confirm", lambda tid=task.id: self._confirm_move(tid), fallback="\u2713",
            fill=theme.BG, text_fill=theme.BILLABLE, size=22, radius=6,
        ).pack(side="right", padx=(4, 0))
        icons.chip(
            row, "deny", lambda: self._toggle_move(task.id), fallback="\u00d7",
            fill=theme.BG, text_fill=theme.DANGER, size=22, radius=6,
        ).pack(side="right")

    def _confirm_move(self, task_id: str) -> None:
        """Relocates ``task_id``'s history onto the chosen destination and
        closes the picker — the "Move..." panel's confirm chip, named so a
        test can call it directly rather than simulating a click on a
        ``tk.Canvas`` row that may be scrolled out of its mapped viewport."""
        if self._move_target_id:
            self.store.relocate_entries(task_id, self._move_target_id)
        self._moving_task_id = None
        self.refresh()

    def _add_project_row(self, parent: tk.Misc, client: Client) -> None:
        row = theme.frame(parent)
        row.pack(fill="x", pady=(2, 4))

        code = theme.entry(row, width=8, font=theme.FONT_MONO)
        read_code = theme.add_placeholder(code, "K-code")
        code.pack(side="left")

        name = theme.entry(row, font=theme.FONT_SMALL)
        read_name = theme.add_placeholder(name, f"New project for {client.name}")
        name.pack(side="left", fill="x", expand=True, padx=3)

        def add() -> None:
            title = read_name().strip()
            if not title:
                return
            # The client is implied by where the row sits, and an internal
            # client's work is non-billable unless said otherwise.
            self.store.add_project(
                client.id,
                title,
                read_code().strip() or "NCC",
                default_billable=not client.is_internal,
            )

        icons.chip(
            row, "confirm", add, fallback="\u2713",
            fill=theme.BG, text_fill=theme.BILLABLE, size=24, radius=6,
        ).pack(side="right", padx=(4, 0))
        for field in (code, name):
            field.bind("<Return>", lambda _e: add())
        theme.bind_tree(row, "<Control-Return>", lambda _e: add())

    # -- delete ---------------------------------------------------------------

    def _delete_chip(self, parent: tk.Misc, item_id: str, action) -> tk.Widget:
        """A trash chip that arms on first click and deletes on the second.

        Two steps rather than a popup: the chip itself becomes the question,
        the same recipe as the task delete chip on the Day tab.
        """
        if item_id in self._confirm_delete:
            wrapper = theme.frame(parent)
            icons.chip(
                wrapper, "confirm", lambda: self._confirm_delete_chip(item_id, action),
                fallback="\u2713", fill=theme.BG, text_fill=theme.BILLABLE, size=22, radius=6,
            ).pack(side="left", padx=(0, 3))
            icons.chip(
                wrapper, "deny", lambda: self._disarm_delete(item_id),
                fallback="\u00d7", fill=theme.BG, text_fill=theme.DANGER, size=22, radius=6,
            ).pack(side="left")
            return wrapper
        return icons.chip(
            parent, "trash", lambda: self._arm_delete(item_id),
            fallback="\U0001f5d1", fill=theme.BG, text_fill=theme.DANGER,
            size=22, radius=6,
        )

    def _arm_delete(self, item_id: str) -> None:
        self._confirm_delete.add(item_id)
        self.refresh()

    def _disarm_delete(self, item_id: str) -> None:
        self._confirm_delete.discard(item_id)
        self.refresh()

    def _confirm_delete_chip(self, item_id: str, action) -> None:
        problem = action(item_id)
        self._confirm_delete.discard(item_id)
        if problem:
            self._delete_errors[item_id] = problem
        else:
            self._delete_errors.pop(item_id, None)
        self.refresh()
