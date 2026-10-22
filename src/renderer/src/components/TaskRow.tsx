import { useState } from 'react'
import type { ISODate } from '@shared/types'
import { formatMinutes, parseDuration, toISODate } from '@shared/dates'
import { useStore, useNow } from '../state'
import { elapsedMinutes, loggedMinutes, plannedMinutes, type TaskContext } from '../selectors'

interface Props {
  context: TaskContext
  date: ISODate
}

export default function TaskRow({ context, date }: Props): JSX.Element {
  const { db, dispatch } = useStore()
  const now = useNow()
  const [expanded, setExpanded] = useState(false)
  const [draft, setDraft] = useState('')

  const { task, project, client } = context
  const timer = db.activeTimer
  const running = timer?.taskId === task.id && toISODate(new Date(timer.startedAt)) === date
  const live = running && timer ? elapsedMinutes(timer.startedAt, now) : 0
  const logged = loggedMinutes(db, task.id, date) + live
  const planned = plannedMinutes(db, task.id, date)
  const entries = db.entries.filter((e) => e.taskId === task.id && e.date === date)

  function addManual(): void {
    const minutes = parseDuration(draft)
    if (!minutes) return
    dispatch({ type: 'entry/add', taskId: task.id, date, minutes })
    setDraft('')
  }

  return (
    <div className={running ? 'taskrow taskrow--running' : 'taskrow'}>
      <div className="taskrow__main">
        <button
          className={running ? 'playbtn playbtn--stop' : 'playbtn'}
          title={running ? 'Stop timer' : 'Start timer'}
          onClick={() => dispatch(running ? { type: 'timer/stop' } : { type: 'timer/start', taskId: task.id })}
        >
          {running ? '■' : '▶'}
        </button>

        <button className="taskrow__body" onClick={() => setExpanded(!expanded)}>
          <div className={task.done ? 'taskrow__title taskrow__title--done' : 'taskrow__title'}>{task.title}</div>
          <div className="taskrow__meta">
            <span className={task.billable ? 'dot dot--billable' : 'dot'} title={task.billable ? 'Billable' : 'Non-billable'} />
            <span className="kcode">{project.kCode}</span>
            <span className="muted">
              {client.name} · {project.name}
            </span>
          </div>
        </button>

        <div className="taskrow__time">
          <div className="taskrow__actual">{formatMinutes(logged)}</div>
          {planned > 0 && <div className="taskrow__planned">of {formatMinutes(planned)}</div>}
        </div>

        <div className="taskrow__adjust">
          <button title="Add 15 minutes" onClick={() => dispatch({ type: 'entry/adjust', taskId: task.id, date, deltaMinutes: 15 })}>
            +
          </button>
          <button title="Remove 15 minutes" onClick={() => dispatch({ type: 'entry/adjust', taskId: task.id, date, deltaMinutes: -15 })}>
            −
          </button>
        </div>
      </div>

      {expanded && (
        <div className="taskrow__detail">
          <div className="taskrow__detailactions">
            <label className="check">
              <input type="checkbox" checked={task.done} onChange={() => dispatch({ type: 'task/toggleDone', id: task.id })} />
              Done
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={task.billable}
                onChange={(e) =>
                  dispatch({
                    type: 'task/upsert',
                    task: { id: task.id, projectId: task.projectId, title: task.title, billable: e.target.checked }
                  })
                }
              />
              Billable
            </label>
            <label className="field field--inline">
              <span className="field__label">Plan</span>
              <input
                className="input--mini"
                defaultValue={planned ? String(planned / 60) : ''}
                placeholder="1h30"
                onBlur={(e) =>
                  dispatch({ type: 'plan/set', taskId: task.id, date, minutes: parseDuration(e.target.value) ?? 0 })
                }
              />
            </label>
            <button className="btn btn--ghost btn--mini" onClick={() => dispatch({ type: 'task/archive', id: task.id, archived: true })}>
              Archive
            </button>
          </div>

          {entries.map((entry) => (
            <div className="entry" key={entry.id}>
              <input
                className="input--mini"
                defaultValue={formatMinutes(entry.minutes)}
                onBlur={(e) => {
                  const minutes = parseDuration(e.target.value)
                  if (minutes !== null) dispatch({ type: 'entry/update', id: entry.id, minutes })
                }}
              />
              <input
                className="entry__note"
                placeholder="What did you do? This becomes the customer note."
                defaultValue={entry.note}
                onBlur={(e) => dispatch({ type: 'entry/update', id: entry.id, note: e.target.value })}
              />
              <span className="entry__source" title={entry.source === 'timer' ? 'Tracked with the timer' : 'Entered manually'}>
                {entry.source === 'timer' ? '⏱' : '✎'}
              </span>
              <button className="iconbtn iconbtn--mini" title="Delete entry" onClick={() => dispatch({ type: 'entry/delete', id: entry.id })}>
                ×
              </button>
            </div>
          ))}

          <div className="entry entry--new">
            <input
              className="input--mini"
              value={draft}
              placeholder="45m"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addManual()}
            />
            <button className="btn btn--ghost btn--mini" onClick={addManual}>
              Log time
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
