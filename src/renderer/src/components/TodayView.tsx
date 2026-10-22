import { useState } from 'react'
import { addDays, formatMinutes, prettyDate, today } from '@shared/dates'
import { useStore } from '../state'
import { dayTotals, tasksForDate } from '../selectors'
import TaskRow from './TaskRow'
import AddTaskPanel from './AddTaskPanel'

export default function TodayView(): JSX.Element {
  const { db, dispatch } = useStore()
  const [date, setDate] = useState(today())

  const tasks = tasksForDate(db, date)
  const { total, billable } = dayTotals(db, date)
  const target = db.settings.dailyTargetMinutes
  const progress = target > 0 ? Math.min(100, (total / target) * 100) : 0

  return (
    <div className="view">
      <div className="daynav">
        <button className="iconbtn" onClick={() => setDate(addDays(date, -1))} title="Previous day">
          ‹
        </button>
        <button className="daynav__label" onClick={() => setDate(today())} title="Jump to today">
          {prettyDate(date)}
        </button>
        <button className="iconbtn" onClick={() => setDate(addDays(date, 1))} title="Next day">
          ›
        </button>
      </div>

      <div className="progress">
        <div className="progress__bar">
          <div className="progress__fill" style={{ width: `${progress}%` }} />
        </div>
        <div className="progress__labels">
          <span>
            <strong>{formatMinutes(total)}</strong> of {formatMinutes(target)}
          </span>
          <span className="muted">{formatMinutes(billable)} billable</span>
        </div>
      </div>

      {tasks.length === 0 ? (
        <p className="empty">Nothing planned for this day yet. Add a task to get started.</p>
      ) : (
        <div className="tasklist">
          {tasks.map((context) => (
            <TaskRow key={context.task.id} context={context} date={date} />
          ))}
        </div>
      )}

      <AddTaskPanel date={date} />

      <label className="field field--block">
        <span className="field__label">Day note — context that isn&apos;t tied to one task</span>
        <textarea
          rows={3}
          placeholder="Client call ran long; blocked on access to the staging environment…"
          defaultValue={db.dayNotes[date] ?? ''}
          key={date}
          onBlur={(e) => dispatch({ type: 'dayNote/set', date, text: e.target.value })}
        />
      </label>
    </div>
  )
}
