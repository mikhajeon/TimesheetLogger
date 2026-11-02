"""End-to-end check of the domain layer: plan, track, export, reload.

Run with: python -m unittest discover tests
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timesheet import bookings, dwm, hotkeys, monitors, paths, recurrence, singleton
from timesheet.dates import (
    add_months,
    format_minutes,
    is_same_month,
    month_label,
    month_weeks,
    parse_duration,
    start_of_month,
    start_of_week,
    week_dates,
    week_label,
)
from timesheet.markdown import build_markdown
from timesheet.models import Database, Recurrence, Settings
from timesheet.monitors import Monitor
from timesheet.store import Store
from timesheet.ui import theme

#: A laptop with a second screen hung off its left, which puts that screen at
#: a negative x — the case naive positioning maths gets wrong.
PRIMARY = Monitor(name=r"\\.\DISPLAY1", x=0, y=0, width=1920, height=1032, primary=True)
SECOND = Monitor(name=r"\\.\DISPLAY2", x=-2560, y=-200, width=2560, height=1392)


class DurationParsing(unittest.TestCase):
    def test_accepted_formats(self) -> None:
        cases = {
            "1.5": 90,
            "90m": 90,
            "1h30": 90,
            "1h 30m": 90,
            "1:30": 90,
            ":45": 45,
            "2h": 120,
            "2": 120,
        }
        for text, expected in cases.items():
            self.assertEqual(parse_duration(text), expected, msg=text)

    def test_rejects_nonsense(self) -> None:
        for text in ("", "   ", "soon", "half an hour"):
            self.assertIsNone(parse_duration(text))

    def test_formatting(self) -> None:
        self.assertEqual(format_minutes(0), "0m")
        self.assertEqual(format_minutes(45), "45m")
        self.assertEqual(format_minutes(60), "1h")
        self.assertEqual(format_minutes(450), "7h 30m")


class CornerSnapping(unittest.TestCase):
    def test_each_corner_of_the_primary_screen(self) -> None:
        margin = monitors.MARGIN
        expected = {
            "top-left": (margin, margin),
            "top-right": (1920 - 470 - margin, margin),
            "bottom-left": (margin, 1032 - 700 - margin),
            "bottom-right": (1920 - 470 - margin, 1032 - 700 - margin),
        }
        for corner, position in expected.items():
            self.assertEqual(
                monitors.corner_position(PRIMARY, corner, 470, 700), position, msg=corner
            )

    def test_corner_of_a_screen_at_a_negative_offset(self) -> None:
        x, y = monitors.corner_position(SECOND, "bottom-right", 470, 700)
        self.assertEqual(x, -2560 + 2560 - 470 - monitors.MARGIN)
        self.assertEqual(y, -200 + 1392 - 700 - monitors.MARGIN)

    def test_window_bigger_than_the_screen_keeps_its_header_reachable(self) -> None:
        tiny = Monitor(name="tiny", x=0, y=0, width=400, height=300)
        margin = monitors.MARGIN
        self.assertEqual(
            monitors.corner_position(tiny, "bottom-right", 1020, 700), (margin, margin)
        )

    def test_falls_back_to_primary_when_the_screen_is_unplugged(self) -> None:
        self.assertIs(monitors.find([PRIMARY], SECOND.name), PRIMARY)
        self.assertIs(monitors.find([PRIMARY, SECOND], SECOND.name), SECOND)

    def test_position_on_a_second_screen_is_not_treated_as_off_screen(self) -> None:
        screens = [PRIMARY, SECOND]
        self.assertTrue(monitors.is_on_screen(-2400, -100, 470, 700, screens))
        self.assertFalse(monitors.is_on_screen(-2400, -100, 470, 700, [PRIMARY]))

    def test_rejects_a_position_barely_overlapping_a_screen(self) -> None:
        # Only 20px of the window would be grabbable at the far right edge.
        self.assertFalse(monitors.is_on_screen(1900, 100, 470, 700, [PRIMARY]))


class StoreRoundTrip(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "timesheet.json"
        self.store = Store(self.path)
        self.store.load()

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_seeds_internal_client(self) -> None:
        self.assertEqual(len(self.store.db.clients), 1)
        self.assertTrue(self.store.db.clients[0].is_internal)
        self.assertEqual(self.store.db.projects[0].k_code, "NCC")

    def test_plan_track_and_reload(self) -> None:
        client = self.store.add_client("Acme Ltd")
        project = self.store.add_project(client.id, "Platform Migration", "K-10234", True)
        task = self.store.add_task(project.id, "Draft migration runbook", True)

        date = "2026-10-19"
        self.store.set_plan(task.id, date, 120)
        self.store.add_entry(task.id, date, 90, "Walked the client through the cutover")

        self.assertEqual(self.store.planned_minutes(task.id, date), 120)
        self.assertEqual(self.store.logged_minutes(task.id, date), 90)
        self.assertEqual(self.store.day_totals(date), (90, 90))

        # The +/- buttons top up a single manual entry rather than adding more.
        self.store.adjust_entry(task.id, date, 15)
        self.assertEqual(self.store.logged_minutes(task.id, date), 105)
        self.assertEqual(len(self.store.entries_for(task.id, date)), 1)

        reloaded = Store(self.path)
        reloaded.load()
        self.assertEqual(reloaded.logged_minutes(task.id, date), 105)
        self.assertEqual(reloaded.db.project(project.id).k_code, "K-10234")

    def test_internal_note_survives_a_reload_and_old_files_default_to_blank(self) -> None:
        client = self.store.add_client("Acme Ltd")
        project = self.store.add_project(client.id, "Platform Migration", "K-10234", True)
        task = self.store.add_task(project.id, "Draft migration runbook", True)
        date = "2026-10-19"
        self.store.add_entry(task.id, date, 90, "Walked the client through the cutover")
        entry = self.store.entries_for(task.id, date)[0]
        self.store.update_entry(entry.id, internal_note="Client pushed back on timeline")

        reloaded = Store(self.path)
        reloaded.load()
        self.assertEqual(
            reloaded.entries_for(task.id, date)[0].internal_note, "Client pushed back on timeline"
        )

        # A file saved before this field existed has no key for it at all —
        # ``Database.from_dict`` falls back to the dataclass default rather
        # than choking on the missing key.
        raw = json.loads(self.path.read_text("utf-8"))
        del raw["entries"][0]["internal_note"]
        self.path.write_text(json.dumps(raw), "utf-8")
        migrated = Store(self.path)
        migrated.load()
        self.assertEqual(migrated.entries_for(task.id, date)[0].internal_note, "")

    def test_a_skipped_recurring_note_occurrence_survives_a_reload(self) -> None:
        client = self.store.add_client("Acme Ltd")
        project = self.store.add_project(client.id, "Platform Migration", "K-10234", True)
        task = self.store.add_task(project.id, "App Cloud", True)
        note = self.store.add_recurring_note(
            task.id, "Daily stand-up", 30,
            Recurrence(frequency="weekly", weekdays=[0, 1, 2, 3, 4], minutes=30, start="2026-10-19"),
        )
        self.store.skip_recurring_note(note.id, "2026-10-19")

        reloaded = Store(self.path)
        reloaded.load()
        self.assertEqual(reloaded.day_rows("2026-10-19"), [])
        self.assertEqual(len(reloaded.day_rows("2026-10-20")), 1, "only the one day was skipped")

    def test_a_file_saved_before_task_level_repeat_was_removed_migrates_it_to_a_note(self) -> None:
        # ``Task.recurrence`` used to be the whole task's own rule; once it
        # was dropped in favour of always-entry-specific recurring notes, a
        # file still carrying one on disk would have quietly lost it on the
        # next load — this is the one-time migration that stands in for it
        # instead (see ``Database.from_dict``'s ``build_tasks``).
        client = self.store.add_client("Acme Ltd")
        project = self.store.add_project(client.id, "Platform Migration", "K-10234", True)
        task = self.store.add_task(project.id, "App Cloud", True)
        self.store.save()

        raw = json.loads(self.path.read_text("utf-8"))
        raw_task = next(t for t in raw["tasks"] if t["id"] == task.id)
        raw_task["recurrence"] = {
            "frequency": "weekly", "weekdays": [1], "minutes": 60, "start": "2026-10-27",
        }
        self.path.write_text(json.dumps(raw), "utf-8")

        migrated = Store(self.path)
        migrated.load()
        self.assertFalse(hasattr(migrated.db.task(task.id), "recurrence"))
        notes = migrated.recurring_notes_for_task(task.id)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].note, "App Cloud")
        self.assertEqual(notes[0].minutes, 60)
        self.assertEqual(notes[0].recurrence.weekdays, [1])
        self.assertEqual(migrated.planned_minutes(task.id, "2026-10-27"), 60)

        # Reloading the same file again, with no save of any kind in
        # between, must not migrate a second time and duplicate the note —
        # ``load`` itself writes the migrated shape straight back out, so a
        # process that's closed some way other than Quit (or killed
        # outright) before ever reaching a save of its own still leaves
        # nothing stale on disk to find next time.
        reloaded_again = Store(self.path)
        reloaded_again.load()
        self.assertEqual(len(reloaded_again.recurring_notes_for_task(task.id)), 1)
        self.assertNotIn("recurrence", json.loads(self.path.read_text("utf-8"))["tasks"][0])

    def test_timer_banks_time_against_its_start_day(self) -> None:
        project = self.store.db.projects[0]
        task = self.store.add_task(project.id, "Timesheet upkeep", False)

        self.store.start_timer(task.id)
        self.assertIsNotNone(self.store.db.active_timer)
        # Backdate so the timer has something worth banking.
        self.store.db.active_timer.started_at = time.time() - 125 * 60
        self.store.stop_timer()

        self.assertIsNone(self.store.db.active_timer)
        self.assertEqual(len(self.store.db.entries), 1)
        self.assertEqual(self.store.db.entries[0].minutes, 125)
        self.assertEqual(self.store.db.entries[0].source, "timer")

    def test_project_level_billable_gates_the_task(self) -> None:
        # A project can turn billability off for everything under it, but
        # never force it on: the task's own flag survives underneath.
        client = self.store.add_client("Acme Ltd")
        project = self.store.add_project(client.id, "Internal migration", "K-1", True)
        task = self.store.add_task(project.id, "Cutover rehearsal", True)
        date = "2026-10-19"
        self.store.add_entry(task.id, date, 60)

        self.assertTrue(self.store.context(task).billable)
        self.assertEqual(self.store.day_totals(date), (60, 60))

        self.store.update_project(project.id, default_billable=False)
        self.assertFalse(self.store.context(task).billable)
        self.assertEqual(self.store.day_totals(date), (60, 0))
        self.assertTrue(task.billable, "the task's own flag should not be touched")

        # Turning the project back on resumes whatever the task already said.
        self.store.update_project(project.id, default_billable=True)
        self.assertTrue(self.store.context(task).billable)
        self.assertEqual(self.store.day_totals(date), (60, 60))

    def test_corrupt_file_is_kept_aside(self) -> None:
        self.path.write_text("{ not json", "utf-8")
        store = Store(self.path)
        store.load()
        self.assertTrue(any(p.name.startswith("timesheet.corrupt") for p in self.path.parent.iterdir()))
        self.assertEqual(len(store.db.clients), 1)  # fell back to a fresh seed


class UndoRedo(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "timesheet.json"
        self.store = Store(self.path)
        self.store.load()
        self.project = self.store.db.projects[0]

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_undo_reverts_the_last_change_redo_reapplies_it(self) -> None:
        before = len(self.store.db.clients)
        self.store.add_client("Acme Ltd")
        self.assertEqual(len(self.store.db.clients), before + 1)

        self.store.undo()
        self.assertEqual(len(self.store.db.clients), before)

        self.store.redo()
        self.assertEqual(len(self.store.db.clients), before + 1)
        self.assertEqual(self.store.db.clients[-1].name, "Acme Ltd")

    def test_undo_and_redo_are_no_ops_with_nothing_queued(self) -> None:
        self.assertFalse(self.store.can_undo())
        self.assertFalse(self.store.can_redo())
        self.store.undo()  # should not raise
        self.store.redo()  # should not raise
        self.assertEqual(len(self.store.db.clients), 1)

    def test_a_call_that_changes_nothing_queues_no_undo_step(self) -> None:
        # A bad id, an empty set of changes: several mutators return early on
        # exactly this, and an undo step that visibly does nothing would be a
        # dead click.
        self.store.update_task("no-such-task-id", title="renamed")
        self.store.update_client("no-such-client-id")
        self.store.add_entry("no-such-task-id", "2026-10-19", -5)  # negative minutes is a no-op
        self.assertFalse(self.store.can_undo())

    def test_a_mutator_that_delegates_to_another_is_one_undo_step(self) -> None:
        # adjust_entry's first nudge on a placeholder calls add_entry
        # internally — one user click, so it should cost one undo step, not
        # two (the second of which would silently do nothing).
        task = self.store.add_task(self.project.id, "Cutover rehearsal", True)
        self.store.undo()  # back to before the task existed; isolate what follows
        task = self.store.add_task(self.project.id, "Cutover rehearsal", True)
        date = "2026-10-19"

        depth_before = len(self.store._undo_stack)
        self.store.adjust_entry(task.id, date, 15)
        self.assertEqual(len(self.store._undo_stack), depth_before + 1)
        self.assertEqual(self.store.logged_minutes(task.id, date), 15)

        self.store.undo()
        self.assertEqual(self.store.logged_minutes(task.id, date), 0)

    def test_starting_a_timer_over_a_running_one_is_one_undo_step(self) -> None:
        # start_timer stops whatever's already running first — also one
        # click, also one step.
        first = self.store.add_task(self.project.id, "First task", True)
        second = self.store.add_task(self.project.id, "Second task", True)
        self.store.start_timer(first.id)

        depth_before = len(self.store._undo_stack)
        self.store.start_timer(second.id)
        self.assertEqual(len(self.store._undo_stack), depth_before + 1)
        self.assertEqual(self.store.db.active_timer.task_id, second.id)

        self.store.undo()
        self.assertEqual(self.store.db.active_timer.task_id, first.id)

    def test_settings_are_not_touched_by_undo(self) -> None:
        self.store.add_client("Acme Ltd")
        self.store.patch_settings(theme="light")

        self.store.undo()  # should only undo the client, not the theme
        self.assertEqual(len(self.store.db.clients), 1)
        self.assertEqual(self.store.db.settings.theme, "light")

    def test_history_is_capped(self) -> None:
        from timesheet.store import _HISTORY_LIMIT

        for index in range(_HISTORY_LIMIT + 10):
            self.store.add_client(f"Client {index}")
        self.assertEqual(len(self.store._undo_stack), _HISTORY_LIMIT)

    def test_reloading_the_file_does_not_carry_undo_history_over(self) -> None:
        self.store.add_client("Acme Ltd")
        self.assertTrue(self.store.can_undo())

        reloaded = Store(self.path)
        reloaded.load()
        self.assertFalse(reloaded.can_undo())
        self.assertFalse(reloaded.can_redo())

    def test_a_fresh_edit_after_undo_clears_the_redo_stack(self) -> None:
        self.store.add_client("Acme Ltd")
        self.store.undo()
        self.assertTrue(self.store.can_redo())

        self.store.add_client("Widgets Co")
        self.assertFalse(self.store.can_redo())


class MarkdownExport(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "t.json")
        self.store.load()

        client = self.store.add_client("Acme Ltd")
        project = self.store.add_project(client.id, "Platform Migration", "K-10234", True)
        billable = self.store.add_task(project.id, "Cutover rehearsal", True)
        internal = self.store.add_task(self.store.db.projects[0].id, "Timesheet upkeep", False)

        self.date = "2026-10-19"
        self.store.add_entry(billable.id, self.date, 128, "Ran the staging cutover with their DBA")
        self.store.add_entry(internal.id, self.date, 20)
        self.store.set_day_note(self.date, "Access to staging was delayed by an hour")

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_contains_bookable_lines_and_narrative(self) -> None:
        dates = week_dates(start_of_week(self.date), True)
        out = build_markdown(self.store.db, dates, "Timesheet — test week")

        self.assertIn("## Timesheet lines", out)
        self.assertIn("`K-10234`", out)
        self.assertIn("## Work carried out, by project", out)
        self.assertIn("Ran the staging cutover with their DBA", out)
        self.assertIn("Access to staging was delayed by an hour", out)

        # Exports never round — 128 minutes is 2.13h, and the Generated
        # line's own total (both tasks, 148 minutes) still reads "2h 28m".
        self.assertIn("| 2.13 |", out)
        self.assertIn("2h 28m", out)

    def test_figures_are_never_rounded(self) -> None:
        out = build_markdown(self.store.db, [self.date], "Exact")
        self.assertIn("| 2.13 |", out)  # 128 minutes, unrounded

    def test_internal_note_never_rolls_up_into_the_export(self) -> None:
        entry = self.store.db.entries[0]
        self.store.update_entry(entry.id, internal_note="Client was difficult about scope")
        out = build_markdown(self.store.db, [self.date], "Timesheet")
        self.assertNotIn("Client was difficult about scope", out)

    def test_week_label(self) -> None:
        self.assertEqual(week_label(start_of_week("2026-10-19")), "2026-W43")


class RecurrenceRules(unittest.TestCase):
    """Oct 2026 starts on a Thursday; 19 Oct is the Monday of week 43."""

    def days(self, rule, start="2026-10-01", end="2026-12-31") -> list[str]:
        return recurrence.dates_between(rule, start, end)

    def test_every_n_days_counts_from_the_start(self) -> None:
        rule = Recurrence(frequency="daily", interval=3, start="2026-10-01")
        self.assertEqual(
            self.days(rule, end="2026-10-12"),
            ["2026-10-01", "2026-10-04", "2026-10-07", "2026-10-10"],
        )

    def test_nothing_falls_before_the_start_or_after_the_end(self) -> None:
        rule = Recurrence(frequency="daily", start="2026-10-05", until="2026-10-07")
        self.assertEqual(self.days(rule), ["2026-10-05", "2026-10-06", "2026-10-07"])

    def test_weekly_on_chosen_days(self) -> None:
        # Mondays and Wednesdays, through the back half of October.
        rule = Recurrence(frequency="weekly", weekdays=[0, 2], start="2026-10-19")
        self.assertEqual(
            self.days(rule, end="2026-10-31"),
            ["2026-10-19", "2026-10-21", "2026-10-26", "2026-10-28"],
        )

    def test_fortnightly_keeps_its_days_in_the_same_week(self) -> None:
        # The trap here is alternating Mon and Thu instead of skipping a week.
        rule = Recurrence(frequency="weekly", interval=2, weekdays=[0, 3], start="2026-10-19")
        self.assertEqual(
            self.days(rule, end="2026-11-15"),
            ["2026-10-19", "2026-10-22", "2026-11-02", "2026-11-05"],
        )

    def test_weekly_with_no_day_chosen_uses_the_day_it_started_on(self) -> None:
        rule = Recurrence(frequency="weekly", start="2026-10-20")  # a Tuesday
        self.assertEqual(
            self.days(rule, end="2026-11-10"),
            ["2026-10-20", "2026-10-27", "2026-11-03", "2026-11-10"],
        )

    def test_monthly_on_chosen_dates(self) -> None:
        rule = Recurrence(frequency="monthly", month_days=[1, 15], start="2026-10-01")
        self.assertEqual(
            self.days(rule),
            ["2026-10-01", "2026-10-15", "2026-11-01", "2026-11-15",
             "2026-12-01", "2026-12-15"],
        )

    def test_the_last_day_lands_in_every_month_whatever_its_length(self) -> None:
        rule = Recurrence(
            frequency="monthly", month_days=[recurrence.LAST_DAY], start="2027-01-01"
        )
        self.assertEqual(
            self.days(rule, "2027-01-01", "2027-04-30"),
            ["2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30"],
        )

    def test_a_date_a_month_is_too_short_for_is_skipped_not_clamped(self) -> None:
        # Deliberate: silently booking the 28th for someone who asked for the
        # 31st is worse than nothing. "Last day" is there for that intent.
        rule = Recurrence(frequency="monthly", month_days=[31], start="2027-01-01")
        self.assertEqual(self.days(rule, "2027-02-01", "2027-02-28"), [])

    def test_chosen_months_give_custom_dates_across_the_year(self) -> None:
        # Quarter-end reporting: the 5th of Jan, Apr, Jul and Oct.
        rule = Recurrence(
            frequency="monthly", month_days=[5], months=[1, 4, 7, 10], start="2027-01-01"
        )
        self.assertEqual(
            self.days(rule, "2027-01-01", "2027-12-31"),
            ["2027-01-05", "2027-04-05", "2027-07-05", "2027-10-05"],
        )

    def test_every_n_months_counts_from_the_start(self) -> None:
        rule = Recurrence(frequency="monthly", interval=3, month_days=[2], start="2026-10-01")
        self.assertEqual(
            self.days(rule, "2026-10-01", "2027-08-01"),
            ["2026-10-02", "2027-01-02", "2027-04-02", "2027-07-02"],
        )

    def test_descriptions_read_like_sentences(self) -> None:
        cases = [
            (Recurrence(frequency="daily", start="2026-10-01"), "Every day"),
            (Recurrence(frequency="daily", interval=3, start="2026-10-01"), "Every 3 days"),
            (
                Recurrence(frequency="weekly", weekdays=[0, 1, 2, 3, 4], start="2026-10-19"),
                "Every weekday",
            ),
            (
                Recurrence(frequency="weekly", interval=2, weekdays=[0, 3], start="2026-10-19"),
                "Every 2 weeks on Mon, Thu",
            ),
            (
                Recurrence(frequency="monthly", month_days=[1, 15], start="2026-10-01"),
                "Every month on the 1st and 15th",
            ),
            (
                Recurrence(
                    frequency="monthly", month_days=[recurrence.LAST_DAY], start="2026-10-01"
                ),
                "Every month on the last day",
            ),
            (
                Recurrence(frequency="monthly", month_days=[3], months=[1, 7], start="2026-10-01"),
                "The 3rd of Jan, Jul",
            ),
            (
                Recurrence(
                    frequency="monthly",
                    month_days=[15, 1, recurrence.LAST_DAY],
                    start="2026-10-01",
                ),
                "Every month on the 1st, 15th and last day",
            ),
            (
                Recurrence(
                    frequency="monthly",
                    interval=3,
                    month_days=[1],
                    months=[1, 4],
                    start="2026-10-01",
                ),
                "Every 3 months, the 1st of Jan, Apr",
            ),
        ]
        for rule, expected in cases:
            self.assertEqual(recurrence.describe(rule), expected)
        self.assertEqual(recurrence.describe(None), "Does not repeat")

    def test_a_planned_duration_survives_being_shown_and_retyped(self) -> None:
        # The repeat editor shows the minutes formatted and reads them back
        # when the field loses focus, so a lossy round-trip would quietly
        # rewrite the rule every time the panel was opened.
        for minutes in (15, 45, 60, 90, 150, 480):
            self.assertEqual(parse_duration(format_minutes(minutes)), minutes)

    def test_an_impossible_rule_gives_up_rather_than_spinning(self) -> None:
        rule = Recurrence(
            frequency="monthly", month_days=[31], months=[2], start="2027-01-01"
        )
        self.assertIsNone(recurrence.next_after(rule, "2027-01-01"))


class RecurringNotePlanning(unittest.TestCase):
    """The same day-planning guarantees ``RecurringPlans`` used to check
    against a task's own ``recurrence`` — removed in favour of recurring
    notes being the only mechanism (see ``models.RecurringNote``) — still
    have to hold with a note standing in for the rule."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "t.json")
        self.store.load()
        self.project = self.store.db.projects[0]
        self.task = self.store.add_task(self.project.id, "Timesheet admin", False)
        self.note = self.store.add_recurring_note(
            self.task.id, "Timesheet admin",
            30, Recurrence(frequency="weekly", weekdays=[4], minutes=30, start="2026-10-19"),
        )
        self.friday, self.next_friday = "2026-10-23", "2026-10-30"

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_the_day_total_agrees_with_the_individual_days(self) -> None:
        # The Week footer and the markdown export both total the day, and
        # used to add up stored plans only, so a note due that day showed up
        # in the cells above a total that ignored it.
        other = self.store.add_task(self.store.db.projects[0].id, "Standup", False)
        self.store.set_plan(other.id, self.friday, 15)
        self.assertEqual(self.store.planned_minutes_on(self.friday), 45)

        # A pinned plan replaces the note's minutes for that task rather than
        # adding to them.
        self.store.set_plan(self.task.id, self.friday, 90)
        self.assertEqual(self.store.planned_minutes_on(self.friday), 105)

        self.store.update_project(self.project.id, closed=True)
        self.assertEqual(self.store.planned_minutes_on(self.next_friday), 0)

    def test_typing_over_one_day_leaves_the_others_alone(self) -> None:
        self.store.set_plan(self.task.id, self.friday, 90)
        self.assertEqual(self.store.planned_minutes(self.task.id, self.friday), 90)
        self.assertEqual(self.store.planned_minutes(self.task.id, self.next_friday), 30)

    def test_clearing_one_day_with_a_plan_skips_just_that_occurrence(self) -> None:
        # A zero has to be written down here; deleting the row would let the
        # note's placeholder put the day straight back.
        self.store.set_plan(self.task.id, self.friday, 0)
        self.assertEqual(self.store.planned_minutes(self.task.id, self.friday), 0)
        self.assertEqual(self.store.planned_minutes(self.task.id, self.next_friday), 30)

    def test_a_one_off_task_still_drops_its_plan_when_cleared(self) -> None:
        one_off = self.store.add_task(self.store.db.projects[0].id, "One-off", False)
        self.store.set_plan(one_off.id, self.friday, 60)
        self.store.set_plan(one_off.id, self.friday, 0)
        self.assertEqual(self.store.db.plans, [])

    def test_a_due_task_shows_up_on_the_day_and_in_the_week(self) -> None:
        shown = [c.task.id for c in self.store.contexts_for_date(self.friday)]
        self.assertIn(self.task.id, shown)
        self.assertNotIn(self.task.id, [c.task.id for c in self.store.contexts_for_date("2026-10-22")])
        self.assertEqual(
            self.store.tasks_with_notes_due(week_dates("2026-10-19", True)), {self.task.id}
        )

    def test_closing_the_project_or_archiving_the_task_stops_its_notes_coming_round(self) -> None:
        self.store.update_project(self.project.id, closed=True)
        self.assertEqual(self.store.planned_minutes(self.task.id, self.friday), 0)
        self.assertEqual(self.store.recurring_notes_due(self.task.id, self.friday), [])

        self.store.update_project(self.project.id, closed=False)
        self.store.update_task(self.task.id, archived=True)
        self.assertEqual(self.store.planned_minutes(self.task.id, self.friday), 0)
        self.assertEqual(self.store.recurring_notes_due(self.task.id, self.friday), [])

    def test_the_notes_rule_survives_a_reload(self) -> None:
        reloaded = Store(self.store.data_file)
        reloaded.load()
        rule = reloaded.db.recurring_note(self.note.id).recurrence
        self.assertIsInstance(rule, Recurrence)
        self.assertEqual(rule.weekdays, [4])
        self.assertEqual(reloaded.planned_minutes(self.task.id, self.friday), 30)


