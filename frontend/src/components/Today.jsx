import { useEffect, useState } from 'react'
import { api, clockTime, money, money0, percent, signed, signedMoney, tone } from '../api.js'
import { Empty, Notice, Section, Stat, Switch, Working } from './bits.jsx'

export default function Today({ data, config, onRefresh, onGoTest }) {
  const [checking, setChecking] = useState(false)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [buying, setBuying] = useState(null)

  const account = data?.account
  const positions = data?.positions || []
  const status = data?.status
  const round = data?.last_round
  const marketOpen = data?.market?.open

  async function checkNow() {
    setChecking(true)
    setError(null)
    try {
      await api.checkNow()
      await onRefresh()
    } catch (e) { setError(e.message) } finally { setChecking(false) }
  }

  async function sell(symbol) {
    if (!confirm(`Sell all your ${symbol} right now, at whatever the market pays?`)) return
    setBusy(symbol)
    setError(null)
    try {
      await api.sell(symbol)
      await onRefresh()
    } catch (e) { setError(e.message) } finally { setBusy(null) }
  }

  async function toggleTrading(on) {
    setError(null)
    try {
      await api.saveConfig({ auto_trade: on })
      await onRefresh()
    } catch (e) { setError(e.message) }
  }

  if (!data) return <div className="page"><Working>Loading…</Working></div>

  if (data.error) {
    return (
      <div className="page">
        <header className="page-head">
          <span className="eyebrow">Today</span>
          <h1>Not connected yet</h1>
        </header>
        <Notice kind="bad" title="Alpaca is not connected.">{data.error}</Notice>
        <div className="card">
          <h3>How to fix it</h3>
          <ol className="recipe">
            <li><span>Sign up for a free Alpaca account and open the <strong>paper trading</strong> dashboard.</span></li>
            <li><span>Generate an API key pair. The key starts with <code>PK</code> for paper accounts.</span></li>
            <li><span>Put both values in the <code>.env</code> file at the project root, next to <code>ALPACA_API_KEY</code> and <code>ALPACA_SECRET_KEY</code>.</span></li>
            <li><span>Restart the app. This page will fill in on its own.</span></li>
          </ol>
        </div>
      </div>
    )
  }

  const dayChange = account?.day_change ?? 0
  const held = positions.reduce((sum, p) => sum + p.profit, 0)

  return (
    <div className="page">
      <header className="page-head">
        <span className="eyebrow">
          Today · {marketOpen ? 'the market is open' : 'the market is closed'}
        </span>
        <h1>Where you stand</h1>
      </header>

      {error && <Notice kind="bad" title="That did not work.">{error}</Notice>}

      <Section>
        <div className="card">
          <div className="row" style={{ alignItems: 'flex-end', gap: 28 }}>
            <div>
              <div className="stat-label">Account value</div>
              <div className="headline-number">{money0(account?.value)}</div>
              <div className={`small ${tone(dayChange)}`} style={{ marginTop: 6 }}>
                {signedMoney(dayChange)} today
              </div>
            </div>
            <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
              <div className="stat-label">Cash not invested</div>
              <div className="stat-value">{money0(account?.cash)}</div>
              <div className="tiny" style={{ marginTop: 4 }}>
                {positions.length} position{positions.length === 1 ? '' : 's'} open
              </div>
            </div>
          </div>
        </div>
      </Section>

      <Section eyebrow="The bot" question="What it is set up to do">
        <div className="card">
          <div className="grid-2" style={{ alignItems: 'start' }}>
            <div>
              <Switch
                checked={config?.auto_trade}
                onChange={toggleTrading}
                label={config?.auto_trade ? 'Trading is on' : 'Suggest only'}
                help={
                  config?.auto_trade
                    ? 'Each morning the bot places paper orders on its own. You can turn this off at any time.'
                    : 'The bot works out what it would buy and tells you, but places no orders. This is the safe way to start.'
                }
              />
              <div className="row" style={{ marginTop: 18 }}>
                <button className="btn ghost small" onClick={checkNow} disabled={checking}>
                  {checking ? 'Checking…' : 'Check now'}
                </button>
                {checking && <Working>Reading prices and applying the rules…</Working>}
              </div>
            </div>

            <dl style={{ display: 'grid', gap: 8 }}>
              <Row label="Strategy" value={status?.strategy} />
              <Row label="Checks each day at" value={`${status?.run_at} New York time`} />
              <Row label="Last checked" value={status?.last_check ? clockTime(status.last_check) : 'not yet today'} />
              <Row label="Watching" value={(status?.watchlist || []).join(', ') || 'nothing yet'} />
            </dl>
          </div>
        </div>
      </Section>

      {buying && (
        <BuyDialog
          symbol={buying}
          onClose={() => setBuying(null)}
          onDone={async () => { setBuying(null); await onRefresh() }}
        />
      )}

      <Section eyebrow="Holdings" question="What you own right now">
        <div className="row" style={{ marginBottom: 12 }}>
          <button className="btn ghost small" onClick={() => setBuying('')}>
            Buy a stock by hand
          </button>
          <span className="tiny">
            Sized by your risk setting, with the same stop and target the bot uses.
          </span>
        </div>
        {positions.length === 0 ? (
          <Empty title="You own nothing at the moment">
            That is the normal state for a swing strategy. It waits for its rules
            to line up, which can take days or weeks.
          </Empty>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Stock</th>
                    <th className="n">Shares</th>
                    <th className="n">Paid</th>
                    <th className="n">Worth now</th>
                    <th className="n">Value</th>
                    <th className="n">Profit</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p) => (
                    <tr key={p.symbol}>
                      <td className="sym">{p.symbol}</td>
                      <td className="n">{p.shares}</td>
                      <td className="n">{money(p.bought_at)}</td>
                      <td className="n">{money(p.price_now)}</td>
                      <td className="n">{money0(p.value)}</td>
                      <td className={`n ${tone(p.profit)}`}>
                        {signedMoney(p.profit)}
                        <div className="tiny">{signed(p.profit_percent)}</div>
                      </td>
                      <td className="n">
                        <button
                          className="btn danger small"
                          onClick={() => sell(p.symbol)}
                          disabled={busy === p.symbol}
                        >
                          {busy === p.symbol ? 'Selling…' : 'Sell now'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="small" style={{ marginTop: 10 }}>
              Together these are {signedMoney(held)} on paper. Each one already has
              a stop and a target sitting at the broker, so they sell themselves
              even if this app is closed.
            </p>
          </>
        )}
      </Section>

      {round ? (
        <Section eyebrow="Latest check" question={`What the bot saw on ${round.considered?.[0]?.as_of || 'the last close'}`}>
          {round.buys.length === 0 && (
            <Notice title="No buys today.">
              Nothing on the watchlist met all the rules. Reasons are listed below.
            </Notice>
          )}
          <div className="signals">
            {[...round.considered]
              .sort((a, b) => Number(b.buy) - Number(a.buy))
              .map((row) => {
                const placed = round.buys.find((b) => b.symbol === row.symbol)
                return (
                  <div key={row.symbol} className={`signal ${row.buy ? 'is-buy' : ''}`}>
                    <span className="signal-sym">{row.symbol}</span>
                    <span className="signal-price">{money(row.price)}</span>
                    <span className="signal-why">
                      {row.reason}
                      {placed?.placed && (
                        <div className="tiny" style={{ marginTop: 3 }}>
                          Bought {placed.shares} shares near {money(placed.entry)} ·
                          stop {money(placed.stop)} · target {money(placed.target)} ·
                          risking {money0(placed.risking)}
                        </div>
                      )}
                      {placed && !placed.placed && (
                        <div className="tiny" style={{ marginTop: 3 }}>
                          {placed.error
                            ? placed.error
                            : `Would buy ${placed.shares} shares near ${money(placed.entry)}, stop ${money(placed.stop)}, target ${money(placed.target)}. Trading is off, so nothing was ordered.`}
                        </div>
                      )}
                    </span>
                    {/* Status, never an action. An earlier version showed a
                        green "buy" pill here, which read as a button and did
                        nothing when pressed. Buying is the button below. */}
                    <span className="signal-end">
                      <span className={`pill ${row.buy ? 'buy' : 'wait'}`}>
                        {placed?.placed ? 'bought' : row.buy ? 'signal' : 'waiting'}
                      </span>
                      {row.buy && !placed?.placed && (
                        <button
                          className="btn small"
                          onClick={() => setBuying(row.symbol)}
                          title={
                            marketOpen
                              ? `Review and buy ${row.symbol}`
                              : 'The market is closed — you can review the plan, but not order yet'
                          }
                        >
                          Buy {row.symbol}
                        </button>
                      )}
                    </span>
                  </div>
                )
              })}
          </div>
        </Section>
      ) : (
        <Section eyebrow="Latest check" question="The bot has not looked yet today">
          <Empty title="Nothing checked yet">
            Press <strong>Check now</strong> above to apply your rules to the latest
            prices, or wait for the daily run at {status?.run_at} New York time.
            {' '}Not sure the strategy is any good yet?{' '}
            <button className="btn ghost small" onClick={onGoTest}>Test it on the past first</button>
          </Empty>
        </Section>
      )}

      {data.activity?.length > 0 && (
        <Section eyebrow="Recent activity">
          <div className="card">
            <div className="log">
              {data.activity.map((a) => (
                <div key={a.id} className={`log-line ${a.level}`}>
                  <time>{clockTime(a.at)}</time>
                  <span>{a.message}</span>
                </div>
              ))}
            </div>
          </div>
        </Section>
      )}
    </div>
  )
}

/**
 * Buying by hand. Shows the whole plan — cost, stop, target, and the exact
 * dollars at risk — before anything is placed, and says plainly when the
 * strategy disagrees with the choice.
 */
function BuyDialog({ symbol, onClose, onDone }) {
  const [typed, setTyped] = useState(symbol || '')
  const [plan, setPlan] = useState(null)
  const [shares, setShares] = useState('')
  const [loading, setLoading] = useState(false)
  const [placing, setPlacing] = useState(false)
  const [error, setError] = useState(null)

  async function look(sym) {
    const clean = (sym || '').trim().toUpperCase()
    if (!clean) return
    setLoading(true)
    setError(null)
    setPlan(null)
    try {
      const p = await api.previewBuy(clean)
      setPlan(p)
      setShares(String(p.shares))
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  useEffect(() => { if (symbol) look(symbol) }, [symbol])

  async function place() {
    setPlacing(true)
    setError(null)
    try {
      await api.buy(plan.symbol, Number(shares))
      await onDone()
    } catch (e) { setError(e.message); setPlacing(false) }
  }

  const cost = plan ? plan.entry * Number(shares || 0) : 0
  const risking = plan ? (plan.entry - plan.stop) * Number(shares || 0) : 0

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-label="Buy a stock by hand"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="card-head">
          <div>
            <span className="eyebrow">Buy by hand</span>
            <h2>{plan ? `Buy ${plan.symbol}` : 'Which stock?'}</h2>
          </div>
          <button className="btn ghost small" onClick={onClose}>Cancel</button>
        </div>

        {error && <Notice kind="bad">{error}</Notice>}

        {!symbol && (
          <div className="row" style={{ gap: 8, flexWrap: 'nowrap', marginBottom: 16 }}>
            <input
              type="text" className="num" placeholder="AAPL" autoFocus
              autoComplete="off" spellCheck="false" aria-label="Stock symbol"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); look(typed) } }}
            />
            <button className="btn ghost" style={{ flex: 'none' }} onClick={() => look(typed)}>
              Look it up
            </button>
          </div>
        )}

        {loading && <Working>Reading prices…</Working>}

        {plan && (
          <>
            <div className={`notice ${plan.strategy_agrees ? 'good' : 'warn'}`}>
              <div>
                <strong>
                  {plan.strategy_agrees
                    ? 'Your strategy agrees with this.'
                    : 'Your strategy would not buy this today.'}
                </strong>{' '}
                {plan.strategy_says}
              </div>
            </div>

            <div className="field" style={{ maxWidth: 180 }}>
              <label className="field-label" htmlFor="hand-shares">Shares</label>
              <span className="field-help">
                Suggested by your risk setting. Change it if you want.
              </span>
              <input
                id="hand-shares" type="number" className="num" min="1" step="1"
                value={shares} onChange={(e) => setShares(e.target.value)}
              />
            </div>

            <div className="stat-row" style={{ marginTop: 4 }}>
              <Stat label="Price" value={money(plan.price)} note={`as of ${plan.as_of}`} />
              <Stat label="This will cost about" value={money0(cost)} />
              <Stat
                label="Sell at a profit"
                value={money(plan.target)}
                note={`+${percent(((plan.target / plan.entry) - 1) * 100)}`}
                tone="gain"
              />
              <Stat
                label="Sell at a loss"
                value={money(plan.stop)}
                note={`−${percent((1 - plan.stop / plan.entry) * 100)}`}
                tone="loss"
              />
              <Stat
                label="Most you can lose"
                value={money0(risking)}
                note="if the stop is hit"
                what="Both orders are placed at Alpaca with the buy, so they work even if this app is closed. A price gap overnight can still cost more than this."
                tone="loss"
              />
            </div>

            {!plan.market_open && (
              <Notice kind="warn" title="The market is closed.">
                An order placed now would wait until it reopens and fill at a
                price nobody can see yet. Come back after{' '}
                {plan.next_open ? clockTime(plan.next_open) : 'the next open'}.
              </Notice>
            )}

            <div className="row" style={{ marginTop: 18 }}>
              <button
                className="btn"
                onClick={place}
                disabled={placing || Number(shares) < 1 || !plan.market_open}
              >
                {placing
                  ? 'Placing…'
                  : plan.market_open
                    ? `Buy ${shares} ${plan.symbol} with paper money`
                    : 'Closed until the market opens'}
              </button>
              <button className="btn ghost" onClick={onClose}>Cancel</button>
            </div>
            <p className="tiny" style={{ marginTop: 10 }}>
              Recorded in the journal as bought by hand, so it is never counted as
              evidence for or against the strategy.
            </p>
          </>
        )}
      </div>
    </div>
  )
}

function Row({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, borderBottom: '1px solid var(--rule-soft)', paddingBottom: 7 }}>
      <dt className="small">{label}</dt>
      <dd className="small" style={{ color: 'var(--ink)', textAlign: 'right', fontWeight: 500 }}>{value}</dd>
    </div>
  )
}
