# CLAUDE.md

Working notes for agent sessions in this repo. The README explains what the
project is; this file is about how to work on it without breaking its rules.

## Invariants — do not violate

- **Timestamps are stored UTC everywhere**; convert to local (Stockholm for
  display, America/New_York for market logic) only at the edges.
- **Versioned artifacts never mutate**: extractor (`EXTRACTOR_VERSION`),
  scorer (`SCORER_VERSION`), universe (`UNIVERSE_VERSION`), strategy
  (`strategy.version`). Any behavior change bumps the version and writes new
  rows alongside old ones.
- **Forward-return columns (`fwd_*`) are written only by backfill-returns.**
  The panel upsert must never touch them (lookahead protection). Intraday
  bars are stripped before computing returns (`strip_incomplete_bars`).
- **The panel covers the full universe including zero-mention symbols** — the
  control group. Blacklisting/exclusions affect the portfolio, never the panel.
- **Execution safety**: reconcile-first (mismatch ⇒ kill switch, never
  self-heal); `client_order_id = {strategy_version}-{decision_id}`
  (deterministic; duplicate ⇒ adopt, never resubmit); kill switch checked
  before every single order; sells are never rail-blocked (except kill/halt).
- **Live mode** needs config `mode="live"` + exact `BOTTSKI_LIVE_CONFIRM` env
  + dedicated `ALPACA_LIVE_*` keys. Never weaken these locks.

## Environment quirks (details in tests and git history)

- Use raw REST for Alpaca news/bars/quotes — alpaca-py's NewsSet silently
  drops `next_page_token` (caps collection at one page).
- macOS dev machine: CPython skips UF_HIDDEN .pth files; if imports break,
  `chflags nohidden .venv/lib/python*/site-packages/*.pth` (workarounds
  already in sitecustomize.py + tests/conftest.py). Linux unaffected.
- Reddit credentials absent ⇒ collector must skip cleanly, never fail.

## Deploying

Deploy is **rsync, not git pull**, to the host in `deploy/hosts.local`
(untracked — operational endpoints are deliberately kept out of this public
repo). ALWAYS `--exclude .env` (the remote .env holds more keys than the
local one) and exclude `.venv data __pycache__ .pytest_cache .git`. After
rsync: chown to the app user, copy `deploy/systemd/*` units if changed,
regenerate the report. `deploy/provision.sh` is idempotent and doubles as the
update path for system-level changes.

## Testing

`uv run pytest` (100+ tests, ~1 min; the IC null-test suite dominates).
Conventions worth keeping: every risk rail has a constructed violation test;
extraction traps are pinned as regression tests after hand-labeling; decide
must stay deterministic (replay-twice test); report assertions are
plain-language strings, not markup details.

## Language & tone

Dashboard copy is written for a non-quant reader: plain language first,
statistics behind disclosures, honest provenance (e.g. "WSB score" is
Tradestie's second-hand reading, not ours). Keep it that way.
