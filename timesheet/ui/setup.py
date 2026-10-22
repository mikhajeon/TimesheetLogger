"""Setup: clients, projects and K-codes, plus overlay preferences."""

from __future__ import annotations

import subprocess
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


class SetupView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.show_archived = tk.BooleanVar(value=False)

        # Tk only holds a weak link to a widget's variable, so anything created
        # inside a method has to be parked here or it gets collected and the
        # checkbox silently renders unchecked.
        self._vars: list[tk.Variable] = []

        self.frame = theme.frame(parent)
        self.scroll = ScrollFrame(self.frame)
        self.scroll.pack(fill="both", expand=True, padx=10, pady=10)

    def _keep(self, variable: tk.Variable) -> tk.Variable:
        self._vars.append(variable)
        return variable

    def refresh(self) -> None:
        self.scroll.clear()
        self._vars.clear()
        self._preferences()
        self._clients()
        self._projects()
        self._shortcuts()
        self._files()

    # -- helpers -------------------------------------------------------------

    def _section(self, title: str) -> tk.Frame:
        theme.label(
            self.scroll.body, title.upper(), fg=theme.MUTED, font=theme.FONT_HEAD
        ).pack(fill="x", pady=(12, 5))
        box = theme.frame(self.scroll.body)
        box.pack(fill="x")
        return box

    # -- sections ------------------------------------------------------------

    def _preferences(self) -> None:
        box = self._section("Preferences")
        settings = self.store.db.settings

        row = theme.frame(box)
        row.pack(fill="x", pady=2)
        theme.label(row, "Round exports to", font=theme.FONT_SMALL).pack(side="left")
        current = next(
            (k for k, v in ROUNDING_CHOICES.items() if v == settings.rounding_minutes),
            "Nearest 15 minutes",
        )
        rounding = self._keep(tk.StringVar(value=current))
        theme.option_menu(
            box, rounding, list(ROUNDING_CHOICES),
            lambda label: self.store.patch_settings(rounding_minutes=ROUNDING_CHOICES[label]),
        ).pack(fill="x", pady=(0, 6))

        row = theme.frame(box)
        row.pack(fill="x", pady=2)
        theme.label(
            row, f"Daily target ({format_minutes(settings.daily_target_minutes)})",
            font=theme.FONT_SMALL,
        ).pack(side="left")
        target = theme.entry(row, width=7)
        target.insert(0, f"{settings.daily_target_minutes / 60:g}")
        target.pack(side="right")
        target.bind(
            "<FocusOut>",
            lambda _e, w=target: self.store.patch_settings(
                daily_target_minutes=parse_duration(w.get()) or settings.daily_target_minutes
            ),
        )

        row = theme.frame(box)
        row.pack(fill="x", pady=(8, 2))
        theme.label(
            row, f"Overlay opacity — {int(settings.opacity * 100)}%", font=theme.FONT_SMALL
        ).pack(side="left")
        slider = tk.Scale(
            box, from_=0.3, to=1.0, resolution=0.05, orient="horizontal",
            bg=theme.BG, fg=theme.TEXT, troughcolor=theme.BG_INPUT, highlightthickness=0,
            bd=0, sliderrelief="flat", activebackground=theme.ACCENT, showvalue=False,
        )
        slider.set(settings.opacity)
        # Attached only after the initial value is in place, so positioning the
        # handle doesn't count as the user moving it.
        slider.config(command=lambda value: self.overlay.set_opacity(float(value)))
        slider.pack(fill="x")

        on_top = self._keep(tk.BooleanVar(value=settings.always_on_top))
        theme.checkbox(
            box, "Always on top", on_top, self.overlay.toggle_always_on_top
        ).pack(fill="x", pady=(6, 0))

    def _clients(self) -> None:
        box = self._section("Clients")
        clients = [
            c for c in self.store.db.clients if self.show_archived.get() or not c.archived
        ]
        for client in clients:
            row = theme.frame(box)
            row.pack(fill="x", pady=1)

            name = theme.entry(row)
            name.insert(0, client.name)
            name.pack(side="left", fill="x", expand=True)
            name.bind(
                "<FocusOut>",
                lambda _e, i=client.id, w=name: self.store.update_client(i, name=w.get()),
            )

            theme.button(
                row, "Restore" if client.archived else "Archive",
                lambda i=client.id, a=client.archived: self.store.update_client(i, archived=not a),
                bg=theme.BG, fg=theme.MUTED, font=theme.FONT_SMALL,
            ).pack(side="right")

            internal = self._keep(tk.BooleanVar(value=client.is_internal))
            theme.checkbox(
                row, "Internal", internal,
                lambda i=client.id, v=internal: self.store.update_client(i, is_internal=v.get()),
            ).pack(side="right", padx=4)

        row = theme.frame(box)
        row.pack(fill="x", pady=(5, 0))
        new_client = theme.entry(row)
        new_client.pack(side="left", fill="x", expand=True)
        theme.button(
            row, "Add client", lambda w=new_client: self._add_client(w),
            bg=theme.BG_INPUT, font=theme.FONT_SMALL,
        ).pack(side="right", padx=(5, 0))
        new_client.bind("<Return>", lambda _e, w=new_client: self._add_client(w))

    def _add_client(self, widget: tk.Entry) -> None:
        name = widget.get().strip()
        if name:
            self.store.add_client(name)

    def _projects(self) -> None:
        box = self._section("Projects & K-codes")
        projects = [
            p for p in self.store.db.projects if self.show_archived.get() or not p.archived
        ]
        client_names = [c.name for c in self.store.db.clients]

        for project in projects:
            row = theme.frame(box)
            row.pack(fill="x", pady=1)

            k_code = theme.entry(row, width=8, font=theme.FONT_MONO)
            k_code.insert(0, project.k_code)
            k_code.pack(side="left")
            k_code.bind(
                "<FocusOut>",
                lambda _e, i=project.id, w=k_code: self.store.update_project(i, k_code=w.get()),
            )

            name = theme.entry(row)
            name.insert(0, project.name)
            name.pack(side="left", fill="x", expand=True, padx=4)
            name.bind(
                "<FocusOut>",
                lambda _e, i=project.id, w=name: self.store.update_project(i, name=w.get()),
            )

            theme.button(
                row, "Restore" if project.archived else "Archive",
                lambda i=project.id, a=project.archived: self.store.update_project(i, archived=not a),
                bg=theme.BG, fg=theme.MUTED, font=theme.FONT_SMALL,
            ).pack(side="right")

            billable = self._keep(tk.BooleanVar(value=project.default_billable))
            theme.checkbox(
                row, "Billable", billable,
                lambda i=project.id, v=billable: self.store.update_project(
                    i, default_billable=v.get()
                ),
            ).pack(side="right", padx=4)

            client = self.store.db.client(project.client_id)
            theme.label(
                box, f"    {client.name if client else '?'}", fg=theme.MUTED, font=theme.FONT_TINY
            ).pack(fill="x")

        row = theme.frame(box)
        row.pack(fill="x", pady=(5, 0))
        new_code = theme.entry(row, width=8, font=theme.FONT_MONO)
        new_code.pack(side="left")
        new_name = theme.entry(row)
        new_name.pack(side="left", fill="x", expand=True, padx=4)
        client_var = self._keep(tk.StringVar(value=client_names[0] if client_names else ""))
        theme.option_menu(row, client_var, client_names or [""]).pack(side="left")
        theme.button(
            row, "Add",
            lambda: self._add_project(new_code, new_name, client_var),
            bg=theme.BG_INPUT, font=theme.FONT_SMALL,
        ).pack(side="right", padx=(5, 0))

        theme.checkbox(
            box, "Show archived", self.show_archived,
            lambda: self.frame.after_idle(self.refresh),
        ).pack(fill="x", pady=(6, 0))

    def _add_project(self, code: tk.Entry, name: tk.Entry, client_var: tk.StringVar) -> None:
        title = name.get().strip()
        client = next((c for c in self.store.db.clients if c.name == client_var.get()), None)
        if not title or client is None:
            return
        self.store.add_project(
            client.id, title, code.get().strip() or "NCC", not client.is_internal
        )

    def _shortcuts(self) -> None:
        box = self._section("Shortcuts")
        rows = [
            ("Ctrl + Alt + T", "Show or hide the overlay"),
            ("Ctrl + Alt + A", "Toggle always on top"),
            ("Ctrl + Alt + S", "Stop the timer, or resume the last task"),
            ("Esc", "Collapse to the floating pill"),
        ]
        for keys, description in rows:
            row = theme.frame(box)
            row.pack(fill="x", pady=1)
            theme.label(row, keys, font=theme.FONT_MONO, fg=theme.TEXT, width=16).pack(side="left")
            theme.label(row, description, fg=theme.MUTED, font=theme.FONT_SMALL).pack(side="left")

        failed = getattr(self.overlay.hotkeys, "failed", [])
        if failed:
            theme.label(
                box,
                "Another app already owns: " + ", ".join(failed),
                fg=theme.DANGER, font=theme.FONT_TINY, wraplength=380, justify="left",
            ).pack(fill="x", pady=(4, 0))

    def _files(self) -> None:
        box = self._section("Files")
        for caption, target in (
            ("Data", paths.data_file()),
            ("Exports", paths.exports_dir()),
        ):
            theme.button(
                box, f"{caption}: {target}",
                lambda t=target: subprocess.Popen(
                    ["explorer", str(t if t.is_dir() else t.parent)]
                ),
                bg=theme.BG_INPUT, fg=theme.MUTED, font=theme.FONT_TINY, pady=4,
            ).pack(fill="x", pady=1)
