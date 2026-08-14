// Small pieces used on more than one screen.

export function Section({ eyebrow, question, children }) {
  return (
    <section className="section">
      {eyebrow && <span className="eyebrow">{eyebrow}</span>}
      {question && <div className="section-q">{question}</div>}
      {children}
    </section>
  )
}

export function Stat({ label, value, note, what, tone }) {
  return (
    <div className="stat">
      <div className="stat-label">
        {label}
        {what && <abbr className="what" title={what}>?</abbr>}
      </div>
      <div className={`stat-value ${tone || ''}`}>{value}</div>
      {note && <div className="stat-note">{note}</div>}
    </div>
  )
}

export function Notice({ kind = '', title, children }) {
  return (
    <div className={`notice ${kind}`}>
      <div>
        {title && <strong>{title}</strong>}
        {title && ' '}
        {children}
      </div>
    </div>
  )
}

export function Empty({ title, children }) {
  return (
    <div className="card empty">
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  )
}

export function Working({ children = 'Working…' }) {
  return <span className="working">{children}</span>
}

export function Switch({ checked, onChange, label, help, disabled }) {
  return (
    <label className="switch">
      <input
        type="checkbox"
        checked={!!checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="switch-track" aria-hidden="true" />
      <span>
        <span className="field-label" style={{ marginBottom: 0 }}>{label}</span>
        {help && <span className="field-help" style={{ marginBottom: 0 }}>{help}</span>}
      </span>
    </label>
  )
}

export function Segmented({ options, value, onChange, ariaLabel }) {
  return (
    <div className="seg" role="group" aria-label={ariaLabel}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** A list of stock symbols you can add to and remove from.
 *
 *  Deliberately not a <form>: this sits inside the backtest form, and nesting
 *  forms is invalid HTML — Enter here would submit the outer one and fire a
 *  test before the symbol had even been added. Enter is handled by hand.
 */
export function SymbolInput({ symbols, onChange, id = 'symbols' }) {
  function add() {
    const input = document.getElementById(id)
    const added = (input?.value || '').toUpperCase().split(/[\s,]+/).filter(Boolean)
    if (!added.length) return
    onChange([...new Set([...symbols, ...added])].slice(0, 20))
    if (input) input.value = ''
  }

  return (
    <div>
      <div className="row" style={{ gap: 8, flexWrap: 'nowrap' }}>
        <input
          id={id}
          type="text"
          className="num"
          placeholder="AAPL"
          autoComplete="off"
          spellCheck="false"
          aria-label="Add a stock symbol"
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
        />
        <button type="button" className="btn ghost small" style={{ flex: 'none' }} onClick={add}>
          Add
        </button>
      </div>
      <div className="chips">
        {symbols.map((s) => (
          <span key={s} className="chip">
            {s}
            <button
              type="button"
              onClick={() => onChange(symbols.filter((x) => x !== s))}
              aria-label={`Remove ${s}`}
            >
              ×
            </button>
          </span>
        ))}
        {symbols.length === 0 && <span className="tiny">No stocks added yet.</span>}
      </div>
    </div>
  )
}

/** Turns one Rules field into a labelled control, using the help text the
 *  Python side ships alongside it. */
export function RuleField({ spec, value, onChange }) {
  const set = (v) => onChange(spec.key, v)

  if (spec.type === 'bool') {
    return (
      <div className="field">
        <Switch checked={value} onChange={set} label={spec.label} help={spec.help} />
      </div>
    )
  }

  return (
    <div className="field">
      <label className="field-label" htmlFor={`rule-${spec.key}`}>
        {spec.label}
        {spec.unit ? <span className="tiny"> ({spec.unit})</span> : null}
      </label>
      <span className="field-help">{spec.help}</span>
      <input
        id={`rule-${spec.key}`}
        type="number"
        className="num"
        value={value ?? ''}
        min={spec.min}
        max={spec.max}
        step={spec.step ?? (spec.type === 'int' ? 1 : 0.1)}
        onChange={(e) =>
          set(e.target.value === '' ? '' : Number(e.target.value))
        }
      />
    </div>
  )
}
