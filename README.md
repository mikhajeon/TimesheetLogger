# Timesheet Logger

An always-on-top Windows overlay for planning your week, tracking time against client
projects, and exporting a Markdown summary that Cursor turns into customer-facing
timesheet notes.

```
python run.py
```

That's the whole setup. **There is nothing to install** — the app uses only the Python
standard library (`tkinter`, `ctypes`, `json`, `dataclasses`), so it needs no package
downloads and no approval for third-party dependencies.

## The loop it's built around

1. **Plan** the week in the Week tab — hours per task per day, grouped by client and K-code.
2. **Track** during the day in the Today tab — start/stop timer, or type hours in after the fact.
3. **Export** a week (or a single day) to `exports/2026-W43.md`.
4. **Draft** the customer note by asking Cursor to work from that file. The Export tab has a
   "Copy Cursor prompt" button, and `.cursor/rules/timesheet-note.mdc` tells Cursor the house style.

## Overlay behaviour

| Shortcut | Does |
| --- | --- |
| `Ctrl`+`Alt`+`T` | Show / hide the overlay |
| `Ctrl`+`Alt`+`A` | Toggle always-on-top |
| `Ctrl`+`Alt`+`S` | Stop the running timer, or resume the last task |
| `Esc` | Collapse to the floating pill |

The window is frameless — drag it by its header. The pin button flips always-on-top, `⇤`
widens it for week planning, and Setup has an opacity slider for when you want it to sit
quietly over whatever you're working in.

`–` collapses the overlay to a thin pill that still shows the running timer; double-click
it to expand. There's no system-tray icon on purpose: tray support would mean a third-party
package, and the pill does the same job without one.

## Data model

- **Client** — may be flagged *internal* (e.g. Internal NCC), which defaults its work to non-billable.
- **Project** — belongs to a client and carries the **K-code** the hours get booked against.
- **Task** — belongs to a project, billable or not.
- **Plan** — intended minutes for a task on a day.
- **Entry** — minutes actually spent, from the timer or typed in, with a note.

Time is always stored to the exact minute. Rounding (default: nearest 15) is applied only
when exporting, so the underlying record stays honest — the export footer always shows the
unrounded total so you can see what the rounding did.

Durations accept `1.5`, `90m`, `1h30`, `1h 30m`, `1:30` and `:45` wherever you type one.

## Where things live

| What | Where |
| --- | --- |
| Your data | `data/timesheet.json` |
| Window position | `data/window.json` |
| Generated notes | `exports/*.md` |

Both are gitignored by default; delete those lines from `.gitignore` if you'd rather keep
your history version-controlled. Set `TIMESHEET_HOME` to put them somewhere else.

A file that fails to parse is moved aside as `timesheet.corrupt-<timestamp>.json` rather
than being overwritten, so a bad write can't cost you a week of tracking.

## Writing a note by hand

The export reads fine on its own. **Timesheet lines** is the table of hours to key in;
**Work carried out, by project** is the narrative source, already grouped per K-code the
way a customer note gets written.

## Development

```
python -m unittest discover -s tests   # domain logic: parsing, store, export
python tests/gui_smoke.py              # builds every tab and closes
python tests/seed_demo.py              # fills TIMESHEET_HOME with a plausible week
powershell -File tests/capture.ps1     # screenshots each tab into shots/
```

`timesheet/` splits into a GUI-free core (`models`, `store`, `dates`, `markdown`) and the
tkinter front end in `timesheet/ui/`. The core has no tkinter import, so it stays testable
without a desktop session.

### A note on tkinter

Two Tk behaviours cost real debugging time and are worth knowing before editing the UI:

- **Widget variables are only weakly referenced.** A `BooleanVar` created as a local is
  garbage-collected and its checkbox silently renders unchecked. Views park theirs in
  `self._vars`.
- **Setting a widget's initial value fires its callback.** An opacity slider that saves on
  change will re-enter the view that is still building it. Store writes that come from a
  widget callback are deferred to the idle queue (`Overlay.refresh`), and `patch_settings`
  ignores no-op writes.