class PromotingARowToARecurringNote(unittest.TestCase):
    """"Repeat" on a Day row is entry-specific: it only ever creates or
    edits the one ``RecurringNote`` that row is (or becomes), never a rule
    that would apply to the whole task (see ``Store.promote_to_recurring_note``)."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "t.json")
        self.store.load()
        client = self.store.add_client("Acme Ltd")
        self.project = self.store.add_project(client.id, "Platform Migration", "K-1", True)
        self.task = self.store.add_task(self.project.id, "App Cloud", True)
        self.monday = "2026-10-19"
        self.rule = Recurrence(frequency="weekly", weekdays=[0], minutes=45, start=self.monday)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def _row(self):
        return next(r for r in self.store.day_rows(self.monday) if r.context.task.id == self.task.id)

    def test_promoting_a_logged_entry_tags_that_entry_without_touching_its_note(self) -> None:
        self.store.add_entry(self.task.id, self.monday, 50, "Deployed the cluster")
        row = self._row()
        note = self.store.promote_to_recurring_note(row, self.monday, self.rule)

        self.assertEqual(note.task_id, self.task.id)
        self.assertEqual(note.note, "Deployed the cluster")
        self.assertEqual(note.recurrence.weekdays, [0])
        entry = self.store.entries_for(self.task.id, self.monday)[0]
        self.assertEqual(entry.recurring_note_id, note.id)
        self.assertEqual(entry.minutes, 50, "the entry itself is untouched by the promotion")

    def test_promoting_an_entry_carries_its_internal_note_onto_the_new_note(self) -> None:
        self.store.add_entry(self.task.id, self.monday, 50, "Deployed the cluster")
        entry = self.store.entries_for(self.task.id, self.monday)[0]
        self.store.update_entry(entry.id, internal_note="Flaky deploy, keep an eye on it")
        row = self._row()
        note = self.store.promote_to_recurring_note(row, self.monday, self.rule)

        self.assertEqual(note.internal_note, "Flaky deploy, keep an eye on it")

    def test_promoting_one_entry_never_tags_a_sibling_logged_the_same_day(self) -> None:
        self.store.add_entry(self.task.id, self.monday, 20, "First pass")
        self.store.add_entry(self.task.id, self.monday, 30, "Second pass")
        rows = [r for r in self.store.day_rows(self.monday) if r.context.task.id == self.task.id]
        first = next(r for r in rows if r.note == "First pass")
        note = self.store.promote_to_recurring_note(first, self.monday, self.rule)

        entries = {e.note: e.recurring_note_id for e in self.store.entries_for(self.task.id, self.monday)}
        self.assertEqual(entries["First pass"], note.id)
        self.assertEqual(entries["Second pass"], "")

    def test_promoting_a_bare_plan_placeholder_clears_todays_plan_so_the_note_takes_over(self) -> None:
        self.store.set_plan(self.task.id, self.monday, 45)
        row = self._row()
        self.assertTrue(row.is_placeholder)
        note = self.store.promote_to_recurring_note(row, self.monday, self.rule)

        self.assertEqual(note.minutes, 45, "the plan's own figure carries over as the note's default")
        self.assertFalse(
            any(p.task_id == self.task.id and p.date == self.monday for p in self.store.db.plans),
            "a leftover plan would shadow the note's own placeholder every day it's due",
        )
        new_row = self._row()
        self.assertEqual(new_row.recurring_note_id, note.id)

    def test_editing_an_already_promoted_rows_rule_updates_the_same_note(self) -> None:
        self.store.add_entry(self.task.id, self.monday, 45, "First one")
        row = self._row()
        note = self.store.promote_to_recurring_note(row, self.monday, self.rule)

        tighter = Recurrence(frequency="weekly", weekdays=[0, 2], minutes=45, start=self.monday)
        self.store.set_recurring_note_rule(note.id, tighter)

        self.assertEqual(len(self.store.all_recurring_notes()), 1, "no duplicate note was created")
        self.assertEqual(self.store.db.recurring_note(note.id).recurrence.weekdays, [0, 2])

    def test_set_row_note_rule_carries_the_rules_own_minutes_onto_the_note(self) -> None:
        # The Day tab's row-level Repeat editor only ever shows the rule's
        # own "Plan" figure for a note, never a separate "Usually" field
        # (that's the Recurring tab's own editor instead) — so changing it
        # has to actually take, or the row's single visible minutes control
        # would be a no-op.
        self.store.set_plan_note(self.task.id, self.monday, "Placeholder with nothing planned")
        row = self._row()
        note = self.store.promote_to_recurring_note(row, self.monday, self.rule)
        self.assertEqual(note.minutes, 0, "nothing was planned yet when it was promoted")

        bigger = Recurrence(frequency="weekly", weekdays=[0], minutes=90, start=self.monday)
        self.store.set_row_note_rule(note.id, bigger)
        self.assertEqual(self.store.db.recurring_note(note.id).minutes, 90)
        self.assertEqual(self.store.planned_minutes(self.task.id, self.monday), 90)

        self.store.set_row_note_rule(note.id, None)
        self.assertIsNone(self.store.db.recurring_note(note.id).recurrence)
        self.assertEqual(
            self.store.db.recurring_note(note.id).minutes, 90,
            "turning repeat off has no rule to read minutes from, so the last figure stands",
        )

    def test_promoting_is_one_undo_step(self) -> None:
        self.store.set_plan(self.task.id, self.monday, 45)
        row = self._row()
        depth_before = len(self.store._undo_stack)
        self.store.promote_to_recurring_note(row, self.monday, self.rule)
        self.assertEqual(len(self.store._undo_stack), depth_before + 1)
        self.store.undo()
        self.assertEqual(self.store.all_recurring_notes(), [])
        self.assertTrue(any(p.task_id == self.task.id for p in self.store.db.plans), "the plan is back")


class DeleteGuards(unittest.TestCase):
    """Deleting is bottom-up: a client or project can't vanish out from under
    something that still references it, and a task with history is refused
    outright rather than silently taking billed time with it."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "t.json")
        self.store.load()
        self.client = self.store.add_client("Acme Ltd")
        self.project = self.store.add_project(self.client.id, "Platform Migration", "K-1", True)
        self.task = self.store.add_task(self.project.id, "Draft runbook", True)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_a_clean_task_deletes_outright(self) -> None:
        self.assertEqual(self.store.delete_task(self.task.id), "")
        self.assertIsNone(self.store.db.task(self.task.id))

    def test_a_task_with_logged_time_is_refused(self) -> None:
        self.store.add_entry(self.task.id, "2026-10-19", 30)
        problem = self.store.delete_task(self.task.id)
        self.assertTrue(problem)
        self.assertIsNotNone(self.store.db.task(self.task.id))

    def test_a_task_with_a_plan_is_refused(self) -> None:
        self.store.set_plan(self.task.id, "2026-10-19", 30)
        self.assertTrue(self.store.delete_task(self.task.id))

    def test_a_task_with_a_recurring_note_is_refused(self) -> None:
        self.store.add_recurring_note(self.task.id, "Standing commitment", 30)
        self.assertTrue(self.store.delete_task(self.task.id))

    def test_a_running_task_is_refused(self) -> None:
        self.store.start_timer(self.task.id)
        self.assertTrue(self.store.delete_task(self.task.id))

    def test_a_project_with_tasks_is_refused_then_deletes_once_empty(self) -> None:
        problem = self.store.delete_project(self.project.id)
        self.assertTrue(problem)
        self.store.delete_task(self.task.id)
        self.assertEqual(self.store.delete_project(self.project.id), "")
        self.assertIsNone(self.store.db.project(self.project.id))

    def test_a_client_with_projects_is_refused_then_deletes_once_empty(self) -> None:
        problem = self.store.delete_client(self.client.id)
        self.assertTrue(problem)
        self.store.delete_task(self.task.id)
        self.store.delete_project(self.project.id)
        self.assertEqual(self.store.delete_client(self.client.id), "")
        self.assertIsNone(self.store.db.client(self.client.id))

    def test_deleting_an_unknown_id_is_a_quiet_no_op(self) -> None:
        self.assertEqual(self.store.delete_task("nope"), "")
        self.assertEqual(self.store.delete_project("nope"), "")
        self.assertEqual(self.store.delete_client("nope"), "")


