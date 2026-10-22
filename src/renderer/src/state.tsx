import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { Action, Database, Paths } from '@shared/types'

interface Store {
  db: Database
  paths: Paths
  dispatch: (action: Action) => void
}

const StoreContext = createContext<Store | null>(null)

export function StoreProvider({ children }: { children: (ready: boolean) => ReactNode }): JSX.Element {
  const [db, setDb] = useState<Database | null>(null)
  const [paths, setPaths] = useState<Paths | null>(null)

  useEffect(() => {
    void window.api.bootstrap().then((boot) => {
      setDb(boot.db)
      setPaths(boot.paths)
    })
    // Main broadcasts after tray/hotkey changes too, not just our own dispatches.
    return window.api.onChange(setDb)
  }, [])

  const dispatch = useCallback((action: Action) => {
    void window.api.dispatch(action).then(setDb)
  }, [])

  const value = useMemo(() => (db && paths ? { db, paths, dispatch } : null), [db, paths, dispatch])

  return <StoreContext.Provider value={value}>{children(value !== null)}</StoreContext.Provider>
}

export function useStore(): Store {
  const store = useContext(StoreContext)
  if (!store) throw new Error('useStore must be used inside a ready StoreProvider')
  return store
}

/** Re-renders on an interval so running timers tick without their own state. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const handle = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(handle)
  }, [intervalMs])
  return now
}
