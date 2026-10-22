import { useState } from 'react'
import { StoreProvider } from './state'
import TitleBar from './components/TitleBar'
import TimerBar from './components/TimerBar'
import TodayView from './components/TodayView'
import WeekView from './components/WeekView'
import SetupView from './components/SetupView'
import ExportView from './components/ExportView'

type Tab = 'today' | 'week' | 'setup' | 'export'

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'today', label: 'Today' },
  { id: 'week', label: 'Week' },
  { id: 'export', label: 'Export' },
  { id: 'setup', label: 'Setup' }
]

function Shell(): JSX.Element {
  const [tab, setTab] = useState<Tab>('today')

  return (
    <div className="app">
      <TitleBar />
      <TimerBar />
      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? 'tab tab--active' : 'tab'}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <main className="content">
        {tab === 'today' && <TodayView />}
        {tab === 'week' && <WeekView />}
        {tab === 'export' && <ExportView />}
        {tab === 'setup' && <SetupView />}
      </main>
    </div>
  )
}

export default function App(): JSX.Element {
  return <StoreProvider>{(ready) => (ready ? <Shell /> : <div className="boot">Loading…</div>)}</StoreProvider>
}
