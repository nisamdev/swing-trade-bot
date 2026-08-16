import { useEffect, useState } from 'react'
import { api, clockTime, money, money0, percent, signed } from '../api.js'
import { Empty, Notice, Section, Working } from './bits.jsx'
import { PriceChart } from './charts.jsx'

/**
 * The scanner's shortlist. Every card says what it found and why, and shows
 * the trade plan — because a score with no reasoning is a horoscope.
 */
export default function Ideas({ config, onBuy, onSaved }) {
  const [data, setData] = useState(null)
  const [scanning, setScanning] = useState(false)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(null)

  useEffect(() => { api.ideas().then(setData).catch((e) => setError(e.message)) }, [])

  async function scanNow() {
    setScanning(true)
    setError(null)
    try {
      setData(await api.scanNow())
    } catch (e) { setError(e.message) } finally { setScanning(false) }
  }

  if (error && !data) return <div className="page"><Notice kind="bad">{error}</Notice></div>
  if (!data) return <div className="page"><Working /></div>

  const ideas = data.ideas || []

  return (
    <div className="page">
      <header className="page-head">
        <span className="eyebrow">
          {data.at ? `Last scanned ${clockTime(data.at)}` : 'Not scanned yet'}
        </span>
        <h1>Trade ideas</h1>
        <p className="lede">
          Your watchlist answers “should I buy what I already chose?”. This asks
          the harder one: out of everything trading today, which handful are
          actually set up? Nothing here is bought automatically — a score is an
          invitation to look at the chart, not a signal.
        </p>
      </header>

      {error && <Notice kind="bad">{error}</Notice>}

      <div className="row" style={{ marginBottom: 24 }}>
        <button className="btn" onClick={scanNow} disabled={scanning}>
          {scanning ? 'Scanning…' : 'Scan now'}
        </button>
        {scanning && <Working>Reading prices for up to {config?.scan_universe_size ?? 60} stocks…</Working>}
        {!scanning && data.looked_at > 0 && (
          <span className="small">
            Looked at {data.looked_at} stocks · kept {ideas.length} ·
            {' '}scans automatically at {config?.scan_at ?? '12:30'} New York time
          </span>
        )}
      </div>

      {ideas.length === 0 ? (
        <Empty title="Nothing is set up right now">
          That is a normal answer, and a useful one. A swing setup needs an
          uptrend, a shelf nearby, and room above — most days most stocks have
          none of those. Try again after the next scan.
        </Empty>
      ) : (
        <Section eyebrow="Shortlist" question="Worth a look today">
          <div className="ideas">
            {ideas.map((idea) => (
              <IdeaCard
                key={idea.symbol}
                idea={idea}
                expanded={open === idea.symbol}
                onToggle={() => setOpen(open === idea.symbol ? null : idea.symbol)}
                onBuy={() => onBuy(idea.symbol)}
              />
            ))}
          </div>
        </Section>
      )}

      {data.rejected?.length > 0 && (
        <Section eyebrow="Ruled out" question="What it looked at and passed on">
          <div className="card">
            <p className="small" style={{ marginBottom: 10 }}>
              Knowing why something was rejected is as useful as knowing why
              something was kept.
            </p>
            <div className="reject-grid">
              {data.rejected.map((r) => (
                <div key={r.symbol} className="reject">
                  <span className="sym">{r.symbol}</span>
                  <span className="tiny">{r.reason}</span>
                </div>
              ))}
            </div>
          </div>
        </Section>
      )}
    </div>
  )
}

function IdeaCard({ idea, expanded, onToggle, onBuy }) {
  const [chart, setChart] = useState(null)

  useEffect(() => {
    if (expanded && !chart) {
      api.prices(idea.symbol, 180).then(setChart).catch(() => {})
    }
  }, [expanded, chart, idea.symbol])

  const band = idea.score >= 75 ? 'strong' : idea.score >= 55 ? 'fair' : 'weak'

  return (
    <article className={`idea is-${band}`}>
      <header className="idea-head">
        <div>
          <h3 className="idea-sym">{idea.symbol}</h3>
          <span className="tiny">{idea.source} · from the close on {idea.as_of}</span>
        </div>
        <div className="idea-score">
          <span className="idea-score-num">{idea.score}</span>
          <span className="tiny">out of 100</span>
        </div>
      </header>

      <p className="idea-headline">{idea.headline}</p>

      <div className="idea-plan">
        <Cell label="Price" value={money(idea.price)} />
        <Cell label="Stop" value={money(idea.stop)} tone="loss" />
        <Cell label="Target" value={money(idea.target)} tone="gain" />
        <Cell label="Reward for the risk" value={`${idea.reward_risk}×`} />
        <Cell label="Size" value={`${idea.shares} sh`} note={money0(idea.cost)} />
        <Cell label="At risk" value={money0(idea.risking)} tone="loss" />
      </div>

      <button className="idea-more" onClick={onToggle} aria-expanded={expanded}>
        {expanded ? 'Hide the reasoning' : 'Why this one?'}
      </button>

      {expanded && (
        <div className="idea-detail">
          <ul className="why-list">
            {idea.reasons.map((r) => (
              <li key={r}><span className="why-mark good">✓</span>{r}</li>
            ))}
            {idea.warnings.map((w) => (
              <li key={w}><span className="why-mark warn">!</span>{w}</li>
            ))}
          </ul>

          <dl className="idea-exits">
            <div>
              <dt>Stop is there because</dt>
              <dd>{idea.stop_reason}</dd>
            </div>
            <div>
              <dt>Target is there because</dt>
              <dd>{idea.target_reason}</dd>
            </div>
          </dl>

          {chart ? (
            <PriceChart
              bars={chart.bars}
              levels={chart.levels}
              zones={chart.zones}
              averages={chart.averages}
            />
          ) : (
            <Working>Loading the chart…</Working>
          )}

          <div className="row" style={{ marginTop: 14 }}>
            <button className="btn" onClick={onBuy}>Review and buy {idea.symbol}</button>
            <span className="tiny">
              Opens the buy screen with this plan. Nothing is ordered until you confirm.
            </span>
          </div>
        </div>
      )}
    </article>
  )
}

function Cell({ label, value, note, tone }) {
  return (
    <div>
      <div className="tiny">{label}</div>
      <div className={`num ${tone || ''}`} style={{ fontWeight: 500 }}>{value}</div>
      {note && <div className="tiny">{note}</div>}
    </div>
  )
}
