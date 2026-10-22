import { formatClock } from '@shared/dates'
import { useStore, useNow } from '../state'
import { describeTask } from '../selectors'

export default function TimerBar(): JSX.Element {
  const { db, dispatch } = useStore()
  const now = useNow()
  const timer = db.activeTimer

  if (!timer) {
    return (
      <div className="timerbar timerbar--idle">
        <span className="timerbar__dot" />
        <span className="timerbar__text">No timer running</span>
        <span className="timerbar__hint">Ctrl+Alt+S resumes</span>
      </div>
    )
  }

  const task = db.tasks.find((t) => t.id === timer.taskId)
  const context = task ? describeTask(db, task) : null

  return (
    <div className="timerbar timerbar--running">
      <span className="timerbar__dot timerbar__dot--live" />
      <div className="timerbar__body">
        <div className="timerbar__text">{task?.title ?? 'Unknown task'}</div>
        <input
          className="timerbar__note"
          placeholder="What are you doing? (goes into the note)"
          value={timer.note}
          onChange={(e) => dispatch({ type: 'timer/setNote', note: e.target.value })}
        />
      </div>
      {context && <span className="kcode">{context.project.kCode}</span>}
      <span className="timerbar__clock">{formatClock(now - timer.startedAt)}</span>
      <button className="btn btn--stop" onClick={() => dispatch({ type: 'timer/stop' })}>
        Stop
      </button>
    </div>
  )
}