class PhaseTaskNesting(unittest.TestCase):
    """A task becomes a "Phase" — a computed label, nothing stored — the
    moment it has a non-archived child, one level deep, leaf-only loggable."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "t.json")
        self.store.load()
        client = self.store.add_client("Acme Ltd")
        self.project = self.store.add_project(client.id, "Platform Migration", "K-1", True)
        self.parent = self.store.add_task(self.project.id, "Cutover", True)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_a_clean_task_can_gain_a_child_and_becomes_a_phase(self) -> None:
        self.assertTrue(self.store.can_add_subtask(self.parent.id))
        self.assertFalse(self.store.is_phase(self.parent))
        child, problem = self.store.add_subtask(self.parent.id, "Draft runbook", True)
        self.assertEqual(problem, "")
        self.assertIsNotNone(child)
        self.assertTrue(self.store.is_phase(self.parent))
        self.assertEqual(child.parent_id, self.parent.id)
        self.assertEqual(child.project_id, self.parent.project_id)

    def test_a_task_with_logged_time_still_becomes_a_phase_and_its_history_moves_with_it(
        self,
    ) -> None:
        # A Phase is never itself loggable (see active_contexts), so history
        # left on the parent would otherwise vanish the moment it gained a
        # child — it moves onto that child instead.
        self.store.add_entry(self.parent.id, "2026-10-19", 30, "Pre-nesting work")
        self.assertTrue(self.store.can_add_subtask(self.parent.id))
        child, problem = self.store.add_subtask(self.parent.id, "Draft runbook", True)
        self.assertEqual(problem, "")
        self.assertIsNotNone(child)
        self.assertTrue(self.store.is_phase(self.parent))
        self.assertEqual(self.store.entries_for(self.parent.id, "2026-10-19"), [])
        moved = self.store.entries_for(child.id, "2026-10-19")
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].note, "Pre-nesting work")

    def test_a_planned_or_repeating_tasks_history_moves_with_it_too(self) -> None:
        planned = self.store.add_task(self.project.id, "Planned", True)
        self.store.set_plan(planned.id, "2026-10-19", 60)
        repeating = self.store.add_task(self.project.id, "Repeating", True)
        note = self.store.add_recurring_note(repeating.id, "Standing commitment", 30)

        plan_child, _ = self.store.add_subtask(planned.id, "Draft runbook", True)
        self.assertEqual(self.store.planned_minutes(planned.id, "2026-10-19"), 0)
        self.assertEqual(self.store.planned_minutes(plan_child.id, "2026-10-19"), 60)

        note_child, _ = self.store.add_subtask(repeating.id, "Draft runbook", True)
        self.assertEqual(self.store.recurring_notes_for_task(repeating.id), [])
        self.assertEqual(self.store.db.recurring_note(note.id).task_id, note_child.id)

    def test_nesting_caps_at_one_level(self) -> None:
        child, _ = self.store.add_subtask(self.parent.id, "Draft runbook", True)
        self.assertFalse(self.store.can_add_subtask(child.id))
        grandchild, problem = self.store.add_subtask(child.id, "Too deep", True)
        self.assertIsNone(grandchild)
        self.assertTrue(problem)

    def test_a_phase_accepts_more_children_freely(self) -> None:
        self.store.add_subtask(self.parent.id, "First", True)
        self.assertTrue(self.store.can_add_subtask(self.parent.id))
        second, problem = self.store.add_subtask(self.parent.id, "Second", True)
        self.assertEqual(problem, "")
        self.assertIsNotNone(second)

    def test_losing_its_last_child_reverts_the_label(self) -> None:
        child, _ = self.store.add_subtask(self.parent.id, "Draft runbook", True)
        self.assertTrue(self.store.is_phase(self.parent))
        self.store.update_task(child.id, archived=True)
        self.assertFalse(self.store.is_phase(self.parent))

    def test_a_phase_is_excluded_from_active_contexts_only_its_child_shows(self) -> None:
        child, _ = self.store.add_subtask(self.parent.id, "Draft runbook", True)
        ids = [c.task.id for c in self.store.active_contexts()]
        self.assertNotIn(self.parent.id, ids)
        self.assertIn(child.id, ids)

    def test_the_child_s_context_carries_the_phase_and_a_prefixed_title(self) -> None:
        child, _ = self.store.add_subtask(self.parent.id, "Draft runbook", True)
        context = self.store.context(child)
        self.assertEqual(context.phase_title, "Cutover")
        self.assertEqual(context.display_title, "Cutover \u203a Draft runbook")

        plain = self.store.context(self.parent)
        self.assertEqual(plain.phase_title, "")
        self.assertEqual(plain.display_title, "Cutover")

    def test_deleting_a_phase_is_refused_while_it_has_children(self) -> None:
        child, _ = self.store.add_subtask(self.parent.id, "Draft runbook", True)
        self.assertTrue(self.store.delete_task(self.parent.id))
        self.store.delete_task(child.id)
        self.assertEqual(self.store.delete_task(self.parent.id), "")


class PlanNotes(unittest.TestCase):
    """A Plan can carry a note ahead of any logged time."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "t.json")
        self.store.load()
        client = self.store.add_client("Acme Ltd")
        project = self.store.add_project(client.id, "Platform Migration", "K-1", True)
        self.task = self.store.add_task(project.id, "Cutover runbook", True)
        self.date = "2026-10-19"

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_a_note_can_exist_before_any_plan_or_entry(self) -> None:
        self.store.set_plan_note(self.task.id, self.date, "Getting started")
        plan = next(p for p in self.store.db.plans if p.task_id == self.task.id)
        self.assertEqual(plan.note, "Getting started")
        self.assertEqual(plan.minutes, 0)

    def test_an_emptied_note_with_nothing_else_on_the_plan_leaves_no_trace(self) -> None:
        self.store.set_plan_note(self.task.id, self.date, "")
        self.assertEqual(self.store.db.plans, [])

    def test_setting_minutes_does_not_wipe_an_existing_note(self) -> None:
        self.store.set_plan_note(self.task.id, self.date, "Getting started")
        self.store.set_plan(self.task.id, self.date, 90)
        plan = next(p for p in self.store.db.plans if p.task_id == self.task.id)
        self.assertEqual(plan.note, "Getting started")
        self.assertEqual(plan.minutes, 90)

    def test_a_bare_plan_placeholder_has_no_internal_note_of_its_own(self) -> None:
        # Unlike ``note``, a plain ``Plan`` has nothing equivalent to carry
        # — the Day tab's internal note field only ever shows for a row
        # backed by a real ``Entry`` or a ``RecurringNote`` (see
        # ``DayRow.internal_note``/``_day_row``).
        self.store.set_plan_note(self.task.id, self.date, "Getting started")
        row = next(r for r in self.store.day_rows(self.date) if r.context.task.id == self.task.id)
        self.assertEqual(row.internal_note, "")


