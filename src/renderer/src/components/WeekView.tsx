import { Fragment, useMemo, useState } from 'react'
import type { ISODate } from '@shared/types'
import {
  addDays,
  dayName,
  formatMinutes,
  isoWeekNumber,
  parseDuration,
  shortDate,
  startOfWeek,
  today,
  weekDates
} from '@shared/dates'
import { useStore } from '../state'
import { activeTasks, loggedMinutes, plannedMinutes, rangeTotals, type TaskContext } from '../selectors'
import AddTaskPanel from './AddTaskPanel'

function groupKey(c: TaskContext): string {
  return `${c.client.name} · ${c.project.name} (${c.project.kCode})`
}

export default function WeekView(): JSX.Element {
  const { db, dispatch } = useStore()
  const [weekStart, setWeekStart] = useState(() => startOfWeek(today()))
  const [showAll, setShowAll] = useState(false)

  const dates = weekDates(weekStart, db.settings.showWeekend)
  const totals = rangeTotals(db, dates)

  const groups = useMemo(() => {
    const inWeek = new Set(dates)
    const touched = new Set<string>()
    for (const plan of db.plans) if (inWeek.has(plan.date)) touched.add(plan.taskId)
    for (const entry of db.entries) if (inWeek.has(entry.date)) touched.add(entry.taskId)

    const visible = activeTasks(db).filter((c) => touched.has(c.task.id) || (showAll && !c.task.done))

    const map = new Map<string, TaskContext[]>()
    for (const context of visible) {
      const key = groupKey(context)
      map.set(key, [...(map.get(key) ?? []), context])
    }
    return [...map.entries()]
  }, [db, dates, showAll])

  function plannedForDay(date: ISODate): number {
    return db.plans.filter((p) => p.date === date).reduce((s, p) => s + p.minutes, 0)
  }

  function actualForDay(date: ISODate): number {
    return db.entries.filter((e) => e.date === date).reduce((s, e) => s + e.minutes, 0)
  }

  return (
    <div className="view">
      <div className="daynav">
        <button className="iconbtn" onClick={() => setWeekStart(addDays(weekStart, -7))} title="Previous week">
          ‹
        </button>
        <button className="daynav__label" onClick={() => setWeekStart(startOfWeek(today()))} title="Jump to this week">
          Week {isoWeekNumber(weekStart)} · {shortDate(weekStart)} – {shortDate(addDays(weekStart, 6))}
        </button>
        <button className="iconbtn" onClick={() => setWeekStart(addDays(weekStart, 7))} title="Next week">
          ›
        </button>
      </div>

      <div className="week__summary">
        <span>
          <strong>{formatMinutes(totals.total)}</strong> tracked
        </span>
        <span className="muted">{formatMinutes(totals.billable)} billable</span>
        <label className="check check--mini">
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />
          All open tasks
        </label>
        <label className="check check--mini">
          <input
            type="checkbox"
            checked={db.settings.showWeekend}
            onChange={(e) => dispatch({ type: 'settings/patch', patch: { showWeekend: e.target.checked } })}
          />
          Weekend
        </label>
      </div>

      {groups.length === 0 ? (
        <p className="empty">Nothing planned this week. Tick “All open tasks” or add one below.</p>
      ) : (
        <div className="week__scroll">
          <table className="week">
            <thead>
              <tr>
                <th className="week__taskcol">Task</th>
                {dates.map((date) => (
                  <th key={date} className={date === today() ? 'week__day week__day--today' : 'week__day'}>
                    <div>{dayName(date)}</div>
                    <div className="muted">{shortDate(date)}</div>
                  </th>
                ))}
                <th className="week__day">Total</th>
              </tr>
            </thead>

            <tbody>
              {groups.map(([heading, contexts]) => (
                <Fragment key={heading}>
                  <tr className="week__grouprow">
                    <td colSpan={dates.length + 2}>{heading}</td>
                  </tr>
                  {contexts.map((context) => {
                    const rowTotal = dates.reduce((s, d) => s + loggedMinutes(db, context.task.id, d), 0)
                    return (
                      <tr key={context.task.id}>
                        <td className="week__taskcol">
                          <span
                            className={context.task.billable ? 'dot dot--billable' : 'dot'}
                            title={context.task.billable ? 'Billable' : 'Non-billable'}
                          />
                          <span className={context.task.done ? 'strike' : undefined}>{context.task.title}</span>
                        </td>
                        {dates.map((date) => {
                          const planned = plannedMinutes(db, context.task.id, date)
                          const actual = loggedMinutes(db, context.task.id, date)
                          return (
                            <td key={date} className="week__cell">
                              <input
                                className="week__input"
                                key={`${context.task.id}-${date}-${planned}`}
                                defaultValue={planned ? String(planned / 60) : ''}
                                placeholder="–"
                                title="Planned hours — accepts 1.5, 1h30 or 90m"
                                onBlur={(e) =>
                                  dispatch({
                                    type: 'plan/set',
                                    taskId: context.task.id,
                                    date,
                                    minutes: parseDuration(e.target.value) ?? 0
                                  })
                                }
                              />
                              <div className={actual > 0 ? 'week__actual' : 'week__actual week__actual--empty'}>
                                {actual > 0 ? formatMinutes(actual) : ''}
                              </div>
                            </td>
                          )
                        })}
                        <td className="week__cell week__cell--total">{formatMinutes(rowTotal)}</td>
                      </tr>
                    )
                  })}
                </Fragment>
              ))}
            </tbody>

            <tfoot>
              <tr>
                <td className="week__taskcol">Planned</td>
                {dates.map((date) => (
                  <td key={date} className="week__cell week__cell--total">
                    {formatMinutes(plannedForDay(date))}
                  </td>
                ))}
                <td className="week__cell week__cell--total">
                  {formatMinutes(dates.reduce((s, d) => s + plannedForDay(d), 0))}
                </td>
              </tr>
              <tr>
                <td className="week__taskcol">Actual</td>
                {dates.map((date) => (
                  <td key={date} className="week__cell week__cell--total">
                    {formatMinutes(actualForDay(date))}
                  </td>
                ))}
                <td className="week__cell week__cell--total">{formatMinutes(totals.total)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      <AddTaskPanel date={weekStart} />
    </div>
  )
}
