"""SQLite access. One file, WAL mode, schema applied idempotently."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SCHEMA_VERSION = "1"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_PATH.read_text())
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
