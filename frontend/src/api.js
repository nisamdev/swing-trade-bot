// Every call goes to a same-origin /api path; Vite forwards it to Python.
// Errors arrive as thrown Error objects carrying the server's own message, so
// screens can show what actually went wrong instead of "something failed".

async function call(path, options = {}) {
  let res
  try {
    res = await fetch(`/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
  } catch {
    throw new Error(
      'Cannot reach the app’s Python side. Is it running? Try: docker compose up'
    )
  }

  if (!res.ok) {
    let message = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body.detail) message = typeof body.detail === 'string' ? body.detail : message
    } catch { /* the body was not JSON; keep the status message */ }
    throw new Error(message)
  }
  return res.status === 204 ? null : res.json()
}

const post = (path, body) =>
  call(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  overview: () => call('/overview'),
  strategies: () => call('/strategies'),
  config: () => call('/config'),
  saveConfig: (patch) => call('/config', { method: 'PUT', body: JSON.stringify(patch) }),
  resetConfig: () => post('/config/reset'),

  backtest: (body) => post('/backtest', body),
  savedBacktests: () => call('/backtests'),
  deleteBacktest: (id) => call(`/backtests/${id}`, { method: 'DELETE' }),

  prices: (symbol) => call(`/prices/${symbol}`),
  journal: () => call('/journal'),
  activity: (limit = 120) => call(`/activity?limit=${limit}`),

  checkNow: () => post('/bot/check-now'),
  previewBuy: (symbol) => call(`/bot/preview-buy/${symbol}`),
  buy: (symbol, shares) => post(`/bot/buy/${symbol}`, { shares: shares ?? null }),
  sell: (symbol) => post(`/bot/sell/${symbol}`),
  cancelOrders: () => post('/bot/cancel-orders'),
}

// -- formatting -------------------------------------------------------------

export const money = (n, digits = 2) =>
  n === null || n === undefined || Number.isNaN(n)
    ? '—'
    : n.toLocaleString('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })

export const money0 = (n) => money(n, 0)

export const percent = (n, digits = 1) =>
  n === null || n === undefined || Number.isNaN(n) ? '—' : `${n.toFixed(digits)}%`

export const signed = (n, digits = 1) =>
  n === null || n === undefined || Number.isNaN(n)
    ? '—'
    : `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`

export const signedMoney = (n) =>
  n === null || n === undefined ? '—' : `${n >= 0 ? '+' : '−'}${money(Math.abs(n))}`

export const shortDate = (iso) =>
  !iso
    ? '—'
    : new Date(iso).toLocaleDateString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric',
      })

export const clockTime = (iso) =>
  !iso ? '—' : new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })

export const tone = (n) => (n > 0 ? 'gain' : n < 0 ? 'loss' : '')
