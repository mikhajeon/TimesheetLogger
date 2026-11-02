"""The Preferences tab: window behaviour, launching, shortcuts and files.

Clients, projects and K-codes used to live here too, until that list grew
enough to want its own tab — see :mod:`timesheet.ui.projects`, which is what
the Setup tab actually shows now.
"""

from __future__ import annotations

import os
import tkinter as tk

from .. import launcher, monitors, paths
from ..dates import format_minutes
from ..hotkeys import format_accelerator
from ..store import Store
from . import icons, theme
from .scroll import ScrollFrame
from .overlay import DEFAULT_HOTKEYS

#: Choices for the duration pickers' own step — never applied to what's
#: exported, which always stays exact (see ``timesheet.ui.export``).
INCREMENT_CHOICES = {
    "1 minute": 1,
    "5 minutes": 5,
    "6 minutes": 6,
    "10 minutes": 10,
    "15 minutes": 15,
    "30 minutes": 30,
}

#: name -> description, for the editable global hotkeys. The accelerator
#: itself isn't here — it's whatever ``overlay.accelerator_for(name)``
#: returns, since the whole point is that it can change.
HOTKEY_ACTIONS = [
    ("collapse", "Collapse to the icon, from anywhere"),
    ("pin", "Toggle always on top"),
    ("timer", "Stop the timer, or resume the last task"),
]

#: Fixed local key bindings — only active while the overlay has focus, and
#: not backed by ``HotkeyListener``, so there's no accelerator to reassign.
LOCAL_SHORTCUTS = [
    ("Esc", "Collapse to the icon (double-click it to come back)"),
    ("Ctrl+Enter", "Add, from anywhere in a new task, project or client form"),
]

#: Tk keysyms for the three modifiers a hotkey capture understands, mapped to
#: the names ``hotkeys._parse`` expects. Held while capturing a new shortcut,
#: then combined with whatever non-modifier key finishes it off.
_CAPTURE_MODIFIERS = {
    "Control_L": "ctrl", "Control_R": "ctrl",
    "Alt_L": "alt", "Alt_R": "alt",
    "Shift_L": "shift", "Shift_R": "shift",
}


