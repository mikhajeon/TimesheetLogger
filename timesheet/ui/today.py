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
from types import SimpleNamespace

from ..dates import add_days, format_duration, pretty_date, today
from ..models import ActiveTimer, Entry, Recurrence
from ..store import DayRow, Store, TaskContext
from .navrow import NavRow
from .. import anim, dwm, winfx
from . import controls, icons, modern, theme
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
#: How Actual Hours reads at exactly 0 — unlike Planned Hours' ``UNSET``,
#: there's no "not decided yet" state here, just nothing logged, so a plain
#: dash reads better in the value box than a literal "0m".
ZERO_ACTUAL = "-"

#: Width of each figure's own cell in the header's "X Planned | Y of Z Done"
#: line (see ``_build_header``) — wide enough for the longest a single
#: day's total realistically reads either way ``Settings.hour_format``
#: shows it (``"23h 45m"`` clock, or ``"23.75"`` decimal), so the number
#: inside never needs the cell itself to grow or shrink around it.
TOTAL_FIGURE_WIDTH = 7


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
#: Both nudge chips' own clickable area, 10% smaller than ``NUDGE_SIZE`` —
#: shrunk from the original budget above, which still sets the ceiling the
#: rest of this column (and ``_day_row``'s layout) is sized against.
NUDGE_CHIP_SIZE = round(NUDGE_SIZE * 0.9)
#: The circular timer selector, stacked under the drag handle in ``actions``
#: (see ``_day_row``) — sized down from its old 18px to leave room for the
#: drag handle above it within the same height budget as ``NUDGE_SIZE``.
TIMER_RADIO_SIZE = 18
#: The delete chip on the expanded detail panel: small enough to share its
#: row with the Repeat checkbox rather than dominate it.
DELETE_SIZE = 18
DELETE_RADIUS = 4

#: The day progress bar's own height, and the strip of half-hour ticks and
#: clock labels drawn beneath it.
PROGRESS_BAR_HEIGHT = 5

#: Corner radius (pixels) of the Day tab's cards and popups.
CARD_RADIUS = 8
#: Corner radius of the entry dialog (see ``_open_modal``).
DIALOG_RADIUS = 14
PROGRESS_AXIS_HEIGHT = 15

#: The Day tab's view-mode selector: ``Settings.day_grouping`` value -> label.
GROUPING_LABELS = {
    "manual": "Manual",
    "customer": "Customer",
    "project": "Project",
    "phase": "Phase",
    "task": "Task",
}
GROUPING_BY_LABEL = {label: mode for mode, label in GROUPING_LABELS.items()}


from .modalcard import ModalCard, crop_clamped as _crop_clamped  # noqa: E402


