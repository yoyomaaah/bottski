"""Reddit collector: submissions + comment trees from configured subreddits.

Idempotent: dedupes on (source, source_id); re-runs only refresh point-in-time
counters. The praw client is injectable so tests run without network.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone

from bottski.config import Settings
from bottski.store import db

logger = logging.getLogger("bottski.collect.reddit")


def _author_hash(author) -> str | None:
    name = getattr(author, "name", None)
    if not name:
        return None  # deleted accounts
    return hashlib.sha256(name.lower().encode()).hexdigest()[:16]


def _ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


def build_client(settings: Settings):
    if not settings.reddit_client_id or not settings.reddit_client_secret:
        raise RuntimeError(
            "Reddit credentials missing — set REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET in .env"
        )
    import praw  # lazy: only needed when actually collecting

    return praw.Reddit(
        client_id=settings.reddit_client_id,
        client_secret=settings.reddit_client_secret,
        user_agent=settings.reddit_user_agent or "bottski/0.1",
    )


def _store_submission(conn: sqlite3.Connection, sub_name: str, post) -> bool:
    return db.upsert_document(
        conn,
        source="reddit_post",
        source_id=post.fullname,
        subreddit_or_publisher=sub_name,
        author_hash=_author_hash(post.author),
        created_utc=_ts(post.created_utc),
        title=post.title,
        body=getattr(post, "selftext", "") or "",
        score_at_fetch=post.score,
        num_comments_at_fetch=post.num_comments,
        url=f"https://reddit.com{post.permalink}",
        raw_json=json.dumps({"upvote_ratio": getattr(post, "upvote_ratio", None)}),
    )


def _store_comment(conn: sqlite3.Connection, sub_name: str, comment) -> bool:
    return db.upsert_document(
        conn,
        source="reddit_comment",
        source_id=comment.fullname,
        subreddit_or_publisher=sub_name,
        author_hash=_author_hash(comment.author),
        created_utc=_ts(comment.created_utc),
        title=None,
        body=comment.body or "",
        score_at_fetch=comment.score,
        num_comments_at_fetch=None,
        url=f"https://reddit.com{comment.permalink}",
        raw_json="{}",
    )


def collect(settings: Settings, conn: sqlite3.Connection, reddit=None) -> dict[str, int]:
    """Pull hot+new submissions and their comment trees for every configured
    subreddit. Returns counts. Commits once per subreddit (crash domain)."""
    reddit = reddit or build_client(settings)
    cfg = settings.collect
    stats = {"posts_new": 0, "comments_new": 0, "refreshed": 0, "errors": 0}

    for sub_name in cfg.subreddits:
        try:
            subreddit = reddit.subreddit(sub_name)
            seen: set[str] = set()
            posts = list(subreddit.hot(limit=cfg.posts_per_subreddit)) + list(
                subreddit.new(limit=cfg.posts_per_subreddit)
            )
            for post in posts:
                if post.fullname in seen:
                    continue
                seen.add(post.fullname)
                if getattr(post, "stickied", False) and not post.num_comments:
                    continue
                if _store_submission(conn, sub_name, post):
                    stats["posts_new"] += 1
                else:
                    stats["refreshed"] += 1
                try:
                    post.comments.replace_more(limit=0)
                    for comment in post.comments.list()[: cfg.comments_per_post]:
                        if _store_comment(conn, sub_name, comment):
                            stats["comments_new"] += 1
                        else:
                            stats["refreshed"] += 1
                except Exception:
                    logger.exception("comments failed for %s", post.fullname)
                    stats["errors"] += 1
            conn.commit()
            logger.info("collected r/%s: %s", sub_name, stats)
        except Exception:
            conn.commit()  # keep whatever landed before the failure
            logger.exception("subreddit %s failed", sub_name)
            stats["errors"] += 1
    return stats