class PerEntryPlannedHours(unittest.TestCase):
    """Planned Hours is per-``Entry`` once anything's logged, not one
    figure shared across every entry the task has on the same day."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "t.json")
        self.store.load()
        client = self.store.add_client("Acme Ltd")
        project = self.store.add_project(client.id, "Platform Migration", "K-1", True)
        self.task = self.store.add_task(project.id, "Cutover runbook", True)
        self.date = "2026-10-19"
        self.store.add_entry(self.task.id, self.date, 20, "First stint")
        self.store.add_entry(self.task.id, self.date, 15, "Second stint")
        self.first, self.second = self.store.entries_for(self.task.id, self.date)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def _row(self, entry_id: str):
        return next(r for r in self.store.day_rows(self.date) if r.id == entry_id)

    def test_a_new_entry_starts_with_planned_hours_unset(self) -> None:
        self.assertEqual(self.first.plan_minutes, 0)
        self.assertEqual(self.second.plan_minutes, 0)

    def test_setting_one_entrys_plan_leaves_its_sibling_alone(self) -> None:
        self.store.update_entry(self.first.id, plan_minutes=45)
        self.assertEqual(self._row(self.first.id).plan_minutes, 45)
        self.assertEqual(self._row(self.second.id).plan_minutes, 0)

    def test_an_old_days_shared_plan_no_longer_backs_a_logged_entrys_row(self) -> None:
        # A task+day Plan still exists for forward-planning purposes, but
        # once something's logged it no longer feeds a row's figure.
        self.store.set_plan(self.task.id, self.date, 90)
        self.assertEqual(self._row(self.first.id).plan_minutes, 0)
        self.assertEqual(self._row(self.second.id).plan_minutes, 0)

    def test_placeholder_row_still_reads_the_day_level_plan(self) -> None:
        bare = self.store.add_task(
            self.store.db.project_of_task(self.task.id).id, "Nothing logged yet", True
        )
        self.store.set_plan(bare.id, self.date, 90)
        row = next(r for r in self.store.day_rows(self.date) if r.context.task.id == bare.id)
        self.assertTrue(row.is_placeholder)
        self.assertEqual(row.plan_minutes, 90)


class DayRowsAndGrouping(unittest.TestCase):
    """Row identity on the Day tab: one row per Entry, one placeholder per
    not-yet-logged task, arranged per the four view modes."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "t.json")
        self.store.load()
        client = self.store.add_client("Acme Ltd")
        self.project = self.store.add_project(client.id, "Platform Migration", "K-1", True)
        self.phase = self.store.add_task(self.project.id, "Cutover", True)
        self.child, _ = self.store.add_subtask(self.phase.id, "Draft runbook", True)
        self.plain = self.store.add_task(self.project.id, "Standalone task", True)
        self.date = "2026-10-19"

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_a_task_with_no_entries_yet_gets_one_placeholder_row(self) -> None:
        self.store.set_plan(self.plain.id, self.date, 90)
        self.store.set_plan_note(self.plain.id, self.date, "Getting started")
        rows = self.store.day_rows(self.date)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row.is_placeholder)
        self.assertTrue(row.is_first_for_task)
        self.assertEqual(row.id, f"task:{self.plain.id}")
        self.assertEqual(row.note, "Getting started")
        self.assertEqual(row.plan_minutes, 90)

    def test_each_entry_becomes_its_own_row_and_only_the_first_carries_controls(self) -> None:
        self.store.add_entry(self.plain.id, self.date, 30, "First stint")
        self.store.add_entry(self.plain.id, self.date, 20, "Second stint")
        rows = self.store.day_rows(self.date)
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0].is_placeholder)
        self.assertEqual(rows[0].note, "First stint")
        self.assertTrue(rows[0].is_first_for_task)
        self.assertEqual(rows[1].note, "Second stint")
        self.assertFalse(rows[1].is_first_for_task)

    def test_a_leaf_task_s_row_carries_its_phase_in_the_context(self) -> None:
        self.store.add_entry(self.child.id, self.date, 30, "Draft v1")
        row = self.store.day_rows(self.date)[0]
        self.assertEqual(row.context.phase_title, "Cutover")
        self.assertEqual(row.context.display_title, "Cutover \u203a Draft runbook")

    def test_manual_mode_is_a_single_flat_group_in_saved_order(self) -> None:
        self.store.add_entry(self.plain.id, self.date, 30, "Logged")
        self.store.add_entry(self.child.id, self.date, 20, "Also logged")
        plain_row = next(r for r in self.store.day_rows(self.date) if r.context.task.id == self.plain.id)
        child_row = next(r for r in self.store.day_rows(self.date) if r.context.task.id == self.child.id)
        self.store.set_day_order(self.date, [child_row.id, plain_row.id])

        groups = self.store.grouped_day_rows(self.date)
        self.assertEqual(len(groups), 1)
        heading, rows = groups[0]
        self.assertEqual(heading, "")
        self.assertEqual([r.id for r in rows], [child_row.id, plain_row.id])

    def test_manual_mode_appends_an_unseen_row_to_the_end(self) -> None:
        self.store.add_entry(self.plain.id, self.date, 30, "Logged")
        plain_row = next(r for r in self.store.day_rows(self.date) if r.context.task.id == self.plain.id)
        self.store.set_day_order(self.date, [plain_row.id])

        # A brand new row shows up after the order was saved.
        self.store.add_entry(self.child.id, self.date, 20, "New arrival")
        _heading, rows = self.store.grouped_day_rows(self.date)[0]
        self.assertEqual(rows[-1].context.task.id, self.child.id)

    def test_project_mode_merges_everything_under_one_project_heading(self) -> None:
        self.store.add_entry(self.plain.id, self.date, 30)
        self.store.add_entry(self.child.id, self.date, 20)
        self.store.patch_settings(day_grouping="project")
        groups = self.store.grouped_day_rows(self.date)
        self.assertEqual(len(groups), 1)
        heading, rows = groups[0]
        self.assertIn("Platform Migration", heading)
        self.assertEqual(len(rows), 2)

    def test_phase_mode_merges_a_phase_s_children_but_keeps_other_tasks_separate(self) -> None:
        other_child, _ = self.store.add_subtask(self.phase.id, "Second child", True)
        self.store.add_entry(self.child.id, self.date, 20)
        self.store.add_entry(other_child.id, self.date, 10)
        self.store.add_entry(self.plain.id, self.date, 30)
        self.store.patch_settings(day_grouping="phase")
        groups = dict(self.store.grouped_day_rows(self.date))
        self.assertEqual(len(groups), 2)
        phase_heading = next(h for h in groups if "Cutover" in h)
        self.assertEqual(len(groups[phase_heading]), 2)

    def test_task_mode_keeps_every_leaf_task_on_its_own_heading(self) -> None:
        other_child, _ = self.store.add_subtask(self.phase.id, "Second child", True)
        self.store.add_entry(self.child.id, self.date, 20)
        self.store.add_entry(other_child.id, self.date, 10)
        self.store.patch_settings(day_grouping="task")
        groups = self.store.grouped_day_rows(self.date)
        self.assertEqual(len(groups), 2)

    def test_nudging_a_specific_entry_leaves_its_sibling_alone(self) -> None:
        self.store.add_entry(self.plain.id, self.date, 30, "A")
        self.store.add_entry(self.plain.id, self.date, 20, "B")
        target = self.store.entries_for(self.plain.id, self.date)[0]
        self.store.adjust_entry_minutes(target.id, 15)
        entries = self.store.entries_for(self.plain.id, self.date)
        self.assertEqual(sorted(e.minutes for e in entries), [20, 45])

    def test_nudging_an_entry_down_past_zero_floors_at_zero_without_removing_it(self) -> None:
        self.store.add_entry(self.plain.id, self.date, 10, "Tiny")
        entry = self.store.entries_for(self.plain.id, self.date)[0]
        self.store.adjust_entry_minutes(entry.id, -15)
        entries = self.store.entries_for(self.plain.id, self.date)
        self.assertEqual(len(entries), 1, "the entry should stay, just at 0m, not disappear")
        self.assertEqual(entries[0].minutes, 0)
        self.assertEqual(entries[0].note, "Tiny", "and keep everything else about it")

    def test_adjust_entry_down_past_zero_also_floors_rather_than_removing(self) -> None:
        # Same as ``adjust_entry_minutes`` above, but through the task+date
        # entry point the +/- chips use before a row has an entry of its own
        # to nudge by id.
        self.store.adjust_entry(self.plain.id, self.date, 10, "Tiny")
        self.store.adjust_entry(self.plain.id, self.date, -15)
        entries = self.store.entries_for(self.plain.id, self.date)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].minutes, 0)

    def test_add_entry_accepts_an_explicit_zero(self) -> None:
        # "+ log another entry" starts one at 0m on purpose, left for its own
        # Actual Hours picker to fill in — only a negative figure is nonsense.
        self.store.add_entry(self.plain.id, self.date, 0, "Just started")
        entries = self.store.entries_for(self.plain.id, self.date)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].minutes, 0)
        self.assertEqual(entries[0].note, "Just started")

    def test_add_entry_rejects_negative_minutes(self) -> None:
        self.store.add_entry(self.plain.id, self.date, -10)
        self.assertEqual(self.store.entries_for(self.plain.id, self.date), [])