class TodayView:
    #: Refreshes block by block (see ``refresh``), so the overlay doesn't
    #: need to hide a whole-window rebuild behind a cover.
    incremental = True

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
        #: The Actual Hours duration-picker button on each non-running row,
        #: keyed by ``DayRow.id`` — unlike the live label above, nothing
        #: inside ``theme.duration_picker`` updates this on its own when the
        #: change comes from outside it (the +/- nudges, see ``_nudge``,
        #: rather than the picker's own dropdown), so this is what lets a
        #: nudge patch the figure it just changed without a full rebuild.
        self._actual_pickers: dict[str, tk.Button] = {}
        #: The minus nudge chip for each row, so a commit that changes an
        #: entry's minutes without a full rebuild (see ``_begin_light_refresh``)
        #: can still keep it in sync — see ``_sync_decrease_chip``.
        self._minus_chips: dict[str, tk.Canvas] = {}
        #: The task last chosen in the add-work panel, held across the
        #: rebuild its own dropdown triggers.
        self._add_task_id: str | None = None
        #: Manual mode's drag state: the row id picked up, if any, and each
        #: row's own widget, so a drop position can be read off its geometry.
        self._drag_id: str | None = None
        self._row_widgets: list[tuple[str, tk.Widget]] = []
        self._refreshing = False
        self._refresh_again = False
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
        self._timer_radios: dict[str, tk.Canvas] = {}
        #: Internal-note text typed but not yet saved, per row, so rebuilding
        #: the modal mid-edit doesn't lose it. Cleared on save, cancel, close.
        self._note_drafts: dict[str, str] = {}
        #: The entry just created by "+ log another entry", so the row for
        #: it can claim its note field the moment the refresh it triggers
        #: rebuilds that row — cleared the instant it's used, so it doesn't
        #: keep stealing focus on every later refresh.
        self._focus_entry_id: str | None = None
        #: Entries made by "+ log another entry" that have had nothing put in
        #: them yet; leaving their note empty drops them again.
        self._fresh_entries: set[str] = set()
        #: Rows whose delete control has been clicked once already, so the
        #: trash chip has given way to its own "Delete"/"Cancel" pair — see
        #: ``_build_modal``. Keyed by ``DayRow.id``, which is stable across a
        #: refresh as long as the row itself still exists.
        self._confirm_delete: set[str] = set()
        #: The progress bar's own last-drawn figures, so its ``<Configure>``
        #: handler (see ``_build_header``) can redraw at the canvas's real
        #: width once Tk actually reports one, without needing a fresh
        #: ``self.store.day_totals`` call of its own — the canvas starts out
        #: at Tk's placeholder 1px width before the window's first layout
        #: pass finishes, so the very first ``_draw_progress`` call (from
        #: this view's own construction, before that pass has run) would
        #: otherwise draw against a width that's about to change.
        self._progress_total = 0
        self._progress_target = 0
        self._progress_planned = 0
        #: Set by ``_begin_light_refresh`` just before a field commit on a
        #: row that already has its own ``Entry`` (note, internal note,
        #: Actual/Planned Hours) — the one kind of edit here that can't
        #: move, add or remove a row, so ``refresh`` can skip tearing the
        #: whole scroll body down and back up for it; the widget that
        #: changed already shows the new value on its own (the note field
        #: kept what was typed, the duration picker already swapped its own
        #: label before firing the commit that sets this).
        #:
        #: ``_light_refresh_base`` is the undo history's own depth (see
        #: ``Store.history_depth``) right before the first of these in an
        #: unflushed burst, and ``_light_refresh_count`` how many have
        #: landed since — not just a bare flag, so ``refresh`` can tell
        #: "exactly this many safe commits and nothing else" apart from a
        #: burst that also picked up something unrelated (a drag, a
        #: delete, another tab), which still needs the full rebuild below.
        self._light_refresh_base: int | None = None
        self._light_refresh_count = 0
        #: A row's detail panel (recurrence, internal note, delete) now
        #: opens as a dimmed modal over the whole tab instead of inline in
        #: the row list — see ``_open_modal`` — so ``self._modal`` (the
        #: scrim) and ``self._modal_panel`` (the card on top of it) are the
        #: only pieces of it that live outside the row that opened it, and
        #: ``self._modal_row_id`` is which row that was. All three are
        #: ``None`` while nothing's open.
        #:
        #: A plain ``tk.Frame`` placed inside ``self.frame``, not a
        #: separate ``Toplevel`` — a second borderless, always-on-top
        #: window was tried here and reverted: nothing opened at all, and
        #: it's not a one-off oversight to retry blind, with no way to see
        #: what two stacked, positioned-by-hand OS windows are actually
        #: doing without running the GUI. A same-window frame can't be made
        #: to show the dimmed rows genuinely through it (Tk has no
        #: per-widget alpha on Windows), so the scrim is a flat colour
        #: instead — ``theme.BG`` blended halfway to black (see
        #: ``theme.darken``), standing in for "50% transparent black".
        self._modal: tk.Frame | None = None
        self._modal_panel: tk.Frame | None = None
        self._modal_row_id: str | None = None
        #: Held so ``_arm_delete_entry``/``_disarm_delete_entry`` can redraw
        #: just this editor's own summary row (see ``RepeatEditor.build``)
        #: rather than the whole modal, let alone the row list underneath.
        self._repeat: RepeatEditor | None = None
        #: Each row's own expand/collapse arrow (see ``_day_row``), keyed by
        #: ``DayRow.id`` — patched in place by ``_set_arrow`` when the modal
        #: opens or closes, rather than rebuilding the row just to flip one
        #: icon.
        self._expand_arrows: dict[str, tk.Label] = {}
        #: Each card's own note box, so the modal's Customer note can update it.
        self._note_boxes: dict[str, tk.Entry] = {}

        self._build_header()
        self.scroll = ScrollFrame(self.frame)
        self.scroll.pack(fill="both", expand=True, padx=8)

    # -- chrome -----------------------------------------------------------

    def _nav_date_text(self) -> str:
        """``Tuesday 2026-11-03`` — the full weekday name beside the date's
        own ISO form (``self.date`` already is one, see ``dates.ISODate``),
        rather than ``pretty_date``'s "27 Oct 2026" or a shortened weekday."""
        return pretty_date(self.date)

    def _build_header(self) -> None:
        nav = theme.frame(self.frame)
        nav.pack(fill="x", padx=8, pady=(8, 4))

        # One row of three rounded pills — see ``navrow.NavRow`` — the same
        # on every tab: the date pill takes half the row, the arrows the rest.
        self.date_label = NavRow(
            nav, self._previous_day, self._jump_to_today, self._next_day,
            text=self._nav_date_text(), highlight=self.date == today(),
        )
        self.date_label.pack(fill="x")

        self.progress = tk.Canvas(
            self.frame, height=PROGRESS_BAR_HEIGHT + PROGRESS_AXIS_HEIGHT, bg=theme.BG,
            highlightthickness=0, bd=0
        )
        self.progress.pack(fill="x", padx=8)
        # Redraws at the canvas's own real width once Tk actually reports
        # one — see ``_progress_total``/``_progress_target`` — rather than
        # only ever being drawn by ``refresh`` itself, which can run before
        # the window's first layout pass has given this canvas any width
        # to speak of.
        self.progress.bind(
            "<Configure>",
            lambda _e: self._draw_progress(
                self._progress_total, self._progress_target, self._progress_planned
            ),
        )

        # "View: by X" and the day's own totals share one row now — the
        # grouping picker stays put on the left, and the two totals move in
        # beside it on the right, rather than each getting a full-width row
        # of its own. Billable packs first so it claims the row's own right
        # edge; the day total packs second, landing just to its left —
        # right-aligned as a pair, "X of Y" before "Z billable" reading
        # left to right, same order they always had.
        view_row = theme.frame(self.frame)
        view_row.pack(fill="x", padx=8, pady=(4, 6))
        theme.label(view_row, "Group by:", fg=theme.MUTED, font=theme.FONT_TINY).pack(side="left")
        self._grouping_select = controls.Select(
            view_row, list(GROUPING_LABELS.values()),
            GROUPING_LABELS[self.store.db.settings.day_grouping],
            self._on_grouping_change, bg=theme.BG,
        )
        self._grouping_select.pack(side="left", padx=(6, 0))

        # Same reasoning as the totals below: its own fixed-width cell for
        # the figure, separate from the static "billable" text beside it,
        # so a digit-count change here doesn't shove the totals group to
        # its left sideways too — this packs first (``side="right"``), so
        # anything it displaces displaces everything already packed there.
        billable = theme.frame(view_row)
        billable.pack(side="right")
        self.billable_total_label = theme.label(
            billable, "", fg=theme.MUTED, font=theme.FONT_SMALL,
            width=TOTAL_FIGURE_WIDTH, anchor="e",
        )
        self.billable_total_label.pack(side="left")
        theme.label(billable, " billable", fg=theme.MUTED, font=theme.FONT_SMALL).pack(side="left")

        # "X Planned | Y of Z Done" used to be one label — a single string
        # rebuilt from scratch on every refresh. A figure changing how many
        # digits it needs (``1h`` to ``1h 15m``, say) shifted the whole
        # label's width, which shoved this label itself (and the billable
        # one beside it) sideways on every change — not just the number
        # that actually moved. Each figure now gets its own fixed-width,
        # right-aligned cell instead, with the words between them in their
        # own plain labels, so only the digits inside a cell ever redraw —
        # nothing to its left or right shifts no matter how long the figure
        # gets.
        totals = theme.frame(view_row)
        totals.pack(side="right", padx=(0, 8))
        self.planned_total_label = theme.label(
            totals, "", font=theme.FONT_SMALL, width=TOTAL_FIGURE_WIDTH, anchor="e"
        )
        self.planned_total_label.pack(side="left")
        theme.label(totals, " Planned | ", font=theme.FONT_SMALL).pack(side="left")
        self.actual_total_label = theme.label(
            totals, "", font=theme.FONT_SMALL, width=TOTAL_FIGURE_WIDTH, anchor="e"
        )
        self.actual_total_label.pack(side="left")
        theme.label(totals, " of ", font=theme.FONT_SMALL).pack(side="left")
        self.target_total_label = theme.label(
            totals, "", font=theme.FONT_SMALL, width=TOTAL_FIGURE_WIDTH, anchor="e"
        )
        self.target_total_label.pack(side="left")
        theme.label(totals, " Done", font=theme.FONT_SMALL).pack(side="left")

    def _previous_day(self) -> None:
        self._go_to(add_days(self.date, -1))

    def _next_day(self) -> None:
        self._go_to(add_days(self.date, 1))

    def _jump_to_today(self) -> None:
        self._go_to(today())

    _day_loading = False

    def _go_to(self, date) -> None:
        """Moves to another day optimistically. The date shows at once and a
        skeleton of the entry list covers the old day's cards; the new day's
        cards are built beneath it (the window staying live, as for a tab
        switch), and only when all of them are in place does the skeleton
        dissolve to reveal them."""
        from .skeleton import Skeleton
        from . import yielding

        self.date = date
        overlay = self.overlay
        if self._day_loading:
            return  # the build under way picks the newest date up when it runs
        if (
            getattr(overlay, "collapsed", False) or getattr(overlay, "_morphing", False)
            or getattr(overlay, "_build_name", None) is not None
            or not hasattr(overlay, "_cover_start")
        ):
            self.refresh()
            return
        try:
            self.date_label.set_text(self._nav_date_text())
            self.date_label.set_highlight(self.date == today())
            skeleton = Skeleton(self.scroll.master, "Today")
            skeleton.show_over(self.scroll)
            self.frame.update_idletasks()  # painted before the build starts
        except tk.TclError:
            self.refresh()
            return
        self._day_loading = True
        state = {"started": False}

        def build() -> None:
            if state["started"]:
                return
            state["started"] = True
            cover = None
            try:
                if not self.frame.winfo_exists():
                    return
                overlay._build_name = "Today"  # the build yields to input meanwhile
                try:
                    while True:
                        built = self.date
                        self.refresh()
                        if built == self.date:
                            break  # (a further click while building: go again)
                finally:
                    overlay._build_name = None
                cover = overlay._cover_start()
            except (yielding.Abandoned, tk.TclError):
                pass
            finally:
                self._day_loading = False
                try:
                    skeleton.dispose()
                except tk.TclError:
                    pass
                if cover is not None:
                    overlay._cover_end(cover, fade=True)

        self.frame.after(12, build)
        # Still on the skeleton two seconds on without the build having
        # begun: begin it now.
        self.frame.after(2000, build)

    def _on_grouping_change(self, label: str) -> None:
        mode = GROUPING_BY_LABEL.get(label)
        if mode:
            from .skeleton import run_behind

            run_behind(
                self.scroll, "Today",
                lambda: self.store.patch_settings(day_grouping=mode),
                overlay=self.overlay,
            )

    def _fmt(self, minutes: float) -> str:
        """An hour figure the way ``Settings.hour_format`` (Preferences,
        under Theme) says to show it — see ``dates.format_duration``."""
        return format_duration(minutes, self.store.db.settings.hour_format == "decimal")

    def _format_actual(self, minutes: int) -> str:
        return ZERO_ACTUAL if not minutes else self._fmt(minutes)

    # -- rendering ---------------------------------------------------------

    def refresh(self) -> None:
        # Building a row calls ``update_idletasks``, which runs the overlay's
        # own queued rebuild (see ``Overlay.refresh``) in the middle of this
        # pass: two passes then interleave and each builds the same block,
        # leaving a duplicate card until a later pass tears it down. A
        # request that arrives mid-pass is run once, right after it instead.
        if self._refreshing:
            self._refresh_again = True
            return
        self._refreshing = True
        try:
            motion = self._motion_begin()
            try:
                self._refresh_once()
                while self._refresh_again:
                    self._refresh_again = False
                    self._refresh_once()
            except BaseException:
                if motion is not None:
                    self._motion_abort(motion)
                raise
            if motion is not None:
                self._motion_end(motion)
            self._motion_date = self.date
        finally:
            self._refreshing = False
            self._refresh_again = False

    # -- card motion ------------------------------------------------------
    #
    # When a refresh moves cards (a new one slides in at the top, one is
    # removed, a card grows), they glide to their new places instead of
    # snapping. Same recipe as the window animations (see animations.mdc):
    # the real window is rebuilt hidden under a snapshot, and only a ghost
    # window is animated — each card is a strip of the before/after
    # snapshots moved a little each frame.

    MOTION_MS = 240

    def _block_geometry(self) -> dict:
        """Each block's ``(signature, top, height)`` within the viewport."""
        canvas = self.scroll._canvas
        top = canvas.winfo_rooty()
        out = {}
        for key, (signature, widgets) in getattr(self.scroll, "_blocks", {}).items():
            live = [w for w in widgets if w.winfo_exists() and w.winfo_ismapped()]
            if not live:
                continue
            y0 = min(w.winfo_rooty() for w in live) - top
            y1 = max(w.winfo_rooty() + w.winfo_height() for w in live) - top
            out[key] = (signature, y0, y1 - y0)
        return out

    def _motion_begin(self):
        """A snapshot of the window and its cards' places, taken before the
        rebuild — or ``None`` when this refresh should just apply in place."""
        overlay = self.overlay
        try:
            if (
                not getattr(self.scroll, "_blocks", None)
                or getattr(self, "_motion_date", None) != self.date
                or self._modal is not None
                or self._day_loading
                or overlay.collapsed
                or getattr(overlay, "_morphing", False)
                or getattr(overlay, "_cover", None) is not None
                or getattr(overlay, "_build_name", None) is not None
                or overlay.root.state() == "withdrawn"
                or not self.scroll.winfo_ismapped()
            ):
                return None
            rect = (
                overlay.root.winfo_x(), overlay.root.winfo_y(),
                overlay.root.winfo_width(), overlay.root.winfo_height(),
            )
            if rect[2] < 2 or rect[3] < 2:
                return None
            # Only a change to which cards there are, or their order, moves
            # anything worth animating; everything else updates in place.
            shown = [k[1] for k in self.scroll._blocks if k[0] == "row"]
            coming = [
                r.id for _heading, rows in self.store.grouped_day_rows(self.date)
                for r in rows
            ]
            if shown == coming:
                return None
            hwnd = overlay._hwnd()
            before = winfx.capture(hwnd, rect[2], rect[3])
        except (OSError, tk.TclError):
            return None
        canvas = self.scroll._canvas
        root = overlay.root
        viewport = (
            canvas.winfo_rootx() - root.winfo_rootx(),
            canvas.winfo_rooty() - root.winfo_rooty(),
            canvas.winfo_width(), canvas.winfo_height(),
        )
        motion = {
            "hwnd": hwnd, "rect": rect, "before": before, "viewport": viewport,
            "old": self._block_geometry(), "scroll": canvas.canvasy(0), "ghost": None,
        }
        # Cover the window now, before anything is rebuilt, so neither the
        # cards being built nor the layout settling is ever on screen.
        try:
            ghost = winfx.Ghost(rect[2], rect[3])
            motion["ghost"] = ghost
            dwm.corners(ghost.hwnd, dwm.ROUNDED)
            ghost.draw(rect, before, before, 0.0, overlay.store.db.settings.opacity)
            anim._vsync()  # on screen before the real window goes transparent
            overlay._morphing = True
            overlay._raise_shield(rect)
            overlay.root.attributes("-alpha", 0.0)
        except (OSError, tk.TclError):
            self._motion_abort(motion)
            return None
        return motion

    def _motion_abort(self, motion: dict) -> None:
        """Puts the window back as it was, with no animation."""
        overlay = self.overlay
        try:
            overlay.root.attributes("-alpha", overlay.store.db.settings.opacity)
        except tk.TclError:
            pass
        overlay._morphing = False
        overlay._drop_shield()
        for item in (motion.get("ghost"), motion.get("before")):
            if item is not None:
                item.close()
        motion["ghost"] = motion["before"] = None

    def _motion_end(self, motion: dict) -> None:
        """Called once the rebuild has finished (or failed): reveals it,
        gliding the cards if any of them moved."""
        overlay = self.overlay
        hwnd, rect, before = motion["hwnd"], motion["rect"], motion["before"]
        ghost = motion["ghost"]
        after = frame = None
        hidden = True
        try:
            # Lay the rebuilt widgets out first: until then new blocks are
            # unmapped and survivors still report their old places.
            overlay.root.update_idletasks()
            new = self._block_geometry()
            old = motion["old"]
            moved = (
                set(old) != set(new)
                or any(abs(old[k][1] - new[k][1]) >= 2 for k in new if k in old)
            ) and abs(self.scroll._canvas.canvasy(0) - motion["scroll"]) < 1
            opacity = overlay.store.db.settings.opacity
            if not moved:
                return  # the finally restores the window under the snapshot
            after = winfx.capture(hwnd, rect[2], rect[3])
            frame = winfx.new_surface(rect[2], rect[3])
            vx, vy, vw, vh = motion["viewport"]
            clip = (vx, vy, vw, vh)

            def compose(t: float) -> None:
                winfx.blit(frame, after, 0, 0, 0, 0, rect[2], rect[3])
                winfx.fill(frame, clip, theme.BG)
                # Nothing ever overlaps: a card that leaves closes up from
                # the bottom while the ones below slide up into its place; one
                # that arrives opens down from its top edge while the ones
                # below slide down out of its way. Every card that stays is
                # drawn once, from the finished layout, at its in-between spot.
                for key in old:
                    if key not in new:
                        _sig, y, h = old[key]
                        winfx.blit(frame, before, vx, vy + y, vx, vy + y, vw,
                                   round(h * (1 - t)), 1, clip)
                for key, (_sig, y, h) in new.items():
                    if key not in old:
                        winfx.blit(frame, after, vx, vy + y, vx, vy + y, vw,
                                   round(h * t), 1, clip)
                    else:
                        now = round(anim.lerp(old[key][1], y, t))
                        winfx.blit(frame, after, vx, vy + now, vx, vy + y, vw, h, 1, clip)
                ghost.draw(rect, frame, frame, 0.0, opacity)

            anim.run(self.MOTION_MS, compose, easing=anim.ease_out_cubic)
            # Reveal: the real window back (already laid out), the ghost
            # fading away over it.
            overlay.root.attributes("-alpha", opacity)
            hidden = False
            overlay._drop_shield()
            ghost.set_click_through(True)
            anim.run(
                120, lambda t: ghost.draw(rect, frame, frame, 0.0, opacity * (1 - t)),
                easing=anim.smoothstep,
            )
        except (OSError, tk.TclError):
            pass  # no motion is a fine fallback: the rebuild is already done
        finally:
            if hidden:
                try:
                    overlay.root.attributes("-alpha", overlay.store.db.settings.opacity)
                except tk.TclError:
                    pass
            overlay._morphing = False
            overlay._drop_shield()
            for item in (ghost, after, frame, before):
                if item is not None:
                    item.close()

    def _refresh_once(self) -> None:
        theme.commit_focused_entry(self.frame)
        self.date_label.set_text(self._nav_date_text())
        self.date_label.set_highlight(self.date == today())
        self._grouping_select.set_value(GROUPING_LABELS[self.store.db.settings.day_grouping])

        total, billable = self.store.day_totals(self.date)
        planned = self.store.planned_minutes_on(self.date)
        target = self.store.db.settings.daily_target_minutes
        self.planned_total_label.config(text=self._fmt(planned))
        self.actual_total_label.config(text=self._fmt(total))
        self.target_total_label.config(text=self._fmt(target))
        self.billable_total_label.config(text=self._fmt(billable))
        # The bar stacks actual, then whatever's still outstanding of what
        # was planned — see ``unfinished_planned_minutes_on`` for why
        # that's a different figure from the header's own gross ``planned``
        # above.
        unfinished = self.store.unfinished_planned_minutes_on(self.date)
        self._draw_progress(total, target, unfinished)

        base, count = self._light_refresh_base, self._light_refresh_count
        self._light_refresh_base, self._light_refresh_count = None, 0
        if base is not None and self.store.history_depth() == base + count:
            # Exactly those field commits and nothing else landed in the
            # same refresh burst — see ``_begin_light_refresh``. Nothing
            # below is left to do.
            return

        # Block-level refresh: each heading, row card and panel is a block
        # that is kept as it is while what it shows is unchanged, and only
        # rebuilt when it isn't (see ``ScrollFrame.block``) — so a change
        # redraws just the card it touched, not the whole list.
        scroll = self.scroll
        scroll.begin_blocks()
        body = scroll.body

        groups = self.store.grouped_day_rows(self.date)
        manual = self.store.db.settings.day_grouping == "manual"
        if not any(rows for _heading, rows in groups):
            scroll.block("empty", "", None, lambda: theme.label(
                body,
                "Nothing planned for this day yet.",
                fg=theme.MUTED,
                font=theme.FONT_SMALL,
                anchor="center",
            ).pack(fill="x", pady=16))
        decimal = self.store.db.settings.hour_format == "decimal"

        def heading_block(
            kind: str, title: str, group_rows, *, fg, font, label_font, pad, add_to=None
        ):
            """A heading with the time logged across its rows beside it. The
            block is keyed on that figure, so it redraws when it moves.
            ``add_to`` (a task's context) puts "+ log another entry" for that
            task at the heading's right-hand end."""
            minutes = sum(r.entry.minutes for r in group_rows if r.entry)

            def build() -> None:
                box = theme.heading_label(body, title, fg=fg, font=label_font)
                theme.label(
                    box, f"\u00b7  {self._format_actual(minutes)}", fg=theme.MUTED, font=font,
                ).pack(side="left", padx=(8, 0))
                if add_to is not None:
                    self._add_another_link(box, add_to).pack(side="right", padx=(0, 4))
                box.pack(fill="x", **pad)

            scroll.block(
                kind, title,
                (minutes, decimal, add_to.task.id if add_to is not None else None), build,
            )

        def lone_task(group_rows):
            """The task all of these rows belong to, when it is just the one."""
            if group_rows and len({r.context.task.id for r in group_rows}) == 1:
                return group_rows[0].context
            return None

        for heading, rows in groups:
            if heading:
                grouping = self.store.db.settings.day_grouping
                # A heading with no task subheading under it is the task's own.
                own_task = grouping == "task" or (
                    grouping == "phase" and rows and not rows[0].context.phase
                )
                heading_block(
                    "heading", heading, rows, fg=None, font=theme.FONT_SMALL,
                    label_font=theme.FONT_HEAD, pad={"pady": (7, 0)},
                    add_to=lone_task(rows) if own_task else None,
                )
            if heading and self.store.db.settings.day_grouping == "customer":
                projects: dict[str, list[DayRow]] = {}
                for row in rows:
                    p = row.context.project
                    projects.setdefault(f"[{p.k_code}] {p.name}", []).append(row)
                for project_title, project_rows in projects.items():
                    heading_block(
                        "project", project_title, project_rows, fg=theme.MUTED,
                        font=theme.FONT_TINY, label_font=theme.FONT_SMALL,
                        pad={"padx": (10, 0), "pady": (0, 1)},
                    )
                    tasks: dict[str, list[DayRow]] = {}
                    for row in project_rows:
                        tasks.setdefault(self._pipe_title(row.context), []).append(row)
                    for task_title, task_rows in tasks.items():
                        heading_block(
                            "task", task_title, task_rows, fg=theme.MUTED,
                            font=theme.FONT_TINY, label_font=theme.FONT_TINY,
                            pad={"padx": (20, 0), "pady": (0, 1)},
                            add_to=lone_task(task_rows),
                        )
                        self._render_rows(body, task_rows, manual)
                continue
            mode = self.store.db.settings.day_grouping
            if heading and mode in ("project", "phase"):
                # Secondary grouping: entries under each task subheading.
                # In phase mode a plain (phase-less) task is its own heading,
                # so only a phase's children get subheadings.
                subgroups: dict[str, list[DayRow]] = {}
                for row in rows:
                    if mode == "phase" and not row.context.phase:
                        subgroups.setdefault("", []).append(row)
                    else:
                        subgroups.setdefault(row.context.task.title if mode == "phase" else self._pipe_title(row.context), []).append(row)
                for sub_title, sub_rows in subgroups.items():
                    if sub_title:
                        heading_block(
                            "sub", sub_title, sub_rows, fg=theme.MUTED,
                            font=theme.FONT_TINY, label_font=theme.FONT_SMALL,
                            pad={"padx": (10, 0), "pady": (0, 1)},
                            add_to=lone_task(sub_rows),
                        )
                    self._render_rows(body, sub_rows, manual)
                continue
            self._render_rows(body, rows, manual)

        # The add panel is rebuilt every pass while it is open (its form has
        # state of its own to match against the store); the closed button is
        # constant. The day note is rebuilt only when its text changes.
        scroll.block(
            "add", "", object() if self._adding else "closed", lambda: self._add_panel(body)
        )
        note = self.store.db.day_notes.get(self.date, "")
        scroll.block("note", "", (self.date, note), lambda: self._day_note(body))
        scroll.end_blocks()

        # Registries of per-row widgets: forget the ones whose card was just
        # destroyed (rebuilt rows have already registered fresh ones), and
        # put the drag order back in on-screen order.
        for registry in (
            self._actual_pickers, self._minus_chips, self._expand_arrows, self._note_boxes
        ):
            for row_id in [k for k, w in registry.items() if not w.winfo_exists()]:
                del registry[row_id]
        position = {w: i for i, w in enumerate(scroll.block_order())}
        self._row_widgets = sorted(
            ((rid, w) for rid, w in self._row_widgets if w.winfo_exists()),
            key=lambda pair: position.get(pair[1], 1 << 30),
        )

    def _draw_progress(self, total: int, target: int, planned: int = 0) -> None:
        self._draw_progress_bar(total, target, planned)

    def _draw_progress_bar(self, total: int, target: int, planned: int = 0) -> None:
        """The day's own progress, against ``target`` (``Settings.
        daily_target_minutes``) — and whatever's still outstanding of what
        was planned, stacked on right after. The bar's own width is
        ``target`` itself, full stop; it used to stretch to twice that so
        an overtime or over-planned half had its own room to grow into,
        which left a full, exactly-on-target day only ever filling the
        first half — reaching "100% done" looked like 50%. Overtime and an
        over-committed plan still get their own colour, just clamped to
        this same width rather than a magnitude past it; the exact figure
        either way is what the header's own "x Planned | y of z Done"
        figures (``planned_total_label`` et al.) are for.

        ``planned`` here is already net of actual — see
        ``Store.unfinished_planned_minutes_on``, the only caller — so it's
        a width to stack immediately after ``total``, not a second
        absolute line measured from zero the way ``total`` itself is. One
        entry logged well past its own plan and a second one not started
        at all reads as actual first, then only the second's own
        shortfall right after it — not as if the first's already-covered
        plan still needed its own stretch of bar too.

        Below target: blue for what's actually logged, then amber for
        whatever's still outstanding — red instead, for that same
        stretch, if actual plus what's still outstanding would run past
        target even before anything's actually overdue yet.

        At or past target: the whole bar is green — red instead, the
        whole way, if ``total`` has actually run past target (overtime),
        or there's still something outstanding that would push past it
        even once target's otherwise been reached.
        """
        self._progress_total = total
        self._progress_target = target
        self._progress_planned = planned
        self.progress.delete("all")
        width = max(1, self.progress.winfo_width())
        settings = self.store.db.settings

        # The bar spans the configured work day (Preferences -> work hours),
        # with a tick at every half hour beneath it. ``target`` still decides
        # the colours (on target / overtime / over-committed), it just isn't
        # what the width is measured against any more.
        start = settings.work_start_minutes
        span = max(30, settings.work_end_minutes - start)
        # A lunch break inside the day stays on the timeline as a gap:
        # progress runs up to its start, skips across, and carries on from
        # its end, so the bar always lines up with the clock labels below.
        lunch = None
        if settings.lunch_enabled:
            lunch_start = max(settings.lunch_start_minutes, start)
            lunch_end = min(settings.lunch_end_minutes, start + span)
            if lunch_end > lunch_start and lunch_end - lunch_start < span:
                lunch = (lunch_start, lunch_end)
        lunch_len = lunch[1] - lunch[0] if lunch else 0
        avail = span - lunch_len  # minutes of working time the bar can hold
        before_lunch = lunch[0] - start if lunch else avail  # working minutes ahead of the gap
        bar_h = PROGRESS_BAR_HEIGHT
        self.progress.create_rectangle(0, 0, width, bar_h, fill=theme.BG_INPUT, outline="")
        if lunch:
            gap_a = round(width * (lunch[0] - start) / span)
            gap_b = round(width * (lunch[1] - start) / span)
            self.progress.create_rectangle(gap_a, 0, gap_b, bar_h, fill=theme.BG, outline="")
            # Diagonal hatching across the gap: the usual "not available"
            # look, so it reads as a deliberate break (nothing fills it)
            # rather than a hole in the bar.
            hatch = theme.blend(theme.BG, theme.MUTED, 0.55)
            for x0 in range(gap_a - bar_h, gap_b, 4):
                # A "/" stroke from the bottom-left to the top-right of the
                # bar, trimmed to the gap's own edges.
                (xa, ya), (xb, yb) = (x0, bar_h), (x0 + bar_h, 0)
                if xb <= gap_a or xa >= gap_b:
                    continue
                if xa < gap_a:
                    xa, ya = gap_a, bar_h - (gap_a - x0)
                if xb > gap_b:
                    xb, yb = gap_b, bar_h - (gap_b - x0)
                self.progress.create_line(xa, ya, xb, yb, fill=hatch)
        self._draw_progress_axis(width, start, span)
        if target <= 0:
            return

        def fill(first: float, last: float, colour: str) -> None:
            """Paints working minutes ``first``..``last``, splitting across
            the lunch gap where they straddle it."""
            first, last = min(max(first, 0), avail), min(max(last, 0), avail)
            if last <= first:
                return
            pieces = []
            if first < before_lunch < last:
                pieces = [(start + first, start + before_lunch),
                          (start + before_lunch + lunch_len, start + last + lunch_len)]
            elif last <= before_lunch:
                pieces = [(start + first, start + last)]
            else:
                pieces = [(start + first + lunch_len, start + last + lunch_len)]
            for clock_a, clock_b in pieces:
                self.progress.create_rectangle(
                    width * (clock_a - start) / span, 0, width * (clock_b - start) / span, bar_h,
                    fill=colour, outline="",
                )

        # User-chosen overrides (``Settings.progress_bar_color`` for the
        # actual stretch, ``planned_bar_color`` for the outstanding one).
        custom = settings.progress_bar_color or None
        planned_custom = settings.planned_bar_color or None

        over_committed = total + planned > target
        if total >= target:
            over = total > target or over_committed
            fill(0, total, custom or (theme.DANGER if over else theme.BILLABLE))
        else:
            fill(0, total, custom or theme.ACCENT)
        if planned > 0:
            fill(total, total + planned,
                 planned_custom or (theme.DANGER if over_committed else theme.PLANNED))

    def _draw_progress_axis(self, width: int, start: int, span: int) -> None:
        """Half-hour ticks (taller on the hour) with clock labels under the
        bar. Every half hour is labelled when there's room; otherwise just
        the hours, then every second hour."""
        end = start + span
        first = -(-start // 30) * 30  # first clock half hour at/after start
        marks = list(range(first, end + 1, 30))
        spacing = width * 30 / span
        label_every = next((n for n in (1, 2, 4, 8) if spacing * n >= 27), 8)
        for minutes in marks:
            x = min(width - 1, round(width * (minutes - start) / span))
            on_hour = minutes % 60 == 0
            self.progress.create_line(
                x, PROGRESS_BAR_HEIGHT, x, PROGRESS_BAR_HEIGHT + (4 if on_hour else 2),
                fill=theme.MUTED,
            )
            # Hourly-or-sparser labelling stays pinned to the clock (9:00,
            # 10:00...) rather than to whichever half hour came first.
            if label_every > 1 and (minutes // 30) % label_every:
                continue
            hour12 = (minutes // 60) % 12 or 12
            # A label sitting on the bar's own edge hugs it so it isn't
            # clipped by the canvas.
            if x <= 12:
                lx, anchor = 0, "nw"
            elif x >= width - 12:
                lx, anchor = width, "ne"
            else:
                lx, anchor = x, "n"
            self.progress.create_text(
                lx, PROGRESS_BAR_HEIGHT + 5, text=f"{hour12}:{minutes % 60:02d}",
                fill=theme.MUTED, font=("Segoe UI", 7), anchor=anchor,
            )
    @staticmethod
    def _pipe_title(context) -> str:
        """Subheading text: ``Phase | Task`` (just the task if no phase)."""
        if context.phase:
            return f"{context.phase.title} | {context.task.title}"
        return context.task.title

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
            is_last = index == last_index[task_id] and not manual
            self.scroll.block(
                "row", row.id,
                self._row_signature(row, manual, is_anchor, is_last),
                lambda row=row, is_anchor=is_anchor, is_last=is_last: self._build_row_block(
                    parent, row, manual, is_anchor, is_last
                ),
            )

            # The detail panel itself no longer renders here — it's a modal
            # over the whole tab now (see ``_open_modal``), not a row in
            # this list, so it survives whatever a later store change does
            # to the rows underneath it.

    def _build_row_block(
        self, parent: tk.Misc, row: DayRow, manual: bool, is_anchor: bool, is_last: bool
    ) -> None:
        """A row's whole block: its card, and the "+ log another entry"
        link under it when it is its task's last row."""
        self._day_row(parent, row, manual, is_anchor)

    def _row_signature(self, row: DayRow, manual: bool, is_anchor: bool, is_last: bool):
        """Everything a row card's look and behaviour depends on. While this
        is unchanged the card is left alone; the object ids are there
        because a card's handlers close over the row's own entry/task
        objects (an undo can replace them with equal-looking new ones)."""
        timer = self.store.db.active_timer
        context = row.context
        return (
            repr(row), manual, is_anchor, is_last,
            id(row.entry), id(context.task), id(context.project), id(context.client),
            timer is not None, timer is not None and self._is_timer_row(timer, row),
            bool(timer and timer.is_paused),
            row.id == self._drag_id, row.id == getattr(self, "_focus_entry_id", None),
            context.task.id in self._expanded, self._expanded_row.get(context.task.id),
            repr(self.store.db.settings), self.date,
        )

    def _day_row(self, parent: tk.Misc, row: DayRow, manual: bool, is_anchor: bool) -> None:
        task = row.context.task
        timer = self.store.db.active_timer
        # The one specific row the timer is running against — never just
        # "some row on this task", or a task with more than one entry
        # logged today would light every single one of them up, and show
        # every one of them the same ticking total, the moment any one of
        # them was actually started (see ``_is_timer_row``).
        timer_is_this_row = timer is not None and self._is_timer_row(timer, row)
        # An anti-aliased rounded card (``modern.Surface``): ``outer`` is what
        # the corners blend into, ``card`` the frame everything below packs
        # into, ``surface`` the canvas that is actually placed, bordered and
        # dragged.
        surface = modern.Surface(
            parent, fill=theme.BG_RAISED, border=theme.LINE, radius=CARD_RADIUS,
            outer=theme.BG,
        )
        card = surface.body
        if row.id == self._drag_id:
            # The row actually being picked up gets its own, distinct
            # highlight — thicker and in the accent colour — so it's clear
            # which one the drop indicator belongs to while several rows
            # are on screen at once.
            surface.set_border(theme.ACCENT, 2)
        else:
            surface.set_border(theme.RUNNING_LINE if timer_is_this_row else theme.LINE)
        surface.pack(fill="x", pady=2)
        self._row_widgets.append((row.id, surface))

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
        # ``_build_modal``), not beside these.
        nudges = theme.frame(card, bg=theme.BG_RAISED)
        # Tight margins on purpose: with the identity line hidden outside
        # Manual mode (see ``meta`` below), this +/- stack — not the note
        # rows — is what the card's own height ends up tracking (``card``
        # auto-sizes to its tallest child, and nothing propagates a pady
        # change here into the note/arrow gaps, which live on their own
        # widgets entirely), so trimming it is how a grouped view's cards
        # get shorter without touching a single note or arrow gap.
        nudges.pack(side="right", fill="y", padx=(4, 0), pady=0)
        # Plain ``+``/``\u2212`` glyphs (``theme.chip``) rather than
        # ``icons.chip``'s own baked PNGs — the minus needs a dimmed,
        # disabled look once there's nothing left to take away (see
        # ``can_decrease`` below), and a fixed-colour image has no such
        # variant to swap to; drawn the same way here for consistency even
        # though the plus never needs one. ``circle=True`` keeps the hover
        # feedback (see ``theme.chip``) to a ring traced around the chip's
        # own circular shape, rather than a squared-off box that would read
        # as mismatched behind a round glyph.
        # Two keys forming one column flush against the card's right edge,
        # each half the card's height, their outer corners curving with the
        # card's own (``modern.EdgeButton``).
        modern.EdgeButton(
            nudges, "plus", lambda: self._nudge(row, NUDGE_MINUTES),
            outer="top", fill=theme.BG_INPUT, bg=theme.BG_RAISED,
        ).pack(side="top", fill="both", expand=True, pady=(0, 2))
        # Nothing to take away from an entry already at 0 (or not logged
        # yet at all) — greyed out and inert rather than a click that would
        # just floor at 0 anyway, the same idea the delete chip already
        # uses for a row with nothing to act on yet. The command is always
        # the real one, never swapped for a no-op — ``enabled`` is what
        # gates the click at creation, and ``set_enabled`` (see
        # ``_sync_decrease_chip``) is what's left to gate it once the
        # figure moves across zero from something other than this chip's
        # own click (the Actual Hours picker, a light refresh elsewhere).
        can_decrease = bool(row.entry and row.entry.minutes > 0)
        minus_chip = modern.EdgeButton(
            nudges, "minus", lambda: self._nudge(row, -NUDGE_MINUTES),
            outer="bottom", fill=theme.BG_INPUT, bg=theme.BG_RAISED,
            enabled=can_decrease,
        )
        minus_chip.pack(side="top", fill="both", expand=True)
        self._minus_chips[row.id] = minus_chip

        stats.pack(side="right", padx=(4, 4), pady=1)

        # A left-hand column mirroring ``nudges``/``stats`` on the right —
        # packed ``side="left"`` before ``top`` below, so it too claims a
        # column spanning the card's full height, with the drag handle and
        # timer selector stacked one above the other in it rather than
        # sharing a row with the note field.
        actions = theme.frame(card, bg=theme.BG_RAISED, height=1)
        actions.pack(side="left", fill="y", padx=(6, 2), pady=0)
        actions.pack_propagate(False)

        # Every view drags now, not just Manual (see ``Store.grouped_day_rows``)
        # — top-left on every card, same spot Manual always had it, so a
        # habit formed there carries straight over to a grouped view.
        handle = theme.grip(actions, bg=theme.BG_RAISED)
        # ``r=row``, not ``rid=row.id`` — ``row.id`` is read fresh at click
        # time rather than frozen into the closure now, so a row promoted
        # since this card was built (see ``_promote_row``) still drags
        # under its own, current id rather than the placeholder one this
        # card happened to be built with.
        handle.bind("<ButtonPress-1>", lambda e, r=row: self._drag_start(r.id, e))
        handle.bind("<B1-Motion>", self._drag_motion)
        handle.bind("<ButtonRelease-1>", self._drag_end)

        # The one entry the timer bar's Start/Stop acts on — a circular,
        # single-choice selector rather than a Play/Stop button on just the
        # task's first row, so any specific entry (any recurring note's own
        # occurrence included) can be the one timed, not only whichever
        # happened to render first. Filled green on the row actually
        # running; filled accent on the one armed to start next; disabled
        # everywhere else — including the running row itself — while
        # something is already running. This used to double as a shortcut
        # straight to Stop on the running row, but that let a stray click
        # here stop (and quietly unselect) a timer the user never meant to
        # touch; Stop is the timer bar's own button's job now, and this is
        # purely a "what's running" indicator for as long as one is.
        #
        # Stacked right under the drag handle, in the same ``actions``
        # column, rather than sharing a row (or a corner ``place``d on
        # ``card``) with anything else — a sibling stacked with ``pack``
        # can't overlap another the way a bare ``place`` call could.
        armed = not timer and self._selected_row_id == row.id
        timer_radio = theme.radio(
            actions,
            timer_is_this_row or armed,
            lambda r=row: self._toggle_timer_selection(r),
            fill=theme.BILLABLE if timer_is_this_row else theme.ACCENT,
            bg=theme.BG_RAISED,
            size=TIMER_RADIO_SIZE,
            enabled=timer is None,
        )
        self._timer_radios[row.id] = timer_radio

        # Handle and selector sit side by side, each centred on the card's
        # full height. Placed (not packed) so the offset is exact: when the
        # spare space is an odd pixel, the extra one goes above, never below.
        handle.update_idletasks()
        gap = 5
        actions.configure(width=handle.winfo_reqwidth() + gap + TIMER_RADIO_SIZE)
        handle.place(x=0, y=0)
        timer_radio.place(x=handle.winfo_reqwidth() + gap, y=0)

        def centre_actions(_e=None, a=actions, items=(handle, timer_radio)) -> None:
            height = a.winfo_height()
            if height < 4:
                return
            for item in items:
                item.place_configure(y=-(-(height - item.winfo_reqheight()) // 2))

        actions.bind("<Configure>", centre_actions)

        # The middle column (identity line in Manual mode, then the note row),
        # centred vertically in whatever height the side columns leave.
        middle = theme.frame(card, bg=theme.BG_RAISED)
        middle.pack(fill="x", expand=True, pady=2)
        top = theme.frame(middle, bg=theme.BG_RAISED)
        top.pack(fill="x", padx=(6, 2))

        # Whether *this* row repeats — an already-logged entry carries its
        # own ``recurring_note_id`` once tagged onto a note (see
        # ``promote_to_recurring_note``), which ``DayRow.recurring_note_id``
        # itself only ever holds before that entry exists (it's how the
        # placeholder finds its note) — so both are checked here.
        is_recurring = bool(
            row.entry.recurring_note_id if row.entry else row.recurring_note_id
        )
        # The "more" button that opens this row's detail modal (see
        # ``_open_modal``): at the right end of the note row, just before the
        # hours labels.
        # ``r=row``, read fresh at click time — a frozen ``row.id`` would stop
        # finding this row once it is promoted in place (``_promote_row``).
        more_button = modern.RoundButton(
            top, "dots", lambda r=row: self._toggle_expanded(task.id, r.id),
            size=NUDGE_CHIP_SIZE + 2, fill=theme.BG_INPUT, bg=theme.BG_RAISED,
        )
        more_button.pack(side="right", padx=(4, 0))
        self._expand_arrows[row.id] = more_button
        note_field = modern.Field(top, bg=theme.BG_RAISED, font=theme.FONT_SMALL, pad=3, lift=1)
        note = note_field.entry
        self._note_boxes[row.id] = note
        if is_recurring:
            # Inside the note box, at its far left, divided from the text by a
            # "|" — it belongs to the entry itself, not to the row's controls.
            note_field.set_lead(icons.label(
                note_field, "recurring", fallback="\u21bb",
                bg=theme.BG_INPUT, fg=theme.MUTED, font=theme.FONT_TINY,
            ))
        note.insert(0, row.note)
        note_field.pack(side="left", fill="x", expand=True, padx=(0, 2))
        saved = {"text": row.note}

        def commit_note(_e=None, r=row, w=note) -> None:
            # Whatever is typed is saved once, whether it is Enter or leaving the
            # box that gets there first.
            if w.get() != saved["text"]:
                saved["text"] = w.get()
                self._commit_row_note(r, w)

        def finish_note() -> None:
            """Enter, or a click outside the card: typed text is saved; a
            new entry whose note is still empty is not kept at all."""
            commit_note()
            self.frame.focus_set()  # leave the box
            entry = r_entry()
            if (
                entry is not None and entry.id in self._fresh_entries
                and not note.get().strip() and not entry.note
                and not entry.minutes and not entry.internal_note
            ):
                self._fresh_entries.discard(entry.id)
                self.frame.after_idle(lambda eid=entry.id: self.store.delete_entry(eid))
            elif note.get().strip() and entry is not None:
                self._fresh_entries.discard(entry.id)

        def r_entry():
            if row.entry is None:
                return None
            for item in self.store.db.entries:
                if item.id == row.entry.id:
                    return item
            return None

        def enter_note(_e=None) -> str:
            finish_note()
            return "break"

        def cancel_note(_e=None) -> str:
            """Escape: drop what was typed since the last save and leave the
            box; it does not reach the app-wide Escape that collapses."""
            note.delete(0, "end")
            note.insert(0, saved["text"])
            finish_note()
            return "break"

        note.bind("<FocusOut>", commit_note)
        note.bind("<Return>", enter_note)
        note.bind("<Escape>", cancel_note)

        # Clicking empty space does not take focus away from an Entry in Tk,
        # so a click anywhere outside this card is treated as leaving it.
        top_level = note.winfo_toplevel()

        def click_away(event: tk.Event) -> None:
            try:
                if not note.winfo_exists() or note.focus_get() is not note:
                    return
                widget = event.widget
                while isinstance(widget, tk.Misc):
                    if widget is card:
                        return
                    widget = widget.master
            except (tk.TclError, KeyError):
                return
            finish_note()

        click_id = top_level.bind("<Button-1>", click_away, add="+")

        def forget_click(_e=None) -> None:
            try:
                top_level.unbind("<Button-1>", click_id)
            except tk.TclError:
                pass

        note.bind("<Destroy>", forget_click, add="+")
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
        # the row's own expanded detail panel now (see ``_build_modal``), not
        # on the card face, so it doesn't compete for space with the
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
        meta = theme.frame(middle, bg=theme.BG_RAISED)
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
            # ``r=row``, read fresh at click time — see the expand arrow's
            # own binding below for why a frozen ``rid=row.id`` would stop
            # finding this row at all once it's been promoted in place.
            widget.bind(
                "<Button-1>",
                lambda _e, tid=task.id, r=row: self._toggle_expanded(tid, r.id),
            )
            widget.config(cursor="hand2")
        if manual:
            meta.pack(fill="x", padx=6, pady=(0, 1), before=top)

        # A shared label column, right-aligned and wide enough for either
        # label, so the values beside them — actual above, planned below —
        # start at the same x no matter which widget (live label, picker,
        # or plain text) ends up filling either slot.
        label_kw = dict(bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
                         anchor="w", pady=0)

        # One row: Planned label, its value, a "|", then Actual label, its value.
        theme.label(stats, "Actual", **label_kw).grid(
            row=0, column=3, padx=(0, 4), sticky="w"
        )
        if timer_is_this_row:
            # Locked while the timer runs on this row — a fixed set of
            # 15-minute choices (see ``theme.duration_picker``) makes no
            # sense to offer against a figure that's about to move on its
            # own regardless of anything picked here. Shows this row's own
            # last-committed total, frozen — not ticking live, which used
            # to make the very same entry read as two different figures at
            # once depending on which of this card or the timer bar you
            # looked at; the live, ticking comparison lives there now
            # instead (``Overlay._tick``'s own ``timer_total``), right
            # beside the timer itself rather than duplicated here.
            base = row.entry.minutes if row.entry else 0
            theme.label(
                stats, self._format_actual(base),
                bg=theme.BG_RAISED, fg=theme.TEXT, font=theme.FONT_TINY,
                width=5, anchor="w",
            ).grid(row=0, column=4, sticky="w")
        else:
            shown = row.entry.minutes if row.entry else 0
            actual_picker = theme.duration_picker(
                stats, shown, lambda m, r=row: self._commit_row_minutes(r, m),
                width=6, font=theme.FONT_TINY, pady=0,
                label=self._format_actual,
                step=self.store.db.settings.entry_increment_minutes,
                open_upward=True,
            )
            actual_picker.grid(row=0, column=4, sticky="w")
            self._actual_pickers[row.id] = actual_picker

        # Shown and editable on every row now, not just whichever one is
        # expanded — the same as Actual Hours just above. Once an entry
        # exists this is that entry's own Planned Hours (see
        # ``Entry.plan_minutes``), independent of every other entry the
        # task may also have logged today. A recurring note's still-unlogged
        # placeholder shows (and edits) that note's own "usual" minutes (see
        # ``RecurringNote.minutes``) — changing it here resets what every
        # future occurrence plans, not just today's, since there's no
        # entry yet for a one-off figure to belong to. Failing both, it's
        # the task's one day-level ``Plan``, same as ever.
        theme.label(stats, "Planned", **label_kw).grid(
            row=0, column=0, padx=(0, 4), sticky="w"
        )
        theme.duration_picker(
            stats, row.plan_minutes, lambda m, r=row: self._commit_row_plan_minutes(r, m),
            width=6, font=theme.FONT_TINY, pady=0,
            label=lambda m: self._fmt(m) if m else UNSET,
            step=self.store.db.settings.entry_increment_minutes,
            open_upward=True,
        ).grid(row=0, column=1, sticky="w")
        tk.Frame(stats, bg=theme.LINE, width=1, bd=0, highlightthickness=0).grid(
            row=0, column=2, padx=8, pady=3, sticky="ns"
        )

    def _add_another_link(self, parent: tk.Misc, context: TaskContext) -> tk.Widget:
        """A second (or third...) entry for a task already logged today —
        a different stint worth its own note, rather than folding into one.

        Starts at 0m — its own Actual Hours picker is right there to fill in
        a real figure, rather than this guessing one (``NUDGE_MINUTES``, say)
        that would just need overwriting anyway. Its note field claims focus
        the moment the row for it exists (see ``_day_row``), so the user can
        start typing straight away instead of having to click into it."""

        def add() -> None:
            task_rows = [
                r for r in self.store.day_rows(self.date)
                if r.context.task.id == context.task.id
            ]
            # A plan-only placeholder (nothing logged yet) vanishes the
            # moment any entry exists, so the new card would take its place.
            # Make it a real, 0m entry first so it stays and moves down.
            for placeholder in task_rows:
                if placeholder.entry is None and placeholder.id.startswith("task:"):
                    kept = self.store.add_entry(
                        context.task.id, self.date, 0, placeholder.plan_note,
                        plan_minutes=placeholder.plan_minutes, clear_plan=True,
                    )
                    if kept is not None:
                        # Same spot in a saved drag order, under its new id.
                        saved = [
                            kept.id if rid == placeholder.id else rid
                            for rid in self.store.day_order_for(self.date)
                        ]
                        if saved != self.store.day_order_for(self.date):
                            self.store.set_day_order(self.date, saved)
                        task_rows = [
                            r for r in task_rows if r is not placeholder
                        ] + [SimpleNamespace(id=kept.id)]
                    break
            task_row_ids = {r.id for r in task_rows}
            entry = self.store.add_entry(context.task.id, self.date, 0, "", first=True)
            if entry is not None:
                # A saved drag order sorts unknown rows after known ones, so
                # slot the new row in ahead of this task's first row there.
                order = list(self.store.day_order_for(self.date))
                spots = [i for i, rid in enumerate(order) if rid in task_row_ids]
                if spots:
                    order.insert(min(spots), entry.id)
                    self.store.set_day_order(self.date, order)
                self._focus_entry_id = entry.id
                self._fresh_entries.add(entry.id)

        return theme.button(
            parent,
            "+ log another entry",
            add,
            bg=theme.BG,
            fg=theme.MUTED,
            font=theme.FONT_TINY,
            pady=0,
        )

    def _open_modal(self, row: DayRow) -> None:
        """This one row's detail panel (recurrence, internal note, delete),
        as a dimmed modal over the whole tab rather than inline under its
        row — see ``_toggle_expanded``, the only caller. Drawn the same way
        ``theme.confirm`` already draws one: a plain ``tk.Frame`` scrim
        placed over ``self.frame``, not a ``Toplevel`` (see ``self._modal``'s
        own comment for why) — the scrim is ``theme.BG`` blended halfway to
        black (``theme.darken``), standing in for "50% transparent black
        over the page".

        Living outside the row list entirely (``self.frame``, not
        ``self.scroll``) is the point of it: a store change mid-edit still
        refreshes the rows underneath exactly as before, just invisibly,
        behind the scrim, rather than needing this panel itself torn down
        and rebuilt along with them.
        """
        self._modal_row_id = row.id
        dim = theme.darken(theme.BG, 0.5)
        # The scrim is a picture of the tab as it is right now, darkened:
        # the view stays visible underneath, dimmed, the way a modal looks.
        # (Tk has no translucent widgets, so it is drawn rather than blended.)
        host = self._modal_host()
        backdrop, image = self._dimmed_backdrop(host)
        card = next(
            (w for rid, w in self._row_widgets if rid == row.id and w.winfo_exists()), None
        )
        card_snap = self._snapshot(card) if card is not None else None
        if image is not None:
            scrim = tk.Label(host, image=image, bd=0, bg=dim)
            scrim.image = image
        else:
            scrim = tk.Frame(host, bg=dim)  # no snapshot: a flat dim
        # Built out of sight, at the app's own size, and only brought over the
        # app once the opening animation has landed (see ``_modal_morph``).
        hw, hh = host.winfo_width(), host.winfo_height()
        scrim.place(x=-hw - 200, y=0, width=hw, height=hh)
        # Clicking the dimmed backdrop itself (not the panel on top of it)
        # closes it, same as ``theme.confirm``'s own scrim.
        scrim.bind("<Button-1>", lambda _e: self._close_modal())
        scrim.focus_set()
        scrim.bind("<Escape>", self._close_modal_from_event)
        self._modal = scrim

        # The dialog: an anti-aliased rounded card. Its corners and edge are
        # drawn straight onto the dimmed picture behind it (``backdrop``), so
        # they blend into exactly what is there instead of a flat colour.
        # ``panel`` is the card's body, which the rest of the modal code
        # builds into and clears on a refresh.
        card_component = ModalCard(
            scrim, backdrop=backdrop, dim=dim, radius=DIALOG_RADIUS,
            fill=theme.BG_RAISED, border=theme.LINE,
        )
        self._modal_card = card_component
        surface = card_component.surface
        panel = card_component.body
        self._modal_surface = surface
        self._modal_panel = panel
        self._build_modal(panel, row)
        scrim.update_idletasks()  # settle the content's height
        # Centred in the app. Placed here, off-screen along with the scrim, so
        # its edge is painted against the right part of the dimmed picture.
        card_component.centre(0.92)  # placed, and its shadow drawn for where that is
        self._modal_backdrop, self._modal_dim = backdrop, dim
        self._reshadow_job = None

        def reveal() -> None:
            scrim.place_forget()
            scrim.place(relx=0, rely=0, relwidth=1, relheight=1)
            host.update_idletasks()
            scrim.focus_set()

        # Swallows its own click so it doesn't fall through to the scrim
        # underneath and close the very panel it landed on.
        for widget in (surface, panel):
            widget.bind("<Button-1>", lambda _e: "break")
        self._modal_panel = panel
        self._modal_morph(True, host, card, surface, card_snap, reveal)
        # The dialog changes height with its content (Repeat switched on or
        # off, say) and is centred, so its shadow and its edge — both drawn
        # for one particular size and place — are redrawn to follow it.
        surface.bind("<Configure>", self._schedule_reshadow, add="+")

    _gliding = False

    def _modal_glide(self, change: Callable[[], None]) -> None:
        """Runs ``change`` — something that alters the dialog's height — with
        the resize animated on the dialog's card. The widgets inside are not
        moved: they are taken off the card for the moment (and the change made
        meanwhile), and the card alone — panel and shadow together — is
        resized and re-centred, frame by frame, to its new size; the widgets
        are put back when it lands."""
        card = getattr(self, "_modal_card", None)
        scrim = self._modal
        if (
            self._gliding or scrim is None or card is None
            or not card.surface.winfo_exists() or not scrim.winfo_exists()
        ):
            change()
            return
        self._gliding = True
        try:
            start = card.rect()
            card.hide_contents()
            card.place_rect(*start)  # pinned where it is: the change cannot move it
            change()
            for _ in range(3):  # the content's height settles over a few passes
                scrim.update_idletasks()
            height = min(card.natural_height(), max(scrim.winfo_height() - 8, 60))
            target = card.centred_rect(height)
            if target != start:
                card.animate_to(target, int(self.MODAL_MS * 1.25), start)
        except tk.TclError:
            pass
        finally:
            try:
                if card.surface.winfo_exists():
                    card.show_contents()
                    card.centre(0.92)
            except tk.TclError:
                pass
            self._gliding = False

    def _schedule_reshadow(self, _event: object = None) -> None:
        if self._modal is None or self._reshadow_job is not None or self._gliding:
            return
        self._reshadow_job = self._modal.after_idle(self._reshadow)

    def _reshadow(self) -> None:
        """The card changed size by itself (its content did): the shadow and
        the card's edge follow."""
        self._reshadow_job = None
        card = getattr(self, "_modal_card", None)
        if self._gliding or self._modal is None or card is None or not card.surface.winfo_exists():
            return
        geom = card.rect()
        if geom == getattr(self, "_shadow_geom", None):
            return
        self._shadow_geom = geom
        card.restyle()

    def _modal_host(self) -> tk.Misc:
        """The widget the modal covers: the whole app shell, not just this tab."""
        shell = getattr(self.overlay, "shell", None)
        if shell is not None and shell.winfo_exists() and shell.winfo_width() > 4:
            return shell
        return self.frame

    _backdrop_token = 0

    def _modal_host(self) -> tk.Misc:
        """The widget the modal covers: the whole app shell, not just this tab."""
        shell = getattr(self.overlay, "shell", None)
        if shell is not None and shell.winfo_exists() and shell.winfo_width() > 4:
            return shell
        return self.frame

    MODAL_MS = 230
    MODAL_DIM = 0.55  # how dark the dimmed app gets, as the scrim's own 55% black

    def _snapshot(self, widget: tk.Misc):
        """A bitmap of just ``widget`` as drawn now, for a ghost to show;
        ``None`` if the window can't be captured."""
        from .. import winfx

        try:
            root = widget.winfo_toplevel()
            hwnd = winfx.root_hwnd(root.winfo_id())
            rect = (
                widget.winfo_rootx() - root.winfo_rootx(),
                widget.winfo_rooty() - root.winfo_rooty(),
                widget.winfo_width(), widget.winfo_height(),
            )
            return winfx.capture_crop(hwnd, root.winfo_width(), root.winfo_height(), rect)
        except (OSError, ValueError, tk.TclError):
            return None

    def _modal_morph(self, opening: bool, host: tk.Misc, card, surface, snap, swap) -> None:
        """Grows the modal out of its entry card, or folds it back into it,
        the way the app's own expand/collapse does (see ``_ghost_morph`` in
        ``overlay.py``): a ghost window dissolves a snapshot (the card, or
        the dialog) into a plain rectangle of the card's colour in place, then
        eases that rectangle to the other shape, its border changing with it;
        a second ghost fades the whole app dark (or back). Nothing real is
        resized per frame. ``swap()`` does the real change once — opening:
        bring the modal over the app; closing: remove it — hidden under the
        ghosts, after which the ghost fades out to reveal the live widgets,
        content included. Without a card or a ghost, ``swap()`` just runs."""
        from .. import anim, winfx

        swapped = False

        def do_swap() -> None:
            nonlocal swapped
            if not swapped:
                swapped = True
                swap()

        ghost = dimmer = None
        keep = []
        try:
            if card is None or not card.winfo_exists() or not surface.winfo_exists():
                return
            hx, hy = host.winfo_rootx(), host.winfo_rooty()
            hw, hh = host.winfo_width(), host.winfo_height()
            card_rect = (
                card.winfo_rootx(), card.winfo_rooty(), card.winfo_width(), card.winfo_height()
            )
            dialog = (
                hx + surface.winfo_x(), hy + surface.winfo_y(),
                surface.winfo_width(), surface.winfo_height(),
            )
            ov = self.overlay
            scale = ov._dpi_scale() if hasattr(ov, "_dpi_scale") else 1.0

            def rgb(colour: str) -> tuple:
                return tuple(v / 257 for v in host.winfo_rgb(colour))

            card_edge = (rgb(card._border), card._thick, card._radius)
            dialog_edge = (rgb(theme.LINE), surface._thick, surface._radius)
            src, dst = (card_rect, dialog) if opening else (dialog, card_rect)
            edge_a, edge_b = (card_edge, dialog_edge) if opening else (dialog_edge, card_edge)
            fill_a, fill_b = (
                (card._fill, theme.BG_RAISED) if opening else (theme.BG_RAISED, card._fill)
            )
            plain_a, plain_b = winfx.solid(fill_a), winfx.solid(fill_b)
            dark = winfx.solid("#000000")
            keep = [plain_a, plain_b, dark]
            dim_rect = (hx, hy, hw, hh)
            dim_edge = ((0, 0, 0), 1, getattr(ov, "PANEL_RADIUS", 10) * scale)

            def level(t: float) -> float:
                return max(self.MODAL_DIM * (t if opening else 1 - t), 1 / 255)

            dimmer = winfx.Ghost(hw, hh, click_through=False)
            ghost = winfx.Ghost(max(src[2], dst[2]), max(src[3], dst[3]), click_through=False)
            first = snap if snap is not None else plain_a
            dimmer.draw(dim_rect, dark, dark, 0.0, level(0.0), border=dim_edge)
            ghost.draw(src, first, first, 0.0, 1.0, border=edge_a)
            anim._vsync()

            if not opening:
                do_swap()  # the dialog goes; the ghosts hold its look
                host.update()
                anim._vsync()

            hold = 0.22 if snap is not None else 0.0

            def frame(t: float) -> None:
                dimmer.draw(dim_rect, dark, dark, 0.0, level(t), border=dim_edge)
                if t < hold:
                    ghost.draw(src, snap, plain_a, t / hold, 1.0, border=edge_a)
                    return
                u = (t - hold) / (1 - hold)
                ghost.draw(
                    anim.lerp_rect(src, dst, u), plain_a, plain_b, u, 1.0,
                    border=ov._lerp_border(edge_a, edge_b, u),
                )

            anim.run(self.MODAL_MS, frame)

            if opening:
                do_swap()  # the real modal, laid out and painted, under the ghosts
                host.update()
                anim._vsync()
            dimmer.close()
            dimmer = None
            ghost.set_click_through(True)
            anim.run(
                120,
                lambda t: ghost.draw(dst, plain_b, plain_b, 0.0, 1.0 - t, border=edge_b),
                easing=anim.ease_out_cubic,
            )
        except (OSError, tk.TclError, AttributeError):
            pass
        finally:
            for item in (ghost, dimmer, snap, *keep):
                if item is not None:
                    try:
                        item.close()
                    except Exception:
                        pass
            do_swap()

    def _modal_collapse(self, scrim, surface, card, remove) -> None:
        """Closes the modal: the widgets inside come off the card at once and
        the card itself — panel and shadow together — shrinks into the entry
        card it came from; then the dimming fades away. ``remove()`` takes the
        real modal out. Without a card to fold into, it just runs."""
        from .. import anim, winfx

        removed = False

        def do_remove() -> None:
            nonlocal removed
            if not removed:
                removed = True
                remove()

        ghost = dark = None
        component = getattr(self, "_modal_card", None)
        try:
            if (
                card is None or component is None or not card.winfo_exists()
                or not surface.winfo_exists()
            ):
                return
            host = scrim.master
            start = component.rect()
            target = (
                card.winfo_rootx() - scrim.winfo_rootx(),
                card.winfo_rooty() - scrim.winfo_rooty(),
                card.winfo_width(), card.winfo_height(),
            )
            component.hide_contents()
            component.place_rect(*start)
            component.animate_to(target, self.MODAL_MS, start)

            # The dimming leaves the way the dialog did: a ghost holds it while
            # the real scrim goes, then fades.
            hx, hy, hw, hh = (
                host.winfo_rootx(), host.winfo_rooty(), host.winfo_width(), host.winfo_height()
            )
            ov = self.overlay
            scale = ov._dpi_scale() if hasattr(ov, "_dpi_scale") else 1.0
            edge = ((0, 0, 0), 1, getattr(ov, "PANEL_RADIUS", 10) * scale)
            dark = winfx.solid("#000000")
            ghost = winfx.Ghost(hw, hh, click_through=False)
            rect = (hx, hy, hw, hh)
            ghost.draw(rect, dark, dark, 0.0, self.MODAL_DIM, border=edge)
            anim._vsync()
            do_remove()
            host.update()
            anim._vsync()
            ghost.set_click_through(True)
            anim.run(
                120,
                lambda t: ghost.draw(
                    rect, dark, dark, 0.0, max(self.MODAL_DIM * (1 - t), 1 / 255), border=edge
                ),
                easing=anim.ease_out_cubic,
            )
        except (OSError, tk.TclError, AttributeError):
            pass
        finally:
            for item in (ghost, dark):
                if item is not None:
                    try:
                        item.close()
                    except Exception:
                        pass
            do_remove()

    def _dimmed_backdrop(self, host: tk.Misc | None = None):
        """``(backdrop, image)`` for the modal's scrim: a snapshot of this
        tab, darkened, as a PhotoImage, and a function giving the RGB bytes
        of any rectangle of it (see ``modern.Surface``). ``(None, None)``
        when the window can't be snapshotted."""
        from .. import winfx
        from . import charts

        try:
            root = self.frame.winfo_toplevel()
            hwnd = winfx.root_hwnd(root.winfo_id())
            rw, rh = root.winfo_width(), root.winfo_height()
            shot = winfx.capture_rgb(hwnd, rw, rh)
            host = host or self.frame
            fx = host.winfo_rootx() - root.winfo_rootx()
            fy = host.winfo_rooty() - root.winfo_rooty()
            fw, fh = host.winfo_width(), host.winfo_height()
            if fw < 4 or fh < 4 or fx < 0 or fy < 0 or fx + fw > rw or fy + fh > rh:
                return None, None
            crop = b"".join(
                shot[((fy + y) * rw + fx) * 3:((fy + y) * rw + fx + fw) * 3] for y in range(fh)
            )
            table = bytes(round(i * 0.45) for i in range(256))  # 55% black
            dimmed = crop.translate(table)
            raster = charts.Raster(fw, fh, "#000000")
            raster.buf = bytearray(dimmed)
            image = raster.photo(host)
        except (OSError, ValueError, tk.TclError):
            return None, None

        def backdrop(x: int, y: int, w: int, h: int) -> bytes | None:
            return _crop_clamped(dimmed, fw, fh, x, y, w, h)

        TodayView._backdrop_token += 1
        backdrop.token = TodayView._backdrop_token  # type: ignore[attr-defined]
        return backdrop, image

    def _close_modal_from_event(self, _event: object = None) -> str:
        self._close_modal()
        # Stops here rather than also reaching ``Overlay``'s own root-level
        # ``<Escape>`` (see ``overlay.py``), which collapses the whole app
        # to its badge — dismissing this modal shouldn't do that too.
        return "break"

    def _close_modal(self) -> None:
        scrim = surface = card = snap = None
        row_id = self._modal_row_id
        if self._modal is not None and self._modal.winfo_exists():
            # Clicking the X chip, the scrim, or pressing Escape never
            # moves keyboard focus the way clicking another text field
            # would (see ``theme.commit_focused_entry``), so a just-typed
            # internal note sitting in a focused, uncommitted ``Text`` box
            # would otherwise be destroyed along with the modal, silently,
            # before its own ``<FocusOut>`` handler ever got to fire.
            theme.commit_focused_entry(self._modal)
            scrim, surface = self._modal, getattr(self, "_modal_surface", None)
            card = next(
                (w for rid, w in self._row_widgets if rid == row_id and w.winfo_exists()), None
            )
        self._modal = None
        self._modal_panel = None
        self._repeat = None
        self._note_drafts.clear()  # an unsaved internal note is discarded
        if self._modal_row_id is not None:
            self._set_arrow(self._modal_row_id, expanded=False)
        self._modal_row_id = None
        self._expanded.clear()
        self._expanded_row.clear()
        if scrim is not None and scrim.winfo_exists():
            def remove() -> None:
                if scrim.winfo_exists():
                    scrim.destroy()

            # Folds back into the entry card (the state above is already
            # cleared, so a new modal can open as soon as this returns).
            self._modal_collapse(scrim, surface, card, remove)

    def _set_arrow(self, row_id: str, *, expanded: bool) -> None:
        """Flips one row's own expand/collapse arrow in place — see
        ``_expand_arrows`` — rather than rebuilding the row just to change
        one icon."""
        arrow = self._expand_arrows.get(row_id)
        if isinstance(arrow, tk.Label) and arrow.winfo_exists():
            icons.swap(
                arrow, "row-collapse" if expanded else "row-expand",
                fallback="\u25b4" if expanded else "\u25be",
            )

    def _modal_row(self) -> DayRow | None:
        """The open modal's row as the store has it now."""
        if self._modal_row_id is None:
            return None
        return next(
            (r for r in self.store.day_rows(self.date) if r.id == self._modal_row_id), None
        )

    def _refresh_modal(self) -> None:
        """Rebuilds this modal's own content against the row's current
        state — see ``_set_row_recurrence``, where promoting a placeholder
        to its own ``RecurringNote`` changes what the internal note and
        delete controls above the Repeat editor should now allow, which
        only rebuilding (of just this panel, not the row list) picks up."""
        if self._modal_panel is None or self._modal_row_id is None:
            return
        row = next(
            (r for r in self.store.day_rows(self.date) if r.id == self._modal_row_id), None
        )
        if row is None:
            self._close_modal()
            return
        for child in self._modal_panel.winfo_children():
            child.destroy()
        self._build_modal(self._modal_panel, row)

    def _build_modal(self, panel: tk.Misc, row: DayRow) -> None:
        """This one row's own Repeat editor, with the row's own delete
        control on the same line as the Repeat checkbox. Nothing structural
        about the task lives here any more — billable, archive and
        task-level delete are all Setup-tab-only now (see
        ``timesheet/ui/projects.py``); this panel exists purely to plan
        today's (and future) logging, and to dismiss this one row, not to
        manage the task record itself. The dot beside the task's name on
        every row still reflects billable, just never edits it from here."""
        header = theme.frame(panel, bg=theme.BG_RAISED)
        header.pack(fill="x", padx=12, pady=(10, 0))
        # Row 1: customer, with the close button at the end.
        top = theme.frame(header, bg=theme.BG_RAISED)
        top.pack(fill="x")
        modern.RoundButton(
            top, "close", self._close_modal, size=24, bg=theme.BG_RAISED,
        ).pack(side="right")
        theme.label(
            top, row.context.client.name,
            bg=theme.BG_RAISED, fg=theme.TEXT, font=theme.FONT_BOLD, anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        # Row 2: K-code and project.
        line = theme.frame(header, bg=theme.BG_RAISED)
        line.pack(fill="x", pady=(2, 0))
        theme.pill(line, row.context.project.k_code).pack(side="left")
        theme.label(
            line, row.context.project.name,
            bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_SMALL, anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))
        # Row 3: billable badge and task name.
        line = theme.frame(header, bg=theme.BG_RAISED)
        line.pack(fill="x", pady=(4, 0))
        billable = row.context.billable
        theme.pill(
            line, "Billable" if billable else "Non-billable",
            fg=theme.BILLABLE if billable else theme.MUTED, bg=theme.BG_INPUT,
        ).pack(side="left")
        theme.label(
            line, row.context.display_title,
            bg=theme.BG_RAISED, fg=theme.TEXT, font=theme.FONT_BOLD, anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        # Header and footer stay put; what is between them scrolls when it
        # is taller than the tab (Repeat expands to a lot more than it
        # starts as) — see ``fit`` at the bottom.
        scroller = ScrollFrame(panel, bg=theme.BG_RAISED)
        scroller.pack(fill="x")
        content = scroller.body

        # Planned and Actual, one row each with its label beside the value,
        # both editable through the same drop-up pickers as the card, and
        # the +/- keys for Actual at the row's right-hand end.
        stats = theme.frame(content, bg=theme.BG_RAISED)
        stats.pack(fill="x", padx=12, pady=(10, 2))
        step = self.store.db.settings.entry_increment_minutes
        timer = self.store.db.active_timer
        locked = timer is not None and self._is_timer_row(timer, row)

        def commit(change):
            def run(minutes: int) -> None:
                current = self._modal_row() or row
                change(current, minutes)
                self._modal_changed()
            return run

        pickers: dict[str, tk.Misc] = {}
        for column, (caption, key) in enumerate((("Planned", "plan"), ("Actual", "actual"))):
            stats.columnconfigure(column, weight=1, uniform="stat")
            card = modern.Surface(stats, fill=theme.BG_INPUT, border=theme.LINE, radius=10,
                                  outer=theme.BG_RAISED)
            card.grid(row=0, column=column, sticky="ew", padx=(0, 6))
            theme.label(
                card.body, caption, bg=theme.BG_INPUT, fg=theme.MUTED, font=theme.FONT_TINY,
            ).pack(side="left", padx=(8, 6), pady=3)
            if key == "plan":
                picker = theme.duration_picker(
                    card.body, row.plan_minutes, commit(self._commit_row_plan_minutes),
                    width=6, font=theme.FONT_BOLD, pady=0,
                    label=lambda m: self._fmt(m) if m else UNSET, step=step, open_upward=True,
                )
            elif locked:
                picker = theme.label(
                    card.body, self._format_actual(row.entry.minutes if row.entry else 0),
                    bg=theme.BG_INPUT, fg=theme.TEXT, font=theme.FONT_BOLD,
                )
            else:
                picker = theme.duration_picker(
                    card.body, row.entry.minutes if row.entry else 0,
                    commit(self._commit_row_minutes),
                    width=6, font=theme.FONT_BOLD, pady=0,
                    label=self._format_actual, step=step, open_upward=True,
                )
            picker.pack(side="left", pady=2)
            pickers[key] = picker

        keys = theme.frame(stats, bg=theme.BG_RAISED)
        keys.grid(row=0, column=2, sticky="ns")

        def nudge(delta: int) -> None:
            current = self._modal_row() or row
            self._nudge(current, delta)
            self._modal_changed()

        modern.EdgeButton(
            keys, "plus", lambda: nudge(NUDGE_MINUTES), outer="top",
            fill=theme.BG_INPUT, bg=theme.BG_RAISED, enabled=not locked,
        ).pack(side="top", fill="both", expand=True, pady=(0, 2))
        minus = modern.EdgeButton(
            keys, "minus", lambda: nudge(-NUDGE_MINUTES), outer="bottom",
            fill=theme.BG_INPUT, bg=theme.BG_RAISED,
            enabled=not locked and bool(row.entry and row.entry.minutes > 0),
        )
        minus.pack(side="top", fill="both", expand=True)

        def sync_stats() -> None:
            """Shows the row's figures as the store has them now."""
            current = self._modal_row()
            if current is None or not stats.winfo_exists():
                return
            minutes = current.entry.minutes if current.entry else 0
            if pickers["plan"].winfo_exists():
                pickers["plan"].config(
                    text=self._fmt(current.plan_minutes) if current.plan_minutes else UNSET
                )
            if pickers["actual"].winfo_exists():
                pickers["actual"].config(text=self._format_actual(minutes))
            minus.set_enabled(not locked and minutes > 0)

        self._modal_sync_stats = sync_stats

        box = theme.frame(content, bg=theme.BG_RAISED)
        box.pack(fill="x", padx=12, pady=(8, 4))

        self._customer_note(box, row)
        self._internal_note(box, row)
        divider = theme.hairline(box)
        divider.pack(fill="x", pady=(0, 5))
        self._modal_divider = divider  # everything above it stays put when Repeat resizes

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
            # Read afresh each time this is drawn: switching Repeat on can
            # turn a bare placeholder into something that can be skipped.
            nonlocal can_dismiss
            current = self._modal_row() or row
            can_dismiss = not current.is_placeholder or bool(current.recurring_note_id)
            # A second click away from destructive rather than a modal over
            # the whole window — the trash chip itself becomes the question,
            # the same recipe the Setup tab's own delete chip already uses
            # (see ``projects.py``'s ``_delete_chip``). "Delete" reads as
            # "Skip" for a recurring note's still-unlogged placeholder,
            # matching what it actually does (see ``_confirm_delete_entry``).
            if row.id in self._confirm_delete:
                wrapper = theme.frame(header_row, bg=theme.BG_RAISED)
                modern.PillButton(
                    wrapper, "Skip" if row.entry is None else "Delete",
                    lambda: self._confirm_delete_entry(self._modal_row() or row), kind="danger",
                    font=theme.FONT_TINY, padx=12, pady=2, bg=theme.BG_RAISED,
                ).pack(side="left")
                modern.PillButton(
                    wrapper, "Cancel", lambda: self._disarm_delete_entry(row.id),
                    font=theme.FONT_TINY, padx=12, pady=2, bg=theme.BG_RAISED,
                ).pack(side="left", padx=(6, 0))
                wrapper.pack(side="right")
                return
            # "trash" once there's something to delete — the same glyph and
            # tint the Setup/Recurring tabs' own delete button uses, sitting
            # straight on the row rather than a coloured chip behind it (see
            # ``_nudge`` just above for why). "delete"'s own MUTED tint says
            # the same thing while there's nothing yet to act on.
            modern.RoundButton(
                header_row, "trash", lambda: self._arm_delete_entry(row.id), size=26,
                fill=theme.blend(theme.BG_RAISED, theme.DANGER, 0.10),
                glyph_colour=theme.DANGER, bg=theme.BG_RAISED, enabled=can_dismiss,
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
            resize=self._modal_glide,
        )

        footer = theme.frame(panel, bg=theme.BG_RAISED)
        footer.pack(fill="x", padx=12, pady=(2, 12))
        modern.PillButton(
            footer, "Done", self._close_modal, kind="primary", bg=theme.BG_RAISED,
        ).pack(side="right")

        def fit(_event: object = None) -> None:
            """Sizes the scrolling middle to its content, but never taller
            than what is left of the tab once header, footer and margins
            are taken out."""
            if not (scroller.winfo_exists() and self._modal is not None and self._modal.winfo_exists()):
                return
            chrome = header.winfo_reqheight() + footer.winfo_reqheight() + 34
            room = self._modal.winfo_height() - 56 - chrome
            want = content.winfo_reqheight()
            scroller._canvas.configure(height=max(90, min(want, room)))

        content.bind("<Configure>", fit, add="+")
        panel.after_idle(fit)

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
            # Promoting makes the internal note usable (the delete control
            # reads the row afresh each time the editor draws it). Done in
            # place: the modal itself is not rebuilt.
            enable = getattr(self, "_note_enable", None)
            if enable is not None:
                enable()

    def _customer_note(self, parent: tk.Misc, row: DayRow) -> None:
        """The entry's own note — the same text as the one on its card (what
        an export carries), editable here too, with the internal note's own
        Save/Cancel row: it appears on the first change, and nothing is saved
        until Save (or Enter) — Cancel or Escape puts the saved text back. A
        rebuild of the modal around the field keeps the typed text as a draft.
        """
        theme.label(
            parent, "Customer note", bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
        ).pack(fill="x", pady=(0, 2))
        field = modern.Field(parent, bg=theme.BG_RAISED, font=theme.FONT_SMALL, radius=10)
        field.pack(fill="x", pady=(0, 5))
        entry = field.entry
        key = f"customer:{row.id}"
        draft = self._note_drafts.get(key)
        entry.insert(0, draft if draft is not None else row.note)
        saved = {"text": row.note}

        # A strip of fixed height, always in place, so the dialog does not
        # change size as the buttons come and go.
        strip = theme.frame(parent, bg=theme.BG_RAISED)
        actions = theme.frame(strip, bg=theme.BG_RAISED)

        def dirty() -> bool:
            return entry.get() != saved["text"]

        def sync(_e: object = None) -> None:
            if not entry.winfo_exists():
                return
            if dirty():
                self._note_drafts[key] = entry.get()
                if not actions.winfo_ismapped():
                    actions.pack(fill="x")
            else:
                self._note_drafts.pop(key, None)
                actions.pack_forget()

        def save() -> None:
            if not entry.winfo_exists():
                return
            if dirty():
                saved["text"] = entry.get()
                current = self._modal_row() or row
                self._commit_row_note(current, entry)
                # The card behind keeps its own copy of the text: bring it along.
                for card_key in {row.id, current.id, self._modal_row_id}:
                    box = self._note_boxes.get(card_key)
                    if box is not None and box.winfo_exists() and box.get() != entry.get():
                        box.delete(0, "end")
                        box.insert(0, entry.get())
            self._note_drafts.pop(key, None)
            actions.pack_forget()
            self._modal.focus_set()

        def cancel() -> None:
            entry.delete(0, "end")
            entry.insert(0, saved["text"])
            self._note_drafts.pop(key, None)
            actions.pack_forget()
            self._modal.focus_set()

        modern.PillButton(
            actions, "Save", save, kind="primary", font=theme.FONT_TINY, padx=12, pady=2,
            bg=theme.BG_RAISED,
        ).pack(side="left")
        modern.PillButton(
            actions, "Cancel", cancel, font=theme.FONT_TINY, padx=12, pady=2,
            bg=theme.BG_RAISED,
        ).pack(side="left", padx=(6, 0))
        actions.update_idletasks()
        strip.pack_propagate(False)
        strip.configure(height=actions.winfo_reqheight())
        strip.pack(fill="x", pady=(0, 5))
        if draft is not None and draft != row.note:
            actions.pack(fill="x")  # an unsaved edit is waiting for Save

        entry.bind("<KeyRelease>", sync, add="+")
        for event in ("<<Paste>>", "<<Cut>>"):
            entry.bind(event, lambda _e: entry.after_idle(sync), add="+")
        entry.bind("<Return>", lambda _e: (save(), "break")[1])
        entry.bind("<Escape>", lambda _e: (cancel(), "break")[1])

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

        Its own Save/Cancel row, rather than committing silently the moment
        focus leaves — shown only once the text actually changes (via the
        ``Text`` widget's own ``<<Modified>>`` flag, reset after the initial
        scripted ``insert`` below so that doesn't count), so reading a note
        without touching it never puts up controls with nothing yet to act
        on. Nothing is saved until Save is pressed or Enter is pressed
        (Shift+Enter adds a line): leaving the field or closing the modal
        throws the edit away. A rebuild of the modal around the field
        (Repeat switched on or off) keeps the typed text as a draft.
        """
        theme.label(
            parent, "Internal note \u2013 not exported",
            bg=theme.BG_RAISED, fg=theme.MUTED, font=theme.FONT_TINY,
        ).pack(fill="x", pady=(0, 2))
        field = modern.Field(parent, bg=theme.BG_RAISED, lines=5, font=theme.FONT_SMALL, radius=10)
        box = field.entry
        field.pack(fill="x", pady=(0, 5))
        can_hold_internal_note = row.entry is not None or bool(row.recurring_note_id)

        draft = self._note_drafts.get(row.id)
        box.insert("1.0", draft if draft is not None else row.internal_note)
        box.edit_modified(False)

        # A strip of fixed height, always in place: the buttons inside it
        # appear on the first edit, but the dialog around them must not grow
        # (it is centred, so growing would shift everything and repaint).
        strip = theme.frame(parent, bg=theme.BG_RAISED)
        actions = theme.frame(strip, bg=theme.BG_RAISED)
        # Not packed here — only once ``on_modified`` sees a real edit.

        saved = {"text": row.internal_note}  # what is stored now

        def current_text() -> str:
            return box.get("1.0", "end-1c")

        def hide_actions() -> None:
            box.edit_modified(False)
            actions.pack_forget()

        def save() -> None:
            self._note_drafts.pop(row.id, None)
            saved["text"] = current_text()
            self._commit_row_internal_note(self._modal_row() or row, current_text())
            hide_actions()

        def cancel() -> None:
            self._note_drafts.pop(row.id, None)
            box.delete("1.0", "end")
            box.insert("1.0", saved["text"])
            hide_actions()

        def on_modified(_e: tk.Event) -> None:
            if box.edit_modified():
                if current_text() == saved["text"]:
                    # Edited back to what is stored (undo, or retyped): there
                    # is nothing left to save or cancel.
                    self._note_drafts.pop(row.id, None)
                    actions.pack_forget()
                else:
                    if not actions.winfo_ismapped():
                        actions.pack(fill="x")
                    self._note_drafts[row.id] = current_text()
            box.edit_modified(False)

        def on_return(_e: tk.Event) -> str:
            save()
            return "break"

        def on_shift_return(_e: tk.Event) -> str:
            box.insert("insert", "\n")
            return "break"

        modern.PillButton(
            actions, "Save", save, kind="primary", font=theme.FONT_TINY, padx=12, pady=2,
            bg=theme.BG_RAISED,
        ).pack(side="left")
        modern.PillButton(
            actions, "Cancel", cancel, font=theme.FONT_TINY, padx=12, pady=2,
            bg=theme.BG_RAISED,
        ).pack(side="left", padx=(6, 0))
        actions.update_idletasks()
        strip.pack_propagate(False)
        strip.configure(height=actions.winfo_reqheight())
        strip.pack(fill="x", pady=(0, 5))

        box.bind("<<Modified>>", on_modified)
        box.bind("<Return>", on_return)
        box.bind("<Shift-Return>", on_shift_return)
        if draft is not None and draft != row.internal_note:
            actions.pack(fill="x")  # an unsaved edit is waiting for Save

        def enable() -> None:
            if box.winfo_exists():
                box.config(state="normal")

        # A bare placeholder has nothing to hang a note on yet: inert until
        # Repeat gives it a recurring note (see ``_set_row_recurrence``).
        self._note_enable = enable
        if not can_hold_internal_note:
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
            label=lambda m: self._fmt(m) if m else UNSET,
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
        """Opens this row's detail panel as a modal (see ``_open_modal``),
        or closes it if it's the one already open — never the row list
        rebuilt, let alone the whole view, just to show or hide it."""
        if self._modal_row_id == row_id:
            self._close_modal()
            return
        row = next((r for r in self.store.day_rows(self.date) if r.id == row_id), None)
        if row is None:
            return
        self._close_modal()
        self._expanded = {task_id}
        self._expanded_row = {task_id: row_id}
        self._set_arrow(row_id, expanded=True)
        self._open_modal(row)

    def _toggle_timer_selection(self, row: DayRow) -> None:
        """A row's own circular selector, clicked.

        Only reachable while nothing is running at all — the radio is
        disabled outright the moment a timer starts, on every row
        including the one it's running against, so there's no longer a
        click here that could stop (and so unselect) a running timer; the
        timer bar's own Stop button is the only way to do that now. While
        nothing's running, this simply arms (or disarms) this row as the
        one the timer bar's own Start button will act on next.
        """
        if self.store.db.active_timer:
            return
        self._selected_row_id = None if self._selected_row_id == row.id else row.id
        # Show it at once: the row isn't rebuilt by the refresh below.
        for row_id, radio in list(self._timer_radios.items()):
            if radio.winfo_exists():
                radio.set_selected(row_id == self._selected_row_id)
        # The timer bar's Start button lives outside this view entirely, so
        # arming a row needs the overlay's own refresh — not just this
        # view's — to pick the change up; unlike a store mutation, nothing
        # else makes that happen on its own.
        self.overlay.refresh()

    @staticmethod
    def _is_timer_row(timer: ActiveTimer, row: DayRow) -> bool:
        """Whether ``row`` is the one specific row ``timer`` is running
        against — not merely "some row on the same task", which several
        call sites (``_day_row``'s own highlight and live label, ``tick``)
        used to get wrong the moment a task had more than one entry logged
        the same day, lighting every one of them up and showing every one
        of them the task's own combined total ticking up together.

        ``timer.entry_id`` (set once the row it started on already had its
        own ``Entry`` — see ``Store.start_timer``) is matched against that
        same entry's row directly. Blank — the row was still a placeholder
        when the timer began, and stays one for as long as the timer just
        runs rather than being edited — falls back to the same task +
        recurring-note pairing a placeholder's own identity is built from
        (``DayRow.id``), guarded by ``row.entry is None`` so a *different*
        placeholder for the same task (there is only ever at most one) or
        one freshly promoted since can't be mistaken for it.
        """
        if timer.task_id != row.context.task.id:
            return False
        if timer.entry_id:
            return row.entry is not None and row.entry.id == timer.entry_id
        return row.entry is None and (timer.recurring_note_id or "") == (row.recurring_note_id or "")

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
        """First click on the trash chip: swaps it for "Delete"/"Cancel" —
        see ``_build_modal``'s ``header_trailing``. That trailing widget is
        redrawn on every ``RepeatEditor.build()`` regardless, so patching
        this state and asking just the editor to redraw itself is enough —
        no reason to touch anything above or below it in the modal, let
        alone the row list behind it."""
        self._confirm_delete.add(row_id)
        if self._repeat is not None:
            self._repeat.build()

    def _disarm_delete_entry(self, row_id: str) -> None:
        self._confirm_delete.discard(row_id)
        if self._repeat is not None:
            self._repeat.build()

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
        # Nothing left for this modal to show once its own row is gone.
        if self._modal_row_id == row.id:
            self._close_modal()

    def _nudge(self, row: DayRow, delta: int) -> None:
        if row.entry is not None:
            self._begin_light_refresh()
            self.store.adjust_entry_minutes(row.entry.id, delta)
        else:
            # Nothing logged yet: the first nudge is what creates the
            # entry — promoted in place below (``_promote_row``) rather
            # than left to the ordinary full rebuild, same as every other
            # field commit here. Its own Planned Hours carries over from
            # whatever this placeholder was already showing (see
            # ``Store.add_entry``), rather than resetting to "Unset" the
            # moment anything's logged. ``clear_plan`` only when this was
            # the task's own bare ``Plan`` placeholder (no recurring note
            # behind it) — see ``Store.add_entry`` for why a Plan set on
            # purpose as its own goal is never this call's to clear.
            self._begin_light_refresh()
            entry = self.store.adjust_entry(
                row.context.task.id, self.date, delta, row.plan_note, row.recurring_note_id,
                row.plan_internal_note, row.plan_minutes,
                clear_plan=not row.recurring_note_id,
            )
            if entry is None:
                # The disabled minus chip already keeps a real click from
                # ever landing here with nothing to take away — only
                # reachable at all from a stale reference, nothing to
                # patch for.
                return
            self._promote_row(row, entry)
        # ``row.entry`` is the same ``Entry`` instance ``self.db.entries``
        # holds (see ``Store.day_rows``), so this already reflects the
        # nudge above — unlike a field committed through the picker
        # itself, nothing updated the button's own text for this one,
        # so it's patched by hand here instead of a full rebuild.
        picker = self._actual_pickers.get(row.id)
        if picker is not None and picker.winfo_exists():
            picker.config(text=self._format_actual(row.entry.minutes))
        self._sync_decrease_chip(row.id, row.entry.minutes)

    _modal_sync_stats = None

    def _modal_changed(self) -> None:
        """A figure was changed from the modal: the card behind is rebuilt
        for it (the card's own light-refresh shortcut assumes the card's
        widget made the edit), and the modal's own figures follow."""
        self._light_refresh_base, self._light_refresh_count = None, 0
        self.frame.after_idle(self.refresh)
        if self._modal_sync_stats is not None:
            self._modal_sync_stats()

    def _begin_light_refresh(self) -> None:
        """Call right before a store mutation that only sets a field on a
        row's already-existing ``Entry`` — see ``_light_refresh_base``."""
        if self._light_refresh_base is None:
            self._light_refresh_base = self.store.history_depth()
        self._light_refresh_count += 1

    def _promote_row(self, row: DayRow, entry: Entry) -> None:
        """Turns a placeholder row — a bare ``Plan``, or a recurring note
        not yet logged — into one backed by a real ``Entry``, in place,
        rather than leaving that to the ordinary full rebuild.

        ``row`` is the very same ``DayRow`` instance every widget this
        row's own ``_day_row`` built already closes over (``lambda r=row:
        ...``, ``lambda: self._nudge(row, ...)``) and reads fresh at click
        time, not a snapshot frozen at render time — so setting its
        ``entry`` here is all it takes for every one of those closures to
        start taking the "already logged" branch from now on, with
        nothing about the row torn down or rebuilt to get there.

        This is the one promotion a burst of rapid clicking used to always
        force a full ``scroll.clear()`` for, no matter which field
        actually changed — and so, going back and forth across several
        still-unlogged rows, the one most likely to still be destroying a
        focused widget (and firing a real ``<FocusOut>``) often enough to
        occasionally outlast even a generous grace period (see
        ``Overlay._collapse_if_focus_left``). Avoiding the rebuild here
        entirely is the more durable fix: nothing gets destroyed, so
        there's nothing for that to ever have to catch.

        ``row.id`` moves from the placeholder's own id to the new entry's
        — see ``DayRow``'s own docstring: that's its identity from here
        on, the same as a fresh ``day_rows()`` call would already give it,
        and ``_toggle_expanded``/``_refresh_modal``/``selected_timer_
        target`` all turn an id back into a row by asking that same fresh
        ``day_rows()`` for one. Left at the old id, every one of those
        would quietly stop finding this row at all the moment it's
        promoted — expanding its card to do anything with it (including
        deleting the very entry just logged) would silently do nothing.
        Everything else that was keyed by the old id — this row's own
        chip/picker/arrow widgets, an open modal, an armed delete, a timer
        selection, a drag in flight — moves to the new one right here too,
        so none of those go dark the same way.
        """
        old_id = row.id
        row.entry = entry
        row.id = entry.id
        if row.id == old_id:
            return
        for widgets in (
            self._minus_chips, self._actual_pickers, self._expand_arrows, self._note_boxes
        ):
            widget = widgets.pop(old_id, None)
            if widget is not None:
                widgets[row.id] = widget
        self._row_widgets = [
            (row.id if rid == old_id else rid, widget) for rid, widget in self._row_widgets
        ]
        if self._modal_row_id == old_id:
            self._modal_row_id = row.id
        if self._selected_row_id == old_id:
            self._selected_row_id = row.id
        for task_id, expanded_id in list(self._expanded_row.items()):
            if expanded_id == old_id:
                self._expanded_row[task_id] = row.id
        if old_id in self._confirm_delete:
            self._confirm_delete.discard(old_id)
            self._confirm_delete.add(row.id)
        if self._drag_id == old_id:
            self._drag_id = row.id

    def _sync_decrease_chip(self, row_id: str, minutes: int) -> None:
        """Keeps a row's minus chip matching its entry's current minutes.

        Needed anywhere minutes changes without a full rebuild (see
        ``_begin_light_refresh``) — the chip was built enabled or not based
        on whatever the figure was at the time, and nothing else about a
        light refresh touches it. Without this, picking a new Actual Hours
        value on the duration picker — rather than this chip's own click —
        could cross zero either way and leave the chip showing the wrong
        side of it until the next full rebuild happened to come along.
        """
        chip = self._minus_chips.get(row_id)
        if chip is None or not chip.winfo_exists():
            return
        can_decrease = minutes > 0
        chip.set_enabled(can_decrease, theme.MUTED if can_decrease else theme.IDLE)

    def _commit_row_note(self, row: DayRow, widget: tk.Entry) -> None:
        text = widget.get()
        if text == row.note:
            return
        if row.entry is not None:
            self._begin_light_refresh()
            self.store.update_entry(row.entry.id, note=text)
        elif row.recurring_note_id:
            # Promotes straight to today's entry, at the note's usual
            # duration — set_plan_note would create a Plan instead, and a
            # pinned Plan suppresses every recurring note's row for the day
            # (see Store.day_rows), swallowing whichever others are left.
            # Its Planned Hours carries the same figure forward rather than
            # resetting to "Unset" (see ``Store.add_entry``). Promoted in
            # place (``_promote_row``), same as every other field commit
            # here, rather than left to the full rebuild.
            self._begin_light_refresh()
            entry = self.store.add_entry(
                row.context.task.id, self.date, row.plan_minutes or DEFAULT_PLAN_MINUTES,
                text, row.recurring_note_id, row.plan_internal_note, row.plan_minutes,
            )
            if entry is None:
                return
            self._promote_row(row, entry)
            # The note field's already showing what was just typed; it's
            # Actual Hours (defaulted above, not left at the placeholder's
            # own "nothing logged" 0) that needs patching to match.
            picker = self._actual_pickers.get(row.id)
            if picker is not None and picker.winfo_exists():
                picker.config(text=self._format_actual(entry.minutes))
            self._sync_decrease_chip(row.id, entry.minutes)
        else:
            self.store.set_plan_note(row.context.task.id, self.date, text)

    def _commit_row_internal_note(self, row: DayRow, text: str) -> None:
        if text == row.internal_note:
            return
        if row.entry is not None:
            self._begin_light_refresh()
            self.store.update_entry(row.entry.id, internal_note=text)
        elif row.recurring_note_id:
            # Mirrors ``_commit_row_note``'s own promotion above — this
            # field only shows at all once there's a recurring note or a
            # real entry to carry it (see ``_day_row``), so the bare
            # ``Plan``-only branch that method also handles never applies.
            self._begin_light_refresh()
            entry = self.store.add_entry(
                row.context.task.id, self.date, row.plan_minutes or DEFAULT_PLAN_MINUTES,
                row.plan_note, row.recurring_note_id, text, row.plan_minutes,
            )
            if entry is None:
                return
            self._promote_row(row, entry)
            picker = self._actual_pickers.get(row.id)
            if picker is not None and picker.winfo_exists():
                picker.config(text=self._format_actual(entry.minutes))
            self._sync_decrease_chip(row.id, entry.minutes)

    def _commit_row_minutes(self, row: DayRow, minutes: int) -> None:
        if row.entry is not None:
            self._begin_light_refresh()
            self.store.update_entry(row.entry.id, minutes=minutes)
            self._sync_decrease_chip(row.id, minutes)
        elif minutes:
            # Choosing a duration on a placeholder is what logs the first
            # entry — its own Planned Hours carries over from whatever this
            # placeholder was already showing (see ``Store.add_entry``),
            # rather than resetting to "Unset" the moment Actual Hours gets
            # a figure for the first time. Promoted in place
            # (``_promote_row``) rather than left to the full rebuild — the
            # picker's own label already shows ``minutes``, the value just
            # picked, so only the minus chip needs patching to match.
            # ``clear_plan`` only when this was the task's own bare
            # ``Plan`` placeholder (no recurring note behind it) — see
            # ``Store.add_entry`` for why a Plan set on purpose as its own
            # goal is never this call's to clear.
            self._begin_light_refresh()
            entry = self.store.add_entry(
                row.context.task.id, self.date, minutes, row.plan_note, row.recurring_note_id,
                row.plan_internal_note, row.plan_minutes,
                clear_plan=not row.recurring_note_id,
            )
            if entry is None:
                return
            self._promote_row(row, entry)
            self._sync_decrease_chip(row.id, entry.minutes)

    def _commit_row_plan_minutes(self, row: DayRow, minutes: int) -> None:
        if row.entry is not None:
            self._begin_light_refresh()
            self.store.update_entry(row.entry.id, plan_minutes=minutes)
        elif row.recurring_note_id:
            # A recurring note's still-unlogged placeholder: its Planned
            # Hours is the note's own "usual" minutes (see
            # ``Store.day_rows``, ``RecurringNote.minutes``), not a
            # day-level ``Plan`` — setting one of those instead would (per
            # ``Store.day_rows``) suppress every recurring note due that
            # day, including this one's own future occurrences. Changing
            # it here changes what every future occurrence plans, not
            # just today's.
            self.store.update_recurring_note(row.recurring_note_id, minutes=minutes)
        else:
            # No entry yet: still the task's one day-level ``Plan`` (see
            # ``_day_row``'s own comment on this), which doesn't move, add
            # or remove a row either — but it can turn a bare Plan into a
            # real one for the first time, which is enough of a structural
            # edge case (``Store.day_rows``, recurring notes it might now
            # suppress) to leave on the ordinary full-rebuild path rather
            # than claim it's always as safe as the entry branch above.
            self.store.set_plan(row.context.task.id, self.date, minutes)

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
            theme.set_border(widget, theme.ACCENT, 2)

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

    # No ``tick()`` any more — the Day tab's own Actual Hours slot shows
    # this row's last-committed total, frozen, while a timer runs on it
    # (see ``_day_row``'s own comment on that), rather than ticking in
    # step with ``Overlay._tick``'s own per-second refresh the way it used
    # to. The live, ticking comparison lives on the timer bar itself now
    # instead (``Overlay._tick``'s ``timer_total``), so there's nothing
    # left here for that per-second call to do.
