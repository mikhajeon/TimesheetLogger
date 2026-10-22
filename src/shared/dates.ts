import type { ISODate } from './types'

const DAY_MS = 86_400_000

export function toISODate(d: Date): ISODate {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** Parses `YYYY-MM-DD` as local midnight, avoiding the UTC shift `new Date(str)` applies. */
export function fromISODate(s: ISODate): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function today(): ISODate {
  return toISODate(new Date())
}

export function addDays(s: ISODate, n: number): ISODate {
  const d = fromISODate(s)
  d.setDate(d.getDate() + n)
  return toISODate(d)
}

/** Monday of the week containing `s`. */
export function startOfWeek(s: ISODate): ISODate {
  const d = fromISODate(s)
  const shift = (d.getDay() + 6) % 7
  d.setDate(d.getDate() - shift)
  return toISODate(d)
}

export function weekDates(weekStart: ISODate, includeWeekend: boolean): ISODate[] {
  const count = includeWeekend ? 7 : 5
  return Array.from({ length: count }, (_, i) => addDays(weekStart, i))
}

/** ISO-8601 week number, e.g. 43. */
export function isoWeekNumber(s: ISODate): number {
  const d = fromISODate(s)
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  target.setDate(target.getDate() + 3 - ((target.getDay() + 6) % 7))
  const firstThursday = new Date(target.getFullYear(), 0, 4)
  firstThursday.setDate(firstThursday.getDate() + 3 - ((firstThursday.getDay() + 6) % 7))
  return 1 + Math.round((target.getTime() - firstThursday.getTime()) / (7 * DAY_MS))
}

export function isoWeekYear(s: ISODate): number {
  const d = fromISODate(s)
  const target = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  target.setDate(target.getDate() + 3 - ((target.getDay() + 6) % 7))
  return target.getFullYear()
}

/** `2026-W43`, the label used for export filenames. */
export function weekLabel(weekStart: ISODate): string {
  return `${isoWeekYear(weekStart)}-W${String(isoWeekNumber(weekStart)).padStart(2, '0')}`
}

export function dayName(s: ISODate, style: 'short' | 'long' = 'short'): string {
  return fromISODate(s).toLocaleDateString(undefined, { weekday: style })
}

export function prettyDate(s: ISODate): string {
  return fromISODate(s).toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'short',
    year: 'numeric'
  })
}

export function shortDate(s: ISODate): string {
  return fromISODate(s).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/** `7h 30m`, or `45m` when under an hour. Empty-ish values render as `0m`. */
export function formatMinutes(mins: number): string {
  const sign = mins < 0 ? '-' : ''
  const abs = Math.abs(Math.round(mins))
  const h = Math.floor(abs / 60)
  const m = abs % 60
  if (h === 0) return `${sign}${m}m`
  if (m === 0) return `${sign}${h}h`
  return `${sign}${h}h ${String(m).padStart(2, '0')}m`
}

/** Decimal hours as most timesheet systems want them, e.g. 2.25. */
export function toDecimalHours(mins: number): number {
  return Math.round((mins / 60) * 100) / 100
}

export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number): string => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

/** Accepts `1.5`, `1h30`, `1h 30m`, `90m`, `:45` and returns minutes. */
export function parseDuration(input: string): number | null {
  const s = input.trim().toLowerCase()
  if (!s) return null

  const hm = s.match(/^(\d+)\s*h(?:ours?)?\s*(\d+)?\s*m?(?:ins?|inutes?)?$/)
  if (hm) return Number(hm[1]) * 60 + Number(hm[2] ?? 0)

  const mOnly = s.match(/^(\d+)\s*m(?:ins?|inutes?)$/)
  if (mOnly) return Number(mOnly[1])

  const colon = s.match(/^(\d*):(\d{1,2})$/)
  if (colon) return Number(colon[1] || 0) * 60 + Number(colon[2])

  const decimal = s.match(/^(\d+(?:\.\d+)?)$/)
  if (decimal) return Math.round(Number(decimal[1]) * 60)

  return null
}

export function roundMinutes(mins: number, increment: number): number {
  if (!increment) return Math.round(mins)
  return Math.round(mins / increment) * increment
}
