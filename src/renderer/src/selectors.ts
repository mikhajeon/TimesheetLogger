import type { Client, Database, ISODate, Project, Task } from '@shared/types'
import { toISODate } from '@shared/dates'

export interface TaskContext {
  task: Task
  project: Project
  client: Client
}

export function describeTask(db: Database, task: Task): TaskContext | null {
  const project = db.projects.find((p) => p.id === task.projectId)
  if (!project) return null
  const client = db.clients.find((c) => c.id === project.clientId)
  if (!client) return null
  return { task, project, client }
}

export function activeTasks(db: Database): TaskContext[] {
  return db.tasks
    .filter((t) => !t.archived)
    .map((t) => describeTask(db, t))
    .filter((c): c is TaskContext => c !== null)
    .sort(
      (a, b) =>
        Number(a.client.isInternal) - Number(b.client.isInternal) ||
        a.client.name.localeCompare(b.client.name) ||
        a.project.name.localeCompare(b.project.name) ||
        a.task.title.localeCompare(b.task.title)
    )
}

export function loggedMinutes(db: Database, taskId: string, date: ISODate): number {
  return db.entries
    .filter((e) => e.taskId === taskId && e.date === date)
    .reduce((sum, e) => sum + e.minutes, 0)
}

export function plannedMinutes(db: Database, taskId: string, date: ISODate): number {
  return db.plans.find((p) => p.taskId === taskId && p.date === date)?.minutes ?? 0
}

/** Tasks worth showing on a given day: planned for it, or already worked on. */
export function tasksForDate(db: Database, date: ISODate): TaskContext[] {
  const relevant = new Set<string>()
  for (const plan of db.plans) if (plan.date === date) relevant.add(plan.taskId)
  for (const entry of db.entries) if (entry.date === date) relevant.add(entry.taskId)
  // A running timer belongs to the day it started on, not whichever day is on screen.
  if (db.activeTimer && toISODate(new Date(db.activeTimer.startedAt)) === date) {
    relevant.add(db.activeTimer.taskId)
  }
  return activeTasks(db).filter((c) => relevant.has(c.task.id))
}

export function dayTotals(db: Database, date: ISODate): { total: number; billable: number } {
  let total = 0
  let billable = 0
  for (const entry of db.entries) {
    if (entry.date !== date) continue
    total += entry.minutes
    const task = db.tasks.find((t) => t.id === entry.taskId)
    if (task?.billable) billable += entry.minutes
  }
  return { total, billable }
}

export function rangeTotals(db: Database, dates: ISODate[]): { total: number; billable: number } {
  return dates.reduce(
    (acc, date) => {
      const day = dayTotals(db, date)
      return { total: acc.total + day.total, billable: acc.billable + day.billable }
    },
    { total: 0, billable: 0 }
  )
}

export function elapsedMinutes(startedAt: number, now: number): number {
  return Math.max(0, (now - startedAt) / 60_000)
}
