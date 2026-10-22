import fs from 'node:fs'
import path from 'node:path'
import type { Action, Database } from '@shared/types'
import { createDatabase, migrate, reduce } from '@shared/reducer'
import { resolvePaths } from './paths'

type Listener = (db: Database) => void

const SAVE_DEBOUNCE_MS = 400

class Store {
  private db: Database = createDatabase()
  private listeners = new Set<Listener>()
  private saveTimer: NodeJS.Timeout | null = null

  load(): Database {
    const { dataFile } = resolvePaths()
    try {
      if (fs.existsSync(dataFile)) {
        this.db = migrate(JSON.parse(fs.readFileSync(dataFile, 'utf8')))
      } else {
        this.db = createDatabase()
        this.flush()
      }
    } catch (error) {
      // A corrupt file is kept aside rather than silently overwritten, so a
      // week of tracking is never lost to a bad write.
      console.error('Could not read timesheet data, starting fresh:', error)
      if (fs.existsSync(dataFile)) {
        fs.renameSync(dataFile, `${dataFile}.corrupt-${Date.now()}`)
      }
      this.db = createDatabase()
      this.flush()
    }
    return this.db
  }

  get(): Database {
    return this.db
  }

  dispatch(action: Action): Database {
    const next = reduce(this.db, action)
    if (next === this.db) return this.db
    this.db = next
    this.scheduleSave()
    for (const listener of this.listeners) listener(this.db)
    return this.db
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private scheduleSave(): void {
    if (this.saveTimer) clearTimeout(this.saveTimer)
    this.saveTimer = setTimeout(() => this.flush(), SAVE_DEBOUNCE_MS)
  }

  /** Writes through a temp file so an interrupted save can't truncate the data. */
  flush(): void {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer)
      this.saveTimer = null
    }
    const { dataFile } = resolvePaths()
    try {
      fs.mkdirSync(path.dirname(dataFile), { recursive: true })
      const tmp = `${dataFile}.tmp`
      fs.writeFileSync(tmp, JSON.stringify(this.db, null, 2), 'utf8')
      fs.renameSync(tmp, dataFile)
    } catch (error) {
      console.error('Failed to save timesheet data:', error)
    }
  }
}

export const store = new Store()
