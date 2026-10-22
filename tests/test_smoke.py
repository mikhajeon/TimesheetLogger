"""End-to-end check of the domain layer: plan, track, export, reload.

Run with: python -m unittest discover tests
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timesheet.dates import format_minutes, parse_duration, start_of_week, week_dates, week_label
from timesheet.markdown import build_markdown
from timesheet.store import Store


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

    def test_corrupt_file_is_kept_aside(self) -> None:
        self.path.write_text("{ not json", "utf-8")
        store = Store(self.path)
        store.load()
        self.assertTrue(any(p.name.startswith("timesheet.corrupt") for p in self.path.parent.iterdir()))
        self.assertEqual(len(store.db.clients), 1)  # fell back to a fresh seed


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

        # 128 minutes rounds up to 135 (2.25h) at the default 15-minute
        # increment, and the exact figure still has to be visible somewhere.
        self.assertIn("| 2.25 |", out)
        self.assertIn("2h 28m", out)

    def test_rounding_setting_is_respected(self) -> None:
        self.store.patch_settings(rounding_minutes=0)
        out = build_markdown(self.store.db, [self.date], "Exact")
        self.assertIn("| 2.13 |", out)  # 128 minutes unrounded

    def test_week_label(self) -> None:
        self.assertEqual(week_label(start_of_week("2026-10-19")), "2026-W43")


if __name__ == "__main__":
    unittest.main()
