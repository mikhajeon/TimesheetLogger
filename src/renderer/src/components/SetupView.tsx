import { useState } from 'react'
import type { RoundingMinutes } from '@shared/types'
import { formatMinutes, parseDuration } from '@shared/dates'
import { useStore } from '../state'

const ROUNDING_OPTIONS: RoundingMinutes[] = [0, 5, 6, 10, 15, 30]

export default function SetupView(): JSX.Element {
  const { db, dispatch, paths } = useStore()
  const [newClient, setNewClient] = useState('')
  const [newProject, setNewProject] = useState({ clientId: '', name: '', kCode: '' })
  const [showArchived, setShowArchived] = useState(false)

  const clients = db.clients.filter((c) => showArchived || !c.archived)
  const projects = db.projects.filter((p) => showArchived || !p.archived)

  return (
    <div className="view">
      <section className="section">
        <h2>Preferences</h2>

        <label className="field">
          <span className="field__label">Round exported time to</span>
          <select
            value={db.settings.roundingMinutes}
            onChange={(e) =>
              dispatch({
                type: 'settings/patch',
                patch: { roundingMinutes: Number(e.target.value) as RoundingMinutes }
              })
            }
          >
            {ROUNDING_OPTIONS.map((value) => (
              <option key={value} value={value}>
                {value === 0 ? 'Exact minutes' : `Nearest ${value} minutes`}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field__label">Daily target ({formatMinutes(db.settings.dailyTargetMinutes)})</span>
          <input
            defaultValue={String(db.settings.dailyTargetMinutes / 60)}
            onBlur={(e) =>
              dispatch({
                type: 'settings/patch',
                patch: { dailyTargetMinutes: parseDuration(e.target.value) ?? db.settings.dailyTargetMinutes }
              })
            }
          />
        </label>

        <label className="field">
          <span className="field__label">Overlay opacity — {Math.round(db.settings.opacity * 100)}%</span>
          <input
            type="range"
            min={0.3}
            max={1}
            step={0.05}
            value={db.settings.opacity}
            onChange={(e) => void window.api.window.setOpacity(Number(e.target.value))}
          />
        </label>

        <label className="check">
          <input
            type="checkbox"
            checked={db.settings.alwaysOnTop}
            onChange={(e) => void window.api.window.setAlwaysOnTop(e.target.checked)}
          />
          Always on top
        </label>
      </section>

      <section className="section">
        <h2>Clients</h2>
        {clients.map((client) => (
          <div className="rowedit" key={client.id}>
            <input
              defaultValue={client.name}
              onBlur={(e) =>
                dispatch({ type: 'client/upsert', client: { ...client, name: e.target.value } })
              }
            />
            <label className="check check--mini" title="Internal NCC work has no external customer">
              <input
                type="checkbox"
                checked={client.isInternal}
                onChange={(e) =>
                  dispatch({ type: 'client/upsert', client: { ...client, isInternal: e.target.checked } })
                }
              />
              Internal
            </label>
            <button
              className="btn btn--ghost btn--mini"
              onClick={() => dispatch({ type: 'client/archive', id: client.id, archived: !client.archived })}
            >
              {client.archived ? 'Restore' : 'Archive'}
            </button>
          </div>
        ))}
        <div className="rowedit">
          <input
            value={newClient}
            placeholder="New client name"
            onChange={(e) => setNewClient(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== 'Enter' || !newClient.trim()) return
              dispatch({ type: 'client/upsert', client: { name: newClient.trim(), isInternal: false } })
              setNewClient('')
            }}
          />
          <button
            className="btn btn--ghost btn--mini"
            disabled={!newClient.trim()}
            onClick={() => {
              dispatch({ type: 'client/upsert', client: { name: newClient.trim(), isInternal: false } })
              setNewClient('')
            }}
          >
            Add
          </button>
        </div>
      </section>

      <section className="section">
        <h2>Projects &amp; K-codes</h2>
        {projects.map((project) => (
          <div className="rowedit rowedit--project" key={project.id}>
            <input
              className="input--kcode"
              defaultValue={project.kCode}
              placeholder="K-code"
              onBlur={(e) => dispatch({ type: 'project/upsert', project: { ...project, kCode: e.target.value } })}
            />
            <input
              defaultValue={project.name}
              onBlur={(e) => dispatch({ type: 'project/upsert', project: { ...project, name: e.target.value } })}
            />
            <select
              value={project.clientId}
              onChange={(e) => dispatch({ type: 'project/upsert', project: { ...project, clientId: e.target.value } })}
            >
              {db.clients.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <label className="check check--mini" title="New tasks on this project start billable">
              <input
                type="checkbox"
                checked={project.defaultBillable}
                onChange={(e) =>
                  dispatch({ type: 'project/upsert', project: { ...project, defaultBillable: e.target.checked } })
                }
              />
              Billable
            </label>
            <button
              className="btn btn--ghost btn--mini"
              onClick={() => dispatch({ type: 'project/archive', id: project.id, archived: !project.archived })}
            >
              {project.archived ? 'Restore' : 'Archive'}
            </button>
          </div>
        ))}
        <div className="rowedit rowedit--project">
          <input
            className="input--kcode"
            value={newProject.kCode}
            placeholder="K-code"
            onChange={(e) => setNewProject({ ...newProject, kCode: e.target.value })}
          />
          <input
            value={newProject.name}
            placeholder="New project name"
            onChange={(e) => setNewProject({ ...newProject, name: e.target.value })}
          />
          <select
            value={newProject.clientId}
            onChange={(e) => setNewProject({ ...newProject, clientId: e.target.value })}
          >
            <option value="">Client…</option>
            {db.clients.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <button
            className="btn btn--ghost btn--mini"
            disabled={!newProject.name.trim() || !newProject.clientId}
            onClick={() => {
              const client = db.clients.find((c) => c.id === newProject.clientId)
              dispatch({
                type: 'project/upsert',
                project: {
                  clientId: newProject.clientId,
                  name: newProject.name.trim(),
                  kCode: newProject.kCode.trim() || 'NCC',
                  defaultBillable: !client?.isInternal
                }
              })
              setNewProject({ clientId: '', name: '', kCode: '' })
            }}
          >
            Add
          </button>
        </div>
        <label className="check check--mini">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
          Show archived
        </label>
      </section>

      <section className="section">
        <h2>Shortcuts</h2>
        <ul className="keys">
          <li>
            <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>T</kbd> show or hide the overlay
          </li>
          <li>
            <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>A</kbd> toggle always on top
          </li>
          <li>
            <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>S</kbd> stop the timer, or resume the last task
          </li>
        </ul>
      </section>

      <section className="section">
        <h2>Files</h2>
        <button className="pathline" onClick={() => void window.api.reveal(paths.dataFile)}>
          {paths.dataFile}
        </button>
        <button className="pathline" onClick={() => void window.api.openPath(paths.exportsDir)}>
          {paths.exportsDir}
        </button>
      </section>
    </div>
  )
}
