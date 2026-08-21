# bottski

A sentiment-driven **paper-trading research bot**: US stocks/ETFs via Alpaca,
signal from financial news and (pending API approval, read-only) Reddit
sentiment. **All trading is simulated — no real money anywhere.** Live mode
exists in code but sits behind a double lock and an explicit multi-month
review gate.

The real deliverable is an answer, with honest statistics, to one question:
**does public sentiment have any predictive value for stock returns?**
The trading is the vehicle; the dataset and the evaluation harness are the
product.

## How it works

```
collect (every 20 min, 24/7)
  news (Alpaca/Benzinga) + external sentiment providers (ApeWisdom, Tradestie)
  + Reddit (dormant until API approval; read-only, application-only OAuth)
    -> raw_documents / external_sentiment            [immutable, deduped]
  -> ticker extraction (versioned, precision-gated)  [document_tickers]
  -> VADER + finance lexicon scoring (versioned)     [document_sentiment]

observe (15:40 ET, weekdays)
  one panel row per (day, symbol) for the FULL ~220-name universe —
  zero-mention names included as the control group   [observations]

decide (15:45 ET) -> execute (15:47 ET)
  transparent rules + hard risk rails; every decision logged with its full
  input snapshot, including holds and rail-blocked counterfactuals
  execution: reconcile-first, deterministic client_order_id (no double-orders),
  server-side stop on every entry, kill switch checked before every order

backfill-returns (17:00 ET)   forward returns, lookahead-guarded
report (every 15 min)         dashboard: plain-language decisions + IC harness
backup (23:30 UTC)            nightly SQLite snapshot to off-box storage
```

Signal evaluation (`bottski/research/ic.py`): per-day cross-sectional rank IC,
**residualized against trailing returns and liquidity** (people post *after*
moves — that must not count as prediction), a 1000-draw shuffle null test,
decay curves, and quintile spreads. Validated against synthetic panels with
known ground truth.

## Commands

| command | what |
|---|---|
| `bottski status` | mode, kill switch, db counts |
| `bottski collect` | pull news + external sentiment (+ Reddit when approved), then extract & score |
| `bottski extract` | re-run ticker extraction on unprocessed documents |
| `bottski observe [--date]` | build the daily observation panel |
| `bottski decide [--dry-run]` | strategy decisions (never places orders itself) |
| `bottski execute` | place paper orders; reconciles first, halts on mismatch |
| `bottski backfill-returns` | fill forward returns on matured panel rows |
| `bottski reconcile` | sync fills, compare positions vs broker; halt on mismatch |
| `bottski report` | write the dashboard (data/www/index.html) |
| `bottski blacklist [add\|remove] [X]` | never-buy list (symbols or `sector:<name>`) |
| `bottski halt` / `resume` | engage / release the kill switch |

## Setup

```
uv sync
cp .env.example .env        # fill in keys (see comments in the file)
cp blacklist.example.txt data/blacklist.txt   # optional never-buy list
uv run pytest               # 100+ tests
uv run bottski status
```

Deployment (a $6/mo VPS with systemd timers, HTTPS dashboard, healthchecks
alerting, nightly off-box backups): see [deploy/README.md](deploy/README.md).

## Data & conduct

- Reddit access is **read-only and pending approval** under Reddit's
  Responsible Builder Policy; until granted, the collector skips cleanly.
  Author identifiers are one-way hashed at ingestion; there is no user-level
  analysis of any kind — analysis is exclusively aggregate per stock ticker.
  No AI/ML training on Reddit content; no resale or redistribution of data.
- Everything is versioned (extractor, scorer, universe, strategy): behavior
  changes create new rows, never mutate history, so scorer generations stay
  comparable on identical raw text.
- Secrets live only in `.env` (gitignored) and are redacted from all logs.

## Safety rails (config-driven, all tested)

Max position %, gross exposure, open positions, order notional, orders/day,
daily loss (trips the kill switch), minimum liquidity, maximum spread,
user blacklist — enforced before any order; sells are never blocked. Blocked
trades are recorded with the blocking rail named, so the rails' cost/benefit
is itself measurable.

## Status

Paper trading and data collection run fully autonomously. Live trading is
gated on: ≥3 months of paper record, a residualized IC that survives the null
test, and a month of clean reconciliation — and may well never happen, because
"the signal has no predictive value" is an acceptable, publishable answer.
