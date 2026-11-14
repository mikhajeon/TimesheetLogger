"""The Preferences tab: window behaviour, launching, shortcuts and files.

Clients, projects and K-codes used to live here too, until that list grew
enough to want its own tab — see :mod:`timesheet.ui.projects`, which is what
the Setup tab actually shows now.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import colorchooser

from .. import launcher, monitors, paths
from ..dates import format_duration
from ..hotkeys import format_accelerator
from ..store import Store
from . import controls, icons, theme
from .scroll import ScrollFrame
from .overlay import DEFAULT_HOTKEYS

#: Choices for how an hour figure reads on screen — never applied to what's
#: exported or copied out, which always stays decimal (see
#: ``dates.format_duration``).
HOUR_FORMAT_CHOICES = {
    "Clock (7h 30m)": "clock",
    "Decimal (7.5)": "decimal",
}

#: Choices for how dates read on screen — see ``Settings.date_format``.
DATE_FORMAT_CHOICES = {
    "2026-11-13": "iso",
    "13/11/2026": "dmy",
    "11/13/2026": "mdy",
    "13 Nov 2026": "text",
}

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

#: Choices for how long with no mouse/keyboard input auto-pauses a running
#: timer — see ``Settings.idle_pause_minutes``. "5 minutes" (the default)
#: is also listed first on purpose: it's what matches Microsoft Teams' own
#: default "Away" threshold, the one most people already have some feel
#: for.
IDLE_PAUSE_CHOICES = {
    "5 minutes": 5,
    "1 minute": 1,
    "2 minutes": 2,
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

    def _fmt(self, minutes: float) -> str:
        """An hour figure the way ``Settings.hour_format`` (the "Hour
        format" row above) says to show it — see ``dates.format_duration``.
        """
        return format_duration(minutes, self.store.db.settings.hour_format == "decimal")

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
        settings = self.store.db.settings

        # -- Appearance ----------------------------------------------------
        card = controls.section(parent, "Appearance")
        # "System" isn't one of ``theme.PALETTES`` — it's resolved against
        # Windows' own light/dark setting instead of naming a palette
        # directly — so it's added here rather than falling out of that dict.
        palettes = {name.title(): name for name in theme.PALETTES}
        palettes["System"] = theme.SYSTEM
        current = next(k for k, v in palettes.items() if v == theme.MODE)
        controls.Segmented(
            card.row("Theme", "System follows Windows' light/dark setting"),
            list(palettes), current,
            lambda value: self.overlay.set_theme(palettes[value]),
        ).pack()

        percent = lambda v: f"{round(v * 100)}%"  # noqa: E731
        controls.Slider(
            card.row("Overlay opacity"), 0.3, 1.0, settings.opacity, 0.05,
            self.overlay.set_opacity, fmt=percent,
        ).pack()

        self._color_row(card, "Actual progress color", "progress_bar_color", theme.ACCENT)
        self._color_row(card, "Planned progress color", "planned_bar_color", theme.PLANNED)

        # -- Window --------------------------------------------------------
        card = controls.section(parent, "Window")
        controls.Switch(
            card.row("Always on top", "Stays above other windows, and won't collapse "
                     "when you click away"),
            settings.always_on_top, lambda _v: self.overlay.toggle_always_on_top(),
        ).pack()
        controls.Switch(
            card.row("Pin while collapsed", "Keeps the badge above other windows"),
            settings.pin_when_collapsed, self.overlay.set_pin_when_collapsed,
        ).pack()

        # -- Time and hours -----------------------------------------------
        card = controls.section(parent, "Time and hours")
        controls.Segmented(
            card.row("Hour format"),
            list(HOUR_FORMAT_CHOICES), self._label_for(
                HOUR_FORMAT_CHOICES, settings.hour_format, "Clock (7h 30m)"
            ),
            lambda value: self.store.patch_settings(hour_format=HOUR_FORMAT_CHOICES[value]),
        ).pack()
        controls.Select(
            card.row("Date format", "Dates shown in the app; exports and files keep ISO"),
            list(DATE_FORMAT_CHOICES),
            self._label_for(DATE_FORMAT_CHOICES, settings.date_format, "2026-11-13"),
            lambda value: self.store.patch_settings(date_format=DATE_FORMAT_CHOICES[value]),
        ).pack()
        controls.Select(
            card.row("Duration picker step"),
            list(INCREMENT_CHOICES),
            self._label_for(INCREMENT_CHOICES, settings.entry_increment_minutes, "15 minutes"),
            lambda value: self.store.patch_settings(
                entry_increment_minutes=INCREMENT_CHOICES[value]
            ),
        ).pack()
        self._daily_target(card)
        self._work_hours(card)

        # -- Idle ----------------------------------------------------------
        card = controls.section(parent, "Idle")
        controls.Switch(
            card.row("Pause when idle", "Pauses a running timer after a spell with no "
                     "mouse or keyboard input"),
            settings.idle_auto_pause,
            lambda value: self.store.patch_settings(idle_auto_pause=value),
        ).pack()
        controls.Select(
            card.row("Idle threshold"),
            list(IDLE_PAUSE_CHOICES),
            self._label_for(IDLE_PAUSE_CHOICES, settings.idle_pause_minutes, "5 minutes"),
            lambda value: self.store.patch_settings(
                idle_pause_minutes=IDLE_PAUSE_CHOICES[value]
            ),
        ).pack()

    @staticmethod
    def _label_for(choices: dict, value, fallback: str) -> str:
        return next((k for k, v in choices.items() if v == value), fallback)

    def _daily_target(self, card: controls.Card) -> None:
        settings = self.store.db.settings
        step = max(1, settings.entry_increment_minutes)
        minutes = sorted({*range(step, 16 * 60 + 1, step), settings.daily_target_minutes})
        labels = {self._fmt(m): m for m in minutes}
        controls.Select(
            card.row("Daily target"),
            list(labels), self._fmt(settings.daily_target_minutes),
            lambda value: self._commit_target(labels[value]),
        ).pack()

    def _color_row(self, card: controls.Card, caption: str, attr: str, fallback: str) -> None:
        """A swatch that opens the colour chooser and (only while there's a
        custom colour to undo) a Reset button."""
        current = getattr(self.store.db.settings, attr)
        holder = card.row(caption)
        if current:
            controls.action(
                holder, "Reset", lambda: self.store.patch_settings(**{attr: ""})
            ).pack(side="right", padx=(8, 0))
        controls.Swatch(
            holder, current or fallback, lambda: self._pick_color(attr, caption, fallback)
        ).pack(side="right")

    def _pick_color(self, attr: str, title: str, fallback: str) -> None:
        # ``askcolor`` returns ``(None, None)`` on Cancel — nothing to patch.
        _rgb, hex_colour = colorchooser.askcolor(
            color=getattr(self.store.db.settings, attr) or fallback, title=title
        )
        if hex_colour:
            self.store.patch_settings(**{attr: hex_colour})

    # -- work hours -----------------------------------------------------

    def _work_hours(self, card: controls.Card) -> None:
        settings = self.store.db.settings
        self._time_pair(
            card, ("Work day starts", "Work day ends"),
            "work_start_minutes", "work_end_minutes", min_gap=30,
        )
        holder = card.row("Work day", "Default is 9:00 to 5:00 (24-hour clock here)")
        controls.action(
            holder, "Default", lambda: self.store.patch_settings(
                work_start_minutes=9 * 60, work_end_minutes=17 * 60
            ),
        ).pack()

        controls.Switch(
            card.row("Skip lunch on the progress bar"),
            settings.lunch_enabled,
            lambda value: self.store.patch_settings(lunch_enabled=value),
        ).pack()
        if settings.lunch_enabled:
            self._time_pair(
                card, ("Lunch starts", "Lunch ends"),
                "lunch_start_minutes", "lunch_end_minutes", min_gap=5,
            )

    def _time_pair(
        self, card: controls.Card, captions: tuple[str, str], start_attr: str, end_attr: str,
        *, min_gap: int,
    ) -> None:
        """Two hour:minute pickers (a start and an end) that never let the
        range collapse below ``min_gap`` minutes."""
        settings = self.store.db.settings
        for caption, attr in zip(captions, (start_attr, end_attr)):
            holder = card.row(caption)
            value = getattr(settings, attr)
            minute_values = sorted({*range(0, 60, 5), value % 60})
            hour = tk.StringVar(value=f"{value // 60:02d}")
            minute = tk.StringVar(value=f"{value % 60:02d}")

            def commit(_v=None, a=attr, h=hour, m=minute) -> None:
                self._commit_time_pair(
                    start_attr, end_attr, min_gap, a, int(h.get()) * 60 + int(m.get())
                )

            def chosen(variable: tk.StringVar):
                def on_choose(value: str) -> None:
                    variable.set(value)
                    commit()
                return on_choose

            # Packed right-to-left: minutes, ":", hours.
            controls.Select(
                holder, [f"{m:02d}" for m in minute_values], minute.get(), chosen(minute)
            ).pack(side="right")
            theme.label(
                holder, ":", bg=theme.BG_RAISED, font=theme.FONT_SMALL
            ).pack(side="right", padx=3)
            controls.Select(
                holder, [f"{h:02d}" for h in range(24)], hour.get(), chosen(hour)
            ).pack(side="right")

    def _commit_time_pair(
        self, start_attr: str, end_attr: str, min_gap: int, attr: str, minutes: int
    ) -> None:
        """Saves one end of a range, nudging the other to keep ``min_gap``."""
        settings = self.store.db.settings
        start, end = getattr(settings, start_attr), getattr(settings, end_attr)
        last = 24 * 60 - 1
        if attr == start_attr:
            start = minutes
            if end < start + min_gap:
                end = min(start + min_gap, last)
                start = min(start, end - min_gap)
        else:
            end = minutes
            if start > end - min_gap:
                start = max(end - min_gap, 0)
                end = max(end, start + min_gap)
        self.store.patch_settings(**{start_attr: start, end_attr: end})
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
            ("startmenu", "Start menu shortcut", "Lists it in Start, where it can be pinned"),
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