class RecurringNotes(unittest.TestCase):
    """Several standing commitments sharing one task's billing bucket, each
    on its own schedule — see ``models.RecurringNote``."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "t.json")
        self.store.load()
        client = self.store.add_client("Acme Ltd")
        self.project = self.store.add_project(client.id, "Platform Migration", "K-1", True)
        self.task = self.store.add_task(self.project.id, "App Cloud", True)
        # A Monday.
        self.monday = "2026-10-19"
        self.tuesday = "2026-10-20"

    def tearDown(self) -> None:
        self.dir.cleanup()

    def _weekday_rule(self, weekdays: list[int], minutes: int) -> Recurrence:
        return Recurrence(frequency="weekly", weekdays=weekdays, minutes=minutes, start=self.monday)

    def test_two_notes_due_the_same_day_each_get_their_own_row(self) -> None:
        self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.add_recurring_note(self.task.id, "Monday admin", 60, self._weekday_rule([0], 60))
        rows = self.store.day_rows(self.monday)
        self.assertEqual(len(rows), 2)
        notes = sorted(r.note for r in rows)
        self.assertEqual(notes, ["Daily stand-up", "Monday admin"])
        self.assertTrue(all(r.is_placeholder for r in rows))
        # Only one row per task is ever marked first, regardless of mode.
        self.assertEqual(sum(r.is_first_for_task for r in rows), 1)

    def test_only_the_note_due_that_weekday_shows_up(self) -> None:
        self.store.add_recurring_note(self.task.id, "Monday admin", 60, self._weekday_rule([0], 60))
        rows = self.store.day_rows(self.tuesday)
        self.assertEqual(rows, [])

    def test_logging_one_note_s_occurrence_stops_its_placeholder_without_hiding_its_sibling(
        self,
    ) -> None:
        standup = self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.add_recurring_note(self.task.id, "Monday admin", 60, self._weekday_rule([0], 60))
        self.store.add_entry(self.task.id, self.monday, 30, "Daily stand-up", standup.id)

        rows = self.store.day_rows(self.monday)
        self.assertEqual(len(rows), 2)
        logged = next(r for r in rows if not r.is_placeholder)
        pending = next(r for r in rows if r.is_placeholder)
        self.assertEqual(logged.note, "Daily stand-up")
        self.assertEqual(pending.note, "Monday admin")

    def test_a_pinned_plan_suppresses_every_recurring_note_that_day(self) -> None:
        self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.set_plan(self.task.id, self.monday, 90)
        rows = self.store.day_rows(self.monday)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, f"task:{self.task.id}")

    def test_planned_minutes_sum_across_notes_due_the_same_day(self) -> None:
        self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.add_recurring_note(self.task.id, "Monday admin", 60, self._weekday_rule([0], 60))
        self.assertEqual(self.store.planned_minutes(self.task.id, self.monday), 90)
        self.assertEqual(self.store.planned_minutes(self.task.id, self.tuesday), 30)

    def test_nudging_one_note_s_placeholder_never_tops_up_its_sibling(self) -> None:
        standup = self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        admin = self.store.add_recurring_note(self.task.id, "Monday admin", 60, self._weekday_rule([0], 60))
        row = next(r for r in self.store.day_rows(self.monday) if r.recurring_note_id == standup.id)
        self.store.adjust_entry(self.task.id, self.monday, 15, row.plan_note, row.recurring_note_id)

        entries = self.store.entries_for(self.task.id, self.monday)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].minutes, 15)
        self.assertEqual(entries[0].recurring_note_id, standup.id)
        # The other note's placeholder is unaffected and still pending.
        rows = self.store.day_rows(self.monday)
        pending = [r for r in rows if r.is_placeholder]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].recurring_note_id, admin.id)

    def test_starting_the_timer_from_a_note_s_row_tags_the_entry_on_stop(self) -> None:
        note = self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.start_timer(self.task.id, note=note.note, recurring_note_id=note.id)
        self.store.db.active_timer.started_at -= 60  # pretend a minute passed
        self.store.stop_timer()
        entries = self.store.entries_for(self.task.id, self.store.db.entries[0].date)
        self.assertEqual(entries[0].recurring_note_id, note.id)
        self.assertEqual(entries[0].note, "Daily stand-up")

    def test_a_note_s_internal_note_template_carries_onto_its_placeholder_and_entry(self) -> None:
        note = self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.update_recurring_note(note.id, internal_note="Keep it under 15 minutes")
        row = next(
            r for r in self.store.day_rows(self.monday) if r.recurring_note_id == note.id
        )
        self.assertEqual(row.internal_note, "Keep it under 15 minutes")

        # Logging today's occurrence — the same call the Day tab's own note
        # field makes to promote a placeholder (see
        # ``TodayView._commit_row_note``) — carries the template over onto
        # the new entry.
        self.store.add_entry(
            self.task.id, self.monday, row.plan_minutes, row.plan_note, note.id,
            row.plan_internal_note,
        )
        logged = self.store.entries_for(self.task.id, self.monday)[0]
        self.assertEqual(logged.internal_note, "Keep it under 15 minutes")

    def test_stopping_the_timer_tags_the_entry_with_its_note_s_internal_note_template(self) -> None:
        note = self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.update_recurring_note(note.id, internal_note="Keep it under 15 minutes")
        self.store.start_timer(self.task.id, note=note.note, recurring_note_id=note.id)
        self.store.db.active_timer.started_at -= 60
        self.store.stop_timer()
        entries = self.store.entries_for(self.task.id, self.store.db.entries[0].date)
        self.assertEqual(entries[0].internal_note, "Keep it under 15 minutes")

    def test_a_task_with_a_recurring_note_can_still_gain_a_subtask(self) -> None:
        # The note moves onto the new child instead of blocking it — see
        # ``Store.add_subtask`` and ``Store.relocate_entries``.
        note = self.store.add_recurring_note(self.task.id, "Daily stand-up", 30)
        self.assertTrue(self.store.can_add_subtask(self.task.id))
        child, problem = self.store.add_subtask(self.task.id, "Draft runbook", True)
        self.assertEqual(problem, "")
        self.assertEqual(self.store.recurring_notes_for_task(self.task.id), [])
        self.assertEqual(self.store.db.recurring_note(note.id).task_id, child.id)

    def test_deleting_the_task_is_refused_while_a_recurring_note_is_on_it(self) -> None:
        self.store.add_recurring_note(self.task.id, "Daily stand-up", 30)
        problem = self.store.delete_task(self.task.id)
        self.assertIn("recurring note", problem)

    def test_deleting_a_recurring_note_never_touches_entries_it_already_produced(self) -> None:
        note = self.store.add_recurring_note(self.task.id, "Daily stand-up", 30)
        self.store.add_entry(self.task.id, self.monday, 30, "Daily stand-up", note.id)
        self.store.delete_recurring_note(note.id)
        entries = self.store.entries_for(self.task.id, self.monday)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].minutes, 30)

    def test_deleting_a_note_s_entry_skips_the_occurrence_rather_than_reviving_the_placeholder(
        self,
    ) -> None:
        # The bug being fixed: deleting the entry a recurring note produced
        # used to just undo the logging, and since the note was still due
        # that day, its placeholder came straight back — making "I didn't
        # need this today" impossible to express by deleting.
        note = self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.add_entry(self.task.id, self.monday, 30, "Daily stand-up", note.id)
        entry = self.store.entries_for(self.task.id, self.monday)[0]

        self.store.delete_entry(entry.id)

        self.assertEqual(self.store.entries_for(self.task.id, self.monday), [])
        self.assertEqual(
            self.store.day_rows(self.monday), [], "the task has no other reason to show that day"
        )
        # Nothing else about the note is touched: it still comes back
        # tomorrow, and still exists at all.
        self.assertEqual(len(self.store.day_rows(self.tuesday)), 1)
        self.assertEqual(len(self.store.recurring_notes_for_task(self.task.id)), 1)

    def test_skipping_a_note_directly_dismisses_its_still_unlogged_placeholder(self) -> None:
        note = self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.skip_recurring_note(note.id, self.monday)
        self.assertEqual(self.store.day_rows(self.monday), [])
        self.assertEqual(self.store.planned_minutes(self.task.id, self.monday), 0)
        # A skip is for one day only.
        self.assertEqual(len(self.store.day_rows(self.tuesday)), 1)

    def test_skipping_one_note_leaves_its_sibling_due_the_same_day_alone(self) -> None:
        standup = self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.add_recurring_note(self.task.id, "Monday admin", 60, self._weekday_rule([0], 60))
        self.store.skip_recurring_note(standup.id, self.monday)

        rows = self.store.day_rows(self.monday)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].recurring_note_id != standup.id, True)

    def test_a_task_only_relevant_via_a_skipped_note_drops_off_the_day_entirely(self) -> None:
        note = self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.skip_recurring_note(note.id, self.monday)
        self.assertNotIn(
            self.task.id, [c.task.id for c in self.store.contexts_for_date(self.monday)]
        )

    def test_a_task_stays_relevant_despite_a_skipped_note_if_something_else_keeps_it_there(
        self,
    ) -> None:
        note = self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.set_plan_note(self.task.id, self.monday, "Something unrelated to log")
        self.store.skip_recurring_note(note.id, self.monday)
        self.assertIn(
            self.task.id, [c.task.id for c in self.store.contexts_for_date(self.monday)]
        )

    def test_skipping_is_one_undo_step_and_reverses_cleanly(self) -> None:
        note = self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        depth_before = len(self.store._undo_stack)
        self.store.skip_recurring_note(note.id, self.monday)
        self.assertEqual(len(self.store._undo_stack), depth_before + 1)
        self.store.undo()
        self.assertEqual(len(self.store.day_rows(self.monday)), 1)

    def test_skipping_the_same_occurrence_twice_is_a_no_op(self) -> None:
        note = self.store.add_recurring_note(
            self.task.id, "Daily stand-up", 30, self._weekday_rule([0, 1, 2, 3, 4], 30)
        )
        self.store.skip_recurring_note(note.id, self.monday)
        depth_before = len(self.store._undo_stack)
        self.store.skip_recurring_note(note.id, self.monday)
        self.assertEqual(len(self.store._undo_stack), depth_before)
        self.assertEqual(len(self.store.db.skipped_notes), 1)


class MonthDateHelpers(unittest.TestCase):
    """The calendar-grid math behind the Month tab."""

    def test_start_of_month_lands_on_the_1st(self) -> None:
        self.assertEqual(start_of_month("2026-10-19"), "2026-10-01")
        self.assertEqual(start_of_month("2026-10-01"), "2026-10-01")

    def test_add_months_steps_forward_and_back_across_year_boundaries(self) -> None:
        self.assertEqual(add_months("2026-10-01", 1), "2026-11-01")
        self.assertEqual(add_months("2026-12-01", 1), "2027-01-01")
        self.assertEqual(add_months("2026-10-01", -1), "2026-09-01")
        self.assertEqual(add_months("2027-01-01", -1), "2026-12-01")

    def test_month_label_reads_as_a_month_and_year(self) -> None:
        self.assertEqual(month_label("2026-10-19"), "October 2026")

    def test_is_same_month_only_matches_year_and_month(self) -> None:
        self.assertTrue(is_same_month("2026-10-01", "2026-10-19"))
        self.assertFalse(is_same_month("2026-09-30", "2026-10-19"))
        self.assertFalse(is_same_month("2025-10-19", "2026-10-19"))

    def test_month_weeks_is_full_monday_start_weeks_covering_the_month(self) -> None:
        weeks = month_weeks("2026-10-19")
        # October 2026 runs Thu 1st to Sat 31st: five calendar rows needed.
        self.assertEqual(len(weeks), 5)
        for week in weeks:
            self.assertEqual(len(week), 7)
        self.assertEqual(weeks[0][0], "2026-09-28", "the first row should start on a Monday")
        self.assertEqual(weeks[-1][-1], "2026-11-01", "the last row should end on a Sunday")
        flat = [d for week in weeks for d in week]
        self.assertIn("2026-10-01", flat)
        self.assertIn("2026-10-31", flat)
        in_month = [d for d in flat if is_same_month(d, "2026-10-01")]
        self.assertEqual(len(in_month), 31)

    def test_month_weeks_handles_a_month_that_starts_on_monday(self) -> None:
        # June 2026 starts on a Monday, so the grid shouldn't pad a leading
        # week of entirely-May days.
        weeks = month_weeks("2026-06-15")
        self.assertEqual(weeks[0][0], "2026-06-01")


class WindowChrome(unittest.TestCase):
    def test_tk_colours_become_win32_colorrefs(self) -> None:
        # COLORREF puts blue in the high byte, the reverse of #rrggbb.
        self.assertEqual(dwm._colorref("#000000"), 0x000000)
        self.assertEqual(dwm._colorref("#ffffff"), 0xFFFFFF)
        self.assertEqual(dwm._colorref("#34c77b"), 0x7BC734)  # the billable green

    def test_unknown_window_is_reported_rather_than_raising(self) -> None:
        # An older Windows refuses the attribute the same way: the badge then
        # keeps its square Tk border instead of the app falling over.
        self.assertFalse(dwm.corners(0, dwm.ROUNDED))


class BookableLines(unittest.TestCase):
    """The rolled-up rows behind the Export tab's click-to-copy table."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / "t.json")
        self.store.load()

        client = self.store.add_client("Acme Ltd")
        project = self.store.add_project(client.id, "Platform Migration", "K-10234", True)
        self.task = self.store.add_task(project.id, "Cutover rehearsal", True)
        self.other = self.store.add_task(project.id, "Runbook review", True)
        self.date = "2026-10-19"

    def tearDown(self) -> None:
        self.dir.cleanup()

    def _lines(self, date: str | None = None) -> list[bookings.Line]:
        return bookings.collect(self.store.db, [date or self.date])

    def test_same_task_on_one_day_becomes_one_line(self) -> None:
        self.store.add_entry(self.task.id, self.date, 45, "Dry run with their DBA")
        self.store.add_entry(self.task.id, self.date, 30, "Fixed the rollback script")

        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].minutes, 75)
        self.assertEqual(
            lines[0].note_text(), "Dry run with their DBA | Fixed the rollback script"
        )

    def test_different_tasks_stay_separate(self) -> None:
        self.store.add_entry(self.task.id, self.date, 45, "Dry run")
        self.store.add_entry(self.other.id, self.date, 20, "Read it back")
        self.assertEqual(len(self._lines()), 2)

    def test_the_same_task_on_another_day_stays_separate(self) -> None:
        self.store.add_entry(self.task.id, self.date, 45, "Dry run")
        self.store.add_entry(self.task.id, "2026-10-20", 60, "Second pass")

        lines = bookings.collect(self.store.db, [self.date, "2026-10-20"])
        self.assertEqual([l.minutes for l in lines], [45, 60])

    def test_a_note_repeated_in_a_day_is_only_joined_once(self) -> None:
        self.store.add_entry(self.task.id, self.date, 30, "Cutover rehearsal")
        self.store.add_entry(self.task.id, self.date, 30, "cutover rehearsal")
        self.assertEqual(self._lines()[0].note_text(), "Cutover rehearsal")

    def test_entries_without_notes_leave_no_empty_separator(self) -> None:
        self.store.add_entry(self.task.id, self.date, 30, "Dry run")
        self.store.add_entry(self.task.id, self.date, 30, "")
        self.assertEqual(self._lines()[0].note_text(), "Dry run")

    def test_hours_are_the_decimal_figure_a_timesheet_wants(self) -> None:
        self.store.add_entry(self.task.id, self.date, 128)
        line = self._lines()[0]
        self.assertEqual(line.hours(15), "2.25")  # 128 rounds up to 135
        self.assertEqual(line.hours(0), "2.13")  # unrounded

    def test_a_non_billable_project_overrides_a_billable_task(self) -> None:
        self.store.update_project(self.task.project_id, default_billable=False)
        self.store.add_entry(self.task.id, self.date, 30)
        line = self._lines()[0]
        self.assertFalse(line.billable)
        self.assertTrue(self.store.db.task(self.task.id).billable, "the task's own flag is untouched")

    def test_a_leaf_task_s_line_is_prefixed_with_its_phase(self) -> None:
        child, _ = self.store.add_subtask(self.task.id, "Draft runbook", True)
        self.store.add_entry(child.id, self.date, 30)
        line = self._lines()[0]
        self.assertEqual(line.phase_title, "Cutover rehearsal")
        self.assertEqual(line.display_title, "Cutover rehearsal \u203a Draft runbook")

    def test_a_plain_task_s_display_title_has_no_phase_prefix(self) -> None:
        self.store.add_entry(self.task.id, self.date, 30)
        line = self._lines()[0]
        self.assertEqual(line.phase_title, "")
        self.assertEqual(line.display_title, "Cutover rehearsal")

    def test_lines_that_round_to_nothing_are_still_listed(self) -> None:
        # Unlike the Markdown export, the table must not quietly drop a row:
        # the hours field is the thing being keyed in, zero or not.
        self.store.add_entry(self.task.id, self.date, 3)
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].hours(15), "0.00")

    def test_internal_note_never_rolls_up_into_a_booking_line(self) -> None:
        self.store.add_entry(
            self.task.id, self.date, 30, "Dry run", internal_note="Not for the client's eyes"
        )
        line = self._lines()[0]
        self.assertEqual(line.note_text(), "Dry run")
        self.assertNotIn("Not for the client's eyes", line.note_text())


