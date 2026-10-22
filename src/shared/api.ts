import type { Action, Database, Paths } from './types'

/**
 * The preload bridge, declared without touching Electron's types so the
 * renderer can consume it without pulling Node typings into the web build.
 */
export interface Api {
  bootstrap(): Promise<{ db: Database; paths: Paths }>
  dispatch(action: Action): Promise<Database>
  /** Subscribes to database changes; returns an unsubscribe function. */
  onChange(callback: (db: Database) => void): () => void
  window: {
    setAlwaysOnTop(value: boolean): Promise<boolean>
    setOpacity(value: number): Promise<number>
    setWide(wide: boolean): Promise<void>
    minimize(): Promise<void>
    hide(): Promise<void>
  }
  /** Writes Markdown into the exports folder and returns the full path. */
  writeExport(filename: string, markdown: string): Promise<string>
  reveal(filePath: string): Promise<void>
  openPath(target: string): Promise<void>
  copy(text: string): Promise<void>
}

declare global {
  interface Window {
    api: Api
  }
}
