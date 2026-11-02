"""The Day tab: what's logged, what's still planned, and the timer.

A row here is an ``Entry`` — or, before anything's logged yet, a stand-in for
the task's ``Plan`` — rather than a task: the one thing worth typing most days
is the note on what the time actually was, so that note is the prominent text
on every row, with the task/phase/project it belongs to demoted to a metadata
line underneath (see ``store.DayRow``). Structural task creation lives in the
Setup tab entirely now — see ``timesheet/ui/projects.py`` — so "add work" here
is always "pick an existing task", never "make a new one".

Rows can be viewed four ways (``Settings.day_grouping``): Manual, the default,
is a flat list in a drag-ordered sequence persisted per day; Project, Phase
and Task group rows under a heading instead. Every mode drags — see
``Store.grouped_day_rows`` — a grouped view just keeps each heading's own
rows together while reordering within (and across) them.
"""

from __future__ import annotations

import tkinter as tk

from ..dates import add_days, day_name, format_minutes, short_date, today
from ..models import Recurrence
from ..store import DayRow, Store, TaskContext
from . import icons, theme
from .repeat import RepeatEditor
from .scroll import ScrollFrame

#: A brand new plan starts unset ("Unset") rather than guessing a duration
#: for it — 0 minutes isn't stored as a real ``Plan`` until something (a
#: nonzero figure, a note) gives it a reason to be, so this is also what a
#: task with nothing planned yet reads back as everywhere else.
DEFAULT_PLAN_MINUTES = 0
#: How a duration reads when it's 0 — spelled out rather than left as a
#: dash or "0m", so it reads as a deliberate state (nothing decided yet)
#: rather than something you'd have to double-check isn't just a real zero.
UNSET = "Unset"
NUDGE_MINUTES = 15
#: The +/- nudge chips, the circular timer selector (see ``TIMER_RADIO_SIZE``)
#: and the Actual/Planned Hours value boxes are all sized to keep a grouped
#: view's card no taller than its note row + the centred expand arrow below
#: it — ``card`` auto-sizes to its tallest child (see ``_day_row``'s own
#: comment on ``nudges``), so every one of these right/left-column controls
#: has to fit within that same budget or the card grows past the arrow,
#: leaving a gap underneath it instead of sitting snug against it.
NUDGE_SIZE = 20
NUDGE_RADIUS = 8
#: The circular timer selector, stacked under the drag handle in ``actions``
#: (see ``_day_row``) — sized down from its old 18px to leave room for the
#: drag handle above it within the same height budget as ``NUDGE_SIZE``.
TIMER_RADIO_SIZE = 14
#: The delete chip on the expanded detail panel: small enough to share its
#: row with the Repeat checkbox rather than dominate it.
DELETE_SIZE = 18
DELETE_RADIUS = 4

#: The Day tab's view-mode selector: ``Settings.day_grouping`` value -> label.
GROUPING_LABELS = {
    "manual": "Manual",
    "project": "By Project",
    "phase": "By Phase",
    "task": "By Task",
}
GROUPING_BY_LABEL = {label: mode for mode, label in GROUPING_LABELS.items()}


