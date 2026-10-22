"""Fills a TIMESHEET_HOME with a plausible week, for screenshots and demos."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timesheet import paths
from timesheet.dates import add_days, start_of_week, today
from timesheet.store import Store


def main() -> None:
    store = Store(paths.data_file())
    store.load()

    acme = store.add_client("Acme Ltd")
    northwind = store.add_client("Northwind Health")
    internal = store.db.clients[0]

    migration = store.add_project(acme.id, "Platform Migration", "K-10234", True)
    support = store.add_project(acme.id, "Managed Support", "K-10311", True)
    portal = store.add_project(northwind.id, "Patient Portal", "K-20876", True)
    admin = store.db.projects[0]

    runbook = store.add_task(migration.id, "Cutover runbook", True)
    tests = store.add_task(migration.id, "Fix staging integration tests", True)
    triage = store.add_task(support.id, "Ticket triage", True)
    design = store.add_task(portal.id, "Appointment booking design review", True)
    upkeep = store.add_task(admin.id, "Timesheet & admin", False)

    monday = start_of_week(today())
    days = [add_days(monday, i) for i in range(5)]
    # Only log actuals up to today, so the demo looks like a week in progress.
    elapsed = min(days.index(today()) + 1 if today() in days else 5, 5)

    plan = {
        runbook.id: [180, 120, 0, 0, 60],
        tests.id: [120, 180, 240, 60, 0],
        triage.id: [60, 60, 60, 60, 60],
        design.id: [0, 0, 120, 180, 120],
        upkeep.id: [30, 30, 30, 30, 60],
    }
    for task_id, minutes in plan.items():
        for date, value in zip(days, minutes):
            if value:
                store.set_plan(task_id, date, value)

    notes = {
        runbook.id: "Drafted the cutover sequence and walked the client's platform team through it",
        tests.id: "Traced three failing integration tests to a stale staging credential and fixed them",
        triage.id: "Triaged the weekend ticket queue and closed four P3s",
        design.id: "Reviewed the booking flow against the accessibility criteria",
        upkeep.id: "",
    }

    actual = {
        runbook.id: [165, 140, 0, 0, 55],
        tests.id: [130, 190, 95, 75, 40],
        triage.id: [45, 70, 55, 50, 35],
        design.id: [0, 0, 110, 165, 95],
        upkeep.id: [25, 20, 35, 15, 20],
    }
    for task_id, minutes in actual.items():
        for date, value in zip(days[:elapsed], minutes[:elapsed]):
            if value:
                store.add_entry(task_id, date, value, notes[task_id])

    store.set_day_note(days[2], "Client pushed the cutover rehearsal to next week")
    print(f"Seeded demo data into {paths.data_file()}")


if __name__ == "__main__":
    main()
