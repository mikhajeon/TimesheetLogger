"""The Setup tab: clients, projects and K-codes, plus overlay preferences."""

from __future__ import annotations

import os
import tkinter as tk

from .. import paths
from ..dates import format_minutes, parse_duration
from ..store import Store
from . import theme
from .scroll import ScrollFrame

ROUNDING_CHOICES = {
    "Exact minutes": 0,
    "Nearest 5 minutes": 5,
    "Nearest 6 minutes": 6,
    "Nearest 10 minutes": 10,
    "Nearest 15 minutes": 15,
    "Nearest 30 minutes": 30,
}

SHORTCUTS = [
    ("Ctrl+Alt+T", "Show or hide the overlay"),
    ("Ctrl+Alt+A", "Toggle always on top"),
    ("Ctrl+Alt+S", "Stop the timer, or resume the last task"),
    ("Esc", "Collapse to the compact bar"),
]


class SetupView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.frame = theme.frame(parent)
        self._show_archived = tk.BooleanVar(value=False)

        self.scroll = ScrollFrame(self.frame)
        self.scroll.pack(fill="both", expand=True, padx=8, pady=8)

    def refresh(self) -> None:
        self.scroll.clear()
        body = self.scroll.body
        self._preferences(body)
        self._clients(body)
        self._projects(body)
        self._shortcuts(body)
        self._files(body)

    # -- sections -----------------------------------------------------------

    def _section(self, parent: tk.Misc, title: str) -> tk.Frame:
        theme.label(parent, title.upper(), fg=theme.MUTED, font=theme.FONT_HEAD).pack(
            fill="x", pady=(12, 4)
        )
        box = theme.frame(parent)
        box.pack(fill="x")
        return box

    def _preferences(self, parent: tk.Misc) -> None:
        box = self._section(parent, "Preferences")
        settings = self.store.db.settings

        row = theme.frame(box)
        row.pack(fill="x", pady=2)
        theme.label(row, "Round exports to", font=theme.FONT_SMALL).pack(side="left")
        current = next(
            (k for k, v in ROUNDING_CHOICES.items() if v == settings.rounding_minutes),
            "Nearest 15 minutes",
        )
        choice = tk.StringVar(value=current)
        theme.option_menu(
            row,
            choice,
            list(ROUNDING_CHOICES),
            command=lambda value: self.store.patch_settings(
                rounding_minutes=ROUNDING_CHOICES[value]
            ),
        ).pack(side="right")

        row = theme.frame(box)
        row.pack(fill="x", pady=2)
        theme.label(
            row,
            f"Daily target ({format_minutes(settings.daily_target_minutes)})",
            font=theme.FONT_SMALL,
        ).pack(side="left")
        target = theme.entry(row, width=7, font=theme.FONT_SMALL)
        target.insert(0, f"{settings.daily_target_minutes / 60:g}")
        target.pack(side="right")
        target.bind("<FocusOut>", lambda _e: self._commit_target(target))

        row = theme.frame(box)
        row.pack(fill="x", pady=2)
        theme.label(row, "Overlay opacity", font=theme.FONT_SMALL).pack(side="left")
        percent = theme.label(
            row, f"{round(settings.opacity * 100)}%", fg=theme.MUTED, font=theme.FONT_TINY
        )

        def on_opacity(value: str) -> None:
            self.overlay.set_opacity(float(value))
            percent.config(text=f"{round(float(value) * 100)}%")

        opacity = tk.Scale(
            row,
            from_=0.3,
            to=1.0,
            resolution=0.05,
            orient="horizontal",
            showvalue=False,
            length=130,
            bg=theme.BG_HOVER,  # this is the slider handle; must contrast the trough
            fg=theme.TEXT,
            troughcolor=theme.BG_INPUT,
            activebackground=theme.ACCENT,
            highlightthickness=0,
            bd=0,
            sliderrelief="flat",
            command=on_opacity,
        )
        opacity.set(settings.opacity)
        opacity.pack(side="right")
        percent.pack(side="right", padx=4)

        # Held on the instance because Tk keeps only a weak reference to the
        # variable; a local would be collected and the box would render empty.
        self._pinned = tk.BooleanVar(value=settings.always_on_top)
        theme.checkbox(
            box, "Always on top", self._pinned, self.overlay.toggle_always_on_top
        ).pack(fill="x", pady=2)

    def _clients(self, parent: tk.Misc) -> None:
        box = self._section(parent, "Clients")
        for client in self.store.db.clients:
            if client.archived and not self._show_archived.get():
                continue
            row = theme.frame(box)
            row.pack(fill="x", pady=1)

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
                lambda cid=client.id, v=internal: self.store.update_client(
                    cid, is_internal=v.get()
                ),
            ).pack(side="left", padx=4)

            theme.button(
                row,
                "Restore" if client.archived else "Archive",
                lambda cid=client.id, a=client.archived: self.store.update_client(
                    cid, archived=not a
                ),
                bg=theme.BG,
                fg=theme.MUTED,
                font=theme.FONT_TINY,
            ).pack(side="right")

        row = theme.frame(box)
        row.pack(fill="x", pady=(4, 0))
        new_name = theme.entry(row, font=theme.FONT_SMALL)
        new_name.pack(side="left", fill="x", expand=True)

        def add() -> None:
            if new_name.get().strip():
                self.store.add_client(new_name.get().strip())

        theme.button(row, "Add client", add, font=theme.FONT_SMALL).pack(side="right", padx=(4, 0))
        new_name.bind("<Return>", lambda _e: add())

    def _projects(self, parent: tk.Misc) -> None:
        box = self._section(parent, "Projects & K-codes")
        clients = {c.name: c.id for c in self.store.db.clients}

        for project in self.store.db.projects:
            if project.archived and not self._show_archived.get():
                continue
            row = theme.frame(box)
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

            theme.button(
                row,
                "Restore" if project.archived else "Archive",
                lambda pid=project.id, a=project.archived: self.store.update_project(
                    pid, archived=not a
                ),
                bg=theme.BG,
                fg=theme.MUTED,
                font=theme.FONT_TINY,
            ).pack(side="right")

        if not clients:
            return

        row = theme.frame(box)
        row.pack(fill="x", pady=(4, 0))
        new_code = theme.entry(row, width=8, font=theme.FONT_MONO)
        new_code.pack(side="left")
        new_name = theme.entry(row, font=theme.FONT_SMALL)
        new_name.pack(side="left", fill="x", expand=True, padx=3)
        client_choice = tk.StringVar(value=next(iter(clients)))
        theme.option_menu(row, client_choice, list(clients)).pack(side="left")

        def add() -> None:
            name = new_name.get().strip()
            if not name:
                return
            client_id = clients[client_choice.get()]
            client = self.store.db.client(client_id)
            self.store.add_project(
                client_id,
                name,
                new_code.get().strip() or "NCC",
                default_billable=not (client.is_internal if client else False),
            )

        theme.button(row, "Add", add, font=theme.FONT_SMALL).pack(side="right", padx=(4, 0))

        theme.checkbox(
            box, "Show archived", self._show_archived, self.refresh
        ).pack(fill="x", pady=(4, 0))

    def _shortcuts(self, parent: tk.Misc) -> None:
        box = self._section(parent, "Shortcuts")
        for keys, description in SHORTCUTS:
            row = theme.frame(box)
            row.pack(fill="x", pady=1)
            theme.label(
                row, keys, bg=theme.BG_INPUT, fg=theme.TEXT, font=theme.FONT_MONO, padx=5
            ).pack(side="left")
            theme.label(row, description, fg=theme.MUTED, font=theme.FONT_SMALL).pack(
                side="left", padx=6
            )

        failed = getattr(self.overlay.hotkeys, "failed", [])
        if failed:
            theme.label(
                box,
                f"Already taken by another app: {', '.join(failed)}",
                fg=theme.DANGER,
                font=theme.FONT_TINY,
            ).pack(fill="x", pady=(4, 0))

    def _files(self, parent: tk.Misc) -> None:
        box = self._section(parent, "Files")
        for caption, target in (
            ("Data", paths.data_file()),
            ("Exports", paths.exports_dir()),
        ):
            theme.button(
                box,
                f"{caption}: {target}",
                lambda t=target: self._open(t),
                bg=theme.BG_INPUT,
                fg=theme.MUTED,
                font=theme.FONT_TINY,
                anchor="w",
            ).pack(fill="x", pady=1)

    # -- actions -------------------------------------------------------------

    def _open(self, target) -> None:
        directory = target if target.is_dir() else target.parent
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)  # noqa: S606 - opening Explorer is the intent

    def _commit_target(self, widget: tk.Entry) -> None:
        minutes = parse_duration(widget.get())
        if minutes:
            self.store.patch_settings(daily_target_minutes=minutes)
