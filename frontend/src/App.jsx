import { useCallback, useEffect, useState } from 'react'
import { api, money0 } from './api.js'
import Today from './components/Today.jsx'
import Test from './components/Test.jsx'
import StrategyPage from './components/StrategyPage.jsx'
import Ideas from './components/Ideas.jsx'
import Journal from './components/Journal.jsx'
import { Notice } from './components/bits.jsx'

const TABS = [
  { id: 'today', key: '1', label: 'Today' },
  { id: 'ideas', key: '2', label: 'Trade ideas' },
  { id: 'test', key: '3', label: 'Test an idea' },
  { id: 'strategy', key: '4', label: 'Strategy' },
  { id: 'journal', key: '5', label: 'Journal' },
]

export default function App() {
  const [tab, setTab] = useState('today')
  const [overview, setOverview] = useState(null)
  const [config, setConfig] = useState(null)
  const [strategies, setStrategies] = useState(null)
  const [buySymbol, setBuySymbol] = useState(null)
  const [theme, setTheme] = useState(() => localStorage.getItem('swing-theme') || 'system')
  const [fatal, setFatal] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const [o, c] = await Promise.all([api.overview(), api.config()])
      setOverview(o)
      setConfig(c)
    } catch (e) {
      setFatal(e.message)
    }
  }, [])

  useEffect(() => {
    api.strategies().then(setStrategies).catch((e) => setFatal(e.message))
    refresh()
  }, [refresh])

  // A swing bot changes once a day. Polling every 30 seconds would be theatre;
  // a minute is already generous, and it keeps the account value honest while
  // the market is open.
  useEffect(() => {
    const id = setInterval(refresh, 60_000)
    return () => clearInterval(id)
  }, [refresh])

  useEffect(() => {
    if (theme === 'system') document.documentElement.removeAttribute('data-theme')
    else document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('swing-theme', theme)
  }, [theme])

  useEffect(() => {
    function onKey(e) {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return
      const hit = TABS.find((t) => t.key === e.key)
      if (hit) setTab(hit.id)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const live = overview?.account && overview.account.mode === 'LIVE'

  return (
    <>
      <div className={`ribbon ${live ? 'is-live' : ''}`}>
        {live ? (
          <>
            <strong>REAL MONEY</strong>
            <span>This account trades for real. Every order below spends actual cash.</span>
          </>
        ) : (
          <>
            <strong>PAPER MONEY</strong>
            <span>Nothing here is real. Orders go to Alpaca's practice account.</span>
          </>
        )}
        <span className="ribbon-spacer" />
        {overview?.account && <span>Account {money0(overview.account.value)}</span>}
        <button
          onClick={() => setTheme(theme === 'dark' ? 'light' : theme === 'light' ? 'system' : 'dark')}
          title="Switch between light, dark, and matching your system"
        >
          {theme === 'system' ? 'Auto' : theme === 'dark' ? 'Dark' : 'Light'}
        </button>
      </div>

      <div className="shell">
        <nav className="spine" aria-label="Sections">
          <div className="brand">
            <div className="brand-mark">
              <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
                <polyline
                  points="3,25 11,16 18,20 29,6"
                  fill="none" stroke="var(--tide)" strokeWidth="3.5"
                  strokeLinecap="square" strokeLinejoin="miter"
                />
              </svg>
              Swing
            </div>
            <div className="brand-sub">Trades that last days, not seconds</div>
          </div>

          <div className="spine-nav">
            {TABS.map((t) => (
              <button
                key={t.id}
                className="tab"
                aria-current={tab === t.id ? 'page' : undefined}
                onClick={() => setTab(t.id)}
              >
                <span className="tab-key" aria-hidden="true">{t.key}</span>
                {t.label}
              </button>
            ))}
          </div>

          <div className="spine-foot">
            <p className="tiny">
              {config?.auto_trade
                ? 'Trading is on. The bot places paper orders each morning.'
                : 'Suggest only. Nothing will be ordered.'}
            </p>
          </div>
        </nav>

        <main className="main">
          {fatal && (
            <div className="page">
              <Notice kind="bad" title="Cannot reach the app.">{fatal}</Notice>
            </div>
          )}

          {!fatal && tab === 'today' && (
            <Today
              data={overview}
              config={config}
              onRefresh={refresh}
              onGoTest={() => setTab('test')}
              openBuy={buySymbol}
              onBuyHandled={() => setBuySymbol(null)}
            />
          )}
          {!fatal && tab === 'ideas' && (
            <Ideas
              config={config}
              onBuy={(symbol) => { setBuySymbol(symbol); setTab('today') }}
            />
          )}
          {/* Test seeds its form from the saved settings, so it must not mount
              before they have arrived or it would start from stale defaults. */}
          {!fatal && tab === 'test' && config && strategies && (
            <Test
              config={config}
              strategies={strategies}
              onNeedConfig={() => setTab('strategy')}
            />
          )}
          {!fatal && tab === 'strategy' && (
            <StrategyPage config={config} strategies={strategies} onSaved={refresh} />
          )}
          {!fatal && tab === 'journal' && <Journal />}
        </main>
      </div>
    </>
  )
}
