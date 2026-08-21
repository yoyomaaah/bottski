# bottski

Sentiment-driven trading bot: US stocks/ETFs via Alpaca, signal from Reddit + news sentiment.
**Paper trading by default; live mode is behind a double lock and an M8 review gate.**

The real deliverable is the answer to: *does the sentiment signal have any predictive value?*
See the observation panel + signal evaluation harness design in the plan.

## Setup

```
uv sync
cp .env.example .env   # fill in keys
uv run pytest
uv run bottski status
```

## Commands

| command | what |
|---|---|
| `bottski status` | mode, kill switch, db counts |
| `bottski halt` / `resume` | engage / release kill switch |
| `bottski collect` | pull Reddit + news into raw_documents (M1) |
| `bottski observe` | write the daily observation panel (M3) |
| `bottski decide` | strategy decisions, never touches broker (M5) |
| `bottski execute` | place orders on Alpaca (M6) |
| `bottski backfill-returns` | fill forward returns, lookahead-safe (M3) |
| `bottski reconcile` | local state vs broker; halt on mismatch (M6) |
| `bottski report` | HTML report + signal-quality metrics (M4) |

Secrets live in `.env` only (gitignored) and are redacted from all log output.
