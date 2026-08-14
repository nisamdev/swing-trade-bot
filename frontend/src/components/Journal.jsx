import { useEffect, useState } from 'react'
import { api, clockTime, money, shortDate } from '../api.js'
import { Empty, Notice, Section, Working } from './bits.jsx'

export default function Journal() {
  const [journal, setJournal] = useState(null)
  const [activity, setActivity] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    Promise.all([api.journal(), api.activity(200)])
      .then(([j, a]) => { setJournal(j); setActivity(a) })
      .catch((e) => setError(e.message))
  }, [])

  if (error) return <div className="page"><Notice kind="bad">{error}</Notice></div>
  if (!journal) return <div className="page"><Working /></div>

  return (
    <div className="page">
      <header className="page-head">
        <span className="eyebrow">The record</span>
        <h1>Every decision, kept</h1>
        <p className="lede">
          A trading journal is the one habit that separates people who improve
          from people who repeat themselves. Each row says what the bot bought,
          what rule made it buy, and how the trade ended.
        </p>
      </header>

      <Section eyebrow="Trades" question="What the bot has bought">
        {journal.length === 0 ? (
          <Empty title="No trades yet">
            Once the bot places its first paper order it will show up here, with the
            reason it gave at the time.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Stock</th>
                  <th>Bought</th>
                  <th className="n">Shares</th>
                  <th className="n">Stop</th>
                  <th className="n">Target</th>
                  <th>Status</th>
                  <th className="why">Why</th>
                </tr>
              </thead>
              <tbody>
                {journal.map((row) => (
                  <tr key={row.id}>
                    <td className="sym">{row.symbol}</td>
                    <td>
                      {shortDate(row.at)}
                      <div className="tiny">{row.strategy.replace(/_/g, ' ')}</div>
                    </td>
                    <td className="n">{row.shares}</td>
                    <td className="n">{money(row.stop)}</td>
                    <td className="n">{money(row.target)}</td>
                    <td>
                      <span className={`pill ${row.status === 'closed' ? 'wait' : 'buy'}`}>
                        {row.status === 'closed' ? 'closed' : 'open'}
                      </span>
                      {row.close_reason && <div className="tiny" style={{ marginTop: 4 }}>{row.close_reason}</div>}
                    </td>
                    <td className="why">{row.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section eyebrow="Log" question="Everything the app did">
        <div className="card">
          <div className="log">
            {activity.map((a) => (
              <div key={a.id} className={`log-line ${a.level}`}>
                <time>{clockTime(a.at)}</time>
                <span>{a.message}</span>
              </div>
            ))}
            {activity.length === 0 && <p className="small">Nothing logged yet.</p>}
          </div>
        </div>
      </Section>
    </div>
  )
}
