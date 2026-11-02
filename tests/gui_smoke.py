"""Builds the real window, visits every tab, then closes.

Not part of the unittest suite because it needs a desktop session.
Run with: python tests/gui_smoke.py
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

workspace = tempfile.mkdtemp(prefix="tslog-smoke-")
os.environ["TIMESHEET_HOME"] = workspace

from timesheet import launcher, paths, singleton  # noqa: E402
from timesheet.recurrence import describe  # noqa: E402
from timesheet.ui import export as export_module  # noqa: E402
from timesheet.ui import icons  # noqa: E402  (must follow the env var)
from timesheet.ui import theme  # noqa: E402  (must follow the env var)
from timesheet.ui.overlay import FULL_SIZE, MIN_SIZE, TAB_LABELS, TABS, Overlay  # noqa: E402


def find_menu_entry(widget: tk.Misc, label: str):
    """Depth-first search for a dropdown entry, as ``(menu, index)``."""
    if isinstance(widget, tk.Menubutton):
        menu = widget.nametowidget(widget.cget("menu"))
        for index in range(menu.index("end") + 1):
            if menu.type(index) == "command" and menu.entrycget(index, "label") == label:
                return menu, index
    for child in widget.winfo_children():
        found = find_menu_entry(child, label)
        if found:
            return found
    return None


def find_widgets(widget: tk.Misc, kind: type) -> list:
    """Every widget of ``kind`` under ``widget``, in the order they were built."""
    found = [widget] if isinstance(widget, kind) else []
    for child in widget.winfo_children():
        found.extend(find_widgets(child, kind))
    return found


def find_add_panel(body: tk.Misc):
    """The open "add task" panel, which is the last frame holding a dropdown."""
    for child in reversed(body.winfo_children()):
        if find_widgets(child, tk.OptionMenu):
            return child
    raise AssertionError("the add-task panel is not open")


def find_buttons(widget: tk.Misc) -> list[tk.Button]:
    """Every button under ``widget``, in the order they were built."""
    found = [widget] if isinstance(widget, tk.Button) else []
    for child in widget.winfo_children():
        found.extend(find_buttons(child))
    return found


def main() -> int:
    overlay = Overlay()
    store = overlay.store

    client = store.add_client("Acme Ltd")
    project = store.add_project(client.id, "Platform Migration", "K-10234", True)
    task = store.add_task(project.id, "Cutover rehearsal", True)
    from timesheet.dates import add_days, start_of_week, today

    store.set_plan(task.id, today(), 120)
    store.add_entry(task.id, today(), 95, "Ran the staging cutover with their DBA")

    assert TAB_LABELS["Today"] == "Day", "the Today tab should read as Day"
    for tab in TABS:
        overlay.show_tab(tab)
        overlay.root.update()
        print(f"  {TAB_LABELS[tab]} ({tab}) rendered")

    # All seven tab labels have to actually fit at the window's own declared
    # floor — ``pack(side="left")`` doesn't wrap, so a bar wider than the
    # window silently drops the last tab or two off the right edge rather
    # than erroring. Caught here rather than only by eye, the way a font-size
    # change once slipped past unnoticed on this exact row.
    bar = overlay.tab_buttons["Today"].master.master
    overlay.root.geometry(f"{MIN_SIZE[0]}x{MIN_SIZE[1]}")
    overlay.root.update()
    assert bar.winfo_reqwidth() <= MIN_SIZE[0], (
        f"the tab bar needs {bar.winfo_reqwidth()}px but MIN_SIZE is only {MIN_SIZE[0]}px wide"
    )
    overlay.root.geometry(f"{FULL_SIZE[0]}x{FULL_SIZE[1]}")
    overlay.root.update()
    print(f"  all {len(TABS)} tabs fit the {MIN_SIZE[0]}px minimum width (bar needs {bar.winfo_reqwidth()}px)")

    overlay._expanded = {task.id}
    overlay.show_tab("Today")
    overlay.root.update()
    print("  Today (task expanded) rendered")

    overlay.collapse(True)
    overlay.root.update()
    # Windows 11 rounds the badge off; where it does, the border is DWM's,
    # because Tk's is a rectangle and would be left hanging at the arcs.
    print(f"  badge corners rounded by Windows: {overlay._rounded}")
    assert overlay.icon.cget("highlightthickness") == (0 if overlay._rounded else 1), (
        "both Tk and DWM are drawing the badge border"
    )
    # Collapsed, the lamp is the only sign a timer is running: the badge is a
    # full-colour illustration and can't be tinted the way the chrome is.
    assert not overlay.icon_lamp.winfo_ismapped(), "the lamp is lit with no timer running"
    store.start_timer(task.id)
    overlay.root.update()
    assert overlay.icon_lamp.winfo_ismapped(), "a timer is running but the lamp is dark"
    store.stop_timer()
    overlay.root.update()
    assert not overlay.icon_lamp.winfo_ismapped(), "the lamp stayed lit after stopping"
    overlay.collapse(False)
    overlay.root.update()
    print("  collapse / expand OK, running lamp tracks the timer")

    overlay.toggle_always_on_top()
    overlay.toggle_always_on_top()
    from timesheet.ui.overlay import SIZE_MODES

    for _ in SIZE_MODES:
        overlay.cycle_size()
        overlay.root.update()
    assert overlay._size_mode == "small", "the size cycle didn't wrap back to small"
    print("  pin toggle and the size cycle (small -> quarter -> half -> small) OK")

    # No close button in the titlebar's chrome cluster — quitting for good is
    # the tray menu's job alone, never a click away in the window itself.
    assert not hasattr(overlay, "close_btn"), "a close button still exists in the titlebar"
    chrome_buttons = [
        b for b in find_buttons(overlay.titlebar)
        if b not in (overlay.undo_btn, overlay.redo_btn)
    ]
    left_to_right = sorted(chrome_buttons, key=lambda b: b.winfo_x())
    assert left_to_right == [overlay.pin_btn, overlay.size_btn, overlay.collapse_btn], (
        "the titlebar's chrome cluster should read anchor, resize, minimize, left to right"
    )
    print("  titlebar: no close button, just anchor / resize / minimize, in that order")

    # Undo/Redo: the titlebar buttons track Store's history, and invoking one
    # does exactly what its keyboard shortcut does.
    undo_task = store.add_task(project.id, "Undo check task", True)
    store.update_task(undo_task.id, title="Renamed via edit")
    overlay.root.update()
    assert overlay.undo_btn.cget("state") == "normal", "an edit just happened; Undo should be live"
    overlay.undo_btn.invoke()
    overlay.root.update()
    assert store.db.task(undo_task.id).title == "Undo check task", "Undo didn't revert the rename"
    assert overlay.redo_btn.cget("state") == "normal", "Undo just ran; Redo should be live"
    overlay.redo_btn.invoke()
    overlay.root.update()
    assert store.db.task(undo_task.id).title == "Renamed via edit", "Redo didn't reapply the rename"

    overlay.root.focus_force()
    overlay.root.event_generate("<Control-z>")
    overlay.root.update()
    assert store.db.task(undo_task.id).title == "Undo check task", "Ctrl+Z didn't undo"
    overlay.root.event_generate("<Control-y>")
    overlay.root.update()
    assert store.db.task(undo_task.id).title == "Renamed via edit", "Ctrl+Y didn't redo"
    store.db.tasks = [t for t in store.db.tasks if t.id != undo_task.id]
    print("  Undo/Redo: titlebar buttons and Ctrl+Z/Ctrl+Y both track Store's history")

    screens = overlay.screens()
    print(f"  {len(screens)} screen(s): " + ", ".join(s.label(i) for i, s in enumerate(screens, 1)))
    for corner in ("top-left", "top-right", "bottom-left", "bottom-right"):
        overlay.set_snap(corner=corner)
        overlay.root.update()
        print(f"    {corner:<13} -> +{overlay.root.winfo_x()}+{overlay.root.winfo_y()}")

    overlay.collapse(True)
    overlay.root.update()
    print(
        f"  collapsed while snapped -> {overlay.root.winfo_width()}x"
        f"{overlay.root.winfo_height()}+{overlay.root.winfo_x()}+{overlay.root.winfo_y()}"
    )
    overlay.collapse(False)
    overlay.set_snap(corner="")
    overlay.root.update()

    def drag_by(dx: int, dy: int) -> tuple[int, int]:
        """Fakes a header press then drag, returning where the window ended up."""
        x, y = overlay.root.winfo_x(), overlay.root.winfo_y()
        press = tk.Event()
        press.x_root, press.y_root = x, y
        overlay._drag_start(press)

        motion = tk.Event()
        motion.x_root, motion.y_root = x + dx, y + dy
        overlay._drag_move(motion)

        overlay.root.update()
        return overlay.root.winfo_x(), overlay.root.winfo_y()

    # Free: the header drags. This also leaves a drag origin behind, which a
    # pinned window must not reuse.
    overlay.set_snap(corner="")
    overlay.root.update()
    start = (overlay.root.winfo_x(), overlay.root.winfo_y())
    moved = drag_by(40, 30)
    assert moved != start, "a free window should follow the drag"
    print(f"  drag while free  {start} -> {moved}")

    overlay.set_snap(corner="top-left")
    overlay.root.update()
    pinned = (overlay.root.winfo_x(), overlay.root.winfo_y())
    after = drag_by(120, 90)
    assert after == pinned, f"a pinned window moved: {pinned} -> {after}"
    print(f"  drag while pinned {pinned} -> {after} (held)")

    # Creating a brand new task is the Setup tab's job now — the Day tab's
    # add-work panel only ever picks among existing ones. Ctrl+Enter still
    # has to work from any field in that panel, not just Note's own Return —
    # including the Planned Hours dropdown, which isn't an Entry at all.
    for index, label in ((0, "the note field"), (1, "the Planned Hours dropdown")):
        extra_task = store.add_task(project.id, f"Ctrl+Enter target {index}", True)
        overlay.show_tab("Today")
        overlay._views["Today"]._open_add()
        overlay.root.update()
        overlay._views["Today"]._add_task_id = extra_task.id
        overlay._views["Today"].refresh()
        overlay.root.update()
        panel = find_add_panel(overlay._views["Today"].scroll.body)
        note_field = find_widgets(panel, tk.Entry)[0]
        note_field.insert(0, f"Typed via {label}")
        overlay.root.update()
        # index 0: the note field itself. index 1: the Planned Hours
        # dropdown — a plain Button opening its own scrollable popup (see
        # ``theme.duration_picker``), the panel's only one; the Task
        # chooser above it is an OptionMenu, a different widget class.
        target = note_field if index == 0 else find_widgets(panel, tk.Button)[0]
        target.focus_set()
        target.event_generate("<Control-Return>")
        for _ in range(3):
            overlay.root.update()
        on_today = {c.task.id for c in store.contexts_for_date(today())}
        # Not planned_minutes > 0: a freshly added task defaults to
        # "Unset" Planned Hours now, not a guessed duration — the note
        # typed into the panel is what actually lands it on today.
        assert extra_task.id in on_today, f"Ctrl+Enter from {label} did not add the task"
        print(f"  Ctrl+Enter from {label} added the task")

    # The add-work panel picks an existing task rather than creating one, and
    # never offers a Billable control — that's the task's own, set in Setup.
    # Both tasks from the loop above are already on today, so there has to be
    # a fresh one left for the panel to offer.
    store.add_task(project.id, "Untouched for the caption check", True)
    today_view = overlay._views["Today"]
    today_view._open_add()
    overlay.root.update()
    panel = find_add_panel(today_view.scroll.body)
    captions = [w.cget("text") for w in find_widgets(panel, tk.Label)]
    assert "Task" in captions, f"missing the Task caption: {captions}"
    assert "Note" in captions, f"missing the Note caption: {captions}"
    assert "Planned Hours" in captions, f"missing the Planned Hours caption: {captions}"
    assert not find_widgets(panel, tk.Checkbutton), "Billable shouldn't be editable from here"
    today_view._close_add()
    overlay.root.update()
    print("  Day tab: add-work panel picks an existing task, with no Billable control")

    # The card's children, in creation order, are now: ``stats``
    # (Actual/Planned Hours), ``nudges`` (+/-, stacked at the card's own
    # right edge), ``actions`` (drag handle, timer radio), ``top`` (note,
    # recurring indicator), ``meta`` (identity), ``toggle_row`` (the
    # centred expand/collapse arrow). Neither the delete chip nor the
    # internal note field lives on the card itself any more — both sit on
    # the row's expanded detail panel instead (see ``expanded_delete_chip``
    # and ``internal_note_box``).
    def nudge_chips_on(card: tk.Widget) -> list[tk.Canvas]:
        nudges = card.winfo_children()[1]
        canvases = [w for w in nudges.winfo_children() if isinstance(w, tk.Canvas)]
        assert len(canvases) == 2, f"expected plus/minus chips in nudges, found {len(canvases)}"
        return canvases

    def timer_radio_on(card: tk.Widget) -> tk.Canvas:
        top_strip = card.winfo_children()[2]
        canvases = [w for w in top_strip.winfo_children() if isinstance(w, tk.Canvas)]
        assert len(canvases) == 1, f"expected just the timer radio on the row's top strip, found {len(canvases)}"
        return canvases[0]

    def expand_arrow_on(card: tk.Widget) -> tk.Label:
        toggle_row = card.winfo_children()[5]
        labels = [w for w in toggle_row.winfo_children() if isinstance(w, tk.Label)]
        assert len(labels) == 1, f"expected just the expand/collapse arrow, found {len(labels)}"
        return labels[0]

    def internal_note_box(task_id: str, row_id: str) -> tk.Text:
        """The internal note field on ``row_id``'s expanded detail panel —
        opening it first if it isn't already (see ``TodayView._detail``'s
        ``_internal_note``), since the field no longer lives on the card
        face itself."""
        if not (task_id in today_view._expanded and today_view._expanded_row.get(task_id) == row_id):
            today_view._toggle_expanded(task_id, row_id)
            overlay.root.update()
        box = today_view._repeat.frame.master
        texts = [w for w in box.winfo_children() if isinstance(w, tk.Text)]
        assert len(texts) == 1, f"expected just the internal note field, found {len(texts)}"
        return texts[0]

    def recurring_icon_on(card: tk.Widget) -> tk.Label | None:
        """The "this row repeats" indicator on ``top`` (``card``'s fourth
        child), if the row has one — see ``_day_row``'s ``is_recurring``."""
        top_strip = card.winfo_children()[3]
        labels = [
            w for w in top_strip.winfo_children()
            if isinstance(w, tk.Label) and str(w.cget("image")) == str(icons.get("recurring"))
        ]
        assert len(labels) <= 1, f"expected at most one recurring indicator, found {len(labels)}"
        return labels[0] if labels else None

    def expanded_delete_chip() -> tk.Canvas:
        """The delete chip on the currently-expanded row's detail panel —
        top-right of its Repeat summary row, the first child of
        ``_repeat.frame`` (see ``TodayView._detail``)."""
        header_row = today_view._repeat.frame.winfo_children()[0]
        canvases = [w for w in header_row.winfo_children() if isinstance(w, tk.Canvas)]
        assert len(canvases) == 1, f"expected just the delete chip on the Repeat row, found {len(canvases)}"
        return canvases[0]

    def expanded_delete_buttons() -> dict[str, tk.Button]:
        """The "Delete"/"Cancel" (or "Skip"/"Cancel") pair that replaces the
        chip ``expanded_delete_chip`` finds, once armed by a first click."""
        header_row = today_view._repeat.frame.winfo_children()[0]
        wrappers = [w for w in header_row.winfo_children() if isinstance(w, tk.Frame)]
        assert len(wrappers) == 1, f"expected the armed Delete/Cancel wrapper, found {len(wrappers)}"
        return {b.cget("text"): b for b in find_buttons(wrappers[0])}

    # Row identity is one row per Entry — a second stint gets its own row,
    # not a merge. ``is_first_for_task`` remains a plain ordering fact about
    # ``day_rows``' own output; every row now carries its own timer selector
    # and its own Planned Hours, not just whichever one is first.
    rows_task = store.add_task(project.id, "Row identity check", True)
    store.set_plan(rows_task.id, today(), 60)
    store.add_entry(rows_task.id, today(), 20, "First stint")
    store.add_entry(rows_task.id, today(), 15, "Second stint")
    overlay.show_tab("Today")
    overlay.root.update()
    own_rows = [r for r in store.day_rows(today()) if r.context.task.id == rows_task.id]
    assert len(own_rows) == 2, f"expected one row per entry, got {len(own_rows)}"
    assert own_rows[0].is_first_for_task and not own_rows[1].is_first_for_task, (
        "day_rows should still only mark one row per task as first"
    )
    print("  Day tab: one row per logged entry")

    # The whole point: each entry's own Planned Hours is independent of
    # every other entry the task logged the same day, not one shared
    # figure read off a day-level Plan any more.
    store.update_entry(own_rows[0].entry.id, plan_minutes=45)
    refreshed = {
        r.id: r for r in store.day_rows(today()) if r.context.task.id == rows_task.id
    }
    assert refreshed[own_rows[0].id].plan_minutes == 45, "an entry's own Planned Hours didn't stick"
    assert refreshed[own_rows[1].id].plan_minutes == 0, (
        "a sibling entry's Planned Hours should stay independent"
    )
    print("  Day tab: each entry's own Planned Hours is independent of its siblings")

    # The detail panel (recurrence, archive, delete) belongs right under
    # whichever row was actually clicked to open it — the first of the two
    # here — not always the task's last row, and not after "+ log another
    # entry" either, wherever that lands relative to the row picked.
    today_view._toggle_expanded(rows_task.id, own_rows[0].id)
    overlay.root.update()
    row_widgets = dict(today_view._row_widgets)
    first_y = row_widgets[own_rows[0].id].winfo_rooty()
    second_y = row_widgets[own_rows[1].id].winfo_rooty()
    detail_y = today_view._repeat.frame.winfo_rooty()
    assert first_y < detail_y < second_y, (
        f"detail panel should sit between the clicked row and the next one: "
        f"first={first_y} detail={detail_y} second={second_y}"
    )
    # Planned Hours no longer follows expand/collapse the way the detail
    # panel does: every row owns its entry's own figure now, so it's an
    # editable dropdown everywhere, expanded or not — the same as Actual
    # Hours always has been. The stats panel (card's first child, see
    # ``_day_row``) should have two duration pickers (Actual + Planned
    # Hours) on both rows, anchor or not — each a plain Button opening its
    # own scrollable popup (see ``theme.duration_picker``), the only
    # Buttons ``stats`` has.
    anchor_stats = row_widgets[own_rows[0].id].winfo_children()[0]
    other_stats = row_widgets[own_rows[1].id].winfo_children()[0]
    anchor_pickers = find_widgets(anchor_stats, tk.Button)
    other_pickers = find_widgets(other_stats, tk.Button)
    assert len(anchor_pickers) == 2, (
        f"every row should have Actual + Planned Hours both editable, found {len(anchor_pickers)}"
    )
    assert len(other_pickers) == 2, (
        f"every row should have Actual + Planned Hours both editable, found {len(other_pickers)}"
    )
    print("  Day tab: Planned Hours is editable on every row, not just the expanded one")

    # Opening one pops up a scrollable list rather than a native OptionMenu
    # trying to show every option at once — 49 fifteen-minute steps up to
    # 12h, plus this particular entry's own odd 20m carried over from data
    # that predates the picker (see ``theme.duration_picker``), would run
    # off the bottom of the screen with no way back to whatever fell past
    # the edge. Re-picking the row's own already-current figure (rather
    # than a different one) leaves every later assertion about this entry's
    # exact minutes untouched.
    actual_picker = anchor_pickers[0]
    actual_picker.invoke()
    overlay.root.update()
    popups = [w for w in anchor_stats.winfo_children() if isinstance(w, tk.Toplevel)]
    assert len(popups) == 1, f"expected the duration picker's popup to open, found {len(popups)}"
    popup = popups[0]
    option_buttons = find_widgets(popup, tk.Button)
    assert len(option_buttons) == 50, (
        f"expected every 15-minute step up to 12h plus this entry's own 20m, found {len(option_buttons)}"
    )
    scrollbars = find_widgets(popup, tk.Scrollbar)
    assert scrollbars and scrollbars[0].winfo_ismapped(), (
        "a list this long should actually show its scrollbar, not just exist unused"
    )
    current_option = next(b for b in option_buttons if b.cget("text") == "20m")
    current_option.invoke()
    overlay.root.update()
    # Picking an option closes the popup before ``on_change`` ever runs (see
    # ``theme.duration_picker``), and this one commits straight back to the
    # store, rebuilding the whole row out from under ``anchor_stats`` —
    # checked against ``popup`` itself, held onto above, rather than
    # anything from that now-stale tree.
    assert not popup.winfo_exists(), "picking an option should close the popup"
    print("  Day tab: the Actual/Planned Hours dropdown opens a scrollable popup instead")

    today_view._toggle_expanded(rows_task.id, own_rows[0].id)  # close it again
    overlay.root.update()
    row_widgets = dict(today_view._row_widgets)
    collapsed_stats = row_widgets[own_rows[0].id].winfo_children()[0]
    assert len(find_widgets(collapsed_stats, tk.Button)) == 2, (
        "Planned Hours should stay editable once collapsed again, same as Actual Hours"
    )
    print("  Day tab: the expanded detail panel sits right under the row that opened it")

    # Every row carries its own circular timer selector now — no more
    # Play/Stop confined to one row per task. Selecting one arms it as the
    # timer bar's Start target; only one can be armed at a time, and
    # starting it fills that same row's own radio in, running.
    first_radio = timer_radio_on(row_widgets[own_rows[0].id])
    second_radio = timer_radio_on(row_widgets[own_rows[1].id])
    assert first_radio.cget("cursor") == "hand2" and second_radio.cget("cursor") == "hand2", (
        "every row's timer selector should be clickable while nothing is running"
    )
    assert not overlay.timer_start.winfo_ismapped(), "nothing should be armed yet"
    first_radio.event_generate("<Button-1>")
    overlay.root.update()
    assert today_view.selected_timer_target().id == own_rows[0].id, (
        "clicking a row's radio should arm that row"
    )
    assert overlay.timer_start.winfo_ismapped(), "arming a row should reveal the timer bar's Start"
    assert own_rows[0].context.display_title in overlay.timer_title.cget("text")
    overlay.start_selected_timer()
    overlay.root.update()
    assert overlay.timer_stop.winfo_ismapped(), "Start should hand off to the usual Stop control"
    assert overlay.store.db.active_timer.task_id == rows_task.id
    row_widgets = dict(today_view._row_widgets)
    running_radio = timer_radio_on(row_widgets[own_rows[0].id])
    oval = running_radio.find_all()[0]
    assert running_radio.itemcget(oval, "fill") == theme.BILLABLE, (
        "the running row's own radio should render filled, in the running colour"
    )
    overlay.store.stop_timer()
    overlay.root.update()
    print("  Day tab: a row's own radio arms and starts the timer bar's Start/Stop")

    # +/- are stacked one above the other, past Actual/Planned Hours, at
    # the card's own right edge.
    logged_row = next(
        r for r in store.day_rows(today())
        if r.context.task.id == rows_task.id and not r.is_placeholder
    )
    logged_card = dict(today_view._row_widgets)[logged_row.id]
    plus_chip, minus_chip = nudge_chips_on(logged_card)
    assert plus_chip.winfo_rooty() < minus_chip.winfo_rooty(), "+ should sit above -"
    assert plus_chip.winfo_rootx() == minus_chip.winfo_rootx(), "+ and - should line up vertically"
    stats_right_edge = logged_card.winfo_children()[0].winfo_rootx() + (
        logged_card.winfo_children()[0].winfo_width()
    )
    assert plus_chip.winfo_rootx() > stats_right_edge, (
        "the nudge stack should sit to the right of Actual/Planned Hours"
    )
    card_right_edge = logged_card.winfo_rootx() + logged_card.winfo_width()
    assert plus_chip.winfo_rootx() + plus_chip.winfo_width() <= card_right_edge, (
        "the nudge stack shouldn't spill past the card's own right edge"
    )
    print("  Day tab: +/- are stacked at the card's right edge, past Actual/Planned Hours")

    # The internal note lives on the row's own expanded detail panel now,
    # five rows tall — a real logged entry's own field, separate from
    # ``row.note`` and never read by ``bookings.collect`` (the only path
    # into an export).
    internal = internal_note_box(rows_task.id, logged_row.id)
    assert internal.cget("state") == "normal", "a logged entry's internal note should be editable"
    assert int(internal.cget("height")) == 5, "the internal note box should be about 5 rows tall"
    internal.delete("1.0", "end")
    internal.insert("1.0", "Private context")
    internal.event_generate("<FocusOut>")
    overlay.root.update()
    stored_entry = next(e for e in store.db.entries if e.id == logged_row.entry.id)
    assert stored_entry.internal_note == "Private context", "the internal note didn't persist"
    assert stored_entry.note == logged_row.note, (
        "editing the internal note shouldn't touch the customer note"
    )
    print("  Day tab: the internal note commits separately from the customer note")
    today_view._toggle_expanded(rows_task.id, logged_row.id)  # close it again
    overlay.root.update()

    # A bare Plan-only placeholder has no entry or recurring note to hang
    # one off, so the field stays visible but disabled — present, not
    # hidden, same idea as the inert delete chip further below.
    bare_task = store.add_task(project.id, "Internal note bare placeholder check", True)
    store.set_plan(bare_task.id, today(), 30)
    today_view.refresh()
    overlay.root.update()
    bare_row = next(r for r in store.day_rows(today()) if r.context.task.id == bare_task.id)
    assert internal_note_box(bare_task.id, bare_row.id).cget("state") == "disabled", (
        "a bare placeholder's internal note should be disabled, not editable"
    )
    store.db.plans = [p for p in store.db.plans if p.task_id != bare_task.id]
    store.db.tasks = [t for t in store.db.tasks if t.id != bare_task.id]
    today_view.refresh()
    overlay.root.update()
    # The row's own widget was rebuilt by the refreshes above — re-fetched
    # rather than reused, same as every other place in this file that
    # mutates the store then keeps asserting against the same logical row.
    logged_card = dict(today_view._row_widgets)[logged_row.id]
    print("  Day tab: a bare placeholder's internal note is present but disabled")

    # The centred arrow under every card expands or collapses it, same as
    # clicking the identity row above does, and its glyph says which way —
    # an icon (see ``icons.label``) rather than text now, so compared by the
    # image it's showing rather than a glyph character.
    arrow = expand_arrow_on(logged_card)
    assert str(arrow.cget("image")) == str(icons.get("row-expand")), (
        "a collapsed row's arrow should point down"
    )
    arrow.event_generate("<Button-1>")
    overlay.root.update()
    logged_card = dict(today_view._row_widgets)[logged_row.id]
    assert str(expand_arrow_on(logged_card).cget("image")) == str(icons.get("row-collapse")), (
        "an expanded row's arrow should point up"
    )
    print("  Day tab: the centred arrow expands and collapses a row, same as its identity row")

    # Deleting a logged entry now lives on its expanded detail panel, top
    # right, on the same line as the Repeat checkbox — a click arms its own
    # "Delete"/"Cancel" pair in place of the chip rather than deleting on the
    # spot or popping a modal over the whole window. Cancel puts the chip
    # back, leaving the entry alone; Delete removes exactly that one.
    chip = expanded_delete_chip()
    assert chip.cget("cursor") == "hand2", "a logged entry's delete chip should be clickable"

    before = len(store.db.entries)
    chip.event_generate("<Button-1>")
    overlay.root.update()
    assert len(store.db.entries) == before, "a click shouldn't delete immediately, only arm it"
    buttons = expanded_delete_buttons()
    assert set(buttons) == {"Delete", "Cancel"}, f"armed buttons missing one: {list(buttons)}"

    buttons["Cancel"].invoke()
    overlay.root.update()
    assert len(store.db.entries) == before, "Cancel shouldn't delete anything"
    chip = expanded_delete_chip()
    assert chip.cget("cursor") == "hand2", "Cancel should put the chip back, not leave it armed"

    chip.event_generate("<Button-1>")
    overlay.root.update()
    expanded_delete_buttons()["Delete"].invoke()
    overlay.root.update()
    assert len(store.db.entries) == before - 1, "Delete should remove exactly the one entry"
    assert logged_row.entry.id not in [e.id for e in store.db.entries], "the wrong entry was removed"
    print("  Day tab: the delete chip arms its own Delete/Cancel pair instead of a modal")

    today_view._toggle_expanded(rows_task.id, logged_row.id)  # close it again
    overlay.root.update()

    # A placeholder — nothing logged yet — gets the same chip in the same
    # spot once expanded, but inert: there's nothing yet for it to act on.
    placeholder_task = store.add_task(project.id, "Delete chip placeholder check", True)
    store.set_plan(placeholder_task.id, today(), 30)
    today_view.refresh()
    overlay.root.update()
    placeholder_row = next(
        r for r in store.day_rows(today()) if r.context.task.id == placeholder_task.id
    )
    assert placeholder_row.is_placeholder, "a plan with no entry should render as a placeholder"
    today_view._toggle_expanded(placeholder_task.id, placeholder_row.id)
    overlay.root.update()
    placeholder_chip = expanded_delete_chip()
    assert placeholder_chip.cget("cursor") == "arrow", "a placeholder's delete chip should be inert"

    before = len(store.db.entries)
    placeholder_chip.event_generate("<Button-1>")
    overlay.root.update()
    assert len(store.db.entries) == before, "an inert chip shouldn't do anything when clicked"
    # Nothing changed, so nothing rebuilt — the very same chip, not an
    # armed Delete/Cancel pair in its place.
    assert expanded_delete_chip() is placeholder_chip, "an inert chip shouldn't arm Delete/Cancel"
    today_view._toggle_expanded(placeholder_task.id, placeholder_row.id)  # close it again
    store.db.plans = [p for p in store.db.plans if p.task_id != placeholder_task.id]
    store.db.tasks = [t for t in store.db.tasks if t.id != placeholder_task.id]
    print("  Day tab: a placeholder row's delete chip is present but inert")

    # The view-mode selector actually changes what's grouped, live.
    today_view = overlay._views["Today"]
    today_view._on_grouping_change("By Project")
    overlay.root.update()
    assert store.db.settings.day_grouping == "project", "the selector didn't reach Settings"
    headings = [h for h, _rows in store.grouped_day_rows(today()) if h]
    assert any("Platform Migration" in h for h in headings), f"no project heading found: {headings}"
    print("  Day tab: view-mode selector switches grouping live")

    # "+ log another entry" belongs under every task's last row, even one
    # that hasn't had anything logged against it yet — a plan-only
    # placeholder needs the option to gain a second entry before its first
    # is ever touched, same as a task with real history already.
    another_entry_task = store.add_task(project.id, "New project entry check", True)
    store.set_plan(another_entry_task.id, today(), 30)
    today_view.refresh()
    overlay.root.update()
    link_texts = [w.cget("text") for w in find_widgets(today_view.scroll.body, tk.Button)]
    assert "+ log another entry" in link_texts, (
        f"a still-unlogged task should still offer to log an entry: {link_texts}"
    )
    print("  Day tab: '+ log another entry' shows under a placeholder row too")

    # Clicking it starts a new entry at 0m — left for its own Actual Hours
    # picker to fill in, rather than guessing a duration that would just
    # need overwriting. Found as the card's own next sibling (several tasks
    # can show this same link, so matching on caption alone could grab the
    # wrong one).
    another_row = next(
        r for r in store.day_rows(today()) if r.context.task.id == another_entry_task.id
    )
    card = dict(today_view._row_widgets)[another_row.id]
    siblings = card.master.winfo_children()
    add_link = siblings[siblings.index(card) + 1]
    assert isinstance(add_link, tk.Button) and add_link.cget("text") == "+ log another entry", (
        f"expected the add-another link right after the card, found {add_link}"
    )
    add_link.invoke()
    overlay.root.update()
    new_entries = store.entries_for(another_entry_task.id, today())
    assert len(new_entries) == 1 and new_entries[0].minutes == 0, (
        f"a freshly logged entry should start at 0m, got {new_entries}"
    )
    print("  Day tab: '+ log another entry' starts the new entry at 0m")

    # Manual mode's rows are one flat, freely-reordered list with no heading
    # to say which task a row belongs to — showing the link there would be
    # guessing which task it's actually for.
    today_view._on_grouping_change("Manual")
    overlay.root.update()
    link_texts = [w.cget("text") for w in find_widgets(today_view.scroll.body, tk.Button)]
    assert "+ log another entry" not in link_texts, "Manual mode shouldn't offer it at all"
    print("  Day tab: Manual mode never shows '+ log another entry'")

    store.db.entries = [e for e in store.db.entries if e.task_id != another_entry_task.id]
    store.db.plans = [p for p in store.db.plans if p.task_id != another_entry_task.id]
    store.db.tasks = [t for t in store.db.tasks if t.id != another_entry_task.id]

    # Manual mode's own drag handle: picking one row up and releasing over
    # another reorders them, read off the row widgets actually on screen.
    # Three tasks so there's a real "somewhere in the middle" to drop into,
    # not just a two-row swap.
    for n in range(3):
        extra = store.add_task(project.id, f"Manual drag check {n}", True)
        store.set_plan(extra.id, today(), 30)
    today_view.refresh()
    overlay.root.update()
    widgets = today_view._row_widgets
    assert len(widgets) >= 3, "need at least three rows on screen to test dragging"
    first_id, first_widget = widgets[0]
    _second_id, second_widget = widgets[1]
    _third_id, third_widget = widgets[2]

    today_view._drag_start(first_id, tk.Event())
    overlay.root.update()
    assert today_view._card_widget(first_id).cget("highlightbackground") == theme.ACCENT, (
        "picking a row up should highlight it in the accent colour"
    )

    motion = tk.Event()
    motion.y_root = second_widget.winfo_rooty() + second_widget.winfo_height() // 2
    today_view._drag_motion(motion)
    overlay.root.update()
    indicator = today_view._drop_indicator
    assert indicator is not None and indicator.winfo_ismapped(), (
        "dragging over another row should show a drop indicator"
    )
    indicator_y = int(indicator.place_info()["y"])
    expected_y = second_widget.winfo_rooty() - second_widget.master.winfo_rooty() - 2
    assert abs(indicator_y - expected_y) <= 1, (
        f"the indicator should sit right at the row it'll land in front of: "
        f"{indicator_y} vs {expected_y}"
    )

    release = tk.Event()
    release.y_root = third_widget.winfo_rooty() + third_widget.winfo_height()
    today_view._drag_end(release)
    overlay.root.update()
    assert today_view._drop_indicator is None, "the indicator should be gone once the drag ends"
    new_order = [row_id for row_id, _w in today_view._row_widgets]
    assert new_order.index(first_id) > 0, f"the dragged row didn't move: {new_order}"
    moved_card = today_view._card_widget(first_id)
    assert moved_card.cget("highlightbackground") != theme.ACCENT, (
        "the highlight should clear once the row's been dropped"
    )
    print("  Day tab: dragging a row's handle reorders it, with a live drop indicator")

    # Dropping past every other row lands it last, not second-to-last — the
    # indicator promises "at the very end" here, so the actual order has to
    # follow through on that, not quietly leave it one short.
    order_before = [row_id for row_id, _w in today_view._row_widgets]
    dragged = order_before[0]
    today_view._drag_start(dragged, tk.Event())
    overlay.root.update()
    last_id, last_widget = today_view._row_widgets[-1]
    past_everything = tk.Event()
    past_everything.y_root = last_widget.winfo_rooty() + last_widget.winfo_height() + 40
    today_view._drag_end(past_everything)
    overlay.root.update()
    order_after = [row_id for row_id, _w in today_view._row_widgets]
    assert order_after[-1] == dragged, (
        f"dropping below every row should land it last: {order_after}"
    )
    print("  Day tab: dropping below every row lands it last, persisted for next time")

    drag_check_ids = {
        t.id for t in store.db.tasks if t.title.startswith("Manual drag check")
    }
    store.db.plans = [p for p in store.db.plans if p.task_id not in drag_check_ids]
    store.db.tasks = [t for t in store.db.tasks if t.id not in drag_check_ids]
    today_view.refresh()
    overlay.root.update()

    # Scoped to the checks above alone — cleared so it doesn't leak an extra
    # line into the Export tab's count further down.
    store.db.entries = [e for e in store.db.entries if e.task_id != rows_task.id]
    store.db.plans = [p for p in store.db.plans if p.task_id != rows_task.id]
    store.db.tasks = [t for t in store.db.tasks if t.id != rows_task.id]
    overlay.root.update()

    # Recurring notes: several standing commitments can share one task's
    # billing bucket, each on its own schedule, each getting its own row
    # until it's logged — daily rules, so this doesn't depend on which
    # weekday the suite happens to run on.
    from timesheet.models import Recurrence

    notes_task = store.add_task(project.id, "Recurring notes check", True)
    standup = store.add_recurring_note(
        notes_task.id, "Daily stand-up", 30, Recurrence(frequency="daily", minutes=30, start=today())
    )
    store.add_recurring_note(
        notes_task.id, "Daily admin", 15, Recurrence(frequency="daily", minutes=15, start=today())
    )
    today_view.refresh()
    overlay.root.update()
    own_rows = [r for r in store.day_rows(today()) if r.context.task.id == notes_task.id]
    assert len(own_rows) == 2, f"expected one placeholder per recurring note, got {len(own_rows)}"
    assert sorted(r.note for r in own_rows) == ["Daily admin", "Daily stand-up"], own_rows
    assert sum(r.is_first_for_task for r in own_rows) == 1, "day_rows should still mark one as first"

    # Logging one note's occurrence stops its placeholder without touching
    # its sibling's — nudging/typing on the Day tab both go through
    # Store.add_entry the same way this does.
    target = next(r for r in own_rows if r.recurring_note_id == standup.id)
    store.add_entry(
        notes_task.id, today(), target.plan_minutes, target.plan_note, target.recurring_note_id
    )
    own_rows = [r for r in store.day_rows(today()) if r.context.task.id == notes_task.id]
    assert len(own_rows) == 2, "the logged one and the still-pending one"
    logged = next(r for r in own_rows if not r.is_placeholder)
    pending = next(r for r in own_rows if r.is_placeholder)
    assert logged.note == "Daily stand-up", logged.note
    assert pending.note == "Daily admin", pending.note
    print("  Day tab: recurring notes get their own rows, each on its own schedule")

    # The recurring indicator sits next to the note field on both a still-
    # pending occurrence and one already logged — ``DayRow.recurring_note_id``
    # only ever covers the former, so ``_day_row`` has to check the entry's
    # own ``recurring_note_id`` too, for the latter (see ``is_recurring``).
    today_view.refresh()
    overlay.root.update()
    row_widgets = dict(today_view._row_widgets)
    assert recurring_icon_on(row_widgets[logged.id]) is not None, (
        "a logged entry tied to a recurring note should show the indicator"
    )
    assert recurring_icon_on(row_widgets[pending.id]) is not None, (
        "a still-pending recurring note's placeholder should show the indicator"
    )
    ordinary_task = store.add_task(project.id, "Recurring indicator control check", True)
    store.set_plan(ordinary_task.id, today(), 30)
    today_view.refresh()
    overlay.root.update()
    ordinary_row = next(r for r in store.day_rows(today()) if r.context.task.id == ordinary_task.id)
    ordinary_card = dict(today_view._row_widgets)[ordinary_row.id]
    assert recurring_icon_on(ordinary_card) is None, (
        "an ordinary row shouldn't show the recurring indicator"
    )
    store.db.plans = [p for p in store.db.plans if p.task_id != ordinary_task.id]
    store.db.tasks = [t for t in store.db.tasks if t.id != ordinary_task.id]
    today_view.refresh()
    overlay.root.update()
    print("  Day tab: the recurring indicator shows only next to a row tied to a recurring note")

    # Unlike an ordinary placeholder, a recurring note's still-unlogged one
    # has something real to dismiss (today's occurrence), so its delete chip
    # is live rather than inert, and arms "Skip" instead of "Delete".
    today_view._toggle_expanded(notes_task.id, pending.id)
    overlay.root.update()
    pending_chip = expanded_delete_chip()
    assert pending_chip.cget("cursor") == "hand2", "a recurring note's placeholder chip should be live"
    pending_chip.event_generate("<Button-1>")
    overlay.root.update()
    buttons = expanded_delete_buttons()
    assert "Skip" in buttons, f"expected a Skip button, found: {list(buttons)}"
    buttons["Skip"].invoke()
    overlay.root.update()
    own_rows = [r for r in store.day_rows(today()) if r.context.task.id == notes_task.id]
    assert sorted(r.note for r in own_rows) == ["Daily stand-up"], (
        f"the skipped note shouldn't come back today: {own_rows}"
    )
    # ... but it's only skipped for today — tomorrow it's due again.
    tomorrow = add_days(today(), 1)
    tomorrow_notes = sorted(
        r.note for r in store.day_rows(tomorrow) if r.context.task.id == notes_task.id
    )
    assert "Daily admin" in tomorrow_notes, f"a skip should be for one day only: {tomorrow_notes}"
    print("  Day tab: dismissing a recurring note's placeholder skips just today, not the series")

    # Recurring notes are managed on their own tab now, not the Setup tab —
    # a task's row there no longer shows them at all.
    overlay.show_tab("Projects")
    overlay.root.update()
    projects_view = overlay._views["Projects"]
    projects_view.refresh()
    overlay.root.update()
    setup_texts = {w.get() for w in find_widgets(projects_view.scroll.body, tk.Entry)}
    for absent in ("Daily stand-up", "Daily admin"):
        assert absent not in setup_texts, f"{absent!r} should not be listed in Setup anymore"
    print("  Setup tab: no longer lists a task's recurring notes")

    # The Recurring tab lists every note across every task, live, grouped by
    # the same client/project/task heading the Day tab uses.
    overlay.show_tab("Recurring")
    overlay.root.update()
    recurring_view = overlay._views["Recurring"]
    store.add_recurring_note(notes_task.id, "Monthly review", 45)
    recurring_view.refresh()
    overlay.root.update()
    recurring_texts = {w.get() for w in find_widgets(recurring_view.scroll.body, tk.Entry)}
    for expected in ("Daily stand-up", "Daily admin", "Monthly review"):
        assert expected in recurring_texts, f"recurring note {expected!r} not listed: {recurring_texts}"
    print("  Recurring tab: every task's recurring notes are listed, live")

    # Creating one through the tab's own add panel sets a schedule
    # immediately — the checkbox starts ticked, not a second step after
    # typing a title.
    before = len(store.db.recurring_notes)
    recurring_view._open_add()
    overlay.root.update()
    recurring_view._add_task_id = notes_task.id
    recurring_view.refresh()
    overlay.root.update()
    panel = find_add_panel(recurring_view.scroll.body)
    # Note is the only free-text field left in this panel now — "Usually"
    # and the embedded editor's own Plan/dates are all pickers, not Entries.
    note_field = find_widgets(panel, tk.Entry)[0]
    note_field.insert(0, "Weekly retro")
    overlay.root.update()
    confirm_chip = find_widgets(panel, tk.Canvas)[0]
    confirm_chip.event_generate("<Button-1>")
    overlay.root.update()
    assert len(store.db.recurring_notes) == before + 1, "the add panel didn't create a note"
    created = next(n for n in store.db.recurring_notes if n.note == "Weekly retro")
    assert created.recurrence is not None, "a new note's schedule should be live from creation, not off"
    print("  Recurring tab: a new series gets its schedule set immediately, not as a second step")

    # Archive hides a note from future planning but keeps it listed here;
    # delete is two steps, same recipe as Setup's client/project/task rows.
    row = next(
        r for r in find_widgets(recurring_view.scroll.body, tk.Frame)
        if find_widgets(r, tk.Entry) and find_widgets(r, tk.Entry)[0].get() == "Weekly retro"
    )
    archive_btn = next(b for b in find_buttons(row) if b.cget("text") == "Archive")
    archive_btn.invoke()
    overlay.root.update()
    assert next(n for n in store.db.recurring_notes if n.id == created.id).archived, (
        "Archive didn't mark the note archived"
    )
    assert created.id not in {
        n.id for n in store.all_recurring_notes()
    }, "an archived note shouldn't show up in the default (non-archived) listing"
    recurring_view._show_archived.set(True)
    recurring_view.refresh()
    overlay.root.update()
    print("  Recurring tab: Archive hides a note from active planning without deleting it")

    # Delete is two steps, same arm/confirm recipe as Setup's rows — the
    # chip itself becomes the question rather than a popup.
    row = next(
        r for r in find_widgets(recurring_view.scroll.body, tk.Frame)
        if find_widgets(r, tk.Entry) and find_widgets(r, tk.Entry)[0].get() == "Weekly retro"
    )
    trash_chip = find_widgets(row, tk.Canvas)[0]
    trash_chip.event_generate("<Button-1>")
    overlay.root.update()
    assert created.id in {n.id for n in store.db.recurring_notes}, "arming delete shouldn't remove it yet"
    row = next(
        r for r in find_widgets(recurring_view.scroll.body, tk.Frame)
        if find_widgets(r, tk.Entry) and find_widgets(r, tk.Entry)[0].get() == "Weekly retro"
    )
    confirm_delete_chip = find_widgets(row, tk.Canvas)[0]
    confirm_delete_chip.event_generate("<Button-1>")
    overlay.root.update()
    assert created.id not in {n.id for n in store.db.recurring_notes}, "confirming didn't delete it"
    print("  Recurring tab: delete arms on the first click, removes on the second")

    overlay.show_tab("Today")
    overlay.root.update()

    # Scoped to the checks above alone, same reason as rows_task.
    store.db.entries = [e for e in store.db.entries if e.task_id != notes_task.id]
    store.db.recurring_notes = [n for n in store.db.recurring_notes if n.task_id != notes_task.id]
    store.db.tasks = [t for t in store.db.tasks if t.id != notes_task.id]
    overlay.root.update()

    # No Archive or trash chip on the Day tab's expanded row — structural
    # task edits (archive, delete) are the Setup tab's job alone now, same
    # as billable and task creation; see ``Store.delete_task``'s own unit
    # tests (test_smoke.py) and the Setup tab's delete-chip check above for
    # that refusal logic and chip mechanism respectively. The one chip the
    # detail panel does have is this row's own delete, top-right beside
    # Repeat — not a task-structural control at all.
    assert not hasattr(today_view, "_delete_button"), (
        "the Day tab shouldn't offer task deletion any more"
    )
    expanded_row_id = next(r.id for r in store.day_rows(today()) if r.context.task.id == task.id)
    today_view._toggle_expanded(task.id, expanded_row_id)
    overlay.root.update()
    detail_box = today_view._repeat.frame.master
    detail_chips = find_widgets(detail_box, tk.Canvas)
    assert len(detail_chips) == 1, (
        f"the expanded row's detail panel should have only its own delete chip, found {len(detail_chips)}"
    )
    button_captions = [w.cget("text") for w in find_widgets(detail_box, tk.Button)]
    assert "Archive" not in button_captions, (
        f"the expanded row's detail panel shouldn't have an Archive button, found {button_captions}"
    )
    today_view._toggle_expanded(task.id, expanded_row_id)  # close it again
    overlay.root.update()
    print("  Day tab: no Archive or task-delete control on an expanded row, just this row's own")

    # The tray icon is a real one: the shell has accepted it by this point
    # or the app is running without a way back from a hidden window.
    tray = overlay.tray
    assert tray is not None and not tray.failed, f"no tray icon: {tray and tray.failed}"
    labels = [label for label, _ in tray._menu if label]
    assert "Quit" in labels, f"the tray menu is missing Quit: {labels}"
    assert any(l.startswith("Start") for l in labels), f"nothing starts a timer: {labels}"

    store.start_timer(task.id)
    overlay.root.update()
    assert any("Cutover rehearsal" in l for l in [x for x, _ in overlay.tray._menu if x]), (
        "the running task isn't offered for stopping"
    )
    overlay._tick()
    assert "Cutover rehearsal" in tray._tooltip, f"the tooltip went stale: {tray._tooltip!r}"
    store.stop_timer()
    overlay.root.update()
    print(f"  tray icon live, menu {labels}, tooltip tracks the timer")

    # With somewhere to restore from, closing hides rather than quits.
    overlay.close()
    overlay.root.update()
    assert overlay.root.state() == "withdrawn", "closing with a tray icon should hide"
    overlay.show_window()
    overlay.root.update()
    assert overlay.root.state() != "withdrawn", "the tray could not bring it back"
    print("  close hides to tray, tray restores it")

    # Writing a .lnk goes through PowerShell, so it either works end to end
    # or not at all. Skipped if one is already there, rather than replacing
    # a shortcut the machine's owner put on their own desktop.
    for location in ("desktop", "startup"):
        link = launcher.shortcut_path(location)
        assert link is not None, f"Windows did not report the {location} folder"
        if link.exists():
            print(f"  {location} shortcut already present, left alone")
            continue
        problem = launcher.create(location)
        assert not problem, f"could not write the {location} shortcut: {problem}"
        assert link.exists(), f"{link} was reported written but isn't there"
        print(f"  {location} shortcut written to {link.parent}")
        assert not launcher.remove(location) and not link.exists(), "removal left it behind"

    # Repeat is entry-specific: switching it on for whichever row is expanded
    # promotes that one row — here, the one the timer just logged — into its
    # own recurring note (see ``Store.promote_to_recurring_note``), never a
    # rule on the whole task. A note is never written out as a plan either,
    # so the proof it took is that the task shows up on a future day it
    # matches and stays away from one it doesn't.
    overlay.show_tab("Today")
    overlay._views["Today"]._expanded = {task.id}
    overlay._views["Today"].refresh()
    overlay.root.update()
    anchor_row = [r for r in store.day_rows(today()) if r.context.task.id == task.id][-1]
    assert anchor_row.recurring_note_id == "", "the row started out not tied to any note"
    assert overlay._views["Today"]._repeat.rule is None, "the row started out not repeating"
    # "Does not repeat" next to an unticked checkbox only says the checkbox's
    # own state back — nothing worth a label while there's no rule to show.
    labels = [w.cget("text") for w in find_widgets(overlay._views["Today"]._repeat.frame, tk.Label)]
    assert "Does not repeat" not in labels, f"the checkbox needs no caption while it's off: {labels}"
    print("  repeat editor: no 'Does not repeat' caption while the checkbox is off")

    def edit(method: str, *args) -> None:
        """Act on whichever editor is on screen now.

        Each save redraws the panel and builds a fresh editor, so holding on
        to one across edits would be driving a widget the user can't see.
        """
        editor = overlay._views["Today"]._repeat
        getattr(editor, method)(*args)
        overlay.root.update()

    # Anchored well clear of "today" in both directions — not just today()
    # itself (the task already has an unrelated plan pinned there, from near
    # the top of this script) but the whole chain below, since wall-clock
    # time marching on would otherwise eventually walk a hardcoded date into
    # colliding with whatever "today" has since become.
    anchor = start_of_week(add_days(today(), -70))  # a Monday, 10 weeks back
    edit("_toggle", True)
    edit("_set_frequency", "Weeks")
    overlay._views["Today"]._repeat.rule.start = anchor
    edit("_flip", "weekdays", 0)
    edit("_flip", "weekdays", 3)
    edit("_set_interval", "2")
    edit("_set_minutes", 45)

    notes = store.recurring_notes_for_task(task.id)
    assert len(notes) == 1, f"the first toggle-on should create exactly one note: {notes}"
    note = notes[0]
    saved = note.recurrence
    assert saved is not None and saved.weekdays == [0, 3] and saved.interval == 2, (
        f"the editor did not reach the note: {saved}"
    )
    promoted_entry = next(
        e for e in store.entries_for(task.id, today()) if e.recurring_note_id == note.id
    )
    assert promoted_entry is not None, "the row that was open should be the one tagged"
    print(f"  repeat saved as '{describe(saved)}' on its own note, plans still {len(store.db.plans)}")
    labels = [w.cget("text") for w in find_widgets(overlay._views["Today"]._repeat.frame, tk.Label)]
    assert any(describe(saved) in label for label in labels), (
        f"a real rule's own description should still show: {labels}"
    )

    hit, miss = add_days(anchor, 14), add_days(anchor, 15)  # a Monday two weeks on, and the Tuesday
    later = add_days(hit, 14)  # another two weeks on again
    assert store.planned_minutes(task.id, hit) == 45, "a matching day planned nothing"
    assert store.planned_minutes(task.id, miss) == 0, "an unmatched day planned something"
    assert any(c.task.id == task.id for c in store.contexts_for_date(hit)), (
        "the task with a note due is missing from a day it falls on"
    )

    # Zero on a day the note is due is a skip, and has to be written down,
    # because the absence of a plan is what the note's own placeholder would
    # otherwise fill in.
    store.set_plan(task.id, hit, 0)
    assert store.planned_minutes(task.id, hit) == 0, "skipping one occurrence didn't hold"
    assert store.planned_minutes(task.id, later) == 45, "the skip leaked into later weeks"
    print("  one occurrence skipped without disturbing the rest")

    edit("_toggle", False)
    assert store.db.recurring_note(note.id).recurrence is None, (
        "turning repeat off didn't save, or created a second note instead of editing this one"
    )
    assert len(store.recurring_notes_for_task(task.id)) == 1, "turning repeat off duplicated the note"
    overlay._views["Today"]._expanded = set()

    # The Week grid sizes its columns to the window, so it has to fit in the
    # narrowest case — seven day columns in a window as narrow as "small"
    # itself — and spread out rather than stranding the days on the right
    # when the window is widened.
    store.patch_settings(show_weekend=True)
    for width in (FULL_SIZE[0], 1020):
        overlay.root.geometry(f"{width}x500")
        overlay.show_tab("Week")
        for _ in range(8):
            overlay.root.update()
        grid = overlay._views["Week"].scroll.body.winfo_children()[0]
        canvas = overlay._views["Week"].scroll._canvas
        wanted, given = grid.winfo_reqwidth(), canvas.winfo_width()
        assert wanted <= given, f"the Week grid is clipped at {width}px: {wanted} > {given}"
        days = grid.grid_bbox(column=1, row=0)[2]
        print(f"  Week at {width}px: grid {wanted} fits {given}, day column {days}px")
    assert days > 60, f"widening left the day columns at {days}px"
    store.patch_settings(show_weekend=False)
    overlay.root.geometry(f"{FULL_SIZE[0]}x{FULL_SIZE[1]}")
    overlay.root.update()

    # The Month tab is a calendar grid: nav moves a whole month at a time,
    # and clicking a day jumps straight into it on the Day tab.
    from timesheet.dates import add_months, format_minutes, month_label, start_of_month

    overlay.show_tab("Month")
    overlay.root.update()
    month_view = overlay._views["Month"]
    this_month = start_of_month(today())
    assert month_view.month_label.cget("text") == month_label(this_month), (
        "the Month tab didn't open on the current month"
    )
    month_view._next()
    overlay.root.update()
    assert month_view.month_label.cget("text") == month_label(add_months(this_month, 1)), (
        "Next didn't move forward a month"
    )
    month_view._this_month()
    overlay.root.update()
    print(f"  Month tab: opened on {month_view.month_label.cget('text')}, nav moves by month")

    month_task = store.add_task(project.id, "Month tab check", True)
    store.add_entry(month_task.id, today(), 25, "Logged for the Month tab check")
    month_view.refresh()
    overlay.root.update()
    day_total = format_minutes(store.day_totals(today())[0])
    assert day_total in month_view.total_label.cget("text"), (
        f"the month total doesn't include today's {day_total}: {month_view.total_label.cget('text')!r}"
    )

    month_view._jump(today())
    overlay.root.update()
    assert overlay.active_tab == "Today", "clicking a day should switch to the Day tab"
    assert overlay._views["Today"].date == today(), "the jump landed on the wrong date"
    print("  Month tab: totals include today, and clicking a day jumps into it on Day")

    # Scoped to the check above alone — cleared so it doesn't change the
    # Export tab's line count and note text further down.
    store.db.entries = [e for e in store.db.entries if e.task_id != month_task.id]
    store.db.tasks = [t for t in store.db.tasks if t.id != month_task.id]

    # The Export table is only useful if clicking a field really does put that
    # field — and nothing else — on the clipboard. Day mode first: its
    # per-entry card list is unchanged from before the week grid existed.
    store.add_entry(task.id, today(), 40, "Signed the runbook off with ops")
    overlay.show_tab("Export")
    overlay.root.update()
    export = overlay._views["Export"]
    export._set_mode("day")
    overlay.root.update()
    assert len(export.lines) == 1, f"two entries should roll into one line, got {export.lines}"

    copied = []
    for button in find_buttons(export.table.body):
        text = button.cget("text")
        if not text:
            continue
        button.invoke()
        overlay.root.update()
        copied.append((text, overlay.root.clipboard_get()))

    hours = next((c for t, c in copied if t == "2.25"), None)
    assert hours == "2.25", f"the hours field copied {hours!r}"
    note = next((c for _, c in copied if " | " in c), None)
    assert note == "Ran the staging cutover with their DBA | Signed the runbook off with ops", (
        f"the notes field copied {note!r}"
    )
    print(f"  Export (Day): clicked {len(copied)} fields, hours {hours!r}, notes joined with ' | '")

    export._copy_table()
    overlay.root.update()
    table = overlay.root.clipboard_get()
    assert table.startswith("Date\tClient"), f"the table copy is not tab-separated: {table[:40]!r}"
    assert len(table.splitlines()) == 2, "expected a header and one row"
    print("  Export (Day): whole table copies as TSV, one row per entry")

    # A comma inside a note would corrupt a naive comma-joined row; the csv
    # module quotes it instead, so the field survives round-trip intact.
    store.add_entry(task.id, today(), 15, "Called the client, then followed up by email")
    overlay.root.update()
    opened: list[Path] = []
    real_startfile = export_module.os.startfile
    export_module.os.startfile = opened.append
    try:
        export._open_csv()
    finally:
        export_module.os.startfile = real_startfile
    assert len(opened) == 1, f"expected one file opened, got {opened}"
    csv_path = opened[0]
    assert csv_path.suffix == ".csv", f"expected a .csv, got {csv_path.name}"
    assert csv_path.parent == paths.exports_dir(), f"wrote outside exports/: {csv_path}"
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        parsed = list(csv.reader(handle))
    assert parsed[0] == export_module.DAY_COLUMNS, f"unexpected header: {parsed[0]!r}"
    assert len(parsed) == 2, f"expected a header and one row, got {len(parsed)}"
    note_field = parsed[1][-1]
    assert "Called the client, then followed up by email" in note_field, (
        f"the note didn't make it into the file: {note_field!r}"
    )
    assert len(parsed[1]) == len(export_module.DAY_COLUMNS), (
        f"the comma in the note split into extra columns: {parsed[1]}"
    )
    print(f"  Export (Day): wrote and opened {csv_path.name}, comma in a note survived quoted")

    # Excel refuses a second file sharing a name with one it already has
    # open, so exporting twice in a row has to land on two different names.
    export_module.os.startfile = opened.append
    try:
        export._open_csv()
    finally:
        export_module.os.startfile = real_startfile
    second_path = opened[1]
    assert second_path != csv_path, "two exports in a row reused the same filename"
    csv_path.unlink()
    second_path.unlink()
    print(f"  Export (Day): exporting twice in a row named the files differently ({csv_path.name} vs {second_path.name})")

    # Week mode pivots the same lines into one row per task, hours filed
    # under the day they fall on — a customer-timesheet-shaped grid, not a
    # per-entry list — with the hour and the note each their own
    # click-to-copy field, the note's chip only live when there's one to copy.
    export._set_mode("week")
    overlay.root.update()
    week_rows = export._week_rows()
    assert len(week_rows) == 1, f"three same-day entries should pivot to one task row, got {week_rows}"
    row = week_rows[0]
    assert row["task"] == "Cutover rehearsal", row["task"]
    todays_line = row["by_date"][today()]
    assert todays_line.hours(0) == "2.50", (
        "150 minutes across three entries should total 2.50h"
    )

    hour_button = next(
        b for b in find_buttons(export.table.body) if b.cget("text") == "2.50"
    )
    hour_button.invoke()
    overlay.root.update()
    assert overlay.root.clipboard_get() == "2.50", "the grid's hour chip should copy just the figure"

    note_chip = next(
        c for c in find_widgets(export.table.body, tk.Canvas) if c.cget("cursor") == "hand2"
    )
    note_chip.event_generate("<Button-1>")
    overlay.root.update()
    copied_note = overlay.root.clipboard_get()
    assert "Signed the runbook off with ops" in copied_note, (
        f"the grid's note chip copied the wrong thing: {copied_note!r}"
    )
    print("  Export (Week): the grid's hour and note fields each copy on their own")

    # Hours with no note at all still gets a cell — the chip just sits there
    # inert, nothing to copy, the same "present but can't act on it" recipe
    # the Day tab's own delete chip uses on a placeholder row.
    noteless_task = store.add_task(project.id, "Noteless entry check", True)
    store.add_entry(noteless_task.id, today(), 30)
    export.refresh()
    overlay.root.update()
    inert_chips = [
        c for c in find_widgets(export.table.body, tk.Canvas) if c.cget("cursor") == "arrow"
    ]
    assert inert_chips, "an entry with no note should still show an inert note chip"
    store.db.entries = [e for e in store.db.entries if e.task_id != noteless_task.id]
    store.db.tasks = [t for t in store.db.tasks if t.id != noteless_task.id]

    week_table = export._week_table()
    assert week_table[0][:3] == export_module.WEEK_LABEL_COLUMNS, (
        f"unexpected week grid header: {week_table[0]}"
    )
    assert week_table[-1][0] == "Total", f"expected a totals row at the bottom: {week_table[-1]}"
    export._copy_table()
    overlay.root.update()
    week_tsv = overlay.root.clipboard_get()
    assert week_tsv.startswith("Customer : Project\tTask\tTime type"), (
        f"the week table copy has the wrong header: {week_tsv[:60]!r}"
    )
    print("  Export (Week): grid, including its totals row, matches the pivoted table copy")

    # The task-info column's ellipsis is measured against the window, same
    # recipe as the Week tab's own title (see ``_task_column_limit``) — it
    # used to be a fixed character count, which clipped "Customer : Project"
    # and the task title identically narrow or wide. Checked the same way as
    # that tab's own grid: has to fit at the narrowest width, and actually
    # grow, not just sit there, once there's more room.
    limits = {}
    for width in (FULL_SIZE[0], 1020):
        overlay.root.geometry(f"{width}x{FULL_SIZE[1]}")
        for _ in range(8):
            overlay.root.update()
        export_grid = export.table.body.winfo_children()[0]
        export_canvas = export.table._canvas
        assert export_grid.winfo_reqwidth() <= export_canvas.winfo_width(), (
            f"the Export week grid is clipped at {width}px: "
            f"{export_grid.winfo_reqwidth()} > {export_canvas.winfo_width()}"
        )
        limits[width] = export._task_column_limit()
        print(
            f"  Export (Week) at {width}px: grid {export_grid.winfo_reqwidth()}px fits "
            f"{export_canvas.winfo_width()}px, task column {limits[width]} chars"
        )
    assert limits[1020] > limits[FULL_SIZE[0]], (
        f"widening the window should reveal more of the task info, got {limits}"
    )
    overlay.root.geometry(f"{FULL_SIZE[0]}x{FULL_SIZE[1]}")
    overlay.root.update()

    # Back to the single entry `task` started with, scoped to the checks
    # above alone — only "has logged time" matters to what comes after.
    store.db.entries = [e for e in store.db.entries if e.task_id != task.id]
    store.add_entry(task.id, today(), 95, "Ran the staging cutover with their DBA")

    # Adding anything rebuilds the whole view; the scroll position has to
    # survive that rather than snapping back to the top. Clients and projects
    # live on the "Projects" tab now — shown on screen as "Setup" — and on
    # its own, without Preferences bulked in beside it, a couple of clients
    # padded in first so there's actually something to scroll.
    for i in range(8):
        store.add_client(f"Padding Client {i}")
    overlay.show_tab("Projects")
    overlay.root.update()
    canvas = overlay._views["Projects"].scroll._canvas
    canvas.yview_moveto(0.45)
    overlay.root.update()
    before = canvas.yview()[0]
    store.add_client("Scrollkeeper Ltd")
    for _ in range(3):
        overlay.root.update()
    after = canvas.yview()[0]
    assert after > before / 2, f"the view jumped to the top: {before:.3f} -> {after:.3f}"
    print(f"  scroll held across a rebuild: {before:.3f} -> {after:.3f}")

    # Same two-step delete as the Day tab's tasks, refused bottom-up here too:
    # a client can't go while a project of its still exists, archived or not.
    projects_view = overlay._views["Projects"]
    throwaway_client = store.add_client("Disposable Ltd")
    throwaway_project = store.add_project(throwaway_client.id, "Throwaway work", "K-99", True)
    overlay.root.update()
    projects_view._arm_delete(throwaway_client.id)
    assert throwaway_client.id in projects_view._confirm_delete, "arming didn't register"
    projects_view._confirm_delete_chip(throwaway_client.id, store.delete_client)
    assert store.db.client(throwaway_client.id) is not None, "a client with a project should refuse"
    assert projects_view._delete_errors.get(throwaway_client.id), "no reason recorded for the refusal"
    projects_view._confirm_delete_chip(throwaway_project.id, store.delete_project)
    projects_view._confirm_delete_chip(throwaway_client.id, store.delete_client)
    assert store.db.client(throwaway_client.id) is None, "an empty client should delete cleanly"
    overlay.root.update()
    print("  Setup tab: delete refused while a project remains, then succeeds once it's gone")

    # Phase/Task nesting lives on the Projects tab: a task can grow a child
    # and starts reading as a "Phase" — a computed label, never stored —
    # regardless of whether it already had its own history; that history
    # relocates onto the new child instead of blocking it (see
    # ``Store.add_subtask``).
    clean_task = store.add_task(project.id, "Documentation", True)
    overlay.root.update()
    assert store.can_add_subtask(clean_task.id), "a brand new task should be eligible"
    assert store.can_add_subtask(task.id), "a task with logged time should be eligible too"
    child, problem = store.add_subtask(clean_task.id, "Write runbook", True)
    assert problem == "", f"adding a sub-task was refused: {problem}"
    overlay.root.update()
    assert store.is_phase(clean_task), "a task with a child should read as a Phase"
    contexts = store.active_contexts()
    ids = [c.task.id for c in contexts]
    assert clean_task.id not in ids, "a Phase should not be directly loggable"
    assert child.id in ids, "its child should be the loggable one"
    child_context = next(c for c in contexts if c.task.id == child.id)
    assert child_context.display_title == "Documentation \u203a Write runbook"
    assert store.delete_task(clean_task.id), "deleting a Phase with a child should be refused"
    print("  Projects tab: a clean task can grow a child and becomes a Phase")

    history_child, problem = store.add_subtask(task.id, "Write-up", True)
    assert problem == "", f"adding a sub-task to a task with history was refused: {problem}"
    overlay.root.update()
    assert store.entries_for(task.id, today()) == [], "the parent's history should have moved off it"
    moved = store.entries_for(history_child.id, today())
    assert len(moved) == 1, "the entry should have moved onto the new child"
    print("  Projects tab: a task's own history relocates onto a new child instead of blocking it")

    # The same relocation, done by hand through the "Move..." control, for
    # consolidating two tasks' history or fixing time logged against the
    # wrong one. Driven through the view's own methods rather than a
    # simulated click on its chip: a row this far down a long list scrolls
    # outside the ``ScrollFrame`` canvas's mapped viewport, and Tk doesn't
    # deliver synthetic button events to an unmapped window — the same
    # reason ``_confirm_delete_chip`` above is called directly too.
    projects_view._toggle_move(history_child.id)
    overlay.root.update()
    move_pickers = find_widgets(projects_view.scroll.body, tk.OptionMenu)
    assert len(move_pickers) == 1, "the Move... panel's destination picker did not render"
    projects_view._move_target_id = child.id
    projects_view._confirm_move(history_child.id)
    overlay.root.update()
    assert store.entries_for(history_child.id, today()) == [], "Move... should have cleared the source"
    assert len(store.entries_for(child.id, today())) == 1, "Move... should have landed on the target"
    assert projects_view._moving_task_id is None, "confirming Move... should close its own panel"
    print("  Projects tab: \"Move...\" relocates a task's history onto another by hand")

    # Through the real dropdown, which rebuilds the view from inside the
    # callback of a widget that view owns. The internal key "Setup" still
    # means what it always did — this is the old Setup content, shown on
    # screen now as "Preferences" — so the position and theme pickers are
    # still found here.
    overlay.show_tab("Setup")
    overlay.root.update()
    for label in ("Top right", "Free (drag anywhere)"):
        menu, index = find_menu_entry(overlay.content, label)
        menu.invoke(index)
        overlay.root.update()
        print(f"  Preferences dropdown '{label}' -> +{overlay.root.winfo_x()}+{overlay.root.winfo_y()}")

    # Shortcuts: each of the three global hotkeys shows its live accelerator
    # as a button; clicking it starts a capture that the next modifier+key
    # combo finishes, and a Reset chip only shows up once it's been changed.
    setup_view = overlay._views["Setup"]
    collapse_btn = next(
        b for b in find_buttons(setup_view.frame) if b.cget("text") == "Ctrl+Alt+T"
    )
    assert find_buttons(collapse_btn.master) == [collapse_btn], (
        "a never-changed shortcut shouldn't show a Reset button alongside it"
    )
    collapse_btn.invoke()
    assert collapse_btn.cget("text") == "Press keys\u2026", "clicking a shortcut should start capture"
    assert overlay.hotkeys._thread is None, (
        "starting a capture should pause the global listener so trying a combo "
        "can't trigger whichever action already owns it"
    )
    collapse_btn.event_generate("<KeyPress>", keysym="Control_L")
    collapse_btn.event_generate("<KeyPress>", keysym="Alt_L")
    collapse_btn.event_generate("<KeyPress>", keysym="p")
    overlay.root.update()
    assert overlay.store.db.settings.hotkeys.get("collapse") == "ctrl+alt+p", (
        f"capturing Ctrl+Alt+P should have rebound 'collapse', got {overlay.store.db.settings.hotkeys!r}"
    )
    assert overlay.accelerator_for("collapse") == "ctrl+alt+p"
    assert overlay.hotkeys._thread is not None, "finishing a capture should resume the global listener"
    print("  Preferences: capturing a key combo rebinds the 'collapse' shortcut")
    print("  Preferences: the global listener is paused for the duration of a capture")

    setup_view = overlay._views["Setup"]  # rebuilt by the change above
    collapse_btn = next(
        b for b in find_buttons(setup_view.frame) if b.cget("text") == "Ctrl+Alt+P"
    )
    # The reset chip uses an icon with no text when the PNGs are built, so
    # it's found structurally — the other button sharing the shortcut's row
    # — rather than by caption.
    reset_btn = next(
        b for b in find_buttons(collapse_btn.master) if b is not collapse_btn
    )
    reset_btn.invoke()
    overlay.root.update()
    assert "collapse" not in overlay.store.db.settings.hotkeys, "Reset should drop the override"
    assert overlay.accelerator_for("collapse") == "ctrl+alt+t", "Reset should fall back to the default"
    print("  Preferences: Reset puts a changed shortcut back to its default")

    # Escape cancels a capture in progress without changing anything.
    setup_view = overlay._views["Setup"]
    collapse_btn = next(
        b for b in find_buttons(setup_view.frame) if b.cget("text") == "Ctrl+Alt+T"
    )
    collapse_btn.invoke()
    assert overlay.hotkeys._thread is None, "capturing should pause the listener here too"
    collapse_btn.event_generate("<KeyPress>", keysym="Escape")
    overlay.root.update()
    assert collapse_btn.cget("text") == "Ctrl+Alt+T", "Escape should cancel the capture, unchanged"
    assert "collapse" not in overlay.store.db.settings.hotkeys
    assert overlay.hotkeys._thread is not None, "Escape should resume the listener, not leave it paused"
    print("  Preferences: Escape cancels a shortcut capture, and resumes the listener")

    # Each switch destroys and rebuilds the whole tree, from inside a callback
    # belonging to a widget in that tree.
    for label in ("Light", "Dark", "Light"):
        menu, index = find_menu_entry(overlay.content, label)
        menu.invoke(index)
        overlay.root.update()
        for tab in TABS:
            overlay.show_tab(tab)
            overlay.root.update()
        print(f"  theme '{label}' applied as '{theme.ACTIVE}', all tabs re-rendered")

    # "System" isn't a palette of its own — it resolves against whatever the
    # OS reports, which this machine's own setting decides, not the test —
    # so only the bookkeeping (mode persisted, a real palette picked, no
    # crash) is checked, not which of dark/light it actually lands on.
    menu, index = find_menu_entry(overlay.content, "System")
    menu.invoke(index)
    overlay.root.update()
    for tab in TABS:
        overlay.show_tab(tab)
        overlay.root.update()
    assert theme.MODE == theme.SYSTEM, f"theme mode should be 'system', got {theme.MODE!r}"
    assert theme.ACTIVE in theme.PALETTES
    assert store.db.settings.theme == theme.SYSTEM
    print(f"  theme 'System' applied, resolved to '{theme.ACTIVE}', setting persisted as 'system'")

    menu, index = find_menu_entry(overlay.content, "Dark")
    menu.invoke(index)
    overlay.root.update()
    for tab in TABS:
        overlay.show_tab(tab)
        overlay.root.update()

    overlay.collapse(True)
    overlay.root.update()
    menu, index = find_menu_entry(overlay.content, "Dark")
    menu.invoke(index)
    overlay.root.update()
    print(f"  theme switched while collapsed, icon mapped: {overlay.icon.winfo_ismapped()}")
    overlay.collapse(False)
    overlay.root.update()

    # A second launch hands off to this one rather than opening a duplicate
    # — found by the window title ``Overlay.__init__`` set, the same way a
    # real second process would (see ``timesheet/singleton.py``). The mutex
    # half of this lives in ``test_smoke.py`` instead: it needs no window at
    # all, so it runs there without needing a real desktop session.
    overlay.root.update()
    assert singleton.focus_existing(), "a second launch should have found this window"
    print("  singleton: a second launch's window search finds the running instance")

    overlay.root.after(150, overlay.quit)
    overlay.run()
    print("GUI smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
