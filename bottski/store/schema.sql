-- bottski schema. All timestamps UTC ISO-8601 strings. Idempotent (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Operational flags (kill switch etc.)
CREATE TABLE IF NOT EXISTS control (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_utc TEXT NOT NULL
);

-- Immutable raw text as collected. Never updated except *_at_fetch refreshes.
CREATE TABLE IF NOT EXISTS raw_documents (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,               -- 'reddit_post' | 'reddit_comment' | 'news'
    source_id TEXT NOT NULL,            -- reddit fullname / news article id
    subreddit_or_publisher TEXT,
    author_hash TEXT,                   -- sha256 of author name, not the name itself
    created_utc TEXT NOT NULL,          -- when the content was posted
    fetched_utc TEXT NOT NULL,          -- when we collected it (point-in-time marker)
    title TEXT,
    body TEXT,
    score_at_fetch INTEGER,
    num_comments_at_fetch INTEGER,
    url TEXT,
    raw_json TEXT,
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_raw_documents_created ON raw_documents (created_utc);

-- Ticker extraction output; re-runnable, versioned.
CREATE TABLE IF NOT EXISTS document_tickers (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES raw_documents (id),
    symbol TEXT NOT NULL,
    match_type TEXT NOT NULL,           -- 'cashtag' | 'bare' | 'company_name'
    confidence REAL NOT NULL,
    extractor_version TEXT NOT NULL,
    UNIQUE (document_id, symbol, extractor_version)
);
CREATE INDEX IF NOT EXISTS idx_document_tickers_symbol ON document_tickers (symbol);

-- Sentiment scoring output; re-runnable, versioned. VADER->FinBERT = new rows.
CREATE TABLE IF NOT EXISTS document_sentiment (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES raw_documents (id),
    symbol TEXT NOT NULL,
    compound REAL NOT NULL,
    pos REAL,
    neu REAL,
    neg REAL,
    scorer_version TEXT NOT NULL,
    UNIQUE (document_id, symbol, scorer_version)
);

-- The observation panel: one row per (obs_date, symbol) for EVERY universe
-- symbol, including zero-mention ones (control group). Forward returns are
-- filled strictly later by backfill-returns; they must be NULL at insert.
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    obs_date TEXT NOT NULL,             -- ET trading date
    obs_ts_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,

    n_mentions INTEGER NOT NULL DEFAULT 0,
    n_unique_authors INTEGER NOT NULL DEFAULT 0,
    score_mean REAL,
    score_sum REAL,
    score_std REAL,
    mention_velocity REAL,
    n_news INTEGER NOT NULL DEFAULT 0,
    news_score_mean REAL,

    close REAL,
    ret_1d REAL,
    ret_5d REAL,
    ret_20d REAL,
    atr14 REAL,
    dist_from_20d_high REAL,
    dollar_volume_20d REAL,
    spread_bps REAL,
    is_halted INTEGER NOT NULL DEFAULT 0,
    is_tradable INTEGER NOT NULL DEFAULT 1,

    extractor_version TEXT,
    scorer_version TEXT,
    universe_version TEXT,

    ext_mentions INTEGER,
    ext_rank INTEGER,
    ext_sentiment_score REAL,
    ext_adanos_sentiment REAL,
    ext_adanos_buzz REAL,

    fwd_ret_1d REAL,
    fwd_ret_3d REAL,
    fwd_ret_5d REAL,
    fwd_ret_10d REAL,
    fwd_filled_utc TEXT,

    UNIQUE (obs_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_observations_symbol ON observations (symbol, obs_date);

-- Every decision, including HOLD and risk-blocked trades (the counterfactual).
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    decision_utc TEXT NOT NULL,
    obs_id INTEGER REFERENCES observations (id),
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,               -- 'buy' | 'sell' | 'hold'
    target_qty REAL,
    target_notional REAL,
    reason_code TEXT NOT NULL,
    inputs_json TEXT NOT NULL,          -- immutable snapshot of everything the decision saw
    blocked_by TEXT,                    -- risk rail name if blocked, else NULL
    strategy_version TEXT NOT NULL,
    mode TEXT NOT NULL                  -- 'paper' | 'live' | 'dry-run'
);
CREATE INDEX IF NOT EXISTS idx_decisions_utc ON decisions (decision_utc);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    decision_id INTEGER NOT NULL REFERENCES decisions (id),
    client_order_id TEXT NOT NULL UNIQUE, -- deterministic: {strategy_version}-{decision_id}
    broker_order_id TEXT,
    submitted_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    order_type TEXT NOT NULL,
    stop_loss_price REAL,
    status TEXT NOT NULL,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders (id),
    filled_utc TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS positions_snapshot (
    id INTEGER PRIMARY KEY,
    snapshot_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    qty REAL NOT NULL,
    avg_entry_price REAL,
    market_value REAL,
    unrealized_pl REAL
);

CREATE TABLE IF NOT EXISTS equity_curve (
    id INTEGER PRIMARY KEY,
    snapshot_utc TEXT NOT NULL UNIQUE,
    equity REAL NOT NULL,
    cash REAL NOT NULL,
    gross_exposure REAL NOT NULL
);

-- Snapshots from third-party Reddit-derived sentiment providers (ApeWisdom,
-- Tradestie). Every fetch is its own row set: observe picks the latest fetch
-- at or before its observation time, so rows are point-in-time honest.
CREATE TABLE IF NOT EXISTS external_sentiment (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    fetched_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    rank INTEGER,
    mentions INTEGER,
    upvotes INTEGER,
    mentions_24h_ago INTEGER,
    sentiment_label TEXT,            -- 'Bullish' | 'Bearish' (tradestie)
    sentiment_score REAL,
    raw_json TEXT NOT NULL,
    UNIQUE (provider, fetched_utc, symbol)
);
CREATE INDEX IF NOT EXISTS idx_external_sentiment_lookup
    ON external_sentiment (provider, symbol, fetched_utc);
