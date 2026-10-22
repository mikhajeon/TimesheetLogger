import type { Action, Database, Entry, Settings } from './types'
import { toISODate } from './dates'

const id = (): string => globalThis.crypto.randomUUID()

export const DEFAULT_SETTINGS: Settings = {
  alwaysOnTop: true,
  opacity: 1,
  roundingMinutes: 15,
  dailyTargetMinutes: 450,
  showWeekend: false
}

export function createDatabase(): Database {
  const internalId = id()
  return {
    version: 1,
    clients: [{ id: internalId, name: 'Internal NCC', isInternal: true, archived: false }],
    projects: [
      {
        id: id(),
        clientId: internalId,
        name: 'Admin & internal',
        kCode: 'NCC',
        defaultBillable: false,
        archived: false
      }
    ],
    tasks: [],
    plans: [],
    entries: [],
    dayNotes: {},
    activeTimer: null,
    settings: { ...DEFAULT_SETTINGS }
  }
}

/** Fills in anything a database written by an older version of the app is missing. */
export function migrate(raw: unknown): Database {
  const base = createDatabase()
  if (!raw || typeof raw !== 'object') return base
  const db = raw as Partial<Database>
  return {
    version: 1,
    clients: db.clients ?? base.clients,
    projects: db.projects ?? base.projects,
    tasks: db.tasks ?? [],
    plans: db.plans ?? [],
    entries: db.entries ?? [],
    dayNotes: db.dayNotes ?? {},
    activeTimer: db.activeTimer ?? null,
    settings: { ...DEFAULT_SETTINGS, ...(db.settings ?? {}) }
  }
}

export function reduce(db: Database, action: Action): Database {
  switch (action.type) {
    case 'client/upsert': {
      const incoming = action.client
      if (incoming.id && db.clients.some((c) => c.id === incoming.id)) {
        return {
          ...db,
          clients: db.clients.map((c) => (c.id === incoming.id ? { ...c, ...incoming } : c))
        }
      }
      return {
        ...db,
        clients: [...db.clients, { archived: false, ...incoming, id: incoming.id ?? id() }]
      }
    }

    case 'client/archive':
      return {
        ...db,
        clients: db.clients.map((c) => (c.id === action.id ? { ...c, archived: action.archived } : c))
      }

    case 'project/upsert': {
      const incoming = action.project
      if (incoming.id && db.projects.some((p) => p.id === incoming.id)) {
        return {
          ...db,
          projects: db.projects.map((p) => (p.id === incoming.id ? { ...p, ...incoming } : p))
        }
      }
      return {
        ...db,
        projects: [...db.projects, { archived: false, ...incoming, id: incoming.id ?? id() }]
      }
    }

    case 'project/archive':
      return {
        ...db,
        projects: db.projects.map((p) => (p.id === action.id ? { ...p, archived: action.archived } : p))
      }

    case 'task/upsert': {
      const incoming = action.task
      if (incoming.id && db.tasks.some((t) => t.id === incoming.id)) {
        return {
          ...db,
          tasks: db.tasks.map((t) => (t.id === incoming.id ? { ...t, ...incoming } : t))
        }
      }
      return {
        ...db,
        tasks: [
          ...db.tasks,
          {
            done: false,
            archived: false,
            createdAt: new Date().toISOString(),
            ...incoming,
            id: incoming.id ?? id()
          }
        ]
      }
    }

    case 'task/archive':
      return {
        ...db,
        tasks: db.tasks.map((t) => (t.id === action.id ? { ...t, archived: action.archived } : t)),
        activeTimer: db.activeTimer?.taskId === action.id && action.archived ? null : db.activeTimer
      }

    case 'task/toggleDone':
      return {
        ...db,
        tasks: db.tasks.map((t) => (t.id === action.id ? { ...t, done: !t.done } : t))
      }

    case 'plan/set': {
      const others = db.plans.filter((p) => !(p.taskId === action.taskId && p.date === action.date))
      if (action.minutes <= 0) return { ...db, plans: others }
      return { ...db, plans: [...others, { taskId: action.taskId, date: action.date, minutes: action.minutes }] }
    }

    case 'entry/add': {
      if (action.minutes <= 0) return db
      const entry: Entry = {
        id: id(),
        taskId: action.taskId,
        date: action.date,
        minutes: action.minutes,
        note: action.note ?? '',
        source: 'manual'
      }
      return { ...db, entries: [...db.entries, entry] }
    }

    // Used by the +/- buttons: tops up a single manual entry rather than
    // littering the day with one-off entries per click.
    case 'entry/adjust': {
      const onDay = db.entries.filter((e) => e.taskId === action.taskId && e.date === action.date)
      // Top up the manual entry if there is one; otherwise subtracting still has
      // to come off something, so fall back to the most recent entry of the day.
      const existing =
        onDay.find((e) => e.source === 'manual') ?? (action.deltaMinutes < 0 ? onDay[onDay.length - 1] : undefined)
      if (!existing) {
        if (action.deltaMinutes <= 0) return db
        return reduce(db, {
          type: 'entry/add',
          taskId: action.taskId,
          date: action.date,
          minutes: action.deltaMinutes
        })
      }
      const next = existing.minutes + action.deltaMinutes
      if (next <= 0) return { ...db, entries: db.entries.filter((e) => e.id !== existing.id) }
      return {
        ...db,
        entries: db.entries.map((e) => (e.id === existing.id ? { ...e, minutes: next } : e))
      }
    }

    case 'entry/update':
      return {
        ...db,
        entries: db.entries.map((e) =>
          e.id === action.id
            ? {
                ...e,
                minutes: action.minutes ?? e.minutes,
                note: action.note ?? e.note
              }
            : e
        )
      }

    case 'entry/delete':
      return { ...db, entries: db.entries.filter((e) => e.id !== action.id) }

    case 'timer/start': {
      // Starting a new timer banks whatever the running one has accrued.
      const banked = db.activeTimer ? reduce(db, { type: 'timer/stop' }) : db
      return { ...banked, activeTimer: { taskId: action.taskId, startedAt: Date.now(), note: '' } }
    }

    case 'timer/setNote':
      if (!db.activeTimer) return db
      return { ...db, activeTimer: { ...db.activeTimer, note: action.note } }

    case 'timer/stop': {
      const timer = db.activeTimer
      if (!timer) return db
      const endedAt = Date.now()
      const minutes = Math.round((endedAt - timer.startedAt) / 60_000)
      if (minutes < 1) return { ...db, activeTimer: null }
      const entry: Entry = {
        id: id(),
        taskId: timer.taskId,
        date: toISODate(new Date(timer.startedAt)),
        minutes,
        note: timer.note,
        source: 'timer',
        startedAt: timer.startedAt,
        endedAt
      }
      return { ...db, entries: [...db.entries, entry], activeTimer: null }
    }

    case 'timer/discard':
      return { ...db, activeTimer: null }

    case 'dayNote/set':
      return { ...db, dayNotes: { ...db.dayNotes, [action.date]: action.text } }

    case 'settings/patch':
      return { ...db, settings: { ...db.settings, ...action.patch } }

    default:
      return db
  }
}
