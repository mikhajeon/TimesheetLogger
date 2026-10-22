import {
  app,
  BrowserWindow,
  clipboard,
  globalShortcut,
  ipcMain,
  Menu,
  nativeImage,
  screen,
  shell,
  Tray
} from 'electron'
import fs from 'node:fs'
import path from 'node:path'
import type { Action, Database } from '@shared/types'
import { store } from './store'
import { resolvePaths, windowStateFile } from './paths'

const DEFAULT_BOUNDS = { width: 460, height: 720, x: undefined as number | undefined, y: undefined as number | undefined }
const WIDE_WIDTH = 1040

let mainWindow: BrowserWindow | null = null
let tray: Tray | null = null
let quitting = false

/* ------------------------------------------------------------------ */
/* Window                                                              */
/* ------------------------------------------------------------------ */

function readWindowState(): typeof DEFAULT_BOUNDS {
  try {
    const saved = JSON.parse(fs.readFileSync(windowStateFile(), 'utf8'))
    // Guard against a monitor that is no longer attached.
    const visible = screen.getAllDisplays().some((d) => {
      const a = d.workArea
      return saved.x < a.x + a.width && saved.x + 80 > a.x && saved.y < a.y + a.height && saved.y + 40 > a.y
    })
    return visible ? { ...DEFAULT_BOUNDS, ...saved } : { ...DEFAULT_BOUNDS, width: saved.width, height: saved.height }
  } catch {
    return DEFAULT_BOUNDS
  }
}

function saveWindowState(): void {
  if (!mainWindow || mainWindow.isDestroyed() || mainWindow.isMinimized()) return
  try {
    const file = windowStateFile()
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, JSON.stringify(mainWindow.getBounds()), 'utf8')
  } catch {
    /* window position is a nicety, never worth surfacing an error for */
  }
}

function createWindow(db: Database): BrowserWindow {
  const state = readWindowState()

  const win = new BrowserWindow({
    ...state,
    minWidth: 380,
    minHeight: 420,
    show: false,
    frame: false,
    resizable: true,
    backgroundColor: '#0f1117',
    title: 'Timesheet Logger',
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true
    }
  })

  win.setAlwaysOnTop(db.settings.alwaysOnTop, 'screen-saver')
  win.setOpacity(db.settings.opacity)

  win.once('ready-to-show', () => win.show())
  win.on('moved', saveWindowState)
  win.on('resized', saveWindowState)

  // The overlay lives in the tray; closing it should park it, not lose the timer.
  win.on('close', (event) => {
    if (quitting) return
    event.preventDefault()
    saveWindowState()
    win.hide()
  })

  const devUrl = process.env['ELECTRON_RENDERER_URL']
  if (devUrl) {
    void win.loadURL(devUrl)
  } else {
    void win.loadFile(path.join(__dirname, '../renderer/index.html'))
  }

  return win
}

function toggleVisibility(): void {
  if (!mainWindow) return
  if (mainWindow.isVisible() && mainWindow.isFocused()) {
    mainWindow.hide()
  } else {
    mainWindow.show()
    mainWindow.focus()
  }
}

function setAlwaysOnTop(value: boolean): void {
  store.dispatch({ type: 'settings/patch', patch: { alwaysOnTop: value } })
  mainWindow?.setAlwaysOnTop(value, 'screen-saver')
  refreshTray()
}

/* ------------------------------------------------------------------ */
/* Tray                                                                */
/* ------------------------------------------------------------------ */

function trayIcon(active: boolean): Electron.NativeImage {
  const file = path.join(app.getAppPath(), 'resources', active ? 'tray-active.png' : 'tray.png')
  const image = nativeImage.createFromPath(file)
  return image.isEmpty() ? image : image.resize({ width: 16, height: 16 })
}

function refreshTray(): void {
  if (!tray) return
  const db = store.get()
  const running = db.activeTimer
  const task = running ? db.tasks.find((t) => t.id === running.taskId) : null

  tray.setImage(trayIcon(Boolean(running)))
  tray.setToolTip(task ? `Tracking: ${task.title}` : 'Timesheet Logger — idle')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: task ? `Tracking: ${task.title}` : 'No timer running', enabled: false },
      running
        ? { label: 'Stop timer', click: () => store.dispatch({ type: 'timer/stop' }) }
        : { label: 'Stop timer', enabled: false },
      { type: 'separator' },
      { label: 'Show overlay', click: () => mainWindow?.show() },
      {
        label: 'Always on top',
        type: 'checkbox',
        checked: db.settings.alwaysOnTop,
        click: (item) => setAlwaysOnTop(item.checked)
      },
      { type: 'separator' },
      { label: 'Open data folder', click: () => void shell.openPath(path.dirname(resolvePaths().dataFile)) },
      { label: 'Open exports folder', click: () => void shell.openPath(resolvePaths().exportsDir) },
      { type: 'separator' },
      {
        label: 'Quit',
        click: () => {
          quitting = true
          app.quit()
        }
      }
    ])
  )
}

