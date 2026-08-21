from bottski.store import db


def test_schema_applies_idempotently(tmp_path):
    p = tmp_path / "x.db"
    conn = db.connect(p)
    conn.close()
    conn = db.connect(p)  # second connect re-runs schema; must not fail
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "raw_documents", "document_tickers", "document_sentiment",
        "observations", "decisions", "orders", "fills",
        "positions_snapshot", "equity_curve", "control", "meta",
    } <= tables


def test_kill_switch_via_flag_and_file(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    kill_file = tmp_path / "KILL"
    assert not db.kill_switch_engaged(conn, kill_file)
    db.set_control(conn, "kill_switch", "1")
    assert db.kill_switch_engaged(conn, kill_file)
    db.set_control(conn, "kill_switch", "0")
    assert not db.kill_switch_engaged(conn, kill_file)
    kill_file.touch()  # file alone must also halt
    assert db.kill_switch_engaged(conn, kill_file)


def test_duplicate_raw_document_is_rejected(tmp_path):
    import sqlite3
    conn = db.connect(tmp_path / "x.db")
    row = ("reddit_post", "t3_abc", "wallstreetbets", "h", "2026-08-21T00:00:00+00:00",
           db.utcnow(), "title", "body", 1, 0, "u", "{}")
    q = ("INSERT INTO raw_documents (source, source_id, subreddit_or_publisher, author_hash,"
         " created_utc, fetched_utc, title, body, score_at_fetch, num_comments_at_fetch, url, raw_json)"
         " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
    conn.execute(q, row)
    try:
        conn.execute(q, row)
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
