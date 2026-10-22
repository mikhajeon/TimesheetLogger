import { useMemo, useState } from 'react'
import { buildMarkdown } from '@shared/markdown'
import {
  addDays,
  isoWeekNumber,
  prettyDate,
  shortDate,
  startOfWeek,
  today,
  weekDates,
  weekLabel
} from '@shared/dates'
import { useStore } from '../state'

type Mode = 'week' | 'day'

export default function ExportView(): JSX.Element {
  const { db, paths } = useStore()
  const [mode, setMode] = useState<Mode>('week')
  const [anchor, setAnchor] = useState(() => today())
  const [status, setStatus] = useState<string | null>(null)
  const [savedPath, setSavedPath] = useState<string | null>(null)

  const { dates, title, filename } = useMemo(() => {
    if (mode === 'day') {
      return {
        dates: [anchor],
        title: `Timesheet — ${prettyDate(anchor)}`,
        filename: `${anchor}.md`
      }
    }
    const start = startOfWeek(anchor)
    return {
      dates: weekDates(start, true),
      title: `Timesheet — Week ${isoWeekNumber(start)} (${shortDate(start)} – ${shortDate(addDays(start, 6))})`,
      filename: `${weekLabel(start)}.md`
    }
  }, [mode, anchor])

  const rounding = db.settings.roundingMinutes
  const markdown = useMemo(
    () =>
      buildMarkdown(db, dates, {
        title,
        roundingLabel: rounding ? `rounded to the nearest ${rounding} min` : 'exact minutes'
      }),
    [db, dates, title, rounding]
  )

  const cursorPrompt = `Draft my timesheet customer notes from exports/${filename}.\n\nOne note per K-code, written for the client to read, in plain past tense. Keep each note to 1–3 sentences, cover what was delivered and why it mattered, and leave out internal chatter. Output the K-code, the hours, and the note together so I can paste each line straight into the timesheet.`

  function flash(message: string): void {
    setStatus(message)
    setTimeout(() => setStatus(null), 2500)
  }

  return (
    <div className="view">
      <div className="segmented">
        <button className={mode === 'week' ? 'seg seg--on' : 'seg'} onClick={() => setMode('week')}>
          Week
        </button>
        <button className={mode === 'day' ? 'seg seg--on' : 'seg'} onClick={() => setMode('day')}>
          Day
        </button>
      </div>

      <div className="daynav">
        <button className="iconbtn" onClick={() => setAnchor(addDays(anchor, mode === 'week' ? -7 : -1))}>
          ‹
        </button>
        <button className="daynav__label" onClick={() => setAnchor(today())}>
          {mode === 'week'
            ? `Week ${isoWeekNumber(startOfWeek(anchor))} · ${shortDate(startOfWeek(anchor))} – ${shortDate(addDays(startOfWeek(anchor), 6))}`
            : prettyDate(anchor)}
        </button>
        <button className="iconbtn" onClick={() => setAnchor(addDays(anchor, mode === 'week' ? 7 : 1))}>
          ›
        </button>
      </div>

      <div className="export__actions">
        <button
          className="btn btn--primary"
          onClick={() => {
            void window.api.writeExport(filename, markdown).then((filePath) => {
              setSavedPath(filePath)
              flash(`Saved ${filename}`)
            })
          }}
        >
          Save to exports/
        </button>
        <button className="btn" onClick={() => void window.api.copy(markdown).then(() => flash('Markdown copied'))}>
          Copy markdown
        </button>
        <button
          className="btn"
          onClick={() => void window.api.copy(cursorPrompt).then(() => flash('Cursor prompt copied'))}
          title="Paste this into Cursor chat after saving the export"
        >
          Copy Cursor prompt
        </button>
      </div>

      {status && <div className="flash">{status}</div>}

      {savedPath && (
        <button className="pathline" onClick={() => void window.api.reveal(savedPath)} title="Show in Explorer">
          {savedPath}
        </button>
      )}

      <pre className="preview">{markdown}</pre>

      <p className="hint">
        Exports land in <code>{paths.exportsDir}</code>. Save the file, then ask Cursor to draft the notes from it.
      </p>
    </div>
  )
}