class ThemeTests(unittest.TestCase):
    """``theme.use`` and its "system" mode — see ``theme.system_palette``."""

    def setUp(self) -> None:
        # Every other test (and the app itself) leaves a palette bound as a
        # side effect of importing/using this module; restore it after.
        self._mode = theme.MODE
        self.addCleanup(theme.use, self._mode)

    def test_a_real_palette_name_is_returned_unchanged(self) -> None:
        self.assertEqual(theme.use("light"), "light")
        self.assertEqual(theme.ACTIVE, "light")
        self.assertEqual(theme.MODE, "light")
        self.assertEqual(theme.TEXT, theme.PALETTES["light"]["TEXT"])

    def test_an_unknown_name_falls_back_to_the_default_and_says_so(self) -> None:
        self.assertEqual(theme.use("not-a-real-palette"), theme.DEFAULT)
        self.assertEqual(theme.ACTIVE, theme.DEFAULT)
        self.assertEqual(theme.MODE, theme.DEFAULT)

    def test_system_mode_is_kept_on_record_regardless_of_what_it_resolves_to(self) -> None:
        self.assertEqual(theme.use("system"), "system")
        self.assertEqual(theme.MODE, "system")
        # Whatever the OS actually reports, it has to be a real, usable
        # palette — "system" itself must never leak into ACTIVE, or every
        # colour token lookup across the app would raise a KeyError.
        self.assertIn(theme.ACTIVE, theme.PALETTES)

    def test_system_palette_matches_a_direct_registry_read(self) -> None:
        import winreg

        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            light, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
        except OSError:
            self.skipTest("this machine's registry doesn't have the key")
        expected = "light" if light else "dark"
        self.assertEqual(theme.system_palette(), expected)


