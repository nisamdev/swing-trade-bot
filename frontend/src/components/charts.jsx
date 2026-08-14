import { useMemo, useState } from 'react'
import { money0, shortDate } from '../api.js'

// Charts are drawn straight onto the page's own graph-paper background rather
// than into a boxed widget: same grid, same pitch, so the plot reads as
// something drafted on the page. Hand-rolled SVG — no chart library, nothing
// to keep up to date, and full control of the two theme palettes.

const PAD = { top: 14, right: 14, bottom: 26, left: 62 }

function niceTicks(min, max, count = 4) {
  const span = max - min || 1
  const raw = span / count
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10
  const first = Math.ceil(min / step) * step
  const out = []
  for (let v = first; v <= max + step * 0.001; v += step) out.push(v)
  return out
}

/**
 * The account balance over time, with buy-and-hold behind it.
 * Two lines, because one line cannot answer "was this worth the effort?".
 */
export function EquityCurve({ curve, benchmark = [], startingCash, height = 260 }) {
  const [hover, setHover] = useState(null)
  const W = 900
  const H = height

  const view = useMemo(() => {
    if (!curve?.length) return null
    const values = [
      ...curve.map((p) => p.value),
      ...benchmark.map((p) => p.value),
      startingCash,
    ]
    const lo = Math.min(...values)
    const hi = Math.max(...values)
    const pad = (hi - lo) * 0.08 || hi * 0.05 || 1
    const yMin = Math.max(0, lo - pad)
    const yMax = hi + pad

    const x = (i, n) => PAD.left + (i / Math.max(n - 1, 1)) * (W - PAD.left - PAD.right)
    const y = (v) =>
      PAD.top + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - PAD.top - PAD.bottom)

    const path = (points) =>
      points
        .map((p, i) => `${i ? 'L' : 'M'}${x(i, points.length).toFixed(1)},${y(p.value).toFixed(1)}`)
        .join('')

    return { yMin, yMax, x, y, path, n: curve.length }
  }, [curve, benchmark, startingCash, H])

  if (!view) return null

  const ticks = niceTicks(view.yMin, view.yMax)
  const last = curve[curve.length - 1]
  const won = last.value >= startingCash
  const lineColor = won ? 'var(--gain)' : 'var(--loss)'

  const step = Math.max(1, Math.floor(curve.length / 6))
  const dateTicks = curve.filter((_, i) => i % step === 0 || i === curve.length - 1)

  function track(e) {
    const rect = e.currentTarget.getBoundingClientRect()
    const ratio = (e.clientX - rect.left) / rect.width
    const px = ratio * W
    const inner = W - PAD.left - PAD.right
    const i = Math.round(((px - PAD.left) / inner) * (curve.length - 1))
    setHover(i >= 0 && i < curve.length ? i : null)
  }

  return (
    <div className="plot">
      <div className="plot-legend">
        <span>
          <i className="swatch" style={{ borderTopColor: lineColor }} />
          Your strategy
        </span>
        {benchmark.length > 0 && (
          <span>
            <i
              className="swatch"
              style={{ borderTopColor: 'var(--faint)', borderTopStyle: 'dashed' }}
            />
            Buying and holding instead
          </span>
        )}
        <span>
          <i className="swatch" style={{ borderTopColor: 'var(--rule)', borderTopStyle: 'dotted' }} />
          What you started with
        </span>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Account balance over time, ending at ${money0(last.value)}`}
        onMouseMove={track}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line
              className="plot-gridline"
              x1={PAD.left} x2={W - PAD.right}
              y1={view.y(t)} y2={view.y(t)}
              strokeDasharray="1 4"
            />
            <text className="plot-axis" x={PAD.left - 8} y={view.y(t) + 3} textAnchor="end">
              {money0(t)}
            </text>
          </g>
        ))}

        {/* The line you have to beat to justify any of this. */}
        <line
          x1={PAD.left} x2={W - PAD.right}
          y1={view.y(startingCash)} y2={view.y(startingCash)}
          stroke="var(--rule)" strokeWidth="1" strokeDasharray="2 3"
        />

        {benchmark.length > 1 && (
          <path
            d={view.path(benchmark)}
            fill="none"
            stroke="var(--faint)"
            strokeWidth="1.5"
            strokeDasharray="5 4"
            strokeLinejoin="round"
          />
        )}

        <path
          d={view.path(curve)}
          fill="none"
          stroke={lineColor}
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {dateTicks.map((p) => {
          const i = curve.indexOf(p)
          return (
            <text
              key={p.day}
              className="plot-axis"
              x={view.x(i, curve.length)}
              y={H - 8}
              textAnchor={i === 0 ? 'start' : i === curve.length - 1 ? 'end' : 'middle'}
            >
              {p.day.slice(0, 7)}
            </text>
          )
        })}

        {hover !== null && curve[hover] && (
          <g>
            <line
              x1={view.x(hover, curve.length)} x2={view.x(hover, curve.length)}
              y1={PAD.top} y2={H - PAD.bottom}
              stroke="var(--tide)" strokeWidth="1"
            />
            <circle
              cx={view.x(hover, curve.length)} cy={view.y(curve[hover].value)}
              r="3.5" fill={lineColor}
            />
          </g>
        )}
      </svg>

      <div className="small" style={{ minHeight: '1.5em', fontFamily: 'var(--font-data)' }}>
        {hover !== null && curve[hover]
          ? `${shortDate(curve[hover].day)} — ${money0(curve[hover].value)}`
          : `${shortDate(curve[0].day)} to ${shortDate(last.day)}`}
      </div>
    </div>
  )
}

/** A stock's closing price with its trend line, for the watchlist detail. */
export function PriceChart({ bars, height = 190 }) {
  const W = 900
  const H = height
  if (!bars?.length) return null

  const values = bars.flatMap((b) => [b.close, b.trend].filter((v) => v != null))
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const pad = (hi - lo) * 0.1 || 1
  const yMin = lo - pad
  const yMax = hi + pad

  const x = (i) => PAD.left + (i / Math.max(bars.length - 1, 1)) * (W - PAD.left - PAD.right)
  const y = (v) => PAD.top + (1 - (v - yMin) / (yMax - yMin)) * (H - PAD.top - PAD.bottom)

  const line = (key) => {
    let started = false
    return bars
      .map((b, i) => {
        if (b[key] == null) return ''
        const cmd = started ? 'L' : 'M'
        started = true
        return `${cmd}${x(i).toFixed(1)},${y(b[key]).toFixed(1)}`
      })
      .join('')
  }

  const ticks = niceTicks(yMin, yMax, 3)

  return (
    <div className="plot">
      <div className="plot-legend">
        <span><i className="swatch" style={{ borderTopColor: 'var(--ink)' }} />Price</span>
        <span><i className="swatch" style={{ borderTopColor: 'var(--tide)' }} />Long-term average</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Price with its long-term average">
        {ticks.map((t) => (
          <g key={t}>
            <line
              className="plot-gridline"
              x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)}
              strokeDasharray="1 4"
            />
            <text className="plot-axis" x={PAD.left - 8} y={y(t) + 3} textAnchor="end">
              ${t.toFixed(0)}
            </text>
          </g>
        ))}
        <path d={line('trend')} fill="none" stroke="var(--tide)" strokeWidth="1.5" />
        <path d={line('close')} fill="none" stroke="var(--ink)" strokeWidth="1.75" strokeLinejoin="round" />
      </svg>
    </div>
  )
}