function createTray(): void {
  tray = new Tray(trayIcon(false))
  tray.on('click', toggleVisibility)
  refreshTray()
}

/* ------------------------------------------------------------------ */
/* Global hotkeys                                                      */
/* ------------------------------------------------------------------ */

function mostRecentTaskId(db: Database): string | null {
  const last = [...db.entries].sort((a, b) => (b.endedAt ?? 0) - (a.endedAt ?? 0))[0]
  return last?.taskId ?? db.tasks.find((t) => !t.archived && !t.done)?.id ?? null
}

function registerShortcuts(): void {
  const bindings: Array<[string, () => void]> = [
    ['Control+Alt+T', toggleVisibility],
    ['Control+Alt+A', () => setAlwaysOnTop(!store.get().settings.alwaysOnTop)],
    [
      'Control+Alt+S',
      () => {
        const db = store.get()
        if (db.activeTimer) {
          store.dispatch({ type: 'timer/stop' })
          return
        }
        const taskId = mostRecentTaskId(db)
        if (taskId) store.dispatch({ type: 'timer/start', taskId })
        else mainWindow?.show()
      }
    ]
  ]

  for (const [accelerator, handler] of bindings) {
    // A hotkey already owned by another app shouldn't stop the app booting.
    if (!globalShortcut.register(accelerator, handler)) {
      console.warn(`Could not register global shortcut ${accelerator}; it is probably taken.`)
    }
  }
}

/* ------------------------------------------------------------------ */
/* IPC                                                                 */
/* ------------------------------------------------------------------ */

function registerIpc(): void {
  ipcMain.handle('app:bootstrap', () => ({
    db: store.get(),
    paths: resolvePaths()
  }))

  ipcMain.handle('db:dispatch', (_event, action: Action) => store.dispatch(action))

  ipcMain.handle('window:alwaysOnTop', (_event, value: boolean) => {
    setAlwaysOnTop(value)
    return value
  })

  ipcMain.handle('window:opacity', (_event, value: number) => {
    const clamped = Math.min(1, Math.max(0.3, value))
    store.dispatch({ type: 'settings/patch', patch: { opacity: clamped } })
    mainWindow?.setOpacity(clamped)
    return clamped
  })

  ipcMain.handle('window:minimize', () => mainWindow?.minimize())
  ipcMain.handle('window:hide', () => mainWindow?.hide())

  ipcMain.handle('window:wide', (_event, wide: boolean) => {
    if (!mainWindow) return
    const bounds = mainWindow.getBounds()
    const work = screen.getDisplayMatching(bounds).workArea
    const width = wide ? Math.min(WIDE_WIDTH, work.width - 40) : DEFAULT_BOUNDS.width
    mainWindow.setBounds({
      ...bounds,
      width,
      x: Math.min(bounds.x, work.x + work.width - width)
    })
    saveWindowState()
  })

  ipcMain.handle('export:write', (_event, payload: { filename: string; markdown: string }) => {
    const { exportsDir } = resolvePaths()
    fs.mkdirSync(exportsDir, { recursive: true })
    const filePath = path.join(exportsDir, payload.filename)
    fs.writeFileSync(filePath, payload.markdown, 'utf8')
    return filePath
  })

  ipcMain.handle('shell:reveal', (_event, filePath: string) => shell.showItemInFolder(filePath))
  ipcMain.handle('shell:open', (_event, target: string) => shell.openPath(target))
  ipcMain.handle('clipboard:write', (_event, text: string) => clipboard.writeText(text))
}

/* ------------------------------------------------------------------ */
/* Lifecycle                                                           */
/* ------------------------------------------------------------------ */

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    mainWindow?.show()
    mainWindow?.focus()
  })

  void app.whenReady().then(() => {
    const db = store.load()
    registerIpc()
    mainWindow = createWindow(db)
    createTray()
    registerShortcuts()

    store.subscribe((next) => {
      refreshTray()
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('db:changed', next)
      }
    })

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) mainWindow = createWindow(store.get())
    })
  })
}

app.on('before-quit', () => {
  quitting = true
  saveWindowState()
  store.flush()
})

app.on('will-quit', () => globalShortcut.unregisterAll())

// Keeping the process alive when the overlay is hidden is the whole point of a
// tray app, so the usual window-all-closed quit is deliberately omitted.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin' && quitting) app.quit()
})
