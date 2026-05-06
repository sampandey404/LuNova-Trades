import { useState } from 'react'
import './App.css'

const TABS = [
  { id: 'wheel',    label: '⚡ Wheel Tracker',  src: '/LuNova-Trades/tools/wheel-tracker.html' },
  { id: 'stocks',   label: '📈 Stock Picks',     src: '/LuNova-Trades/tools/stock-picks/index.html' },
  { id: 'research', label: '🔬 Research Desk',   src: '/LuNova-Trades/tools/research-desk/index.html' },
]

function App() {
  const [active, setActive] = useState('wheel')

  return (
    <>
      <nav className="nav">
        <span className="nav-brand">LuNova Trades</span>
        {TABS.map(t => (
          <button
            key={t.id}
            className={`nav-tab${active === t.id ? ' active' : ''}`}
            onClick={() => setActive(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div className="frame-wrap">
        {TABS.map(t => (
          <iframe
            key={t.id}
            src={t.src}
            className="tool-frame"
            title={t.label}
            style={{ display: active === t.id ? 'block' : 'none' }}
          />
        ))}
      </div>
    </>
  )
}

export default App
