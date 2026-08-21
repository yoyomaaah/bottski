"""Alpaca news collector (Benzinga-backed). Incremental via a control-table
watermark; overlap is harmless because upsert dedupes on article id."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from bottski.config import Settings
from bottski.store import db

logger = logging.getLogger("bottski.collect.news")

WATERMARK_KEY = "news_last_created_utc"
OVERLAP = timedelta(hours=1)
MAX_PAGES = 20


def build_client(settings: Settings):
    if not settings.alpaca_key_id or not settings.alpaca_secret_key:
        raise RuntimeError(
            "Alpaca credentials missing — set ALPACA_PAPER_KEY_ID / ALPACA_PAPER_SECRET_KEY in .env"
        )
    from alpaca.data.historical.news import NewsClient  # lazy

    return NewsClient(api_key=settings.alpaca_key_id, secret_key=settings.alpaca_secret_key)


def _start_time(conn: sqlite3.Connection) -> datetime:
    mark = db.get_control(conn, WATERMARK_KEY)
    if mark:
        return datetime.fromisoformat(mark) - OVERLAP
    return datetime.now(timezone.utc) - timedelta(hours=24)


def _store_article(conn: sqlite3.Connection, art) -> bool:
    created = art.created_at
    if isinstance(created, datetime):
        created = created.astimezone(timezone.utc).isoformat(timespec="seconds")
    symbols = list(getattr(art, "symbols", None) or [])
    return db.upsert_document(
        conn,
        source="news",
        source_id=str(art.id),
        subreddit_or_publisher=getattr(art, "source", None) or "benzinga",
        author_hash=None,
        created_utc=created,
        title=art.headline,
        body=(getattr(art, "summary", "") or "") or (getattr(art, "content", "") or ""),
        score_at_fetch=None,
        num_comments_at_fetch=None,
        url=getattr(art, "url", None),
        raw_json=json.dumps({"symbols": symbols}),
    )


def collect(settings: Settings, conn: sqlite3.Connection, client=None) -> dict[str, int]:
    from alpaca.data.requests import NewsRequest  # lazy

    client = client or build_client(settings)
    start = _start_time(conn)
    stats = {"news_new": 0, "refreshed": 0}
    latest_created = start

    page_token = None
    for _ in range(MAX_PAGES):
        req = NewsRequest(start=start, limit=50, include_content=False, page_token=page_token)
        res = client.get_news(req)
        articles = res.data.get("news", []) if hasattr(res, "data") else []
        for art in articles:
            if _store_article(conn, art):
                stats["news_new"] += 1
            else:
                stats["refreshed"] += 1
            created = art.created_at
            if isinstance(created, datetime) and created > latest_created:
                latest_created = created
        conn.commit()
        page_token = getattr(res, "next_page_token", None)
        if not page_token:
            break

    if latest_created > start:
        db.set_control(conn, WATERMARK_KEY, latest_created.isoformat(timespec="seconds"))
    logger.info("news collected: %s", stats)
    return stats