class SetupView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.frame = theme.frame(parent)

        self.scroll = ScrollFrame(self.frame)
        self.scroll.pack(fill="both", expand=True, padx=8, pady=8)

    def refresh(self) -> None:
        theme.commit_focused_entry(self.frame)
        self.scroll.clear()
        body = self.scroll.body
        self._preferences(body)
        self._position(body)
        self._launching(body)
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
        theme.label(row, "Theme", font=theme.FONT_SMALL).pack(side="left")
        # "System" isn't one of ``theme.PALETTES`` — it's resolved against
        # Windows' own light/dark setting instead of naming a palette
        # directly — so it's added here rather than falling out of that dict.
        palettes = {name.title(): name for name in theme.PALETTES}
        palettes["System"] = theme.SYSTEM
        palette = tk.StringVar(
            value=next(k for k, v in palettes.items() if v == theme.MODE)
        )
        theme.option_menu(
            row,
            palette,
            list(palettes),
            command=lambda value: self.overlay.set_theme(palettes[value]),
        ).pack(side="right")

        row = theme.frame(box)
        row.pack(fill="x", pady=2)
        theme.label(row, "Duration picker step", font=theme.FONT_SMALL).pack(side="left")
        current = next(
            (k for k, v in INCREMENT_CHOICES.items() if v == settings.entry_increment_minutes),
            "15 minutes",
        )
        increment = tk.StringVar(value=current)
        theme.option_menu(
            row,
            increment,
            list(INCREMENT_CHOICES),
            command=lambda value: self.store.patch_settings(
                entry_increment_minutes=INCREMENT_CHOICES[value]
            ),
        ).pack(side="right")

        row = theme.frame(box)
        row.pack(fill="x", pady=2)
        theme.label(
            row,
            f"Daily target ({format_minutes(settings.daily_target_minutes)})",
            font=theme.FONT_SMALL,
        ).pack(side="left")
        theme.duration_picker(
            row, settings.daily_target_minutes, self._commit_target,
            max_minutes=16 * 60, width=7, font=theme.FONT_SMALL,
            step=settings.entry_increment_minutes,
        ).pack(side="right")

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

    def _position(self, parent: tk.Misc) -> None:
        box = self._section(parent, "Screen position")
        settings = self.store.db.settings
        screens = self.overlay.screens()

        row = theme.frame(box)
        row.pack(fill="x", pady=2)
        theme.label(row, "Pin to corner", font=theme.FONT_SMALL).pack(side="left")
        corners = {monitors.FREE_LABEL: "", **monitors.CORNERS}
        corner = tk.StringVar(
            value=next(k for k, v in corners.items() if v == settings.snap_corner)
        )
        theme.option_menu(
            row,
            corner,
            list(corners),
            command=lambda value: self.overlay.set_snap(corner=corners[value]),
        ).pack(side="right")

        if len(screens) > 1:
            row = theme.frame(box)
            row.pack(fill="x", pady=2)
            theme.label(row, "Screen", font=theme.FONT_SMALL).pack(side="left")
            names = {s.label(i): s.name for i, s in enumerate(screens, 1)}
            chosen = monitors.find(screens, settings.snap_screen).name
            screen = tk.StringVar(value=next(k for k, v in names.items() if v == chosen))
            theme.option_menu(
                row,
                screen,
                list(names),
                command=lambda value: self.overlay.set_snap(screen=names[value]),
            ).pack(side="right")

        hint = (
            "Pinned, so the overlay can't be dragged. Choose Free to move it."
            if settings.snap_corner
            else "One screen found. Others are listed here as you connect them."
            if len(screens) == 1
            else f"{len(screens)} screens found."
        )
        theme.label(box, hint, fg=theme.MUTED, font=theme.FONT_TINY).pack(fill="x", pady=(2, 0))

    def _launching(self, parent: tk.Misc) -> None:
        box = self._section(parent, "Launching")

        # Whether a shortcut exists is read off disk each time this is drawn,
        # so one deleted by hand doesn't leave the box still ticked.
        self._launch_vars = {}
        for location, caption, note in (
            ("desktop", "Desktop shortcut", "An icon to start it from"),
            ("startup", "Start when I sign in", "Runs hidden, waiting in the tray"),
        ):
            variable = tk.BooleanVar(value=launcher.exists(location))
            self._launch_vars[location] = variable
            theme.checkbox(
                box,
                caption,
                variable,
                lambda loc=location: self._set_shortcut(loc),
            ).pack(fill="x", pady=2)
            theme.label(box, note, fg=theme.MUTED, font=theme.FONT_TINY).pack(
                fill="x", padx=22, pady=(0, 2)
            )

        self._tray = tk.BooleanVar(value=self.store.db.settings.tray)
        theme.checkbox(box, "Show in the notification area", self._tray, self._set_tray).pack(
            fill="x", pady=2
        )
        tray = getattr(self.overlay, "tray", None)
        note = (
            "Closing the overlay hides it; right-click the tray icon to quit"
            if self.store.db.settings.tray
            else "Closing the overlay quits it"
        )
        theme.label(box, note, fg=theme.MUTED, font=theme.FONT_TINY).pack(
            fill="x", padx=22, pady=(0, 2)
        )

        self._launch_error = theme.label(box, "", fg=theme.DANGER, font=theme.FONT_TINY)
        if tray is not None and tray.failed:
            self._fail(f"The notification area refused the icon: {tray.failed}")

    def _shortcuts(self, parent: tk.Misc) -> None:
        box = self._section(parent, "Shortcuts")
        settings = self.store.db.settings

        for name, description in HOTKEY_ACTIONS:
            current = self.overlay.accelerator_for(name)
            row = theme.frame(box)
            row.pack(fill="x", pady=1)
            keys_btn = theme.button(
                row,
                format_accelerator(current),
                lambda: None,
                bg=theme.BG_INPUT,
                fg=theme.TEXT,
                font=theme.FONT_MONO,
                padx=5,
                pady=1,
                width=11,
            )
            keys_btn.config(command=lambda n=name, b=keys_btn: self._capture_hotkey(n, b))
            keys_btn.pack(side="left")
            theme.label(row, description, fg=theme.MUTED, font=theme.FONT_SMALL).pack(
                side="left", padx=6
            )
            if name in settings.hotkeys:
                icons.button(
                    row,
                    "undo",
                    lambda n=name: self.overlay.set_hotkey(n, None),
                    fallback="Reset",
                    bg=theme.BG,
                    fg=theme.MUTED,
                    font=theme.FONT_TINY,
                    padx=4,
                    pady=1,
                ).pack(side="right")

        for keys, description in LOCAL_SHORTCUTS:
            row = theme.frame(box)
            row.pack(fill="x", pady=1)
            theme.label(
                row, keys, bg=theme.BG_INPUT, fg=theme.TEXT, font=theme.FONT_MONO, padx=5
            ).pack(side="left")
            theme.label(row, description, fg=theme.MUTED, font=theme.FONT_SMALL).pack(
                side="left", padx=6
            )

        theme.label(
            box,
            "Click a shortcut, then press a key combo (with Ctrl, Alt or Shift) to change it. Esc cancels.",
            fg=theme.MUTED,
            font=theme.FONT_TINY,
        ).pack(fill="x", pady=(4, 0))

        failed = getattr(self.overlay.hotkeys, "failed", [])
        if failed:
            theme.label(
                box,
                f"Already taken by another app: {', '.join(format_accelerator(a) for a in failed)}",
                fg=theme.DANGER,
                font=theme.FONT_TINY,
            ).pack(fill="x", pady=(4, 0))

    def _capture_hotkey(self, name: str, button: tk.Button) -> None:
        """Puts ``button`` into "listening" mode: the next modifier-plus-key
        combo the user presses becomes ``name``'s new accelerator. Escape, or
        clicking away, cancels without changing anything.

        The global listener is paused for the duration (see
        ``HotkeyListener.pause``) — otherwise trying out a combo that
        happens to already belong to some other action (this one included)
        would fire that action system-wide while it's merely being tried on
        for size here, focus on this button notwithstanding.
        """
        held: set[str] = set()
        button.config(text="Press keys\u2026")
        self.overlay.hotkeys.pause()

        def finish(accelerator: str | None) -> None:
            button.unbind("<KeyPress>")
            button.unbind("<FocusOut>")
            if accelerator is not None:
                self.overlay.set_hotkey(name, accelerator)
            # ``set_hotkey`` only triggers a rebuild (which would relabel this
            # button itself) when the accelerator actually changed — capturing
            # the same combo it already had leaves settings untouched, so the
            # label is fixed up here either way, guarded in case it rebuilt.
            if button.winfo_exists():
                button.config(text=format_accelerator(self.overlay.accelerator_for(name)))
            self.overlay.hotkeys.resume()

        def on_key(event: tk.Event) -> str:
            keysym = event.keysym
            if keysym in _CAPTURE_MODIFIERS:
                held.add(_CAPTURE_MODIFIERS[keysym])
                return "break"
            if keysym == "Escape":
                finish(None)
                return "break"
            if held and len(keysym) == 1 and keysym.isalnum():
                mods = [m for m in ("ctrl", "alt", "shift") if m in held]
                finish("+".join([*mods, keysym.lower()]))
            return "break"

        button.bind("<KeyPress>", on_key)
        button.bind("<FocusOut>", lambda _e: finish(None))
        button.focus_set()

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

    def _set_shortcut(self, location: str) -> None:
        wanted = self._launch_vars[location].get()
        problem = launcher.set_enabled(location, wanted)
        if problem:
            # Put the tick back: the box should show what's on disk, and
            # nothing was written.
            self._launch_vars[location].set(not wanted)
            self._fail(f"Could not {'create' if wanted else 'remove'} it: {problem}")
        else:
            self._fail("")

    def _set_tray(self) -> None:
        self.overlay.set_tray(self._tray.get())
        tray = self.overlay.tray
        self._fail(f"The notification area refused the icon: {tray.failed}" if tray and tray.failed else "")

    def _fail(self, message: str) -> None:
        self._launch_error.config(text=message)
        if message:
            self._launch_error.pack(fill="x", pady=(4, 0))
        else:
            self._launch_error.pack_forget()

    def _open(self, target) -> None:
        directory = target if target.is_dir() else target.parent
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)  # noqa: S606 - opening Explorer is the intent

    def _commit_target(self, minutes: int) -> None:
        if minutes:
            self.store.patch_settings(daily_target_minutes=minutes)
