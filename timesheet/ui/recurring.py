"""The Recurring tab: every standing commitment across every task, in one
place, with its schedule set right where it's created.

Split out of the Setup tab rather than kept under each task there: a
``RecurringNote`` isn't part of a project's structure the way a task or a
K-code is — it's an ongoing pattern of logging, closer in kind to the Day tab
than to the client/project/task tree. A task's own "+ Add sub-task" row in
the Setup tab no longer competes with a note parked on it either — gaining a
child just moves the note onto that child instead (see
``Store.relocate_entries``), rather than refusing the child over it.

Creating one here puts the schedule in view from the first click too, rather
than a title typed first and the recurrence set as a separate step
afterward — see ``_add_panel``.
"""

from __future__ import annotations

import tkinter as tk

from ..models import Recurrence, RecurringNote
from ..recurrence import describe as describe_recurrence, today_iso
from ..store import Store
from . import icons, theme
from .repeat import RepeatEditor
from .scroll import ScrollFrame

DEFAULT_MINUTES = 30


class RecurringView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.frame = theme.frame(parent)
        self._show_archived = tk.BooleanVar(value=False)
        self._adding = False
        #: The task last chosen in the add panel, held across the rebuild
        #: its own dropdown triggers — same recipe as the Day tab's.
        self._add_task_id: str | None = None
        #: Note id -> its schedule editor open.
        self._expanded: set[str] = set()
        #: Note ids armed for delete, waiting on a second click.
        self._confirm_delete: set[str] = set()

        self._build_header()
        self.scroll = ScrollFrame(self.frame)
        self.scroll.pack(fill="both", expand=True, padx=8)

    def _build_header(self) -> None:
        row = theme.frame(self.frame)
        row.pack(fill="x", padx=8, pady=(8, 4))
        theme.label(row, "RECURRING", fg=theme.MUTED, font=theme.FONT_HEAD).pack(side="left")
        theme.checkbox(
            row, "Show archived", self._show_archived, self.refresh
        ).pack(side="right")

    def refresh(self) -> None:
        theme.commit_focused_entry(self.frame)
        self.scroll.clear()
        body = self.scroll.body
        notes = self.store.all_recurring_notes(include_archived=self._show_archived.get())

        if not notes:
            theme.label(
                body,
                "Nothing recurring yet. A standing commitment \u2014 a daily "
                "stand-up, a Monday admin slot \u2014 comes back on its own "
                "schedule under whichever task bills it.",
                fg=theme.MUTED, font=theme.FONT_SMALL, wraplength=380, justify="left",
            ).pack(fill="x", pady=12)
        else:
            for heading, group in self._grouped(notes):
                theme.label(body, heading, font=theme.FONT_HEAD).pack(
                    fill="x", pady=(10, 3)
                )
                for note in group:
                    self._note_row(body, note)

        self._add_panel(body)

    def _grouped(self, notes: list[RecurringNote]) -> list[tuple[str, list[RecurringNote]]]:
        """Notes under the same client/project/task heading one after
        another, in the order their task is first seen — stable across a
        refresh rather than alphabetised, so a row doesn't jump around while
        you're editing it."""
        groups: dict[str, list[RecurringNote]] = {}
        order: list[str] = []
        for note in notes:
            task = self.store.db.task(note.task_id)
            context = self.store.context(task) if task else None
            heading = (
                f"{context.heading} \u2014 {context.display_title}" if context else "Unknown task"
            )
            if heading not in groups:
                groups[heading] = []
                order.append(heading)
            groups[heading].append(note)
        return [(heading, groups[heading]) for heading in order]

    # -- rows -----------------------------------------------------------

    def _note_row(self, parent: tk.Misc, note: RecurringNote) -> None:
        row = theme.frame(parent)
        row.pack(fill="x", pady=1)

        text = theme.entry(row, font=theme.FONT_SMALL)
        text.insert(0, note.note)
        text.pack(side="left", fill="x", expand=True)
        text.bind(
            "<FocusOut>",
            lambda _e, nid=note.id, w=text: self.store.update_recurring_note(nid, note=w.get()),
        )

        theme.duration_picker(
            row, note.minutes,
            lambda m, nid=note.id: self.store.update_recurring_note(nid, minutes=m),
            width=6, font=theme.FONT_TINY,
            step=self.store.db.settings.entry_increment_minutes,
        ).pack(side="left", padx=4)

        theme.button(
            row,
            theme.ellipsize(describe_recurrence(note.recurrence), 24),
            lambda nid=note.id: self._toggle_expanded(nid),
            bg=theme.BG,
            fg=theme.MUTED if note.recurrence else theme.DANGER,
            font=theme.FONT_TINY,
        ).pack(side="left", padx=(0, 4))

        self._delete_chip(row, note.id).pack(side="right")
        theme.button(
            row,
            "Restore" if note.archived else "Archive",
            lambda nid=note.id, a=note.archived: self.store.update_recurring_note(
                nid, archived=not a
            ),
            bg=theme.BG,
            fg=theme.MUTED,
            font=theme.FONT_TINY,
        ).pack(side="right", padx=(0, 4))

        if note.id in self._expanded:
            editor_row = theme.frame(parent)
            editor_row.pack(fill="x", pady=(0, 4))
            RepeatEditor(
                editor_row,
                note.recurrence,
                lambda rule, nid=note.id: self.store.set_recurring_note_rule(nid, rule),
                step=self.store.db.settings.entry_increment_minutes,
            )

    def _toggle_expanded(self, note_id: str) -> None:
        self._expanded.symmetric_difference_update({note_id})
        self.refresh()

    # -- add panel --------------------------------------------------------

    def _add_panel(self, parent: tk.Misc) -> None:
        if not self._adding:
            theme.button(
                parent,
                "+ Add a recurring series",
                self._open_add,
                bg=theme.BG,
                fg=theme.MUTED,
                pady=6,
            ).pack(fill="x", pady=(10, 0))
            return

        panel = theme.frame(parent, bg=theme.BG_RAISED)
        panel.pack(fill="x", pady=(10, 0))
        inner = theme.frame(panel, bg=theme.BG_RAISED)
        inner.pack(fill="x", padx=8, pady=8)

        # Any non-Phase task whose project isn't closed — there's no
        # "already on this day" notion here the way there is on the Day tab,
        # since this isn't tied to one.
        available = [c for c in self.store.active_contexts() if not c.project.closed]
        if not available:
            theme.label(
                inner,
                "No open task to attach one to yet \u2014 add one in the Setup tab.",
                bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_SMALL,
            ).pack(fill="x")
            icons.chip(
                inner, "deny", self._close_add, fallback="\u00d7",
                fill=theme.BG_RAISED, text_fill=theme.DANGER, bg=theme.BG_RAISED,
            ).pack(anchor="e", pady=(6, 0))
            return

        theme.label(
            inner, "Task", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(fill="x")
        task_labels = {
            f"{c.project.k_code} \u00b7 {c.project.name} \u00b7 {c.display_title}": c.task.id
            for c in available
        }
        by_id = {tid: label for label, tid in task_labels.items()}
        if self._add_task_id not in by_id:
            self._add_task_id = next(iter(task_labels.values()))
        task_choice = tk.StringVar(value=by_id[self._add_task_id])
        theme.option_menu(
            inner, task_choice, list(task_labels),
            command=lambda value: setattr(
                self, "_add_task_id", task_labels.get(value, self._add_task_id)
            ),
        ).pack(fill="x", pady=(0, 6))

        theme.label(
            inner, "Note", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(fill="x")
        note_entry = theme.entry(inner)
        note_entry.pack(fill="x", pady=(0, 6))
        note_entry.focus_set()

        row = theme.frame(inner, bg=theme.BG_RAISED)
        row.pack(fill="x", pady=(0, 2))
        theme.label(
            row, "Usually", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(side="left", padx=(0, 3))
        usual_minutes = {"value": DEFAULT_MINUTES}
        theme.duration_picker(
            row, usual_minutes["value"], lambda m: usual_minutes.__setitem__("value", m),
            width=7, font=theme.FONT_SMALL,
            step=self.store.db.settings.entry_increment_minutes,
        ).pack(side="left")

        # Live from the first click rather than a second step after naming
        # it: on by default, at a sensible starting guess, since there'd be
        # little point opening this tab for a note that never recurs.
        editor = RepeatEditor(
            inner, Recurrence(start=today_iso()), lambda _rule: None, bg=theme.BG_RAISED,
            step=self.store.db.settings.entry_increment_minutes,
        )

        actions = theme.frame(inner, bg=theme.BG_RAISED)
        actions.pack(fill="x", pady=(6, 0))

        def create() -> None:
            task_id = self._add_task_id
            title = note_entry.get().strip()
            if not task_id or not title:
                return
            self.store.add_recurring_note(task_id, title, usual_minutes["value"], editor.rule)
            self._adding = False
            self.refresh()

        icons.chip(
            actions, "confirm", create, fallback="\u2713",
            fill=theme.BG_RAISED, text_fill=theme.BILLABLE, bg=theme.BG_RAISED,
        ).pack(side="right", padx=(4, 0))
        icons.chip(
            actions, "deny", self._close_add, fallback="\u00d7",
            fill=theme.BG_RAISED, text_fill=theme.DANGER, bg=theme.BG_RAISED,
        ).pack(side="right")

        note_entry.bind("<Return>", lambda _e: create())
        theme.bind_tree(panel, "<Control-Return>", lambda _e: create())

    def _open_add(self) -> None:
        self._adding = True
        self.refresh()

    def _close_add(self) -> None:
        self._adding = False
        self.refresh()

    # -- delete -----------------------------------------------------------

    def _delete_chip(self, parent: tk.Misc, note_id: str) -> tk.Widget:
        """A trash chip that arms on first click and deletes on the second —
        same recipe as the Setup tab's client/project/task rows. No guard to
        explain here: see ``Store.delete_recurring_note``."""
        if note_id in self._confirm_delete:
            wrapper = theme.frame(parent)
            icons.chip(
                wrapper, "confirm", lambda: self._confirm_delete_note(note_id),
                fallback="\u2713", fill=theme.BG, text_fill=theme.BILLABLE, size=22, radius=6,
            ).pack(side="left", padx=(0, 3))
            icons.chip(
                wrapper, "deny", lambda: self._disarm_delete(note_id),
                fallback="\u00d7", fill=theme.BG, text_fill=theme.DANGER, size=22, radius=6,
            ).pack(side="left")
            return wrapper
        return icons.chip(
            parent, "trash", lambda: self._arm_delete(note_id),
            fallback="\U0001f5d1", fill=theme.BG, text_fill=theme.DANGER,
            size=22, radius=6,
        )

    def _arm_delete(self, note_id: str) -> None:
        self._confirm_delete.add(note_id)
        self.refresh()

    def _disarm_delete(self, note_id: str) -> None:
        self._confirm_delete.discard(note_id)
        self.refresh()

    def _confirm_delete_note(self, note_id: str) -> None:
        self.store.delete_recurring_note(note_id)
        self._confirm_delete.discard(note_id)
        self._expanded.discard(note_id)
        self.refresh()
