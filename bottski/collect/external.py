"""Collectors for third-party Reddit-derived sentiment feeds.

These give us the social leg without Reddit API approval: ApeWisdom (per-ticker
mention counts across finance subreddits) and Tradestie (top-50 WSB tickers
with bullish/bearish labels). Both free, no key. Their ticker extraction and
methodology are opaque, so rows are stored verbatim per provider and kept
separate from our own scored data — the evaluation harness measures each
signal's value independently.

Every fetch is stored as its own snapshot (keyed by provider + fetched_utc);
nothing is ever overwritten, so panel reads stay point-in-time honest.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import urllib.request

from bottski.config import Settings
from bottski.store import db

logger = logging.getLogger("bottski.collect.external")

APEWISDOM_URL = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}"
APEWISDOM_MAX_PAGES = 3  # 100/page; beyond ~300 the tail is noise
TRADESTIE_URL = "https://tradestie.com/api/v1/apps/reddit"
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _http_get_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": "bottski/0.1 (research)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _insert(conn: sqlite3.Connection, provider: str, fetched: str, symbol: str, **kw) -> bool:
    symbol = symbol.strip().upper()
    if not TICKER_RE.match(symbol):
        return False
    conn.execute(
        "INSERT OR IGNORE INTO external_sentiment (provider, fetched_utc, symbol,"
        " rank, mentions, upvotes, mentions_24h_ago, sentiment_label,"
        " sentiment_score, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (provider, fetched, symbol, kw.get("rank"), kw.get("mentions"),
         kw.get("upvotes"), kw.get("mentions_24h_ago"), kw.get("sentiment_label"),
         kw.get("sentiment_score"), kw.get("raw_json", "{}")),
    )
    return True


def collect_apewisdom(settings: Settings, conn: sqlite3.Connection, fetch=None) -> dict[str, int]:
    fetch = fetch or _http_get_json
    fetched = db.utcnow()
    stats = {"apewisdom_rows": 0, "pages": 0}
    for page in range(1, APEWISDOM_MAX_PAGES + 1):
        data = fetch(APEWISDOM_URL.format(page=page))
        results = data.get("results", [])
        if not results:
            break
        stats["pages"] += 1
        for r in results:
            if _insert(
                conn, "apewisdom", fetched, r.get("ticker", ""),
                rank=r.get("rank"), mentions=r.get("mentions"),
                upvotes=r.get("upvotes"), mentions_24h_ago=r.get("mentions_24h_ago"),
                raw_json=json.dumps(r),
            ):
                stats["apewisdom_rows"] += 1
        if page >= int(data.get("pages", 1)):
            break
    conn.commit()
    logger.info("apewisdom collected: %s", stats)
    return stats


def collect_tradestie(settings: Settings, conn: sqlite3.Connection, fetch=None) -> dict[str, int]:
    fetch = fetch or _http_get_json
    fetched = db.utcnow()
    stats = {"tradestie_rows": 0}
    for r in fetch(TRADESTIE_URL):
        if _insert(
            conn, "tradestie", fetched, r.get("ticker", ""),
            mentions=r.get("no_of_comments"),
            sentiment_label=r.get("sentiment"),
            sentiment_score=r.get("sentiment_score"),
            raw_json=json.dumps(r),
        ):
            stats["tradestie_rows"] += 1
    conn.commit()
    logger.info("tradestie collected: %s", stats)
    return stats