class SingleInstance(unittest.TestCase):
    """The Win32 mutex that stops a second launch opening a duplicate
    overlay — see ``timesheet/singleton.py``. Not ``focus_existing()``
    itself: that needs a real window on screen with the app's own title,
    which is covered by ``gui_smoke.py`` instead."""

    def setUp(self) -> None:
        # Isolated per test, same as everywhere else TIMESHEET_HOME matters,
        # so one test claiming a mutex can't make another see it as taken.
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self._previous_home = os.environ.get("TIMESHEET_HOME")
        os.environ["TIMESHEET_HOME"] = self.dir.name
        self.addCleanup(self._restore_home)

    def _restore_home(self) -> None:
        if self._previous_home is None:
            os.environ.pop("TIMESHEET_HOME", None)
        else:
            os.environ["TIMESHEET_HOME"] = self._previous_home

    def test_mutex_name_is_deterministic(self) -> None:
        self.assertEqual(singleton._mutex_name(), singleton._mutex_name())

    def test_different_homes_get_different_mutex_names(self) -> None:
        name = singleton._mutex_name()
        with tempfile.TemporaryDirectory() as other:
            os.environ["TIMESHEET_HOME"] = other
            self.assertNotEqual(singleton._mutex_name(), name)

    def test_first_claim_succeeds_second_reports_already_running(self) -> None:
        # Win32 reports ERROR_ALREADY_EXISTS even when it's this same process
        # asking twice — a mutex by that name already exists either way —
        # which is exactly what makes this testable without a second process.
        self.assertFalse(singleton.already_running())
        self.assertTrue(singleton.already_running())

    def test_a_different_data_folder_is_not_blocked_by_this_ones_mutex(self) -> None:
        self.assertFalse(singleton.already_running())
        with tempfile.TemporaryDirectory() as other:
            os.environ["TIMESHEET_HOME"] = other
            self.assertFalse(singleton.already_running())

    def test_mutex_name_is_derived_from_the_resolved_home(self) -> None:
        expected = hashlib.sha1(str(paths.home()).encode("utf-8")).hexdigest()
        self.assertIn(expected, singleton._mutex_name())


