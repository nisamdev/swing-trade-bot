import { useEffect, useRef, useState } from 'react'
import {
  api, money, money0, percent, shortDate, signed, signedMoney, tone,
} from '../api.js'
import { EquityCurve } from './charts.jsx'
import {
  Empty, Notice, RuleField, Section, Segmented, Stat, SymbolInput, Working,
} from './bits.jsx'

const PERIODS = [
  { value: 1, label: '1 year' },
  { value: 3, label: '3 years' },
  { value: 5, label: '5 years' },
  { value: 10, label: '10 years' },
]

const MARK = { good: '✓', warn: '!', bad: '×', info: 'i' }

export default function Test({ config, strategies, onNeedConfig }) {
  const [symbols, setSymbols] = useState(config?.watchlist?.slice(0, 5) || ['SPY'])
  const [years, setYears] = useState(5)
  const [cash, setCash] = useState(config?.money?.starting_cash ?? 10000)
  const [risk, setRisk] = useState(config?.money?.risk_percent ?? 1)
  const [overrides, setOverrides] = useState({})
  const [tweaking, setTweaking] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState([])
  const answerRef = useRef(null)

  const strategy = strategies?.find((s) => s.key === config?.rules?.strategy)
  const rules = { ...config?.rules, ...overrides }

  useEffect(() => { api.savedBacktests().then(setSaved).catch(() => {}) }, [])

  async function run(e) {
    e?.preventDefault()
    setRunning(true)
    setError(null)
    try {
      const out = await api.backtest({
        symbols,
        years,
        rules: overrides,
        money: { starting_cash: Number(cash), risk_percent: Number(risk) },
      })
      setResult(out)
      // Land on the answer, not back at the form. The verdict is the whole
      // reason the button was pressed.
      requestAnimationFrame(() =>
        answerRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      )
    } catch (err) {
      setError(err.message)
      setResult(null)
    } finally {
      setRunning(false)
    }
  }

  async function keep() {
    const label = prompt(
      'Name this test so you can compare it later:',
      `${strategy?.name || 'Test'} · ${symbols.join(' ')} · ${years}y`
    )
    if (!label) return
    await api.backtest({
      symbols, years, rules: overrides,
      money: { starting_cash: Number(cash), risk_percent: Number(risk) },
      save_as: label,
    })
    setSaved(await api.savedBacktests())
  }

  if (!config || !strategies) return <Working>Loading your settings…</Working>

  return (
    <div className="page">
      <header className="page-head">
        <span className="eyebrow">Step one</span>
        <h1>Test the idea on the past</h1>
        <p className="lede">
          Run your strategy over years of real prices and see what it would have
          done — before a single dollar, real or pretend, is involved. The test
          buys at the next morning's price, pays a spread on every trade, and
          assumes the worst when a day is ambiguous.
        </p>
      </header>

      {error && <Notice kind="bad" title="That test could not run.">{error}</Notice>}

      <form onSubmit={run}>
        <Section eyebrow="What to test" question="Which stocks, over how long?">
          <div className="card">
            <div className="grid-2">
              <div>
                <div className="field">
                  <span className="field-label">Stocks</span>
                  <span className="field-help">
                    Up to 20. They share one pot of money, just like a real account.
                  </span>
                  <SymbolInput symbols={symbols} onChange={setSymbols} />
                </div>
              </div>

              <div>
                <div className="field">
                  <span className="field-label">How far back</span>
                  <span className="field-help">
                    Longer is better. A strategy that only works in one good year is not a strategy.
                  </span>
                  <Segmented
                    options={PERIODS}
                    value={years}
                    onChange={setYears}
                    ariaLabel="How far back to test"
                  />
                </div>

                <div className="row" style={{ gap: 16, alignItems: 'flex-start' }}>
                  <div className="field" style={{ flex: 1, minWidth: 130 }}>
                    <label className="field-label" htmlFor="cash">Starting money</label>
                    <input
                      id="cash" type="number" className="num" min="100" step="100"
                      value={cash} onChange={(e) => setCash(e.target.value)}
                    />
                  </div>
                  <div className="field" style={{ flex: 1, minWidth: 130 }}>
                    <label className="field-label" htmlFor="risk">Risk per trade</label>
                    <input
                      id="risk" type="number" className="num" min="0.1" max="10" step="0.1"
                      value={risk} onChange={(e) => setRisk(e.target.value)}
                    />
                    <span className="field-help" style={{ marginTop: 4 }}>
                      Percent of the account you accept losing if the trade goes wrong.
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </Section>

        <Section eyebrow="The rules" question={strategy ? `Using “${strategy.name}”` : 'Strategy'}>
          <div className="card">
            <p className="small">{strategy?.tagline}</p>
            <ol className="recipe">
              {strategy?.how_it_works.map((step) => <li key={step}><span>{step}</span></li>)}
            </ol>
            <div className="row" style={{ marginTop: 16 }}>
              <button type="button" className="btn ghost small" onClick={() => setTweaking(!tweaking)}>
                {tweaking ? 'Hide the dials' : 'Adjust the dials for this test'}
              </button>
              <button type="button" className="btn ghost small" onClick={onNeedConfig}>
                Switch strategy
              </button>
              {Object.keys(overrides).length > 0 && (
                <button type="button" className="btn ghost small" onClick={() => setOverrides({})}>
                  Reset to saved settings
                </button>
              )}
            </div>

            {tweaking && (
              <div className="grid-2" style={{ marginTop: 18 }}>
                {strategy?.settings.map((spec) => (
                  <RuleField
                    key={spec.key}
                    spec={spec}
                    value={rules[spec.key]}
                    onChange={(k, v) => setOverrides((o) => ({ ...o, [k]: v }))}
                  />
                ))}
              </div>
            )}
            {Object.keys(overrides).length > 0 && (
              <p className="tiny" style={{ marginTop: 10 }}>
                These changes apply to this test only. Save them on the Strategy page to keep them.
              </p>
            )}
          </div>
        </Section>

        <div className="row" style={{ marginBottom: 34 }}>
          <button type="submit" className="btn" disabled={running || !symbols.length}>
            {running ? 'Running…' : 'Run the test'}
          </button>
          {running && <Working>Downloading prices and replaying every day…</Working>}
          {result && !running && (
            <button type="button" className="btn ghost" onClick={keep}>Save this result</button>
          )}
        </div>
      </form>

      <div ref={answerRef} style={{ scrollMarginTop: 20 }}>
        {result && <Results result={result} />}
      </div>

      {saved.length > 0 && (
        <Section eyebrow="Your notebook" question="Tests you saved">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Test</th>
                  <th className="n">Return</th>
                  <th className="n">Beat holding by</th>
                  <th className="n">Trades</th>
                  <th className="n">Worst drop</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {saved.map((row) => (
                  <tr key={row.id}>
                    <td>
                      {row.label}
                      <div className="tiny">{shortDate(row.at)}</div>
                    </td>
                    <td className={`n ${tone(row.stats.return_percent)}`}>
                      {signed(row.stats.return_percent)}
                    </td>
                    <td className={`n ${tone(row.stats.beat_buy_and_hold_by)}`}>
                      {signed(row.stats.beat_buy_and_hold_by)}
                    </td>
                    <td className="n">{row.stats.trades}</td>
                    <td className="n">{percent(row.stats.worst_drop_percent)}</td>
                    <td className="n">
                      <button
                        className="btn ghost small"
                        onClick={async () => {
                          await api.deleteBacktest(row.id)
                          setSaved(await api.savedBacktests())
                        }}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  )
}

function Results({ result }) {
  const s = result.stats
  const v = result.verdict

  return (
    <>
      <Section eyebrow="The answer">
        <div className={`verdict is-${v.grade}`}>
          <span className="eyebrow">Verdict</span>
          <h2>{v.headline}</h2>
          <p>{result.plain_english}</p>
          <ul className="checks">
            {v.checks.map((c) => (
              <li className="check" key={c.name}>
                <span className={`check-mark ${c.status}`} aria-hidden="true">{MARK[c.status]}</span>
                <span className="check-name">{c.name}</span>
                <span className="check-value">{c.value}</span>
                <span className="check-note">{c.note}</span>
              </li>
            ))}
          </ul>
          <p className="advice"><strong>What to do next:</strong> {v.advice}</p>
        </div>
      </Section>

      {s.trades > 0 && (
        <>
          <Section eyebrow="The money" question="How the account moved">
            <div className="card">
              <EquityCurve
                curve={result.equity_curve}
                benchmark={result.benchmark_curve}
                startingCash={s.starting_cash}
              />
            </div>
          </Section>

          <Section eyebrow="The numbers" question="What actually happened">
            <div className="stat-row">
              <Stat
                label="Ended with"
                value={money0(s.final_value)}
                note={`from ${money0(s.starting_cash)}`}
                tone={tone(s.profit)}
              />
              <Stat
                label="Total return"
                value={signed(s.return_percent)}
                note={`about ${signed(s.yearly_return_percent)} a year`}
                tone={tone(s.return_percent)}
              />
              <Stat
                label="Buy and hold"
                value={signed(s.buy_and_hold.return_percent)}
                note="doing nothing instead"
              />
              <Stat
                label="Trades"
                value={s.trades}
                note={`${s.wins} won, ${s.losses} lost`}
              />
              <Stat
                label="Win rate"
                value={percent(s.win_rate, 0)}
                what="How often a trade made money. On its own it means little — big winners can carry a low win rate."
              />
              <Stat
                label="Profit factor"
                value={s.profit_factor ?? '—'}
                what="Dollars made for every dollar lost. Under 1.0 means the strategy loses money. Aim for 1.5 or better."
              />
              <Stat
                label="Worst drop"
                value={percent(s.worst_drop_percent)}
                note={s.worst_drop_day ? shortDate(s.worst_drop_day) : ''}
                what="The biggest fall from a high point to the low that followed. This is the pain you would have had to sit through."
                tone={s.worst_drop_percent > 20 ? 'loss' : ''}
              />
              <Stat
                label="Average hold"
                value={`${s.avg_hold_days} days`}
                note="how long money was tied up"
              />
              <Stat
                label="Average win"
                value={money0(s.avg_win)}
                tone="gain"
              />
              <Stat
                label="Average loss"
                value={money0(s.avg_loss)}
                tone="loss"
              />
              <Stat
                label="Per trade"
                value={signedMoney(s.expectancy)}
                what="What one average trade was worth, winners and losers together. Negative means every trade costs you money on average."
                tone={tone(s.expectancy)}
              />
              <Stat
                label="Steadiness"
                value={s.sharpe ?? '—'}
                what="Sharpe ratio: return compared with how bumpy the ride was. Above 1 is good, above 2 is unusual."
              />
            </div>
            {s.skipped_no_cash > 0 && (
              <Notice kind="warn" title={`${s.skipped_no_cash} signals were skipped.`}>
                There was not enough spare cash to take them. That is realistic —
                but it also means these results depend on which trades happened to
                come first. Try more starting money or fewer stocks.
              </Notice>
            )}
          </Section>

          {result.per_symbol.length > 1 && (
            <Section eyebrow="By stock" question="Which names carried the result?">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Stock</th>
                      <th className="n">Trades</th>
                      <th className="n">Won</th>
                      <th className="n">Win rate</th>
                      <th className="n">Profit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.per_symbol.map((r) => (
                      <tr key={r.symbol}>
                        <td className="sym">{r.symbol}</td>
                        <td className="n">{r.trades}</td>
                        <td className="n">{r.wins}</td>
                        <td className="n">{percent(r.win_rate, 0)}</td>
                        <td className={`n ${tone(r.profit)}`}>{signedMoney(r.profit)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="tiny" style={{ marginTop: 8 }}>
                If one stock made all the money, the strategy has not been shown to
                work — that stock has.
              </p>
            </Section>
          )}

          <Section eyebrow="Every trade" question="Exactly what it did, and why">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Stock</th>
                    <th>Bought</th>
                    <th className="n">At</th>
                    <th>Sold</th>
                    <th className="n">At</th>
                    <th className="n">Days</th>
                    <th className="n">Profit</th>
                    <th className="why">Why it bought · why it sold</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trades.map((t, i) => (
                    <tr key={`${t.symbol}-${t.entry_day}-${i}`}>
                      <td className="sym">{t.symbol}</td>
                      <td>{shortDate(t.entry_day)}</td>
                      <td className="n">{money(t.entry_price)}</td>
                      <td>{shortDate(t.exit_day)}</td>
                      <td className="n">{money(t.exit_price)}</td>
                      <td className="n">{t.held_days}</td>
                      <td className={`n ${tone(t.profit)}`}>
                        {signedMoney(t.profit)}
                        <div className="tiny">{signed(t.profit_percent)}</div>
                      </td>
                      <td className="why">
                        {t.reason}
                        <div style={{ marginTop: 4, opacity: 0.8 }}>→ {t.exit_reason}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </>
      )}

      {s.trades === 0 && (
        <Empty title="Nothing to chart">
          No trades means no results to show. Try a longer period, more stocks, or
          looser rules on the Strategy page.
        </Empty>
      )}
    </>
  )
}
