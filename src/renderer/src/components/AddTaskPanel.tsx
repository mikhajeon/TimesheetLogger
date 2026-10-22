import { useMemo, useState } from 'react'
import type { ISODate } from '@shared/types'
import { parseDuration } from '@shared/dates'
import { useStore } from '../state'
import { activeTasks } from '../selectors'

const DEFAULT_PLAN_MINUTES = 30

interface Props {
  /** When set, the task is also added to that day's plan. */
  date?: ISODate
  onDone?: () => void
}

export default function AddTaskPanel({ date, onDone }: Props): JSX.Element {
  const { db, dispatch } = useStore()
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [projectId, setProjectId] = useState(() => db.projects.find((p) => !p.archived)?.id ?? '')
  const [plan, setPlan] = useState('')
  const [billableOverride, setBillableOverride] = useState<boolean | null>(null)

  const projects = useMemo(() => db.projects.filter((p) => !p.archived), [db.projects])
  const project = projects.find((p) => p.id === projectId)
  const billable = billableOverride ?? project?.defaultBillable ?? false

  const existing = useMemo(() => {
    const plannedToday = new Set(db.plans.filter((p) => p.date === date).map((p) => p.taskId))
    return activeTasks(db).filter((c) => !c.task.done && !plannedToday.has(c.task.id))
  }, [db, date])

  function addExisting(taskId: string): void {
    if (!taskId) return
    if (date) dispatch({ type: 'plan/set', taskId, date, minutes: DEFAULT_PLAN_MINUTES })
    onDone?.()
  }

  function createTask(): void {
    const trimmed = title.trim()
    if (!trimmed || !projectId) return

    // The reducer generates the id, so the plan is attached by matching the
    // task back out of the returned database.
    void window.api
      .dispatch({ type: 'task/upsert', task: { projectId, title: trimmed, billable } })
      .then((next) => {
        const created = [...next.tasks].reverse().find((t) => t.title === trimmed && t.projectId === projectId)
        if (created && date) {
          const minutes = parseDuration(plan) ?? DEFAULT_PLAN_MINUTES
          dispatch({ type: 'plan/set', taskId: created.id, date, minutes })
        }
        setTitle('')
        setPlan('')
        setBillableOverride(null)
        onDone?.()
      })
  }

  if (!open) {
    return (
      <button className="addbar" onClick={() => setOpen(true)}>
        + Add task{date ? ' to this day' : ''}
      </button>
    )
  }

  return (
    <div className="panel">
      {date && existing.length > 0 && (
        <label className="field">
          <span className="field__label">Add an existing task</span>
          <select defaultValue="" onChange={(e) => addExisting(e.target.value)}>
            <option value="">Choose…</option>
            {existing.map(({ task, project: p, client }) => (
              <option key={task.id} value={task.id}>
                {client.name} · {p.kCode} · {task.title}
              </option>
            ))}
          </select>
        </label>
      )}

      <div className="panel__divider">or create a new one</div>

      <label className="field">
        <span className="field__label">Project</span>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          {projects.map((p) => {
            const client = db.clients.find((c) => c.id === p.clientId)
            return (
              <option key={p.id} value={p.id}>
                {client?.name} · {p.name} ({p.kCode})
              </option>
            )
          })}
        </select>
      </label>

      <label className="field">
        <span className="field__label">Task</span>
        <input
          autoFocus
          value={title}
          placeholder="e.g. Draft migration runbook"
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && createTask()}
        />
      </label>

      <div className="panel__row">
        <label className="check">
          <input type="checkbox" checked={billable} onChange={(e) => setBillableOverride(e.target.checked)} />
          Billable
        </label>
        {date && (
          <label className="field field--inline">
            <span className="field__label">Plan</span>
            <input className="input--mini" value={plan} placeholder="1h30" onChange={(e) => setPlan(e.target.value)} />
          </label>
        )}
      </div>

      <div className="panel__actions">
        <button className="btn btn--ghost" onClick={() => setOpen(false)}>
          Cancel
        </button>
        <button className="btn btn--primary" onClick={createTask} disabled={!title.trim() || !projectId}>
          Add task
        </button>
      </div>
    </div>
  )
}
