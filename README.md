# Timesheet Logger

An always-on-top Windows overlay for planning your week, tracking time against client
projects, and exporting a Markdown summary that Cursor turns into customer-facing
timesheet notes.

```
npm install
npm run dev
```

> **On the Datacom network, `npm install` needs two things first.** See
> [Corporate network notes](#corporate-network-notes) — TLS inspection and a Zscaler
> rule on `.gz` downloads both get in the way.

## The loop it's built around

1. **Plan** the week in the Week tab — hours per task per day, grouped by client and K-code.
2. **Track** during the day in the Today tab — start/stop timer, or type hours in after the fact.
3. **Export** a week (or a single day) to `exports/2026-W43.md`.
4. **Draft** the customer note by asking Cursor to work from that file. The Export tab has a
   "Copy Cursor prompt" button; `.cursor/rules/timesheet-note.mdc` tells Cursor the house style.

## Overlay behaviour

| Shortcut | Does |
| --- | --- |
| `Ctrl`+`Alt`+`T` | Show / hide the overlay |
| `Ctrl`+`Alt`+`A` | Toggle always-on-top |
| `Ctrl`+`Alt`+`S` | Stop the running timer, or resume the last task |

Closing the window parks it in the system tray — the timer keeps running. Quit properly
from the tray menu. The pin button in the title bar flips always-on-top, the `⇤` button
widens the window for week planning, and Setup has an opacity slider for when you want it
to sit quietly over whatever you're working in.

## Data model

- **Client** — may be flagged *internal* (e.g. Internal NCC), which defaults its work to non-billable.
- **Project** — belongs to a client and carries the **K-code** the hours get booked against.
- **Task** — belongs to a project, billable or not.
- **Plan** — intended minutes for a task on a day.
- **Entry** — minutes actually spent, from the timer or typed in, with a note.

Time is always stored to the exact minute. Rounding (default: nearest 15) is applied only
when exporting, so the underlying record stays honest.

## Where things live

| What | Where |
| --- | --- |
| Your data | `data/timesheet.json` |
| Window position | `data/window.json` |
| Generated notes | `exports/*.md` |

Both are gitignored by default. Delete those lines from `.gitignore` if you'd rather keep
your history version-controlled. Set `TIMESHEET_HOME` to put them somewhere else — a
packaged build defaults to `Documents\TimesheetLogger`.

## Writing a note by hand

The export is readable on its own. **Timesheet lines** is the table of hours to key in;
**Work carried out, by project** is the narrative source, already grouped the way a customer
note is written.

## Corporate network notes

Two separate things block `npm install` on this machine.

**1. TLS inspection.** The proxy re-signs HTTPS with a root CA that Node doesn't trust, so
npm fails with `UNABLE_TO_GET_ISSUER_CERT_LOCALLY` — after retrying silently for minutes.
Node 22.15+ can read the Windows certificate store instead:

```powershell
setx NODE_OPTIONS "--use-system-ca"   # once, then reopen the terminal
```

**2. Zscaler blocks `.gz` downloads.** Tarballs from `registry.npmjs.org` return 403 with a
"Are you sure you want to visit this site? You are downloading gz filetype" caution page.
npm can't click Continue, so every package fails. Options, best first:

- Get IT to exempt `registry.npmjs.org` from the gz-download caution rule.
- Point npm at an internal Artifactory/Nexus mirror if one exists.
- Run `npm install` once off the corporate network; `node_modules/` is portable afterwards.

PyPI is unaffected — wheels are zips, not gzip — which is why Python tooling works here.

## Packaging

```
npm run package   # unpacked build in release/win-unpacked
npm run dist      # NSIS installer in release/
```
