/** Local calendar date, `YYYY-MM-DD`. Never a UTC timestamp. */
export type ISODate = string

export interface Client {
  id: string
  name: string
  /** Internal NCC work: there is no external customer to bill. */
  isInternal: boolean
  archived: boolean
}

export interface Project {
  id: string
  clientId: string
  name: string
  /** The K-code this project is booked against in the timesheet system. */
  kCode: string
  defaultBillable: boolean
  archived: boolean
}

export interface Task {
  id: string
  projectId: string
  title: string
  billable: boolean
  done: boolean
  archived: boolean
  createdAt: string
}

/** How many minutes you intend to spend on a task on a given day. */
export interface Plan {
  taskId: string
  date: ISODate
  minutes: number
}

/** Minutes actually spent. Always stored exact; rounding happens at export. */
export interface Entry {
  id: string
  taskId: string
  date: ISODate
  minutes: number
  note: string
  source: 'timer' | 'manual'
  startedAt?: number
  endedAt?: number
}

export interface ActiveTimer {
  taskId: string
  startedAt: number
  note: string
}

export type RoundingMinutes = 0 | 5 | 6 | 10 | 15 | 30

export interface Settings {
  alwaysOnTop: boolean
  /** Window opacity 0.3–1. Lets the overlay sit over other apps unobtrusively. */
  opacity: number
  /** Nearest increment used when exporting. 0 means report exact minutes. */
  roundingMinutes: RoundingMinutes
  /** Target billable minutes per working day, used for the day progress bar. */
  dailyTargetMinutes: number
  /** Show Saturday and Sunday columns in the week planner. */
  showWeekend: boolean
}

export interface Database {
  version: number
  clients: Client[]
  projects: Project[]
  tasks: Task[]
  plans: Plan[]
  entries: Entry[]
  /** Free-text note per day, for things that belong in the customer note but aren't a task. */
  dayNotes: Record<ISODate, string>
  activeTimer: ActiveTimer | null
  settings: Settings
}

export type Action =
  | { type: 'client/upsert'; client: Omit<Client, 'id' | 'archived'> & { id?: string; archived?: boolean } }
  | { type: 'client/archive'; id: string; archived: boolean }
  | { type: 'project/upsert'; project: Omit<Project, 'id' | 'archived'> & { id?: string; archived?: boolean } }
  | { type: 'project/archive'; id: string; archived: boolean }
  | { type: 'task/upsert'; task: Omit<Task, 'id' | 'archived' | 'createdAt' | 'done'> & { id?: string; done?: boolean } }
  | { type: 'task/archive'; id: string; archived: boolean }
  | { type: 'task/toggleDone'; id: string }
  | { type: 'plan/set'; taskId: string; date: ISODate; minutes: number }
  | { type: 'entry/add'; taskId: string; date: ISODate; minutes: number; note?: string }
  | { type: 'entry/adjust'; taskId: string; date: ISODate; deltaMinutes: number }
  | { type: 'entry/update'; id: string; minutes?: number; note?: string }
  | { type: 'entry/delete'; id: string }
  | { type: 'timer/start'; taskId: string }
  | { type: 'timer/setNote'; note: string }
  | { type: 'timer/stop' }
  | { type: 'timer/discard' }
  | { type: 'dayNote/set'; date: ISODate; text: string }
  | { type: 'settings/patch'; patch: Partial<Settings> }

export interface ExportResult {
  markdown: string
  filePath: string
}

export interface Paths {
  dataFile: string
  exportsDir: string
}
