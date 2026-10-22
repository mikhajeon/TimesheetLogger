"""Export: build the Markdown that Cursor turns into customer notes."""

from __future__ import annotations

import subprocess
import tkinter as tk

from .. import paths
from ..dates import (
    add_days,
    iso_week_number,
    pretty_date,
    short_date,
    start_of_week,
    today,
    week_dates,
    week_label,
)
from ..markdown import build_markdown
from ..store import Store
from . import theme

CURSOR_PROMPT = (
    "Draft my timesheet customer notes from exports/{filename}.\n\n"
    "One note per K-code, written for the client to read, in plain past tense. "
    "Keep each note to 1-3 sentences, cover what was delivered and why it mattered, "
    "and leave out internal chatter. Output the K-code, the hours, and the note "
    "together so I can paste each line straight into the timesheet."
)


class ExportView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.mode = "week"
        self.anchor = today()
        self._saved_path = None

        self.frame = theme.frame(parent)
        self._build()

    def _build(self) -> None:
        segmented = theme.frame(self.frame, bg=theme.BG_INPUT)
        segmented.pack(fill="x", padx=10, pady=(10, 6))
        self.mode_buttons = {}
        for name in ("week", "day"):
            btn = theme.button(
                segmented, name.capitalize(), lambda n=name: self._set_mode(n),
                bg=theme.BG_INPUT, fg=theme.MUTED, pady=4,
            )
            btn.pack(side="left", fill="x", expand=True)
            self.mode_buttons[name] = btn

        nav = theme.frame(self.frame)
        nav.pack(fill="x", padx=10)
        theme.button(nav, "\u2039", lambda: self._shift(-1), bg=theme.BG, fg=theme.MUTED).pack(side="left")
        self.range_label = theme.button(nav, "", self._go_today, bg=theme.BG, font=theme.FONT_BOLD)
        self.range_label.config(anchor="center")
        self.range_label.pack(side="left", fill="x", expand=True)
        theme.button(nav, "\u203a", lambda: self._shift(1), bg=theme.BG, fg=theme.MUTED).pack(side="left")

        actions = theme.frame(self.frame)
        actions.pack(fill="x", padx=10, pady=8)
        theme.button(
            actions, "Save to exports/", self._save,
            bg=theme.ACCENT, fg="#0a1020", hover="#7aa2ff", font=theme.FONT_BOLD,
        ).pack(side="left")
        theme.button(actions, "Copy markdown", self._copy_markdown).pack(side="left", padx=5)
        theme.button(actions, "Copy Cursor prompt", self._copy_prompt).pack(side="left")

        self.status = theme.label(self.frame, "", fg=theme.BILLABLE, font=theme.FONT_SMALL)
        self.status.pack(fill="x", padx=10)

        self.preview = tk.Text(
            self.frame,
            bg=theme.BG_RAISED, fg=theme.TEXT, font=theme.FONT_MONO,
            relief="flat", bd=0, wrap="word", padx=8, pady=7,
            highlightthickness=1, highlightbackground=theme.LINE,
            insertbackground=theme.TEXT,
        )
        self.preview.pack(fill="both", expand=True, padx=10, pady=(6, 4))

        self.path_hint = theme.button(
            self.frame, "", self._open_folder, bg=theme.BG, fg=theme.MUTED, font=theme.FONT_TINY
        )
        self.path_hint.config(anchor="w")
        self.path_hint.pack(fill="x", padx=10, pady=(0, 10))

    # -- range selection ----------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self.refresh()

    def _shift(self, step: int) -> None:
        self.anchor = add_days(self.anchor, step * (7 if self.mode == "week" else 1))
        self.refresh()

    def _go_today(self) -> None:
        self.anchor = today()
        self.refresh()

    def _range(self) -> tuple[list[str], str, str]:
        if self.mode == "day":
            return [self.anchor], f"Timesheet — {pretty_date(self.anchor)}", f"{self.anchor}.md"
        start = start_of_week(self.anchor)
        title = (
            f"Timesheet — Week {iso_week_number(start)} "
            f"({short_date(start)} – {short_date(add_days(start, 6))})"
        )
        return week_dates(start, True), title, f"{week_label(start)}.md"

    # -- rendering ----------------------------------------------------------

    def refresh(self) -> None:
        for name, btn in self.mode_buttons.items():
            btn.config(fg=theme.TEXT if name == self.mode else theme.MUTED)

        dates, title, filename = self._range()
        if self.mode == "day":
            self.range_label.config(text=pretty_date(self.anchor))
        else:
            start = start_of_week(self.anchor)
            self.range_label.config(
                text=f"Week {iso_week_number(start)} · "
                f"{short_date(start)} – {short_date(add_days(start, 6))}"
            )

        self.markdown = build_markdown(self.store.db, dates, title)
        self.filename = filename

        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", self.markdown)

        self.path_hint.config(text=f"Exports folder: {paths.exports_dir()}")

    # -- actions -------------------------------------------------------------

    def _flash(self, message: str) -> None:
        self.status.config(text=message)
        self.frame.after(2500, lambda: self.status.config(text=""))

    def _save(self) -> None:
        folder = paths.exports_dir()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / self.filename
        target.write_text(self.markdown, "utf-8")
        self._saved_path = target
        self._flash(f"Saved {self.filename}")

    def _copy_markdown(self) -> None:
        self._to_clipboard(self.markdown)
        self._flash("Markdown copied")

    def _copy_prompt(self) -> None:
        self._to_clipboard(CURSOR_PROMPT.format(filename=self.filename))
        self._flash("Cursor prompt copied — save the export first")

    def _to_clipboard(self, text: str) -> None:
        self.frame.clipboard_clear()
        self.frame.clipboard_append(text)
        self.frame.update()

    def _open_folder(self) -> None:
        folder = paths.exports_dir()
        folder.mkdir(parents=True, exist_ok=True)
        # startfile is the least surprising way to open Explorer on Windows.
        subprocess.Popen(["explorer", str(folder)])
