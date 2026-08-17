import { useEffect, useState } from 'react'
import { api, money, money0, percent, shortDate, signed, signedMoney, tone } from '../api.js'
import { EquityCurve } from './charts.jsx'
import { Empty, Notice, Section, Segmented, Stat, Working } from './bits.jsx'

const PERIODS = [
  { value: '1M', label: '1 month' },
  { value: '3M', label: '3 months' },
  { value: '1A', label: '1 year' },
  { value: 'all', label: 'All' },
]

/**
 * The live counterpart to the backtest: the same questions, asked of trades
 * that really happened. Deliberately blunt about sample size — a good week
 * and a lucky week look identical, and the page says so.
 */
export default function Performance() {
  const [period, setPeriod] = useState('3M')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.performance(period)
      .then((d) => { setData(d); setError(null) })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [period])

  if (error) return <div className="page"><Notice kind="bad">{error}</Notice></div>
  if (!data && loading) return <div className="page"><Working /></div>

  const s = data?.stats

  return (
    <div className="page">
      <header className="page-head">
        <span className="eyebrow">Live paper account</span>
        <h1>How it is actually doing</h1>
        <p className="lede">
          The backtest grades the strategy against the past. This grades it
          against reality — the same questions, asked of trades that really
          happened.
        </p>
      </header>

      <div className="row" style={{ marginBottom: 22 }}>
        <Segmented
          options={PERIODS}
          value={period}
          onChange={setPeriod}
          ariaLabel="How far back to show"
        />
        {loading && <Working>Loading…</Working>}
      </div>

      {data?.shared_account && (
        <Notice kind="warn" title="This paper account is shared.">
          {data.foreign_fills} fills on it came from something else — your other
          bot, or a trade placed in Alpaca's own dashboard. The trade list below
          counts only orders this app placed, but the account value chart cannot
          be separated, so it includes everything. For a clean read, point this
          app at its own paper account.
        </Notice>
      )}

      {s && data.too_early && s.trades > 0 && (
        <Notice kind="warn" title="Too early to conclude anything.">
          {s.trades} trade{s.trades === 1 ? '' : 's'} over {s.days_running} days
          cannot tell you whether a strategy works. Twenty trades is the point
          where the numbers stop being noise, and even then a month of a rising
          market flatters everything.
        </Notice>
      )}

      {s && (
        <Section eyebrow="The short version">
          <div className="card">
            <div className="row" style={{ alignItems: 'flex-end', gap: 32 }}>
              <div>
                <div className="stat-label">Account value</div>
                <div className="headline-number">{money0(s.value_now)}</div>
                <div className={`small ${tone(s.change)}`} style={{ marginTop: 6 }}>
                  {signedMoney(s.change)} · {signed(s.return_percent)} since{' '}
                  {money0(s.started_with)}
                </div>
              </div>
              {s.benchmark && (
                <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
                  <div className="stat-label">
                    Just holding {s.benchmark.symbol} instead
                  </div>
                  <div className="stat-value">
                    {signed(s.benchmark.return_percent)}
                  </div>
                  <div className={`tiny ${tone(s.beat_benchmark_by)}`} style={{ marginTop: 4 }}>
                    you are {signed(s.beat_benchmark_by)} against it
                  </div>
                </div>
              )}
            </div>
            <p style={{ marginTop: 18 }}>{s.plain_english}</p>
          </div>
        </Section>
      )}

      {data?.curve?.length > 1 && (
        <Section eyebrow="The money" question="Account value day by day">
          <div className="card">
            <EquityCurve
              curve={data.curve}
              benchmark={data.benchmark_curve}
              startingCash={s.started_with}
            />
          </div>
        </Section>
      )}

      {s && (
        <Section eyebrow="The numbers" question="What the trades did">
          <div className="stat-row">
            <Stat
              label="Completed trades"
              value={s.trades}
              note={`${s.wins} won, ${s.losses} lost`}
            />
            <Stat label="Win rate" value={percent(s.win_rate, 0)}
              what="How often a trade made money. Meaningless on its own — big winners can carry a low win rate." />
            <Stat label="Profit factor" value={s.profit_factor ?? '—'}
              what="Dollars made for every dollar lost. Under 1.0 loses money." />
            <Stat label="Banked" value={signedMoney(s.realised)} tone={tone(s.realised)}
              what="Profit from trades that are finished. This is real; the rest can still change." />
            <Stat label="On paper" value={signedMoney(s.unrealised)} tone={tone(s.unrealised)}
              note={`${s.open_positions} still open`}
              what="Gain or loss on positions you still hold. It is not yours until you sell." />
            <Stat label="Average win" value={money0(s.avg_win)} tone="gain" />
            <Stat label="Average loss" value={money0(s.avg_loss)} tone="loss" />
            <Stat label="Average hold" value={`${s.avg_hold_days} days`}
              note="a swing trade should be days, not hours" />
            <Stat label="Worst drop" value={percent(s.worst_drop_percent)}
              tone={s.worst_drop_percent > 15 ? 'loss' : ''}
              what="The biggest fall from a high point to the low after it." />
            <Stat label="Running for" value={`${s.days_running} days`} />
          </div>
        </Section>
      )}

      <Section eyebrow="Every completed trade" question="What it bought, and how it ended">
        {!data?.trades?.length ? (
          <Empty title="No completed trades yet">
            A trade shows up here once it has been bought <em>and</em> sold. Open
            positions are on the Today page — they are not results until they close.
          </Empty>
        ) : (
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
                </tr>
              </thead>
              <tbody>
                {data.trades.map((t, i) => (
                  <tr key={`${t.symbol}-${t.sold_at}-${i}`}>
                    <td className="sym">{t.symbol}</td>
                    <td>{shortDate(t.bought_at)}</td>
                    <td className="n">{money(t.bought_for)}</td>
                    <td>{shortDate(t.sold_at)}</td>
                    <td className="n">{money(t.sold_for)}</td>
                    <td className="n">{t.held_days}</td>
                    <td className={`n ${tone(t.profit)}`}>
                      {signedMoney(t.profit)}
                      <div className="tiny">{signed(t.profit_percent)}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  )
}