class HotkeyShortcuts(unittest.TestCase):
    """Accelerator parsing/formatting and the editable-shortcuts storage —
    not ``HotkeyListener.start()`` itself, which needs a real Win32 message
    loop and is exercised by hand instead."""

    def test_accelerators_format_for_display(self) -> None:
        self.assertEqual(hotkeys.format_accelerator("ctrl+alt+t"), "Ctrl+Alt+T")
        self.assertEqual(hotkeys.format_accelerator("alt+s"), "Alt+S")

    def test_settings_default_to_no_hotkey_overrides(self) -> None:
        self.assertEqual(Settings().hotkeys, {})

    def test_hotkey_overrides_round_trip_through_storage(self) -> None:
        db = Database()
        db.settings.hotkeys["collapse"] = "ctrl+alt+p"
        restored = Database.from_dict(db.to_dict())
        self.assertEqual(restored.settings.hotkeys, {"collapse": "ctrl+alt+p"})

    def test_bind_can_be_looked_up_and_replaced_by_name(self) -> None:
        # Exercises the bookkeeping ``bind``/``rebind`` do without touching
        # Win32 — nothing here is registered until ``start()`` runs.
        listener = hotkeys.HotkeyListener()
        listener.bind("collapse", "ctrl+alt+t", lambda: None)
        listener.bind("pin", "ctrl+alt+a", lambda: None)
        self.assertEqual(listener._accelerators, [(1, "ctrl+alt+t"), (2, "ctrl+alt+a")])

        listener.rebind("collapse", "ctrl+alt+p")
        self.assertEqual(listener._accelerators, [(1, "ctrl+alt+p"), (2, "ctrl+alt+a")])

    def test_rebinding_an_unknown_name_is_a_no_op(self) -> None:
        listener = hotkeys.HotkeyListener()
        listener.bind("collapse", "ctrl+alt+t", lambda: None)
        listener.rebind("not-a-real-action", "ctrl+alt+p")
        self.assertEqual(listener._accelerators, [(1, "ctrl+alt+t")])

    def test_pausing_a_never_started_listener_is_a_no_op(self) -> None:
        # ``pause``/``resume`` are exercised live by hand (see the class
        # docstring) — this just covers the bookkeeping that doesn't touch
        # Win32: pausing something never started shouldn't raise or leave
        # it looking "resumed" when it was never running to begin with.
        listener = hotkeys.HotkeyListener()
        listener.bind("collapse", "ctrl+alt+t", lambda: None)
        listener.pause()
        self.assertIsNone(listener._thread)

    def test_rebind_while_paused_leaves_resuming_to_the_caller(self) -> None:
        # The Setup tab's capture flow pauses before it ever calls rebind
        # (see ``timesheet.ui.setup._capture_hotkey``) — rebind seeing an
        # already-paused listener should swap the entry without starting a
        # real Win32 registration out from under a capture still in
        # progress.
        listener = hotkeys.HotkeyListener()
        listener.bind("collapse", "ctrl+alt+t", lambda: None)
        listener.pause()
        listener.rebind("collapse", "ctrl+alt+p")
        self.assertEqual(listener._accelerators, [(1, "ctrl+alt+p")])
        self.assertIsNone(listener._thread, "rebind shouldn't resume a listener it didn't pause itself")


if __name__ == "__main__":
    unittest.main()
