import { contextBridge, ipcRenderer } from 'electron'
import type { Action, Database } from '@shared/types'
import type { Api } from '@shared/api'

const api: Api = {
  bootstrap: () => ipcRenderer.invoke('app:bootstrap'),

  dispatch: (action: Action) => ipcRenderer.invoke('db:dispatch', action),

  // Main owns the database, so tray and hotkey changes come back through here
  // as well as the renderer's own dispatches.
  onChange: (callback) => {
    const handler = (_event: unknown, db: Database): void => callback(db)
    ipcRenderer.on('db:changed', handler)
    return () => {
      ipcRenderer.off('db:changed', handler)
    }
  },

  window: {
    setAlwaysOnTop: (value) => ipcRenderer.invoke('window:alwaysOnTop', value),
    setOpacity: (value) => ipcRenderer.invoke('window:opacity', value),
    setWide: (wide) => ipcRenderer.invoke('window:wide', wide),
    minimize: () => ipcRenderer.invoke('window:minimize'),
    hide: () => ipcRenderer.invoke('window:hide')
  },

  writeExport: (filename, markdown) => ipcRenderer.invoke('export:write', { filename, markdown }),

  reveal: (filePath) => ipcRenderer.invoke('shell:reveal', filePath),
  openPath: (target) => ipcRenderer.invoke('shell:open', target),
  copy: (text) => ipcRenderer.invoke('clipboard:write', text)
}

contextBridge.exposeInMainWorld('api', api)
