"""SQLite access. One file, WAL mode, schema applied idempotently."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SCHEMA_VERSION = "2"

# Columns added after a table already shipped; CREATE IF NOT EXISTS won't add
# them to existing databases, so connect() applies these idempotently.
_COLUMN_MIGRATIONS = [
    ("observations", "ext_mentions", "INTEGER"),
    ("observations", "ext_rank", "INTEGER"),
    ("observations", "ext_sentiment_score", "REAL"),
    ("observations", "ext_adanos_sentiment", "REAL"),
    ("observations", "ext_adanos_buzz", "REAL"),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_PATH.read_text())
    for table, col, typ in _COLUMN_MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()
    return conn


def get_control(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM control WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_control(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO control (key, value, updated_utc) VALUES (?, ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_utc = excluded.updated_utc",
        (key, value, utcnow()),
    )
    conn.commit()


def kill_switch_engaged(conn: sqlite3.Connection, kill_file: Path) -> bool:
    """Halted if EITHER the DB flag or the file exists. Checked before every order."""
    return get_control(conn, "kill_switch", "0") == "1" or kill_file.exists()


def upsert_document(
    conn: sqlite3.Connection,
    *,
    source: str,
    source_id: str,
    subreddit_or_publisher: str | None,
    author_hash: str | None,
    created_utc: str,
    title: str | None,
    body: str | None,
    score_at_fetch: int | None,
    num_comments_at_fetch: int | None,
    url: str | None,
    raw_json: str,
) -> bool:
    """Insert a document, or refresh only its point-in-time counters if it
    already exists. Title/body/created are immutable after first sight.
    Returns True if the row was newly inserted."""
    existing = conn.execute(
        "SELECT id FROM raw_documents WHERE source = ? AND source_id = ?",
        (source, source_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE raw_documents SET score_at_fetch = ?, num_comments_at_fetch = ?, "
            "fetched_utc = ? WHERE id = ?",
            (score_at_fetch, num_comments_at_fetch, utcnow(), existing["id"]),
        )
        return False
    conn.execute(
        "INSERT INTO raw_documents (source, source_id, subreddit_or_publisher, "
        "author_hash, created_utc, fetched_utc, title, body, score_at_fetch, "
        "num_comments_at_fetch, url, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (source, source_id, subreddit_or_publisher, author_hash, created_utc,
         utcnow(), title, body, score_at_fetch, num_comments_at_fetch, url, raw_json),
    )
    return True
