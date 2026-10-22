import { useState } from 'react'
import { useStore } from '../state'

export default function TitleBar(): JSX.Element {
  const { db } = useStore()
  const [wide, setWide] = useState(false)
  const pinned = db.settings.alwaysOnTop

  return (
    <header className="titlebar">
      <div className="titlebar__drag">
        <span className="titlebar__title">Timesheet</span>
      </div>

      <button
        className={pinned ? 'iconbtn iconbtn--on' : 'iconbtn'}
        title={`Always on top: ${pinned ? 'on' : 'off'} (Ctrl+Alt+A)`}
        onClick={() => void window.api.window.setAlwaysOnTop(!pinned)}
      >
        {pinned ? '📌' : '📍'}
      </button>

      <button
        className={wide ? 'iconbtn iconbtn--on' : 'iconbtn'}
        title={wide ? 'Shrink to overlay width' : 'Widen for planning'}
        onClick={() => {
          setWide(!wide)
          void window.api.window.setWide(!wide)
        }}
      >
        {wide ? '⇥' : '⇤'}
      </button>

      <button className="iconbtn" title="Minimise" onClick={() => void window.api.window.minimize()}>
        –
      </button>

      <button
        className="iconbtn iconbtn--close"
        title="Hide to tray (Ctrl+Alt+T to bring back)"
        onClick={() => void window.api.window.hide()}
      >
        ×
      </button>
    </header>
  )
}
