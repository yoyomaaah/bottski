"""Alpaca news collector (Benzinga-backed), via the raw REST endpoint.

We deliberately do NOT use alpaca-py's NewsClient here: its NewsSet model drops
next_page_token (always None), which silently caps collection at one page.

Incremental via a control-table watermark; overlap is harmless because upsert
dedupes on article id."""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from bottski.config import Settings
from bottski.store import db

logger = logging.getLogger("bottski.collect.news")

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
WATERMARK_KEY = "news_last_created_utc"
OVERLAP = timedelta(hours=1)
MAX_PAGES = 40
PAGE_LIMIT = 50


def build_fetcher(settings: Settings):
    if not settings.alpaca_key_id or not settings.alpaca_secret_key:
        raise RuntimeError(
            "Alpaca credentials missing — set ALPACA_PAPER_KEY_ID / ALPACA_PAPER_SECRET_KEY in .env"
        )
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_key_id,
        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
    }

    def fetch(params: dict) -> dict:
        url = NEWS_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    return fetch


def _parse_created(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _start_time(conn: sqlite3.Connection) -> datetime:
    mark = db.get_control(conn, WATERMARK_KEY)
    if mark:
        return datetime.fromisoformat(mark) - OVERLAP
    return datetime.now(timezone.utc) - timedelta(hours=24)


def _store_article(conn: sqlite3.Connection, art: dict) -> bool:
    created = _parse_created(art["created_at"]).astimezone(timezone.utc)
    return db.upsert_document(
        conn,
        source="news",
        source_id=str(art["id"]),
        subreddit_or_publisher=art.get("source") or "benzinga",
        author_hash=None,
        created_utc=created.isoformat(timespec="seconds"),
        title=art.get("headline"),
        body=art.get("summary") or art.get("content") or "",
        score_at_fetch=None,
        num_comments_at_fetch=None,
        url=art.get("url"),
        raw_json=json.dumps({"symbols": art.get("symbols") or []}),
    )


def collect(settings: Settings, conn: sqlite3.Connection, fetch=None) -> dict[str, int]:
    fetch = fetch or build_fetcher(settings)
    start = _start_time(conn)
    stats = {"news_new": 0, "refreshed": 0, "pages": 0}
    latest_created = start

    page_token = None
    for _ in range(MAX_PAGES):
        params = {
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "limit": PAGE_LIMIT,
            "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token
        data = fetch(params)
        stats["pages"] += 1
        for art in data.get("news", []):
            if _store_article(conn, art):
                stats["news_new"] += 1
            else:
                stats["refreshed"] += 1
            created = _parse_created(art["created_at"])
            if created > latest_created:
                latest_created = created
        conn.commit()
        page_token = data.get("next_page_token")
        if not page_token:
            break
    else:
        logger.warning("hit MAX_PAGES=%d — backlog remains, next run continues", MAX_PAGES)

    if latest_created > start:
        db.set_control(conn, WATERMARK_KEY, latest_created.isoformat(timespec="seconds"))
    logger.info("news collected: %s", stats)
    return stats
