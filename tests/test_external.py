"""External provider collectors — snapshot immutability is the contract."""

from bottski.collect import external
from bottski.config import load_settings
from bottski.store import db

PAPER_ENV = {"ALPACA_PAPER_KEY_ID": "pk", "ALPACA_PAPER_SECRET_KEY": "sk"}


def _settings(config_file):
    return load_settings(config_file("paper"), env=PAPER_ENV)


def apewisdom_fake(pages):
    def fetch(url):
        page = int(url.rstrip("/").rsplit("/", 1)[-1])
        return {"pages": len(pages), "results": pages[page - 1] if page <= len(pages) else []}
    return fetch


def test_apewisdom_pages_and_stores(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    pages = [
        [{"rank": 1, "ticker": "NVDA", "mentions": 500, "upvotes": 900, "mentions_24h_ago": 300}],
        [{"rank": 101, "ticker": "GME", "mentions": 20, "upvotes": 40, "mentions_24h_ago": 25}],
    ]
    stats = external.collect_apewisdom(s, conn, fetch=apewisdom_fake(pages))
    assert stats == {"apewisdom_rows": 2, "pages": 2}
    row = conn.execute(
        "SELECT * FROM external_sentiment WHERE symbol='NVDA'").fetchone()
    assert row["provider"] == "apewisdom" and row["mentions"] == 500
    assert row["mentions_24h_ago"] == 300


def test_tradestie_stores_labels(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    data = [
        {"no_of_comments": 150, "sentiment": "Bullish", "sentiment_score": 0.21, "ticker": "TSLA"},
        {"no_of_comments": 80, "sentiment": "Bearish", "sentiment_score": -0.11, "ticker": "AAPL"},
    ]
    stats = external.collect_tradestie(s, conn, fetch=lambda url: data)
    assert stats == {"tradestie_rows": 2}
    row = conn.execute(
        "SELECT * FROM external_sentiment WHERE symbol='TSLA'").fetchone()
    assert row["sentiment_label"] == "Bullish" and row["sentiment_score"] == 0.21


def test_garbage_tickers_are_rejected(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    data = [
        {"no_of_comments": 1, "sentiment": "Bullish", "sentiment_score": 0.1, "ticker": "TSLA"},
        {"no_of_comments": 1, "sentiment": "Bullish", "sentiment_score": 0.1, "ticker": "not a ticker!!"},
        {"no_of_comments": 1, "sentiment": "Bullish", "sentiment_score": 0.1, "ticker": ""},
    ]
    stats = external.collect_tradestie(s, conn, fetch=lambda url: data)
    assert stats == {"tradestie_rows": 1}


def test_snapshots_accumulate_never_overwrite(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    data = [{"no_of_comments": 1, "sentiment": "Bullish", "sentiment_score": 0.1, "ticker": "TSLA"}]
    external.collect_tradestie(s, conn, fetch=lambda url: data)
    import time
    time.sleep(1.1)  # utcnow() has second resolution; force a new fetched_utc
    external.collect_tradestie(s, conn, fetch=lambda url: data)
    n = conn.execute(
        "SELECT COUNT(*) c FROM external_sentiment WHERE symbol='TSLA'").fetchone()["c"]
    assert n == 2  # two snapshots, both kept


def test_adanos_quota_guard(config_file):
    import os
    s = load_settings(config_file("paper"), env={**PAPER_ENV, "ADANOS_API_KEY": "k"})
    conn = db.connect(s.db_path)
    page = [{"ticker": "NVDA", "sentiment_score": 0.31, "mentions": 3164,
             "total_upvotes": 24477, "trend": "falling", "buzz_score": 81.0},
            {"ticker": "not a ticker", "sentiment_score": 0.1}]
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        return page

    stats = external.collect_adanos(s, conn, fetch=fetch)
    assert stats["adanos_rows"] == 1 and calls["n"] == 1   # garbage ticker filtered
    row = conn.execute("SELECT * FROM external_sentiment WHERE provider='adanos'").fetchone()
    assert row["sentiment_score"] == 0.31 and row["mentions"] == 3164
    import json
    assert json.loads(row["raw_json"])["buzz_score"] == 81.0

    # second call same day must NOT spend a request
    stats2 = external.collect_adanos(s, conn, fetch=fetch)
    assert stats2.get("skipped_quota") == 1 and calls["n"] == 1
    # ...unless forced
    external.collect_adanos(s, conn, fetch=fetch, force=True)
    assert calls["n"] == 2


def test_adanos_skips_without_key(config_file):
    s = load_settings(config_file("paper"), env=PAPER_ENV)  # no ADANOS_API_KEY
    conn = db.connect(s.db_path)
    stats = external.collect_adanos(s, conn, fetch=lambda url: [])
    assert stats.get("skipped") == 1
