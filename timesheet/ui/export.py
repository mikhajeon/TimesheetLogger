"""The Export tab: write the Markdown that Cursor drafts the customer note from."""

from __future__ import annotations

import os
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

CURSOR_PROMPT = """Draft my timesheet customer notes from exports/{filename}.

One note per K-code, written for the client to read, in plain past tense. Keep \
each note to 1-3 sentences, cover what was delivered and why it mattered, and \
leave out internal chatter. Output the K-code, the hours, and the note together \
so I can paste each line straight into the timesheet."""


class ExportView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.mode = "week"
        self.anchor = today()
        self.frame = theme.frame(parent)
        self._saved_path: str | None = None

        self._build()

    def _build(self) -> None:
        segmented = theme.frame(self.frame)
        segmented.pack(fill="x", padx=8, pady=(8, 4))
        self.week_btn = theme.button(
            segmented, "Week", lambda: self._set_mode("week"), bg=theme.BG_INPUT
        )
        self.week_btn.pack(side="left", fill="x", expand=True)
        self.day_btn = theme.button(
            segmented, "Day", lambda: self._set_mode("day"), bg=theme.BG_INPUT
        )
        self.day_btn.pack(side="left", fill="x", expand=True)

        nav = theme.frame(self.frame)
        nav.pack(fill="x", padx=8, pady=2)
        theme.button(nav, "\u2039", self._previous, bg=theme.BG, padx=7).pack(side="left")
        self.range_label = theme.button(
            nav, "", self._reset, bg=theme.BG, font=theme.FONT_BOLD
        )
        self.range_label.pack(side="left", fill="x", expand=True)
        theme.button(nav, "\u203a", self._next, bg=theme.BG, padx=7).pack(side="left")

        actions = theme.frame(self.frame)
        actions.pack(fill="x", padx=8, pady=4)
        theme.button(
            actions, "Save to exports/", self._save, bg=theme.ACCENT, fg="#0a1020", hover="#6f9bff"
        ).pack(side="left")
        theme.button(actions, "Copy markdown", self._copy_markdown).pack(side="left", padx=4)
        theme.button(actions, "Copy Cursor prompt", self._copy_prompt).pack(side="left")

        self.status = theme.label(self.frame, "", fg=theme.BILLABLE, font=theme.FONT_SMALL)
        self.status.pack(fill="x", padx=8)

        self.preview = tk.Text(
            self.frame,
            bg=theme.BG_RAISED,
            fg=theme.TEXT,
            font=theme.FONT_MONO,
            relief="flat",
            bd=0,
            wrap="word",
            padx=8,
            pady=7,
            highlightthickness=1,
            highlightbackground=theme.LINE,
        )
        self.preview.pack(fill="both", expand=True, padx=8, pady=(4, 4))

        self.path_label = theme.button(
            self.frame,
            f"Exports: {paths.exports_dir()}",
            self._open_exports,
            bg=theme.BG,
            fg=theme.MUTED,
            font=theme.FONT_TINY,
        )
        self.path_label.pack(fill="x", padx=8, pady=(0, 8))

    # -- range -------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self.refresh()

    def _previous(self) -> None:
        self.anchor = add_days(self.anchor, -7 if self.mode == "week" else -1)
        self.refresh()

    def _next(self) -> None:
        self.anchor = add_days(self.anchor, 7 if self.mode == "week" else 1)
        self.refresh()

    def _reset(self) -> None:
        self.anchor = today()
        self.refresh()

    def _range(self) -> tuple[list[str], str, str]:
        """Returns ``(dates, title, filename)`` for the selected period."""
        if self.mode == "day":
            return [self.anchor], f"Timesheet \u2014 {pretty_date(self.anchor)}", f"{self.anchor}.md"
        start = start_of_week(self.anchor)
        title = (
            f"Timesheet \u2014 Week {iso_week_number(start)} "
            f"({short_date(start)} \u2013 {short_date(add_days(start, 6))})"
        )
        return week_dates(start, True), title, f"{week_label(start)}.md"

    # -- rendering ----------------------------------------------------------

    def refresh(self) -> None:
        dates, title, filename = self._range()
        self.week_btn.config(fg=theme.TEXT if self.mode == "week" else theme.MUTED)
        self.day_btn.config(fg=theme.TEXT if self.mode == "day" else theme.MUTED)

        if self.mode == "day":
            self.range_label.config(text=pretty_date(self.anchor))
        else:
            start = start_of_week(self.anchor)
            self.range_label.config(
                text=f"Week {iso_week_number(start)} \u00b7 {short_date(start)} "
                f"\u2013 {short_date(add_days(start, 6))}"
            )

        self.markdown = build_markdown(self.store.db, dates, title)
        self.filename = filename

        self.preview.config(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", self.markdown)
        self.preview.config(state="disabled")

    # -- actions -------------------------------------------------------------

    def _flash(self, message: str) -> None:
        self.status.config(text=message)
        self.frame.after(2500, lambda: self.status.config(text=""))

    def _save(self) -> None:
        directory = paths.exports_dir()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / self.filename
        target.write_text(self.markdown, "utf-8")
        self._saved_path = str(target)
        self._flash(f"Saved {self.filename}")

    def _copy(self, text: str) -> None:
        self.frame.clipboard_clear()
        self.frame.clipboard_append(text)

    def _copy_markdown(self) -> None:
        self._copy(self.markdown)
        self._flash("Markdown copied")

    def _copy_prompt(self) -> None:
        self._copy(CURSOR_PROMPT.format(filename=self.filename))
        self._flash("Cursor prompt copied \u2014 save the export first")

    def _open_exports(self) -> None:
        directory = paths.exports_dir()
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)  # noqa: S606 - opening Explorer is the intent
