import type { Database, ISODate } from './types'
import { formatMinutes, prettyDate, roundMinutes, shortDate, toDecimalHours } from './dates'

interface LineItem {
  title: string
  minutes: number
  notes: string[]
}

/**
 * One bookable timesheet line: a K-code, on a day, at one billable status.
 * This matches how the hours actually get entered, so the export maps 1:1
 * onto what you paste in.
 */
interface Booking {
  date: ISODate
  clientName: string
  isInternal: boolean
  projectName: string
  kCode: string
  billable: boolean
  exactMinutes: number
  roundedMinutes: number
  items: LineItem[]
}

function collectBookings(db: Database, dates: ISODate[]): Booking[] {
  const inRange = new Set(dates)
  const byKey = new Map<string, Booking>()

  for (const entry of db.entries) {
    if (!inRange.has(entry.date)) continue
    const task = db.tasks.find((t) => t.id === entry.taskId)
    if (!task) continue
    const project = db.projects.find((p) => p.id === task.projectId)
    if (!project) continue
    const client = db.clients.find((c) => c.id === project.clientId)

    const key = `${project.id}|${task.billable}|${entry.date}`
    let booking = byKey.get(key)
    if (!booking) {
      booking = {
        date: entry.date,
        clientName: client?.name ?? 'Unknown client',
        isInternal: client?.isInternal ?? false,
        projectName: project.name,
        kCode: project.kCode,
        billable: task.billable,
        exactMinutes: 0,
        roundedMinutes: 0,
        items: []
      }
      byKey.set(key, booking)
    }

    booking.exactMinutes += entry.minutes
    let item = booking.items.find((i) => i.title === task.title)
    if (!item) {
      item = { title: task.title, minutes: 0, notes: [] }
      booking.items.push(item)
    }
    item.minutes += entry.minutes
    if (entry.note.trim()) item.notes.push(entry.note.trim())
  }

  const increment = db.settings.roundingMinutes
  for (const booking of byKey.values()) {
    booking.roundedMinutes = roundMinutes(booking.exactMinutes, increment)
  }

  // Anything that rounds away to nothing is dropped; the exact-minutes footer
  // at the end of the export is what makes those crumbs visible.
  return [...byKey.values()]
    .filter((b) => b.roundedMinutes > 0)
    .sort(
      (a, b) =>
        a.date.localeCompare(b.date) ||
        Number(a.isInternal) - Number(b.isInternal) ||
        a.clientName.localeCompare(b.clientName) ||
        a.kCode.localeCompare(b.kCode)
    )
}

function plannedMinutes(db: Database, date: ISODate): number {
  return db.plans.filter((p) => p.date === date).reduce((sum, p) => sum + p.minutes, 0)
}

function actualMinutes(db: Database, date: ISODate): number {
  return db.entries.filter((e) => e.date === date).reduce((sum, e) => sum + e.minutes, 0)
}

export interface MarkdownOptions {
  title: string
  /** Included verbatim so a reader knows what the numbers were rounded to. */
  roundingLabel: string
}

export function buildMarkdown(db: Database, dates: ISODate[], options: MarkdownOptions): string {
  const bookings = collectBookings(db, dates)
  const out: string[] = []

  const inRange = new Set(dates)
  const totalExact = db.entries.filter((e) => inRange.has(e.date)).reduce((s, e) => s + e.minutes, 0)
  const totalRounded = bookings.reduce((s, b) => s + b.roundedMinutes, 0)
  const billableRounded = bookings.filter((b) => b.billable).reduce((s, b) => s + b.roundedMinutes, 0)

  out.push(`# ${options.title}`)
  out.push('')
  out.push(
    `_Generated ${new Date().toLocaleString()} · ${options.roundingLabel} · ` +
      `${formatMinutes(totalRounded)} total, ${formatMinutes(billableRounded)} billable_`
  )
  out.push('')

  if (bookings.length === 0) {
    out.push('> No time logged in this period yet.')
    out.push('')
    return out.join('\n')
  }

  // ---- Section 1: the numbers to key in -------------------------------
  out.push('## Timesheet lines')
  out.push('')
  out.push('| Date | Client | Project | K-code | Billable | Hours |')
  out.push('| --- | --- | --- | --- | --- | --- |')
  for (const b of bookings) {
    out.push(
      `| ${shortDate(b.date)} | ${b.clientName} | ${b.projectName} | \`${b.kCode}\` | ` +
        `${b.billable ? 'Yes' : 'No'} | ${toDecimalHours(b.roundedMinutes).toFixed(2)} |`
    )
  }
  out.push('')

  // ---- Section 2: narrative source, grouped the way a note is written --
  const byProject = new Map<string, Booking[]>()
  for (const b of bookings) {
    const key = `${b.clientName} — ${b.projectName} (${b.kCode})`
    byProject.set(key, [...(byProject.get(key) ?? []), b])
  }

  out.push('## Work carried out, by project')
  out.push('')
  for (const [heading, group] of byProject) {
    const groupTotal = group.reduce((s, b) => s + b.roundedMinutes, 0)
    const billable = group.some((b) => b.billable)
    out.push(`### ${heading}`)
    out.push('')
    out.push(
      `${formatMinutes(groupTotal)} (${toDecimalHours(groupTotal).toFixed(2)}h) · ` +
        `${billable ? 'billable' : 'non-billable'}`
    )
    out.push('')
    for (const b of group) {
      out.push(`- **${prettyDate(b.date)}** — ${formatMinutes(b.roundedMinutes)}`)
      for (const item of b.items.sort((x, y) => y.minutes - x.minutes)) {
        out.push(`  - ${item.title} (${formatMinutes(item.minutes)})`)
        for (const note of item.notes) out.push(`    - ${note}`)
      }
    }
    out.push('')
  }

  // ---- Section 3: did the week go to plan? ----------------------------
  out.push('## Planned vs actual')
  out.push('')
  out.push('| Day | Planned | Actual | Delta |')
  out.push('| --- | --- | --- | --- |')
  for (const date of dates) {
    const planned = plannedMinutes(db, date)
    const actual = actualMinutes(db, date)
    if (planned === 0 && actual === 0) continue
    const delta = actual - planned
    out.push(
      `| ${shortDate(date)} | ${formatMinutes(planned)} | ${formatMinutes(actual)} | ` +
        `${delta > 0 ? '+' : ''}${formatMinutes(delta)} |`
    )
  }
  out.push('')

  // ---- Section 4: context that isn't attached to a task ---------------
  const notes = dates.filter((d) => db.dayNotes[d]?.trim())
  if (notes.length > 0) {
    out.push('## Day notes')
    out.push('')
    for (const date of notes) {
      out.push(`- **${prettyDate(date)}**: ${db.dayNotes[date].trim()}`)
    }
    out.push('')
  }

  out.push(`_Exact tracked time before rounding: ${formatMinutes(totalExact)}._`)
  out.push('')

  return out.join('\n')
}