class TodayView:
    def __init__(self, parent: tk.Misc, store: Store, overlay) -> None:
        self.store = store
        self.overlay = overlay
        self.date = today()
        self.frame = theme.frame(parent)
        #: Tasks whose detail panel (recurrence) is open.
        self._expanded: set[str] = set()
        #: Which row an expanded task's detail panel renders under — the one
        #: actually clicked, not always the task's last row for the day.
        #: Falls back to the last row if a task got into ``_expanded`` some
        #: other way (tests setting it directly, for instance).
        self._expanded_row: dict[str, str] = {}
        self._adding = False
        self._actual_labels: dict[str, tk.Label] = {}
        #: The task last chosen in the add-work panel, held across the
        #: rebuild its own dropdown triggers.
        self._add_task_id: str | None = None
        #: Manual mode's drag state: the row id picked up, if any, and each
        #: row's own widget, so a drop position can be read off its geometry.
        self._drag_id: str | None = None
        self._row_widgets: list[tuple[str, tk.Widget]] = []
        #: The live "it'll land here" bar shown while dragging — created on
        #: the first motion of a given drag, torn down the moment it ends.
        self._drop_indicator: tk.Frame | None = None
        #: The row armed to start next, from its own circular selector —
        #: never more than one, and only meaningful while nothing's already
        #: running (see ``selected_timer_target``). Cleared implicitly
        #: whenever its row stops existing (the entry's deleted, or the day
        #: changes) rather than tracked explicitly, since looking it up
        #: fresh each time already handles that for free.
        self._selected_row_id: str | None = None
        #: The entry just created by "+ log another entry", so the row for
        #: it can claim its note field the moment the refresh it triggers
        #: rebuilds that row — cleared the instant it's used, so it doesn't
        #: keep stealing focus on every later refresh.
        self._focus_entry_id: str | None = None
        #: Rows whose delete control has been clicked once already, so the
        #: trash chip has given way to its own "Delete"/"Cancel" pair — see
        #: ``_detail``. Keyed by ``DayRow.id``, which is stable across a
        #: refresh as long as the row itself still exists.
        self._confirm_delete: set[str] = set()

        self._build_header()
        self.scroll = ScrollFrame(self.frame)
        self.scroll.pack(fill="both", expand=True, padx=8)

    # -- chrome -----------------------------------------------------------

    def _nav_date_text(self) -> str:
        """A quarter of the nav row is narrower than ``pretty_date`` needs —
        "Tuesday 27 Oct 2026" never fit a square button — so this drops to
        the weekday's short form and no year, same as the Export tab's own
        day headings. Still enough to tell which day without opening it."""
        return f"{day_name(self.date)} {short_date(self.date)}"

    def _build_header(self) -> None:
        nav = theme.frame(self.frame)
        nav.pack(fill="x", padx=8, pady=(8, 4))

        # Each a squared, tapered-corner button in its own right rather than
        # a flat native one — same drawn-chip look as the delete/confirm
        # controls elsewhere. The date chip keeps a quarter of the row; the
        # two arrows split whatever that leaves between them, so all three
        # still reach both ends of the row rather than (3 \u00d7 a quarter)
        # leaving a gap down one side — see ``_layout_nav``, which keeps that
        # true as the row is resized.
        self.prev_chip = icons.chip(
            nav, "prev", self._previous_day, fallback="\u2039",
            fill=theme.BG_INPUT, text_fill=theme.TEXT,
            height=theme.NAV_CHIP_SIZE, radius=10, font=theme.FONT_ICON,
        )
        self.prev_chip.pack(side="left")

        self.date_label = theme.chip(
            nav, self._jump_to_today, fill=theme.BG_INPUT, text=self._nav_date_text(),
            text_fill=theme.TEXT, height=theme.NAV_CHIP_SIZE, radius=10, font=theme.FONT_BOLD,
        )
        self.date_label.pack(side="left", padx=4)

        self.next_chip = icons.chip(
            nav, "next", self._next_day, fallback="\u203a",
            fill=theme.BG_INPUT, text_fill=theme.TEXT,
            height=theme.NAV_CHIP_SIZE, radius=10, font=theme.FONT_ICON,
        )
        self.next_chip.pack(side="left")

        nav.bind("<Configure>", self._layout_nav)

        self.progress = tk.Canvas(
            self.frame, height=5, bg=theme.BG_INPUT, highlightthickness=0, bd=0
        )
        self.progress.pack(fill="x", padx=8)

        totals = theme.frame(self.frame)
        totals.pack(fill="x", padx=8, pady=(4, 2))
        self.total_label = theme.label(totals, "", font=theme.FONT_SMALL)
        self.total_label.pack(side="left")
        self.billable_label = theme.label(totals, "", fg=theme.MUTED, font=theme.FONT_SMALL)
        self.billable_label.pack(side="right")

        view_row = theme.frame(self.frame)
        view_row.pack(fill="x", padx=8, pady=(0, 6))
        theme.label(view_row, "View", fg=theme.MUTED, font=theme.FONT_TINY).pack(side="left")
        self._grouping_var = tk.StringVar(
            value=GROUPING_LABELS[self.store.db.settings.day_grouping]
        )
        theme.option_menu(
            view_row, self._grouping_var, list(GROUPING_LABELS.values()),
            command=self._on_grouping_change,
        ).pack(side="left", padx=(4, 0))

    #: ``date_label``'s own ``padx`` on each side (see ``_build_header``) —
    #: real gaps between the chips, not usable width, so they come off the
    #: row before what's left is split between the two arrows.
    NAV_GAP = 8

    def _layout_nav(self, event: tk.Event) -> None:
        middle = max(1, event.width // 4)
        arrow = max(1, (event.width - middle - self.NAV_GAP) // 2)
        self.date_label.config(width=middle)
        self.prev_chip.config(width=arrow)
        self.next_chip.config(width=arrow)

    def _previous_day(self) -> None:
        self.date = add_days(self.date, -1)
        self.refresh()

    def _next_day(self) -> None:
        self.date = add_days(self.date, 1)
        self.refresh()

    def _jump_to_today(self) -> None:
        self.date = today()
        self.refresh()

    def _on_grouping_change(self, label: str) -> None:
        mode = GROUPING_BY_LABEL.get(label)
        if mode:
            self.store.patch_settings(day_grouping=mode)

    # -- rendering ---------------------------------------------------------

    def refresh(self) -> None:
        theme.commit_focused_entry(self.frame)
        self.date_label.set_text(self._nav_date_text())
        self._grouping_var.set(GROUPING_LABELS[self.store.db.settings.day_grouping])

        total, billable = self.store.day_totals(self.date)
        target = self.store.db.settings.daily_target_minutes
        self.total_label.config(text=f"{format_minutes(total)} of {format_minutes(target)}")
        self.billable_label.config(text=f"{format_minutes(billable)} billable")
        self._draw_progress(total, target)

        self.scroll.clear()
        self._actual_labels.clear()
        self._row_widgets = []
        body = self.scroll.body

        groups = self.store.grouped_day_rows(self.date)
        manual = self.store.db.settings.day_grouping == "manual"
        if not any(rows for _heading, rows in groups):
            theme.label(
                body,
                "Nothing planned for this day yet.",
                fg=theme.MUTED,
                font=theme.FONT_SMALL,
                anchor="center",
            ).pack(fill="x", pady=16)
        for heading, rows in groups:
            if heading:
                theme.label(
                    body, heading, font=theme.FONT_HEAD
                ).pack(fill="x", pady=(7, 2))
            self._render_rows(body, rows, manual)

        self._add_panel(body)
        self._day_note(body)

    def _draw_progress(self, total: int, target: int) -> None:
        self.progress.delete("all")
        width = max(1, self.progress.winfo_width())
        fraction = min(1.0, total / target) if target else 0.0
        self.progress.create_rectangle(
            0, 0, width * fraction, 5, fill=theme.ACCENT, outline=""
        )

    def _render_rows(self, parent: tk.Misc, rows: list[DayRow], manual: bool) -> None:
        last_index: dict[str, int] = {}
        last_row_id: dict[str, str] = {}
        for index, row in enumerate(rows):
            last_index[row.context.task.id] = index
            last_row_id[row.context.task.id] = row.id
        for index, row in enumerate(rows):
            task_id = row.context.task.id
            # The detail panel, and this row's own editable Planned Hours
            # (see ``_day_row``), both sit right under whichever row was
            # actually clicked to open it — not always the task's last row
            # for the day. "+ log another entry" stays tied to the last row
            # regardless of where that anchor is, and shows even when that
            # row is still a placeholder — the row itself isn't a real entry
            # yet, so this is the only way to get a second one logged before
            # the first is ever touched.
            #
            # Manual mode never shows it at all: its rows are one flat,
            # freely-interleaved list with no heading to say which task a
            # given row belongs to beyond the small print already on the
            # row itself, so a link sitting under an unrelated-looking next
            # row would be guessing which task it's for.
            anchor = self._expanded_row.get(task_id, last_row_id[task_id])
            is_anchor = task_id in self._expanded and row.id == anchor
            self._day_row(parent, row, manual, is_anchor)
            if is_anchor:
                self._detail(parent, row)
            if index == last_index[task_id] and not manual:
                self._add_another_link(parent, row.context)

    def _day_row(self, parent: tk.Misc, row: DayRow, manual: bool, is_anchor: bool) -> None:
        task = row.context.task
        running = bool(
            self.store.db.active_timer and self.store.db.active_timer.task_id == task.id
        )
        card = theme.frame(parent, bg=theme.BG_RAISED, highlightthickness=1)
        if row.id == self._drag_id:
            # The row actually being picked up gets its own, distinct
            # highlight — thicker and in the accent colour — so it's clear
            # which one the drop indicator belongs to while several rows
            # are on screen at once.
            card.config(highlightbackground=theme.ACCENT, highlightthickness=2)
        else:
            card.config(highlightbackground=theme.RUNNING_LINE if running else theme.LINE)
        card.pack(fill="x", pady=2)
        self._row_widgets.append((row.id, card))

        # Packed, and so reserved, before ``top``/``meta`` below — a
        # side="right" child claims a column spanning the parent's full
        # height, not just its own packing turn's slice of it (and,
        # unanchored, sits centred in it). ``stats`` is created here (so
        # it's still the card's first child) but not geometry-managed
        # until after ``nudges`` just below, so the nudge stack — not
        # Actual/Planned Hours — claims the card's own rightmost edge.
        stats = theme.frame(card, bg=theme.BG_RAISED)

        # The +/- nudges, stacked one above the other, sitting past
        # Actual/Planned Hours at the card's own right edge. Deleting a
        # row lives on its expanded detail panel instead now (see
        # ``_detail``), not beside these.
        nudges = theme.frame(card, bg=theme.BG_RAISED)
        # Tight margins on purpose: with the identity line hidden outside
        # Manual mode (see ``meta`` below), this +/- stack — not the note
        # rows — is what the card's own height ends up tracking (``card``
        # auto-sizes to its tallest child, and nothing propagates a pady
        # change here into the note/arrow gaps, which live on their own
        # widgets entirely), so trimming it is how a grouped view's cards
        # get shorter without touching a single note or arrow gap.
        nudges.pack(side="right", padx=(4, 6), pady=1)
        # ``fill`` matches ``bg`` rather than standing out on its own: the
        # icon already draws its own ring (``circle-plus``/``circle-minus``),
        # so a filled backdrop behind it just doubled up on the same idea.
        # ``circle=True`` keeps the hover feedback (see ``theme.chip``) to a
        # ring traced around that same shape, rather than a squared-off box
        # that reads as mismatched behind a round glyph.
        icons.chip(
            nudges, "plus", lambda: self._nudge(row, NUDGE_MINUTES),
            fallback="+", fill=theme.BG_RAISED, text_fill=theme.MUTED, bg=theme.BG_RAISED,
            size=NUDGE_SIZE, radius=NUDGE_RADIUS, circle=True,
        ).pack(side="top")
        icons.chip(
            nudges, "minus", lambda: self._nudge(row, -NUDGE_MINUTES),
            fallback="\u2212", fill=theme.BG_RAISED, text_fill=theme.MUTED, bg=theme.BG_RAISED,
            size=NUDGE_SIZE, radius=NUDGE_RADIUS, circle=True,
        ).pack(side="top")

        stats.pack(side="right", padx=(4, 4), pady=1)

        # A left-hand column mirroring ``nudges``/``stats`` on the right —
        # packed ``side="left"`` before ``top`` below, so it too claims a
        # column spanning the card's full height, with the drag handle and
        # timer selector stacked one above the other in it rather than
        # sharing a row with the note field.
        actions = theme.frame(card, bg=theme.BG_RAISED)
        actions.pack(side="left", padx=(6, 2), pady=1, anchor="n")

        # Every view drags now, not just Manual (see ``Store.grouped_day_rows``)
        # — top-left on every card, same spot Manual always had it, so a
        # habit formed there carries straight over to a grouped view.
        handle = theme.label(
            actions, "\u2630", bg=theme.BG_RAISED, fg=theme.MUTED,
            font=theme.FONT_SMALL, cursor="fleur",
        )
        handle.pack(side="top")
        handle.bind("<ButtonPress-1>", lambda e, rid=row.id: self._drag_start(rid, e))
        handle.bind("<B1-Motion>", self._drag_motion)
        handle.bind("<ButtonRelease-1>", self._drag_end)

        # The one entry the timer bar's Start/Stop acts on — a circular,
        # single-choice selector rather than a Play/Stop button on just the
        # task's first row, so any specific entry (any recurring note's own
        # occurrence included) can be the one timed, not only whichever
        # happened to render first. Filled green on the row actually
        # running; filled accent on the one armed to start next; disabled
        # everywhere else while something is already running, since only
        # one timer runs at a time regardless of which row started it.
        #
        # Stacked right under the drag handle, in the same ``actions``
        # column, rather than sharing a row (or a corner ``place``d on
        # ``card``) with anything else — a sibling stacked with ``pack``
        # can't overlap another the way a bare ``place`` call could.
        timer = self.store.db.active_timer
        timer_is_this_row = bool(
            timer and timer.task_id == task.id
            and (timer.recurring_note_id or "") == (row.recurring_note_id or "")
        )
        armed = not timer and self._selected_row_id == row.id
        timer_radio = theme.radio(
            actions,
            timer_is_this_row or armed,
            lambda r=row, running_here=timer_is_this_row: self._toggle_timer_selection(
                r, running_here
            ),
            fill=theme.BILLABLE if timer_is_this_row else theme.ACCENT,
            bg=theme.BG_RAISED,
            size=TIMER_RADIO_SIZE,
            enabled=timer is None or timer_is_this_row,
        )
        timer_radio.pack(side="top", pady=(4, 0))

        top = theme.frame(card, bg=theme.BG_RAISED)
        top.pack(fill="x", padx=(6, 2), pady=(5, 1))

        # Whether *this* row repeats — an already-logged entry carries its
        # own ``recurring_note_id`` once tagged onto a note (see
        # ``promote_to_recurring_note``), which ``DayRow.recurring_note_id``
        # itself only ever holds before that entry exists (it's how the
        # placeholder finds its note) — so both are checked here.
        is_recurring = bool(
            row.entry.recurring_note_id if row.entry else row.recurring_note_id
        )
        if is_recurring:
            # Packed before ``note`` below, not after: a side="right" child
            # claims its slice of the row in the order it's packed, same as
            # ``stats``/``nudges`` above claim the card's own right edge
            # before ``top`` is even built — packed after an
            # expand=True/fill="x" sibling, it would get no room at all.
            icons.label(
                top, "recurring", fallback="\u21bb",
                bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
            ).pack(side="right", padx=(4, 0))

        note = theme.entry(top, font=theme.FONT_SMALL)
        note.insert(0, row.note)
        note.pack(side="left", fill="x", expand=True, padx=(0, 2))
        note.bind("<FocusOut>", lambda _e, r=row, w=note: self._commit_row_note(r, w))
        note.bind("<Return>", lambda _e: self.frame.focus_set())
        # "+ log another entry" (see ``_add_another_link``) wants its new
        # row's note field focused the instant it exists, rather than
        # leaving the user to click into it — cleared right away so a
        # later refresh of this same row (ticking seconds, say) doesn't
        # keep yanking focus back here.
        if row.entry is not None and row.entry.id == self._focus_entry_id:
            self._focus_entry_id = None
            note.focus_set()

        # The internal note itself — never rolled up into an export (see
        # ``bookings.collect``, which reads ``Entry.note`` alone) — lives on
        # the row's own expanded detail panel now (see ``_detail``), not on
        # the card face, so it doesn't compete for space with the
        # customer-facing note above.

        # The dot/K-code/"Client \u00b7 Project \u00b7 Task" identity line.
        # A grouped view's own heading already says all of this once for
        # every row under it, so repeating it per row there is pure noise
        # — shown only in Manual mode, where each row can belong to a
        # different task and nothing else on screen says which. Built
        # either way, so the click target and its binding below always
        # exist, but packed only in Manual — and ahead of the note there
        # (``before=top``), not below it: in a flat, undifferentiated list
        # it's the one thing worth reading before the note itself.
        meta = theme.frame(card, bg=theme.BG_RAISED)
        identity = theme.frame(meta, bg=theme.BG_RAISED)
        identity.pack(side="left", fill="x", expand=True)
        theme.dot(identity, row.context.billable, bg=theme.BG_RAISED).pack(side="left", pady=1)
        theme.pill(identity, row.context.project.k_code).pack(side="left", padx=4)
        identity_label = theme.label(
            identity,
            f"{row.context.client.name} \u00b7 {row.context.project.name} \u00b7 "
            f"{row.context.display_title}",
            bg=theme.BG_RAISED,
            fg=theme.MUTED if row.context.project.closed else theme.TEXT,
            font=theme.FONT_TINY,
        )
        identity_label.pack(side="left", fill="x", expand=True)
        for widget in (identity, identity_label):
            widget.bind(
                "<Button-1>",
                lambda _e, tid=task.id, rid=row.id: self._toggle_expanded(tid, rid),
            )
            widget.config(cursor="hand2")
        if manual:
            meta.pack(fill="x", padx=6, pady=(5, 2), before=top)

        # A shared label column, right-aligned and wide enough for either
        # label, so the values beside them — actual above, planned below —
        # start at the same x no matter which widget (live label, picker,
        # or plain text) ends up filling either slot.
        label_kw = dict(bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
                         width=len("Planned Hours"), anchor="e", pady=0)

        theme.label(stats, "Actual Hours", **label_kw).grid(
            row=0, column=0, padx=(0, 5), sticky="e"
        )
        if timer_is_this_row:
            # Ticking live while it runs, so a fixed set of 15-minute
            # choices (see ``theme.duration_picker``) would either lag
            # behind or have to grow a new option every minute — a plain
            # label just reads it back, the same as it's shown anywhere
            # else a figure's purely informational rather than editable.
            live = self.store.logged_minutes(task.id, self.date) + self.store.live_minutes(
                task.id, self.date
            )
            actual_label = theme.label(
                stats, format_minutes(live),
                bg=theme.BG_RAISED, fg=theme.TEXT, font=theme.FONT_TINY,
                width=5, anchor="w",
            )
            actual_label.grid(row=0, column=1, sticky="w")
            self._actual_labels[task.id] = actual_label
        else:
            shown = row.entry.minutes if row.entry else 0
            theme.duration_picker(
                stats, shown, lambda m, r=row: self._commit_row_minutes(r, m),
                width=6, font=theme.FONT_TINY, pady=0,
                step=self.store.db.settings.entry_increment_minutes,
            ).grid(row=0, column=1, sticky="w")

        if row.recurring_note_id and row.is_placeholder:
            # No Planned Hours here: it operates on the task's single Plan
            # for the day, and setting it would create one — which, per
            # Store.day_rows, suppresses every recurring note's row that
            # day, including this one's siblings. The usual duration is
            # shown instead, as a hint rather than a live field.
            theme.label(
                stats, f"usually {format_minutes(row.plan_minutes)}", **label_kw
            ).grid(row=1, column=0, columnspan=2, pady=(2, 0), sticky="e")
        else:
            # Shown and editable on every row now, not just whichever one
            # is expanded — the same as Actual Hours just above. Once an
            # entry exists this is that entry's own Planned Hours (see
            # ``Entry.plan_minutes``), independent of every other entry the
            # task may also have logged today; before that, it's still the
            # task's one day-level ``Plan``, same as ever, since there's no
            # specific entry yet for a figure to belong to.
            theme.label(stats, "Planned Hours", **label_kw).grid(
                row=1, column=0, padx=(0, 5), sticky="e"
            )
            if row.entry is not None:
                set_minutes = lambda m, eid=row.entry.id: self.store.update_entry(
                    eid, plan_minutes=m
                )
            else:
                set_minutes = lambda m, tid=task.id: self.store.set_plan(tid, self.date, m)
            theme.duration_picker(
                stats, row.plan_minutes, set_minutes,
                width=6, font=theme.FONT_TINY, pady=0,
                label=lambda m: format_minutes(m) if m else UNSET,
                step=self.store.db.settings.entry_increment_minutes,
            ).grid(row=1, column=1, sticky="w")

        # A dedicated, centred affordance for expanding or collapsing this
        # row's detail panel (see ``_detail``) — Manual mode's identity row
        # above toggles it too (a bigger, easier target there), but this is
        # the one dependable spot for it regardless of grouping or how long
        # that row's text runs. The glyph itself says which way it goes.
        toggle_row = theme.frame(card, bg=theme.BG_RAISED)
        toggle_row.pack(fill="x")
        expand_arrow = icons.label(
            toggle_row,
            "row-collapse" if is_anchor else "row-expand",
            fallback="\u25b4" if is_anchor else "\u25be",
            bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_ICON, cursor="hand2",
        )
        expand_arrow.pack()
        expand_arrow.bind(
            "<Button-1>", lambda _e, tid=task.id, rid=row.id: self._toggle_expanded(tid, rid)
        )

    def _add_another_link(self, parent: tk.Misc, context: TaskContext) -> None:
        """A second (or third...) entry for a task already logged today —
        a different stint worth its own note, rather than folding into one.

        Starts at 0m — its own Actual Hours picker is right there to fill in
        a real figure, rather than this guessing one (``NUDGE_MINUTES``, say)
        that would just need overwriting anyway. Its note field claims focus
        the moment the row for it exists (see ``_day_row``), so the user can
        start typing straight away instead of having to click into it."""

        def add() -> None:
            entry = self.store.add_entry(context.task.id, self.date, 0, "")
            if entry is not None:
                self._focus_entry_id = entry.id

        theme.button(
            parent,
            "+ log another entry",
            add,
            bg=theme.BG,
            fg=theme.MUTED,
            font=theme.FONT_TINY,
            pady=2,
        ).pack(fill="x", pady=(0, 2))

    def _detail(self, parent: tk.Misc, row: DayRow) -> None:
        """This one row's own Repeat editor, tucked under the last row for
        the task, with the row's own delete control on the same line as
        the Repeat checkbox. Nothing structural about the task lives here
        any more — billable, archive and task-level delete are all
        Setup-tab-only now (see ``timesheet/ui/projects.py``); this panel
        exists purely to plan today's (and future) logging, and to dismiss
        this one row, not to manage the task record itself. The dot beside
        the task's name on every row still reflects billable, just never
        edits it from here."""
        theme.hairline(parent).pack(fill="x")
        box = theme.frame(parent, bg=theme.BG_RAISED)
        box.pack(fill="x", padx=6, pady=5)

        self._internal_note(box, row)
        theme.hairline(box).pack(fill="x", pady=(0, 5))

        # Always present, placeholder or not — it used to only appear once
        # an entry existed, which made the first "+" click (which is what
        # creates that entry) look like it had conjured a delete button out
        # of nowhere. Inert until there's something to act on, rather than
        # absent. A recurring note's still-unlogged placeholder is the one
        # exception: there's nothing to delete yet, but dismissing it
        # outright (skipping today's occurrence) is still something worth
        # offering here rather than only after first logging, then deleting.
        can_dismiss = not row.is_placeholder or bool(row.recurring_note_id)

        def header_trailing(header_row: tk.Misc) -> None:
            # A second click away from destructive rather than a modal over
            # the whole window — the trash chip itself becomes the question,
            # the same recipe the Setup tab's own delete chip already uses
            # (see ``projects.py``'s ``_delete_chip``). "Delete" reads as
            # "Skip" for a recurring note's still-unlogged placeholder,
            # matching what it actually does (see ``_confirm_delete_entry``).
            if row.id in self._confirm_delete:
                wrapper = theme.frame(header_row, bg=theme.BG_RAISED)
                theme.button(
                    wrapper, "Skip" if row.entry is None else "Delete",
                    lambda: self._confirm_delete_entry(row),
                    bg=theme.DANGER, fg=theme.ON_DANGER,
                    font=theme.FONT_TINY, padx=6, pady=1,
                ).pack(side="left")
                theme.button(
                    wrapper, "Cancel", lambda: self._disarm_delete_entry(row.id),
                    font=theme.FONT_TINY, padx=6, pady=1,
                ).pack(side="left", padx=(4, 0))
                wrapper.pack(side="right")
                return
            # "trash" once there's something to delete — the same glyph and
            # tint the Setup/Recurring tabs' own delete button uses, sitting
            # straight on the row rather than a coloured chip behind it (see
            # ``_nudge`` just above for why). "delete"'s own MUTED tint says
            # the same thing while there's nothing yet to act on.
            icons.chip(
                header_row, "trash" if can_dismiss else "delete",
                (lambda: self._arm_delete_entry(row.id)) if can_dismiss else (lambda: None),
                fallback="\u00d7",
                fill=theme.BG_RAISED, text_fill=theme.DANGER if can_dismiss else theme.MUTED,
                bg=theme.BG_RAISED, size=DELETE_SIZE, radius=DELETE_RADIUS,
                enabled=can_dismiss,
            ).pack(side="right")

        # Held on the instance because Tk keeps only a weak reference to the
        # variables the editor owns; a local would let them be collected and
        # the editor would render empty. See ``_set_row_recurrence`` for why
        # a logged entry's own tie is read off ``row.entry`` rather than
        # ``row.recurring_note_id``.
        note_id = row.entry.recurring_note_id if row.entry else row.recurring_note_id
        recurring_note = self.store.db.recurring_note(note_id) if note_id else None
        self._repeat = RepeatEditor(
            box,
            recurring_note.recurrence if recurring_note else None,
            lambda rule, r=row: self._set_row_recurrence(r, rule),
            bg=theme.BG_RAISED,
            header_trailing=header_trailing,
            step=self.store.db.settings.entry_increment_minutes,
        )

    def _set_row_recurrence(self, row: DayRow, rule: Recurrence | None) -> None:
        """Wires this one row's Repeat editor to its own ``RecurringNote``
        rather than a task-wide rule — see ``Store.promote_to_recurring_note``.
        A row not already tied to a note creates one the first time Repeat
        is switched on; after that (including switching it back off again)
        this is the same note every time, never a fresh one.

        ``DayRow.recurring_note_id`` is "" for a logged entry regardless of
        whether that entry is itself tied to one (see its own docstring), so
        a real entry's tie has to be read off ``row.entry`` instead.
        """
        note_id = row.entry.recurring_note_id if row.entry else row.recurring_note_id
        if note_id:
            self.store.set_row_note_rule(note_id, rule)
        elif rule is not None:
            self.store.promote_to_recurring_note(row, self.date, rule)
            self.overlay.refresh()

    def _internal_note(self, parent: tk.Misc, row: DayRow) -> None:
        """This one row's own internal note — never rolled up into an
        export (see ``bookings.collect``, which reads ``Entry.note``
        alone) — on its expanded detail panel rather than the card face,
        where a single-line field left it cramped. Five rows tall, so it
        reads like a place to actually write something, not just a second
        label field. Disabled rather than hidden for a bare Plan
        placeholder: there's no record yet to hang it off (``Plan`` has no
        internal note of its own), so it stays visible but inert, the same
        idea the delete chip already uses for a row nothing can act on yet.
        """
        theme.label(
            parent, "Internal note \u2013 not exported",
            bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
        ).pack(fill="x", pady=(0, 2))
        box = theme.text_box(parent, height=5, font=theme.FONT_SMALL)
        box.pack(fill="x", pady=(0, 5))
        can_hold_internal_note = row.entry is not None or bool(row.recurring_note_id)
        if can_hold_internal_note:
            box.insert("1.0", row.internal_note)
            box.bind(
                "<FocusOut>",
                lambda _e, r=row, w=box: self._commit_row_internal_note(
                    r, w.get("1.0", "end-1c")
                ),
            )
        else:
            box.config(state="disabled")

    def _day_note(self, parent: tk.Misc) -> None:
        theme.label(
            parent,
            "Day note \u2014 context that isn't tied to one task",
            fg=theme.MUTED,
            font=theme.FONT_TINY,
        ).pack(fill="x", pady=(10, 2))
        box = theme.text_box(parent, height=3)
        box.insert("1.0", self.store.db.day_notes.get(self.date, ""))
        box.pack(fill="x", pady=(0, 10))
        box.bind("<FocusOut>", lambda _e: self._commit_day_note(box))

    def _add_panel(self, parent: tk.Misc) -> None:
        if not self._adding:
            theme.button(
                parent,
                "+ Add task to this day",
                self._open_add,
                bg=theme.BG,
                fg=theme.MUTED,
                pady=6,
            ).pack(fill="x", pady=(6, 0))
            return

        panel = theme.frame(parent, bg=theme.BG_RAISED)
        panel.pack(fill="x", pady=(6, 0))
        inner = theme.frame(panel, bg=theme.BG_RAISED)
        inner.pack(fill="x", padx=8, pady=8)

        # Only existing, leaf tasks not already on this day — making a new
        # one is the Setup tab's job now, and anything already here has its
        # own row to log more time or add a note against instead.
        on_day = {c.task.id for c in self.store.contexts_for_date(self.date)}
        available = [
            c for c in self.store.active_contexts()
            if not c.project.closed and c.task.id not in on_day
        ]
        if not available:
            theme.label(
                inner,
                "Every open task is already on this day \u2014 add more in the Setup tab.",
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
        row.pack(fill="x")
        theme.label(
            row, "Planned Hours", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY
        ).pack(side="left", padx=(0, 3))
        plan_minutes = {"value": DEFAULT_PLAN_MINUTES}
        theme.duration_picker(
            row, plan_minutes["value"], lambda m: plan_minutes.__setitem__("value", m),
            width=7, font=theme.FONT_SMALL,
            label=lambda m: format_minutes(m) if m else UNSET,
            step=self.store.db.settings.entry_increment_minutes,
        ).pack(side="left")

        actions = theme.frame(inner, bg=theme.BG_RAISED)
        actions.pack(fill="x", pady=(6, 0))

        def create() -> None:
            task_id = self._add_task_id
            if not task_id:
                return
            self.store.set_plan(task_id, self.date, plan_minutes["value"])
            note = note_entry.get().strip()
            if note:
                self.store.set_plan_note(task_id, self.date, note)
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

        # Enter finishes the note field; Ctrl+Enter adds from anywhere in
        # the panel, including either dropdown.
        note_entry.bind("<Return>", lambda _e: create())
        theme.bind_tree(panel, "<Control-Return>", lambda _e: create())

    # -- actions ------------------------------------------------------------

    def _open_add(self) -> None:
        self._adding = True
        self.refresh()

    def _close_add(self) -> None:
        self._adding = False
        self.refresh()

    def _toggle_expanded(self, task_id: str, row_id: str) -> None:
        self._expanded.symmetric_difference_update({task_id})
        if task_id in self._expanded:
            self._expanded_row[task_id] = row_id
        else:
            self._expanded_row.pop(task_id, None)
        self.refresh()

    def _toggle_timer_selection(self, row: DayRow, running_here: bool) -> None:
        """A row's own circular selector, clicked.

        On the row actually running, this is a shortcut straight to Stop —
        the same convenience the old Play/Stop button offered on one row.
        Everywhere else, while nothing is running, it arms (or disarms) this
        row as the one the timer bar's own Start button will act on; while
        something else is already running, it does nothing; only one timer
        ever runs at a time, so there is nothing else here to select yet.
        """
        if running_here:
            self.store.stop_timer()
            return
        if self.store.db.active_timer:
            return
        self._selected_row_id = None if self._selected_row_id == row.id else row.id
        # The timer bar's Start button lives outside this view entirely, so
        # arming a row needs the overlay's own refresh — not just this
        # view's — to pick the change up; unlike a store mutation, nothing
        # else makes that happen on its own.
        self.overlay.refresh()

    def selected_timer_target(self) -> DayRow | None:
        """The row armed to start next, if any — looked up fresh rather than
        held onto, so a row that's stopped existing since (its entry
        deleted, say) quietly yields nothing instead of a stale one."""
        if self.store.db.active_timer or not self._selected_row_id:
            return None
        return next(
            (r for r in self.store.day_rows(self.date) if r.id == self._selected_row_id), None
        )

    def _arm_delete_entry(self, row_id: str) -> None:
        """First click on the trash chip: swaps it for "Delete"/"Cancel"
        rather than popping a modal over the whole window — see
        ``_detail``'s ``header_trailing``."""
        self._confirm_delete.add(row_id)
        self.refresh()

    def _disarm_delete_entry(self, row_id: str) -> None:
        self._confirm_delete.discard(row_id)
        self.refresh()

    def _confirm_delete_entry(self, row: DayRow) -> None:
        """The second click — "Delete" (or "Skip", for a recurring note's
        still-unlogged placeholder) itself armed by ``_arm_delete_entry``.

        Deleting a logged entry has no undo visible in the UI, unlike the
        +/- nudges — reversible right up until the entry's gone entirely —
        so unlike those, this is one click short of destructive and gets its
        own pair of buttons rather than acting on the first click.

        A recurring note's still-unlogged placeholder gets the same pair,
        but dismisses today's occurrence outright (see
        ``Store.skip_recurring_note``) rather than deleting an entry that
        doesn't exist yet.
        """
        self._confirm_delete.discard(row.id)
        if row.entry is not None:
            self.store.delete_entry(row.entry.id)
        elif row.recurring_note_id:
            self.store.skip_recurring_note(row.recurring_note_id, self.date)

    def _nudge(self, row: DayRow, delta: int) -> None:
        if row.entry is not None:
            self.store.adjust_entry_minutes(row.entry.id, delta)
        else:
            # Nothing logged yet: the first nudge is what creates the entry.
            self.store.adjust_entry(
                row.context.task.id, self.date, delta, row.plan_note, row.recurring_note_id,
                row.plan_internal_note,
            )

    def _commit_row_note(self, row: DayRow, widget: tk.Entry) -> None:
        text = widget.get()
        if text == row.note:
            return
        if row.entry is not None:
            self.store.update_entry(row.entry.id, note=text)
        elif row.recurring_note_id:
            # Promotes straight to today's entry, at the note's usual
            # duration — set_plan_note would create a Plan instead, and a
            # pinned Plan suppresses every recurring note's row for the day
            # (see Store.day_rows), swallowing whichever others are left.
            self.store.add_entry(
                row.context.task.id, self.date, row.plan_minutes or DEFAULT_PLAN_MINUTES,
                text, row.recurring_note_id, row.plan_internal_note,
            )
        else:
            self.store.set_plan_note(row.context.task.id, self.date, text)

    def _commit_row_internal_note(self, row: DayRow, text: str) -> None:
        if text == row.internal_note:
            return
        if row.entry is not None:
            self.store.update_entry(row.entry.id, internal_note=text)
        elif row.recurring_note_id:
            # Mirrors ``_commit_row_note``'s own promotion above — this
            # field only shows at all once there's a recurring note or a
            # real entry to carry it (see ``_day_row``), so the bare
            # ``Plan``-only branch that method also handles never applies.
            self.store.add_entry(
                row.context.task.id, self.date, row.plan_minutes or DEFAULT_PLAN_MINUTES,
                row.plan_note, row.recurring_note_id, text,
            )

    def _commit_row_minutes(self, row: DayRow, minutes: int) -> None:
        if row.entry is not None:
            self.store.update_entry(row.entry.id, minutes=minutes)
        elif minutes:
            # Choosing a duration on a placeholder is what logs the first entry.
            self.store.add_entry(
                row.context.task.id, self.date, minutes, row.plan_note, row.recurring_note_id,
                row.plan_internal_note,
            )

    def _commit_day_note(self, widget: tk.Text) -> None:
        text = widget.get("1.0", "end-1c")
        if text != self.store.db.day_notes.get(self.date, ""):
            self.store.set_day_note(self.date, text)

    # -- manual drag-reorder --------------------------------------------------

    def _drag_start(self, row_id: str, event: tk.Event) -> None:
        self._drag_id = row_id
        # No render happens again until the drag ends (see ``_drag_end``),
        # so the dragged card's own highlight — set from ``self._drag_id``
        # when ``_day_row`` first built it — has to be pushed onto the
        # widget directly here, rather than waiting on a redraw that isn't
        # coming until the mouse button comes back up.
        widget = self._card_widget(row_id)
        if widget is not None:
            widget.config(highlightbackground=theme.ACCENT, highlightthickness=2)

    def _drag_motion(self, event: tk.Event) -> None:
        if self._drag_id is None:
            return
        others = [
            (row_id, widget) for row_id, widget in self._row_widgets
            if row_id != self._drag_id and widget.winfo_exists()
        ]
        if not others:
            self._hide_drop_indicator()  # nothing else to reorder against
            return
        parent = others[0][1].master
        target = self._drop_target(event.y_root)
        if target is not None:
            edge = next(widget for row_id, widget in others if row_id == target)
            y = edge.winfo_rooty() - parent.winfo_rooty() - 2
        else:
            last = others[-1][1]
            y = last.winfo_rooty() + last.winfo_height() - parent.winfo_rooty()
        self._show_drop_indicator(parent, y)

    def _drag_end(self, event: tk.Event) -> None:
        dragged = self._drag_id
        self._drag_id = None
        self._hide_drop_indicator()
        reordered = False
        if dragged is not None:
            order = [row_id for row_id, widget in self._row_widgets if widget.winfo_exists()]
            if dragged in order:
                target = self._drop_target(event.y_root)
                order.remove(dragged)
                if target is not None:
                    order.insert(order.index(target), dragged)
                else:
                    order.append(dragged)
                self.store.set_day_order(self.date, order)
                reordered = True
        if not reordered:
            # A store change would otherwise be what clears the dragged
            # row's own highlight back to normal (see ``_day_row``) — with
            # no store change this time, nothing else is going to redraw it.
            # Deferred, same reason ``Overlay.refresh`` defers its own
            # rebuild: a callback still on the stack (this one) shouldn't
            # destroy the very widget it's running from.
            self.frame.after_idle(self.refresh)

    def _card_widget(self, row_id: str) -> tk.Widget | None:
        return next(
            (w for rid, w in self._row_widgets if rid == row_id and w.winfo_exists()), None
        )

    def _drop_target(self, y_root: int) -> str | None:
        """The row to land in front of, among rows other than the one being
        dragged — or ``None`` for "at the very end", the only two outcomes a
        drop can actually produce. Found by comparing ``y_root`` against
        each row's own screen position — there's no drop-target widget to
        ask directly, since the drag started on a handle inside the row
        that's moving."""
        for row_id, widget in self._row_widgets:
            if row_id == self._drag_id or not widget.winfo_exists():
                continue
            if y_root <= widget.winfo_rooty() + widget.winfo_height() // 2:
                return row_id
        return None

    def _show_drop_indicator(self, parent: tk.Misc, y: int) -> None:
        """A thin bar at the boundary the dragged row would land on if
        dropped right now.

        "Transparent" in the sense Tk widgets can actually manage — there's
        no real alpha channel short of a whole ``Toplevel``'s own, so this
        fakes it the same way ``theme.blend`` always does: mixed toward the
        page's own background rather than drawn as a flat, fully opaque
        stripe, so it reads as a hover highlight rather than new content.
        """
        if self._drop_indicator is None or not self._drop_indicator.winfo_exists():
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

    def tick(self) -> None:
        """Updates the running task's own row without rebuilding the list."""
        timer = self.store.db.active_timer
        if not timer:
            return
        widget = self._actual_labels.get(timer.task_id)
        if widget is None or not widget.winfo_exists():
            return
        logged = self.store.logged_minutes(
            timer.task_id, self.date
        ) + self.store.live_minutes(timer.task_id, self.date)
        widget.config(text=format_minutes(logged) if logged else "0m")
