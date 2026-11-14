"""The Setup tab: clients, projects, K-codes, and the task/phase tree.

Split out of what used to be the Setup tab (now Preferences) because this list
is the one meant to grow — a handful of clients can easily carry a dozen
projects between them, and that no longer belongs beside window chrome and
shortcut toggles.

Laid out as cards: each client is a card, each of its projects a card inside
it, and a project's tasks are the rows inside that. Every client, project,
task and sub-task has a drag handle (a six-dot grip) to rearrange it among its own
siblings; that order is what the Export tab lists tasks in too (see
``bookings.setup_order``).

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

#: How far into a tall block (a client's or project's card) the pointer has
#: to be before a drag drops *after* it rather than before: the top of the
#: card, not its midpoint, decides — otherwise a long card would only ever
#: be dropped onto from its very middle.
DROP_ZONE = 36


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

        #: Everything that can be dragged this pass, in on-screen order:
        #: ``(kind, id, group, widget)``. ``group`` is what it may be
        #: reordered within (a client's projects, a project's tasks...).
        self._blocks: list[tuple[str, str, object, tk.Widget]] = []
        self._drag: dict | None = None
        self._drop_indicator: tk.Frame | None = None

        self.scroll = ScrollFrame(self.frame)
        self.scroll.pack(fill="both", expand=True, padx=8, pady=8)

    def refresh(self) -> None:
        theme.commit_focused_entry(self.frame)
        self._drag = None
        self._drop_indicator = None
        self._blocks = []
        self.scroll.clear()
        self._clients(self.scroll.body)

    # -- cards ----------------------------------------------------------------

    @staticmethod
    def _card(parent: tk.Misc, fill: str, pady=(0, 8)) -> tk.Frame:
        card = theme.frame(parent, bg=fill, highlightthickness=0)
        theme.round_corners(card, 8, border=theme.LINE)
        card.pack(fill="x", pady=pady)
        return card

    @staticmethod
    def _inner(card: tk.Frame, fill: str, pady=(6, 6)) -> tk.Frame:
        inner = theme.frame(card, bg=fill)
        inner.pack(fill="x", padx=8, pady=pady)
        return inner

    @staticmethod
    def _small_button(parent: tk.Misc, text: str, command, bg: str) -> tk.Button:
        return theme.button(
            parent, text, command, bg=bg, fg=theme.MUTED, font=theme.FONT_TINY,
        )

    # -- sections -----------------------------------------------------------

    def _clients(self, parent: tk.Misc) -> None:
        theme.label(parent, "CLIENTS & PROJECTS", fg=theme.MUTED, font=theme.FONT_HEAD).pack(
            fill="x", pady=(4, 6)
        )
        box = theme.frame(parent)
        box.pack(fill="x")

        for client in self.store.db.clients:
            if client.archived and not self._show_archived.get():
                continue
            self._client_card(box, client)

        row = theme.frame(box)
        row.pack(fill="x", pady=(0, 0))
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
        ).pack(fill="x", pady=(6, 0))

    # -- clients ----------------------------------------------------------------

    def _client_card(self, parent: tk.Misc, client: Client) -> None:
        fill = theme.BG_RAISED
        card = self._card(parent, fill)
        self._blocks.append(("client", client.id, None, card))
        head = self._inner(card, fill, pady=(8, 6))

        self._handle(head, fill, "client", client.id, None, card, (theme.LINE, 1)).pack(
            side="left", padx=(0, 6)
        )
        name = theme.entry(head, font=theme.FONT_BOLD)
        name.insert(0, client.name)
        name.pack(side="left", fill="x", expand=True)
        name.bind(
            "<FocusOut>",
            lambda _e, cid=client.id, w=name: self.store.update_client(cid, name=w.get()),
        )

        internal = tk.BooleanVar(value=client.is_internal)
        theme.checkbox(
            head, "Internal", internal,
            lambda cid=client.id, v=internal: self.store.update_client(cid, is_internal=v.get()),
            bg=fill,
        ).pack(side="left", padx=4)

        self._delete_chip(head, client.id, self.store.delete_client, fill).pack(side="right")
        self._small_button(
            head, "Restore" if client.archived else "Archive",
            lambda cid=client.id, a=client.archived: self.store.update_client(cid, archived=not a),
            fill,
        ).pack(side="right", padx=(0, 4))

        error = self._delete_errors.get(client.id)
        if error:
            theme.label(
                card, f"Can't delete \u2014 {error}. Archive it instead.",
                bg=fill, fg=theme.DANGER, font=theme.FONT_TINY,
            ).pack(fill="x", padx=10, pady=(0, 4))

        body = theme.frame(card, bg=fill)
        body.pack(fill="x", padx=8, pady=(0, 8))
        for project in self.store.db.projects:
            if project.client_id != client.id:
                continue
            if project.archived and not self._show_archived.get():
                continue
            self._project_card(body, project)
        self._add_project_row(body, client, fill)

    # -- projects ---------------------------------------------------------------

    def _project_card(self, parent: tk.Misc, project: Project) -> None:
        fill = theme.BG
        card = self._card(parent, fill, pady=(0, 6))
        self._blocks.append(("project", project.id, project.client_id, card))

        top = self._inner(card, fill, pady=(7, 2))
        self._handle(
            top, fill, "project", project.id, project.client_id, card, (theme.LINE, 1)
        ).pack(side="left", padx=(0, 6))
        code = theme.entry(top, width=8, font=theme.FONT_MONO)
        code.insert(0, project.k_code)
        code.pack(side="left")
        code.bind(
            "<FocusOut>",
            lambda _e, pid=project.id, w=code: self.store.update_project(pid, k_code=w.get()),
        )
        name = theme.entry(top, font=theme.FONT_SMALL)
        name.insert(0, project.name)
        name.pack(side="left", fill="x", expand=True, padx=(4, 0))
        name.bind(
            "<FocusOut>",
            lambda _e, pid=project.id, w=name: self.store.update_project(pid, name=w.get()),
        )
        self._delete_chip(top, project.id, self.store.delete_project, fill).pack(
            side="right", padx=(4, 0)
        )

        options = self._inner(card, fill, pady=(0, 2))
        billable = tk.BooleanVar(value=project.default_billable)
        theme.checkbox(
            options, "Billable", billable,
            lambda pid=project.id, v=billable: self.store.update_project(
                pid, default_billable=v.get()
            ),
            bg=fill,
        ).pack(side="left")
        self._small_button(
            options, "Restore" if project.archived else "Archive",
            lambda pid=project.id, a=project.archived: self.store.update_project(
                pid, archived=not a
            ),
            fill,
        ).pack(side="right")
        self._small_button(
            options, "Reopen" if project.closed else "Close",
            lambda p=project: self._toggle_closed(p), fill,
        ).pack(side="right", padx=(0, 4))

        error = self._delete_errors.get(project.id)
        if error:
            theme.label(
                card, f"Can't delete \u2014 {error}. Archive it instead.",
                bg=fill, fg=theme.DANGER, font=theme.FONT_TINY,
            ).pack(fill="x", padx=10, pady=(0, 2))

        theme.hairline(card).pack(fill="x", padx=8, pady=(2, 0))
        body = theme.frame(card, bg=fill)
        body.pack(fill="x", padx=8, pady=(4, 6))
        for task in self.store.db.tasks:
            if task.project_id != project.id or task.parent_id:
                continue
            if task.archived and not self._show_archived.get():
                continue
            self._task_block(body, task, indent=0, fill=fill)
        self._add_task_row(body, project, fill)

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

    # -- tasks ------------------------------------------------------------------

    def _task_block(self, parent: tk.Misc, task: Task, *, indent: int, fill: str) -> None:
        """A task row, and — for a top-level one — its sub-tasks beneath it
        and, while eligible, a "+ Add sub-task" row. The whole block is what
        a drag moves; a child never gets sub-tasks of its own, since nesting
        stops at one level."""
        block = theme.frame(parent, bg=fill)
        block.pack(fill="x", padx=((14, 0) if indent else (0, 0)))
        group = (task.project_id, task.parent_id)
        self._blocks.append(("task", task.id, group, block))

        phase = self.store.is_phase(task)
        row = theme.frame(block, bg=fill, highlightthickness=0)
        theme.round_corners(row, 6, border=fill)
        row.pack(fill="x", pady=1)
        self._handle(row, fill, "task", task.id, group, row, (fill, 1)).pack(
            side="left", padx=(2, 5)
        )

        if phase:
            theme.label(row, "PHASE", bg=fill, fg=theme.ACCENT, font=theme.FONT_TINY).pack(
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
                row, "billed via its tasks", bg=fill, fg=theme.MUTED, font=theme.FONT_TINY
            ).pack(side="left", padx=4)
        else:
            billable = tk.BooleanVar(value=task.billable)
            theme.checkbox(
                row, "Billable", billable,
                lambda tid=task.id, v=billable: self.store.update_task(tid, billable=v.get()),
                bg=fill,
            ).pack(side="left", padx=4)

        self._delete_chip(row, task.id, self.store.delete_task, fill).pack(side="right")
        self._small_button(
            row, "Restore" if task.archived else "Archive",
            lambda tid=task.id, a=task.archived: self.store.update_task(tid, archived=not a),
            fill,
        ).pack(side="right", padx=(0, 4))

        if self.store.task_has_own_history(task.id):
            self._small_button(
                row, "Move\u2026", lambda tid=task.id: self._toggle_move(tid), fill,
            ).pack(side="right", padx=(0, 4))

        error = self._delete_errors.get(task.id)
        if error:
            theme.label(
                block, f"Can't delete \u2014 {error}. Archive it instead.",
                bg=fill, fg=theme.DANGER, font=theme.FONT_TINY,
            ).pack(fill="x", pady=(0, 2))

        if self._moving_task_id == task.id:
            self._move_entries_row(block, task, fill)

        if indent == 0:
            for child in self.store.db.tasks:
                if child.parent_id != task.id:
                    continue
                if child.archived and not self._show_archived.get():
                    continue
                self._task_block(block, child, indent=1, fill=fill)
            if self.store.can_add_subtask(task.id):
                self._add_subtask_row(block, task, fill)

    def _add_task_row(self, parent: tk.Misc, project: Project, fill: str) -> None:
        row = theme.frame(parent, bg=fill)
        row.pack(fill="x", pady=(4, 0))

        name = theme.entry(row, font=theme.FONT_SMALL)
        read_name = theme.add_placeholder(name, "New task")
        name.pack(side="left", fill="x", expand=True)

        billable = tk.BooleanVar(value=project.default_billable)
        if project.default_billable:
            theme.checkbox(row, "Billable", billable, lambda: None, bg=fill).pack(
                side="left", padx=4
            )

        def add() -> None:
            title = read_name().strip()
            if not title:
                return
            self.store.add_task(project.id, title, billable.get() and project.default_billable)
            name.delete(0, "end")

        icons.chip(
            row, "confirm", add, fallback="\u2713", bg=fill,
            fill=fill, text_fill=theme.BILLABLE, size=24, radius=6,
        ).pack(side="right", padx=(4, 0))
        name.bind("<Return>", lambda _e: add())
        theme.bind_tree(row, "<Control-Return>", lambda _e: add())

    def _add_subtask_row(self, parent: tk.Misc, task: Task, fill: str) -> None:
        row = theme.frame(parent, bg=fill)
        row.pack(fill="x", padx=(14, 0), pady=(2, 2))

        name = theme.entry(row, font=theme.FONT_SMALL)
        read_name = theme.add_placeholder(name, "+ Add sub-task")
        name.pack(side="left", fill="x", expand=True)

        billable = tk.BooleanVar(value=task.billable)
        theme.checkbox(row, "Billable", billable, lambda: None, bg=fill).pack(side="left", padx=4)

        def add() -> None:
            title = read_name().strip()
            if not title:
                return
            _child, problem = self.store.add_subtask(task.id, title, billable.get())
            if not problem:
                name.delete(0, "end")

        icons.chip(
            row, "confirm", add, fallback="\u2713", bg=fill,
            fill=fill, text_fill=theme.BILLABLE, size=22, radius=6,
        ).pack(side="right", padx=(4, 0))
        name.bind("<Return>", lambda _e: add())
        theme.bind_tree(row, "<Control-Return>", lambda _e: add())

    def _toggle_move(self, task_id: str) -> None:
        self._moving_task_id = None if self._moving_task_id == task_id else task_id
        self.refresh()

    def _move_entries_row(self, parent: tk.Misc, task: Task, fill: str) -> None:
        """Lets this task's logged time, plans and recurring notes move onto
        another task in one step — see ``Store.relocate_entries``. Offered
        by hand here for consolidating two tasks' history, or fixing time
        logged against the wrong one; ``add_subtask`` does the same thing
        automatically for a brand new child.
        """
        targets = [c for c in self.store.active_contexts() if c.task.id != task.id]
        row = theme.frame(parent, bg=fill)
        row.pack(fill="x", pady=(0, 4))
        if not targets:
            theme.label(
                row, "No other task to move it onto yet.", bg=fill, fg=theme.MUTED,
                font=theme.FONT_TINY,
            ).pack(side="left")
            icons.chip(
                row, "deny", lambda: self._toggle_move(task.id), fallback="\u00d7", bg=fill,
                fill=fill, text_fill=theme.DANGER, size=22, radius=6,
            ).pack(side="right")
            return

        theme.label(
            row, "Move its history to", bg=fill, fg=theme.MUTED, font=theme.FONT_TINY
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
            bg=fill, fill=fill, text_fill=theme.BILLABLE, size=22, radius=6,
        ).pack(side="right", padx=(4, 0))
        icons.chip(
            row, "deny", lambda: self._toggle_move(task.id), fallback="\u00d7",
            bg=fill, fill=fill, text_fill=theme.DANGER, size=22, radius=6,
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

    def _add_project_row(self, parent: tk.Misc, client: Client, fill: str) -> None:
        row = theme.frame(parent, bg=fill)
        row.pack(fill="x", pady=(2, 0))

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
            row, "confirm", add, fallback="\u2713", bg=fill,
            fill=fill, text_fill=theme.BILLABLE, size=24, radius=6,
        ).pack(side="right", padx=(4, 0))
        for field in (code, name):
            field.bind("<Return>", lambda _e: add())
        theme.bind_tree(row, "<Control-Return>", lambda _e: add())

    # -- delete ---------------------------------------------------------------

    def _delete_chip(self, parent: tk.Misc, item_id: str, action, fill: str) -> tk.Widget:
        """A trash chip that arms on first click and deletes on the second.

        Two steps rather than a popup: the chip itself becomes the question,
        the same recipe as the task delete chip on the Day tab.
        """
        if item_id in self._confirm_delete:
            wrapper = theme.frame(parent, bg=fill)
            icons.chip(
                wrapper, "confirm", lambda: self._confirm_delete_chip(item_id, action),
                fallback="\u2713", bg=fill, fill=fill, text_fill=theme.BILLABLE,
                size=22, radius=6,
            ).pack(side="left", padx=(0, 3))
            icons.chip(
                wrapper, "deny", lambda: self._disarm_delete(item_id),
                fallback="\u00d7", bg=fill, fill=fill, text_fill=theme.DANGER,
                size=22, radius=6,
            ).pack(side="left")
            return wrapper
        return icons.chip(
            parent, "trash", lambda: self._arm_delete(item_id),
            fallback="\U0001f5d1", bg=fill, fill=fill, text_fill=theme.DANGER,
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

    # -- drag-reorder -----------------------------------------------------------
    #
    # Same recipe as the Day and Recurring tabs' drag handles, for four
    # kinds of block at once: a drag only ever lands among the blocks of its
    # own kind and group (a client among clients, a project among its
    # client's projects, a task among its project's — or its parent's —
    # tasks), so nothing can be dragged out of the card it belongs to.

    def _handle(
        self, parent: tk.Misc, fill: str, kind: str, item_id: str, group, outline: tk.Widget,
        rest: tuple,
    ) -> tk.Label:
        handle = theme.grip(parent, bg=fill)
        handle.bind(
            "<ButtonPress-1>",
            lambda _e: self._drag_start(kind, item_id, group, outline, rest),
        )
        handle.bind("<B1-Motion>", self._drag_motion)
        handle.bind("<ButtonRelease-1>", self._drag_end)
        return handle

    def _drag_start(self, kind: str, item_id: str, group, outline: tk.Widget, rest: tuple) -> None:
        self._drag = {"kind": kind, "id": item_id, "group": group, "outline": outline, "rest": rest}
        theme.set_border(outline, theme.ACCENT, 2)

    def _siblings(self) -> list[tuple[str, tk.Widget]]:
        drag = self._drag
        return [
            (item_id, widget) for kind, item_id, group, widget in self._blocks
            if drag is not None and kind == drag["kind"] and group == drag["group"]
            and item_id != drag["id"] and widget.winfo_exists()
        ]

    def _drop_target(self, y_root: int) -> str | None:
        """The sibling to land in front of; ``None`` for the end."""
        for item_id, widget in self._siblings():
            zone = min(widget.winfo_height() // 2, DROP_ZONE)
            if y_root <= widget.winfo_rooty() + zone:
                return item_id
        return None

    def _drag_motion(self, event: tk.Event) -> None:
        if self._drag is None:
            return
        siblings = self._siblings()
        if not siblings:
            self._hide_drop_indicator()
            return
        parent = siblings[0][1].master
        target = self._drop_target(event.y_root)
        if target is not None:
            edge = next(widget for item_id, widget in siblings if item_id == target)
            y = edge.winfo_rooty() - parent.winfo_rooty() - 3
        else:
            last = siblings[-1][1]
            y = last.winfo_rooty() + last.winfo_height() - parent.winfo_rooty()
        self._show_drop_indicator(parent, y)

    def _drag_end(self, event: tk.Event) -> None:
        drag, self._drag = self._drag, None
        if drag is None:
            return
        self._hide_drop_indicator()
        # Worked out while ``drag`` still describes the drag (the siblings
        # are the ones sharing its kind and group).
        self._drag = drag
        target = self._drop_target(event.y_root)
        self._drag = None
        mover = {
            "client": self.store.move_client,
            "project": self.store.move_project,
            "task": self.store.move_task,
        }[drag["kind"]]
        mover(drag["id"], target)
        # Nothing may have changed (dropped where it was), in which case the
        # store sends no update and the highlight has to be put back here.
        if drag["outline"].winfo_exists():
            theme.set_border(drag["outline"], *drag["rest"])
        self.refresh()

    def _show_drop_indicator(self, parent: tk.Misc, y: int) -> None:
        if (
            self._drop_indicator is None
            or not self._drop_indicator.winfo_exists()
            or self._drop_indicator.master is not parent
        ):
            self._hide_drop_indicator()
            self._drop_indicator = tk.Frame(
                parent, bg=theme.blend(theme.ACCENT, theme.BG, 0.3),
                height=4, highlightthickness=0, bd=0,
            )
        self._drop_indicator.place(x=0, y=y, relwidth=1.0)
        self._drop_indicator.lift()

    def _hide_drop_indicator(self) -> None:
        if self._drop_indicator is not None and self._drop_indicator.winfo_exists():
            self._drop_indicator.destroy()
        self._drop_indicator = None
