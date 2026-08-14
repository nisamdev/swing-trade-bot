import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Notice, RuleField, Section, Switch, SymbolInput, Working } from './bits.jsx'

export default function StrategyPage({ config, strategies, onSaved }) {
  const [draft, setDraft] = useState(config)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [savedAt, setSavedAt] = useState(null)

  useEffect(() => { setDraft(config) }, [config])

  if (!draft || !strategies) return <div className="page"><Working /></div>

  const chosen = strategies.find((s) => s.key === draft.rules.strategy) || strategies[0]
  const dirty = JSON.stringify(draft) !== JSON.stringify(config)

  const setRule = (key, value) =>
    setDraft((d) => ({ ...d, rules: { ...d.rules, [key]: value } }))
  const setMoney = (key, value) =>
    setDraft((d) => ({ ...d, money: { ...d.money, [key]: value } }))

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await api.saveConfig({
        watchlist: draft.watchlist,
        rules: draft.rules,
        money: draft.money,
        run_at: draft.run_at,
      })
      await onSaved()
      setSavedAt(new Date())
    } catch (e) { setError(e.message) } finally { setSaving(false) }
  }

  async function reset() {
    if (!confirm('Put every setting back to its starting value?')) return
    await api.resetConfig()
    await onSaved()
  }

  return (
    <div className="page">
      <header className="page-head">
        <span className="eyebrow">Your rules</span>
        <h1>Pick a strategy and set the dials</h1>
        <p className="lede">
          A strategy is just a list of conditions that all have to be true before
          it buys, plus the rules for getting back out. Nothing here is magic, and
          nothing here is secret — you can read exactly what each one does.
        </p>
      </header>

      {error && <Notice kind="bad" title="Could not save.">{error}</Notice>}

      <Section eyebrow="The idea" question="Which approach do you want to trade?">
        <div className="pick">
          {strategies.map((s) => (
            <button
              key={s.key}
              type="button"
              className="pick-card"
              aria-pressed={s.key === chosen.key}
              onClick={() => setRule('strategy', s.key)}
            >
              <h3>{s.name}</h3>
              <p>{s.tagline}</p>
              <div className="best-for">{s.best_for}</div>
            </button>
          ))}
        </div>
      </Section>

      <Section eyebrow="How it decides" question={`“${chosen.name}”, rule by rule`}>
        <div className="card">
          <p className="small" style={{ marginBottom: 6 }}>
            These are checked in order. If an earlier one fails, the later ones
            never get asked.
          </p>
          <ol className="recipe">
            {chosen.how_it_works.map((step) => <li key={step}><span>{step}</span></li>)}
          </ol>
        </div>
      </Section>

      <Section eyebrow="The dials" question="Fine-tune it">
        <div className="card">
          <div className="grid-2">
            {chosen.settings.map((spec) => (
              <RuleField
                key={spec.key}
                spec={spec}
                value={draft.rules[spec.key]}
                onChange={setRule}
              />
            ))}
          </div>
          <Notice kind="warn" title="One at a time.">
            Change a single dial, re-run the test, and note what happened. Turning
            several at once tells you nothing about which one mattered — and dialling
            everything until the past looks perfect is the fastest way to build
            something that fails the moment it meets a new week.
          </Notice>
        </div>
      </Section>

      <Section eyebrow="The money" question="How much to put behind each trade">
        <div className="card">
          <div className="grid-2">
            <div className="field">
              <label className="field-label" htmlFor="risk_percent">Risk per trade (%)</label>
              <span className="field-help">
                The share of the account you accept losing if a trade goes straight
                to its stop. 1% is a common starting point. Above 2% is aggressive.
              </span>
              <input
                id="risk_percent" type="number" className="num" min="0.1" max="10" step="0.1"
                value={draft.money.risk_percent}
                onChange={(e) => setMoney('risk_percent', Number(e.target.value))}
              />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="max_position_percent">Most in one stock (%)</label>
              <span className="field-help">
                A ceiling on any single position, so one name cannot become your
                whole account.
              </span>
              <input
                id="max_position_percent" type="number" className="num" min="1" max="100" step="1"
                value={draft.money.max_position_percent}
                onChange={(e) => setMoney('max_position_percent', Number(e.target.value))}
              />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="max_open_positions">Most positions at once</label>
              <span className="field-help">
                How many trades can be open together. More means less riding on any
                one of them, but also thinner slices of cash.
              </span>
              <input
                id="max_open_positions" type="number" className="num" min="1" max="20" step="1"
                value={draft.money.max_open_positions}
                onChange={(e) => setMoney('max_open_positions', Number(e.target.value))}
              />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="slippage_bps">Assumed slippage (basis points)</label>
              <span className="field-help">
                The gap between the price you see and the price you get. 5 means
                0.05%. Used in tests only — keep it honest, or the test lies to you.
              </span>
              <input
                id="slippage_bps" type="number" className="num" min="0" max="100" step="1"
                value={draft.money.slippage_bps}
                onChange={(e) => setMoney('slippage_bps', Number(e.target.value))}
              />
            </div>
            <div className="field">
              <label className="field-label" htmlFor="starting_cash">Starting money for tests</label>
              <span className="field-help">
                Only affects backtests. Your paper account's real balance is used
                when the bot trades.
              </span>
              <input
                id="starting_cash" type="number" className="num" min="100" step="100"
                value={draft.money.starting_cash}
                onChange={(e) => setMoney('starting_cash', Number(e.target.value))}
              />
            </div>
          </div>
        </div>
      </Section>

      <Section eyebrow="The watchlist" question="Which stocks should it watch?">
        <div className="card">
          <p className="field-help">
            The bot only ever looks at these. Start with a handful of large, liquid
            names or index funds — thin, jumpy stocks make every rule here misfire.
          </p>
          <SymbolInput
            symbols={draft.watchlist}
            onChange={(list) => setDraft((d) => ({ ...d, watchlist: list }))}
            id="watchlist"
          />
          <div className="field" style={{ marginTop: 20, maxWidth: 220 }}>
            <label className="field-label" htmlFor="run_at">Check each day at</label>
            <span className="field-help">
              New York time. Shortly after the 9:30 open is the match for how the
              tests work: decide on yesterday's finished day, buy this morning.
            </span>
            <input
              id="run_at" type="text" className="num"
              value={draft.run_at}
              onChange={(e) => setDraft((d) => ({ ...d, run_at: e.target.value }))}
            />
          </div>
        </div>
      </Section>

      <div className="save-bar">
        <button className="btn" onClick={save} disabled={!dirty || saving}>
          {saving ? 'Saving…' : dirty ? 'Save settings' : 'Saved'}
        </button>
        <button className="btn ghost" onClick={reset}>Reset everything</button>
        {savedAt && !dirty && (
          <span className="small">Saved at {savedAt.toLocaleTimeString()}.</span>
        )}
      </div>
    </div>
  )
}
