# Swing

A swing-trading bot with a plain-English interface. Swing trading means holding
a stock for **days or weeks**, not seconds — so this app checks the market once
a day and then leaves it alone.

It does three things:

1. **Tests a strategy on years of real past prices**, and tells you honestly
   whether it worked — including whether it beat simply buying and holding.
2. **Runs that strategy on paper money** through Alpaca's practice account.
3. **Keeps a journal** of every decision and the reason behind it.

Nothing here uses jargon without explaining it. No prior trading knowledge is
assumed.

---

## Start it

You need [Docker](https://docs.docker.com/get-docker/). Nothing else gets
installed on your machine.

```bash
cp .env.example .env      # then paste your Alpaca paper keys into it
docker compose up --build
```

Open **http://localhost:5180**.

### Getting Alpaca keys

1. Sign up free at [alpaca.markets](https://alpaca.markets) — no deposit needed.
2. Open the **paper trading** dashboard and generate an API key pair.
   A paper key starts with `PK`.
3. Paste both values into `.env` next to `ALPACA_API_KEY` and
   `ALPACA_SECRET_KEY`.

`ALPACA_PAPER=true` keeps you on fake money. Leave it that way.

---

## How to actually use it

**Do these in order. Skipping step 1 is how people lose money.**

### 1. Test the idea on the past → the *Test an idea* tab

Pick some stocks, pick a period, press **Run the test**. You get a verdict in
one sentence, then the numbers behind it.

The test is deliberately pessimistic, because a flattering backtest is worse
than no backtest:

| It could have cheated by | Instead it |
|---|---|
| Buying at the price that triggered the signal | Buys at the **next morning's open** — you cannot trade a price that has already happened |
| Assuming perfect fills | Charges slippage on every buy and every sell |
| Guessing the good outcome when a day hit both the stop and the target | Assumes the **stop** hit first |
| Filling a stop at the stop price on a gap down | Fills at the open, which is worse — like a real stop |
| Having unlimited money | Skips signals when the cash is already committed, and says how many it skipped |
| Ignoring the alternative | Always reports what **buying and holding** would have returned |

That last row matters most. A strategy that makes money but trails buy-and-hold
has cost you time and added risk for nothing. The verdict fails it.

### 2. Watch it on paper → the *Today* tab

Leave the switch on **Suggest only** at first. Each morning the bot works out
what it would buy and shows you the reason, but places no orders. Run that for a
few weeks and see whether you agree with it.

When you trust it, switch on **Trading**. It then places paper orders on its own
at 09:40 New York time each trading day.

### 3. Only then think about real money

Real money needs months of paper results, not days. This app defaults to paper
and will show a red banner if it is ever pointed at a live account.

---

## The three strategies

Each one is a short list of conditions that must **all** be true before it buys.
The app shows them written out, in order, on the Strategy tab.

| Strategy | The idea | Suits |
|---|---|---|
| **Buy the dip** | Wait for a healthy, rising stock to go on sale, then buy the bounce | Steady large stocks and index funds. The gentlest. |
| **Breakout** | Buy the day a stock pushes to a new high on heavy trading | Fast movers. More trades, more false starts. |
| **Trend change** | Buy when the short-term average price crosses above the long-term one | Patient, hands-off trading. Fewest trades. |

All three share the same safety rules:

- **The trend filter.** Never buy a stock trading below its 200-day average
  price. Buying dips in a falling stock is the classic way to lose money fast.
- **A stop and a target sized to the stock.** Both are set in multiples of ATR —
  how much that stock typically moves in a day. A jumpy stock gets a wider stop
  than a sleepy one, because a fixed 5% stop is far too tight for one and far
  too loose for the other.
- **Risk-based position sizing.** You choose the percent of the account you
  accept losing if a trade goes to its stop (1% by default). The share count is
  worked back from that, so every losing trade costs about the same no matter
  which stock it was.
- **A time stop.** A trade that has gone nowhere for 20 days is sold, so your
  money is not parked in a dud.

Every buy order goes to Alpaca as a **bracket order**: the stop and the target
are placed at the broker the moment the buy fills. They protect the trade even
if this app is switched off.

---

## Things worth knowing

- **The bot and the backtest run the same code.** The strategy files decide both.
  A backtest here tests what will actually trade.
- **It only acts on finished days.** During market hours today's bar is still
  forming, so the bot decides on yesterday's completed day and buys this
  morning — exactly the sequence the backtest simulates.
- **The daily round has a 90-minute window.** Start the app at 3pm and it waits
  for tomorrow rather than buying at a time of day nothing was tested at.
- **It is long-only.** No shorting, no options, no leverage, no crypto.
- **One Alpaca account, one bot.** If you point another bot at the same paper
  keys, its positions show up here too and the two will confuse each other.
- **Free IEX data reports only part of the market's volume.** The volume rule
  compares a day against its own average, so the ratio still works. Set
  `ALPACA_DATA_FEED=sip` if you have a paid Alpaca data plan.
- **Past results are not a promise.** Every strategy here looks better on
  history than it will behave live. That is why paper trading is step 2.

---

## What is in the box

```
backend/
  main.py               the web API — thin routes, no thinking
  config.py             settings read from .env
  app/
    models.py           plain data types: a Bar, the Rules, the Money settings
    indicators.py       chart maths (moving averages, RSI, ATR) as pure functions
    strategies.py       the three strategies, and the words used to explain them
    backtest.py         the pessimistic simulator
    market.py           everything that talks to Alpaca
    engine.py           the once-a-day live loop
    store.py            a single SQLite file under ./data
  tests/                32 tests, most of them about the backtest not cheating
frontend/
  src/components/       Today, Test, Strategy, Journal
  src/styles.css        the visual system
```

### Running the tests

```bash
docker compose run --rm api python -m pytest tests -q
```

Most of them exist to prove the simulator is not lying — that entries fill at
the next open, that gaps cost more than the stop, that costs can only ever make
a result worse.

### Without Docker

If you would rather run it directly you need Python 3.12+ and Node 20+:

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
(cd backend && ../.venv/bin/uvicorn main:app --port 8020 --reload)
(cd frontend && npm install && npm run dev)
```

On Debian or Ubuntu, `python3 -m venv` needs `sudo apt install python3-venv`
first.

---

## A word about expectations

Most simple strategies do not beat buying an index fund and forgetting about it.
This app is built to show you that clearly rather than hide it — if a test says
buy-and-hold won, believe it.

The value here is in learning how a rule-based system behaves: why a stop gets
hit, what a losing streak feels like, how much a strategy's result depends on
one lucky stock. That is worth a lot, and it costs nothing on paper.
