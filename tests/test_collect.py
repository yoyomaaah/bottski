"""Collector tests against fake clients — idempotency is the contract."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bottski.collect import news as news_mod
from bottski.collect import reddit as reddit_mod
from bottski.config import load_settings
from bottski.store import db

PAPER_ENV = {"ALPACA_PAPER_KEY_ID": "pk", "ALPACA_PAPER_SECRET_KEY": "sk"}


# --- fakes ------------------------------------------------------------------

class FakeComments(list):
    def replace_more(self, limit):
        pass

    def list(self):
        return self


def make_comment(i, post_id):
    return SimpleNamespace(
        fullname=f"t1_c{i}_{post_id}", author=SimpleNamespace(name=f"user{i}"),
        created_utc=1755750000 + i, body=f"comment {i} about $TSLA",
        score=i, permalink=f"/r/x/{post_id}/c{i}",
    )


def make_post(i, n_comments=2):
    post_id = f"p{i}"
    return SimpleNamespace(
        fullname=f"t3_{post_id}", author=SimpleNamespace(name=f"op{i}"),
        created_utc=1755740000 + i, title=f"post {i}", selftext="body $AAPL",
        score=10 * i, num_comments=n_comments, upvote_ratio=0.9, stickied=False,
        permalink=f"/r/x/{post_id}",
        comments=FakeComments(make_comment(j, post_id) for j in range(n_comments)),
    )


class FakeSubreddit:
    def __init__(self, posts):
        self._posts = posts

    def hot(self, limit):
        return self._posts[:limit]

    def new(self, limit):
        return self._posts[:limit]  # overlaps hot — dedupe must handle it


class FakeReddit:
    def __init__(self, posts):
        self._posts = posts

    def subreddit(self, name):
        return FakeSubreddit(self._posts)


class FakeNewsFetch:
    """Mimics the raw REST endpoint: pages of dicts + next_page_token."""

    def __init__(self, pages):
        self._pages = pages
        self.requests_seen = []

    def __call__(self, params):
        self.requests_seen.append(params)
        i = len(self.requests_seen) - 1
        page = self._pages[i] if i < len(self._pages) else []
        more = i + 1 < len(self._pages)
        return {"news": page, "next_page_token": f"tok{i}" if more else None}


def make_article(i, created=None):
    created = created or datetime(2026, 8, 21, 10, i, tzinfo=timezone.utc)
    return {
        "id": 1000 + i, "headline": f"headline {i}", "summary": f"summary {i}",
        "content": "", "symbols": ["AAPL", "TSLA"], "source": "benzinga",
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "url": f"https://example.com/{i}",
    }


def _settings(config_file):
    return load_settings(config_file("paper"), env=PAPER_ENV)


# --- reddit -----------------------------------------------------------------

def test_reddit_collect_is_idempotent(config_file, tmp_path):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    fake = FakeReddit([make_post(i) for i in range(3)])

    stats1 = reddit_mod.collect(s, conn, reddit=fake)
    assert stats1["posts_new"] == 3 and stats1["comments_new"] == 6

    count = conn.execute("SELECT COUNT(*) c FROM raw_documents").fetchone()["c"]
    stats2 = reddit_mod.collect(s, conn, reddit=fake)
    assert stats2["posts_new"] == 0 and stats2["comments_new"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM raw_documents").fetchone()["c"] == count


def test_reddit_refresh_updates_score_but_not_body(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    post = make_post(1, n_comments=0)
    reddit_mod.collect(s, conn, reddit=FakeReddit([post]))
    post.score = 999
    post.selftext = "EDITED BODY"
    reddit_mod.collect(s, conn, reddit=FakeReddit([post]))
    row = conn.execute(
        "SELECT body, score_at_fetch FROM raw_documents WHERE source_id = 't3_p1'"
    ).fetchone()
    assert row["score_at_fetch"] == 999      # counter refreshed
    assert row["body"] == "body $AAPL"       # first-seen text is immutable


def test_reddit_author_is_hashed_never_stored(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    reddit_mod.collect(s, conn, reddit=FakeReddit([make_post(1, n_comments=0)]))
    row = conn.execute("SELECT author_hash FROM raw_documents").fetchone()
    assert row["author_hash"] and "op1" not in row["author_hash"]


def test_reddit_deleted_author_is_null(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    post = make_post(1, n_comments=0)
    post.author = None
    reddit_mod.collect(s, conn, reddit=FakeReddit([post]))
    assert conn.execute("SELECT author_hash FROM raw_documents").fetchone()["author_hash"] is None


def test_reddit_one_bad_subreddit_does_not_stop_the_rest(config_file):
    s = _settings(config_file)
    s.collect.subreddits.extend(["okaysub"])  # config has ["wallstreetbets"]
    conn = db.connect(s.db_path)

    class HalfBrokenReddit(FakeReddit):
        def subreddit(self, name):
            if name == "wallstreetbets":
                raise RuntimeError("api down")
            return super().subreddit(name)

    stats = reddit_mod.collect(s, conn, reddit=HalfBrokenReddit([make_post(1)]))
    assert stats["errors"] == 1
    assert stats["posts_new"] == 1  # okaysub still collected


# --- news -------------------------------------------------------------------

def test_news_collect_is_idempotent_and_stores_symbols(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)

    stats1 = news_mod.collect(s, conn, fetch=FakeNewsFetch([[make_article(i) for i in range(4)]]))
    assert stats1["news_new"] == 4
    stats2 = news_mod.collect(s, conn, fetch=FakeNewsFetch([[make_article(i) for i in range(4)]]))
    assert stats2["news_new"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM raw_documents").fetchone()["c"] == 4

    row = conn.execute("SELECT raw_json FROM raw_documents LIMIT 1").fetchone()
    assert json.loads(row["raw_json"])["symbols"] == ["AAPL", "TSLA"]


def test_news_follows_pagination_tokens(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    pages = [[make_article(i)] for i in range(3)]  # 3 pages, 1 article each
    fetch = FakeNewsFetch(pages)
    stats = news_mod.collect(s, conn, fetch=fetch)
    assert stats["news_new"] == 3 and stats["pages"] == 3
    assert "page_token" not in fetch.requests_seen[0]
    assert fetch.requests_seen[1]["page_token"] == "tok0"
    assert fetch.requests_seen[2]["page_token"] == "tok1"


def test_news_watermark_advances_and_is_reused(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    # relative to now: a fixed date silently ages out of the 24h default window
    latest = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    news_mod.collect(s, conn, fetch=FakeNewsFetch([[make_article(1, created=latest)]]))
    assert db.get_control(conn, news_mod.WATERMARK_KEY) == latest.isoformat(timespec="seconds")

    fetch2 = FakeNewsFetch([[]])
    news_mod.collect(s, conn, fetch=fetch2)
    expected_start = (latest - news_mod.OVERLAP).isoformat().replace("+00:00", "Z")
    assert fetch2.requests_seen[0]["start"] == expected_start  # incremental with overlap
