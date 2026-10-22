import { app } from 'electron'
import path from 'node:path'
import type { Paths } from '@shared/types'

/**
 * Running from source keeps everything inside the repo so Cursor can read the
 * exports directly. A packaged build can't write next to its executable, so it
 * falls back to Documents. `TIMESHEET_HOME` overrides both.
 */
export function appHome(): string {
  if (process.env.TIMESHEET_HOME) return path.resolve(process.env.TIMESHEET_HOME)
  if (app.isPackaged) return path.join(app.getPath('documents'), 'TimesheetLogger')
  return app.getAppPath()
}

export function resolvePaths(): Paths {
  const home = appHome()
  return {
    dataFile: path.join(home, 'data', 'timesheet.json'),
    exportsDir: path.join(home, 'exports')
  }
}

export function windowStateFile(): string {
  return path.join(appHome(), 'data', 'window.json')
}
