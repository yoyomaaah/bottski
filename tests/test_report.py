import json

from bottski.config import load_settings
from bottski.research import report
from bottski.store import db

PAPER_ENV = {"ALPACA_PAPER_KEY_ID": "pk", "ALPACA_PAPER_SECRET_KEY": "sk"}


def test_report_graceful_without_forward_returns(config_file, tmp_path):
    s = load_settings(config_file("paper"), env=PAPER_ENV)
    conn = db.connect(s.db_path)
    out = tmp_path / "report.html"
    summary = report.build(s, conn, out_path=out)
    assert "waiting on forward returns" in summary
    html = out.read_text()
    assert "bottski" in html and "Waiting for the first forward returns" in html


def test_report_renders_metrics_when_returns_exist(config_file, tmp_path):
    s = load_settings(config_file("paper"), env=PAPER_ENV)
    conn = db.connect(s.db_path)
    import numpy as np
    rng = np.random.default_rng(1)
    for day in ("2026-08-10", "2026-08-11"):
        for i in range(15):
            sig = rng.normal()
            conn.execute(
                "INSERT INTO observations (obs_date, obs_ts_utc, symbol, n_mentions,"
                " n_news, score_mean, ret_1d, ret_5d, dollar_volume_20d, fwd_ret_1d)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (day, db.utcnow(), f"S{i}", 5, 5, sig, rng.normal(0, 0.02),
                 rng.normal(0, 0.04), 1e7, sig * 0.01),
            )
    conn.commit()
    out = tmp_path / "report.html"
    summary = report.build(s, conn, out_path=out)
    assert "score_mean" in summary
    assert "Signal quality" in out.read_text()


def test_report_trading_sections(config_file, tmp_path):
    s = load_settings(config_file("paper"), env=PAPER_ENV)
    conn = db.connect(s.db_path)
    conn.execute(
        "INSERT INTO positions_snapshot (snapshot_utc, symbol, qty,"
        " avg_entry_price, market_value, unrealized_pl)"
        " VALUES ('2026-08-21T20:00:00+00:00', 'TSLA', 20, 100, 2100, 100)")
    for i, eq in enumerate((100000, 100500, 100200)):
        conn.execute(
            "INSERT INTO equity_curve (snapshot_utc, equity, cash, gross_exposure)"
            f" VALUES ('2026-08-2{i}T20:00:00+00:00', {eq}, {eq}, 0)")
    for sym, blocked, inputs in [
        ("TSLA", None, {"panel": {"score_mean": 0.4, "n_mentions": 7}}),
        ("THIN", "min_dollar_volume", {"panel": {}}),
    ]:
        conn.execute(
            "INSERT INTO decisions (decision_utc, symbol, action, target_notional,"
            " reason_code, inputs_json, blocked_by, strategy_version, mode) VALUES"
            " (datetime('now'), ?, 'buy', 2000, 'sentiment_entry', ?, ?, 'v0', 'paper')",
            (sym, json.dumps(inputs), blocked))
    conn.commit()
    out = tmp_path / "index.html"
    report.build(s, conn, out_path=out)
    html = out.read_text()
    assert "Open positions" in html and "TSLA" in html
    assert "stock trades too thinly" in html      # blocked counterfactual, in plain language
    assert "<svg" in html                         # equity curve rendered
    assert "http-equiv='refresh'" in html


def test_decision_evidence_drilldown(config_file, tmp_path):
    s = load_settings(config_file("paper"), env=PAPER_ENV)
    conn = db.connect(s.db_path)
    inputs = {
        "panel": {"score_mean": 0.42, "n_mentions": 7, "ret_5d": 0.03,
                  "dollar_volume_20d": 4.8e7, "spread_bps": 4.0, "n_news": 5,
                  "news_score_mean": 0.38, "ext_sentiment_score": 0.12,
                  "ext_mentions": 88, "close": 101.5, "ret_1d": 0.012,
                  "dist_from_20d_high": -0.04},
        "account": {"equity": 100000, "n_positions": 1},
    }
    conn.execute(
        "INSERT INTO decisions (decision_utc, symbol, action, target_notional,"
        " reason_code, inputs_json, blocked_by, strategy_version, mode) VALUES"
        " (datetime('now'), 'TSLA', 'buy', 2000, 'sentiment_entry', ?, NULL, 'v0', 'paper')",
        (json.dumps(inputs),))
    # a near-miss hold with signal must also appear
    conn.execute(
        "INSERT INTO decisions (decision_utc, symbol, action, reason_code,"
        " inputs_json, strategy_version, mode) VALUES"
        " (datetime('now'), 'WEAK', 'hold', 'score_below_entry', ?, 'v0', 'paper')",
        (json.dumps({"panel": {"score_mean": 0.1, "n_mentions": 4}, "account": {}}),))
    conn.commit()
    out = tmp_path / "index.html"
    report.build(s, conn, out_path=out)
    html = out.read_text()
    assert "what the bot saw" in html
    assert "avg sentiment (need ≥ +0.25)" in html
    assert "5 of 7 mentions were news articles" in html
    assert "WSB sentiment (Tradestie): 0.12" in html
    assert "WEAK" in html and "passed" in html      # near-miss hold visible
    assert "How the bot decides" in html            # rules card present


def test_receipts_chatter_and_health(config_file, tmp_path):
    s = load_settings(config_file("paper"), env=PAPER_ENV)
    conn = db.connect(s.db_path)
    # document inside the Aug 20 window (Aug 19 16:00 ET -> Aug 20 15:40 ET)
    conn.execute(
        "INSERT INTO raw_documents (id, source, source_id, subreddit_or_publisher,"
        " created_utc, fetched_utc, title, body, raw_json) VALUES"
        " (1, 'news', 'n1', 'benzinga', '2026-08-20T14:00:00+00:00', ?,"
        " 'Supermicro upgraded on AI server demand', '', '{}')", (db.utcnow(),))
    from bottski.score.vader import SCORER_VERSION
    conn.execute(
        "INSERT INTO document_sentiment (document_id, symbol, compound, scorer_version)"
        " VALUES (1, 'SMCI', 0.64, ?)", (SCORER_VERSION,))
    cur = conn.execute(
        "INSERT INTO observations (obs_date, obs_ts_utc, symbol, n_mentions,"
        " score_mean, ext_sentiment_score, ext_rank, close, dollar_volume_20d)"
        " VALUES ('2026-08-20', ?, 'SMCI', 3, 0.64, 0.1, 4, 36.0, 1e8)", (db.utcnow(),))
    obs_id = cur.lastrowid
    conn.execute(
        "INSERT INTO decisions (decision_utc, obs_id, symbol, action, target_notional,"
        " reason_code, inputs_json, strategy_version, mode) VALUES"
        " (datetime('now'), ?, 'SMCI', 'buy', 2000, 'sentiment_entry',"
        " ?, 'v0', 'paper')", (obs_id, json.dumps({"panel": {"score_mean": 0.64,
                                                             "n_mentions": 3}, "account": {}})))
    conn.commit()
    out = tmp_path / "index.html"
    report.build(s, conn, out_path=out)
    html = out.read_text()
    # receipts: the actual headline appears with its individual score
    assert "Supermicro upgraded on AI server demand" in html
    assert "+0.64" in html and "the actual text behind" in html
    # chatter card with disagreement flag (0.64 vs 0.1 -> disagree)
    assert "What the market is talking about" in html
    assert "disagree" in html
    # source health strip
    assert "Data-source health" in html
    assert "News (Alpaca/Benzinga)" in html
    assert "ApeWisdom (WSB mentions)" in html


def test_ticker_links_and_tooltips(config_file, tmp_path):
    s = load_settings(config_file("paper"), env=PAPER_ENV)
    s.universe_file.write_text("symbol,name,aliases,sector\nBRK.B,Berkshire Hathaway,,finance\n")
    conn = db.connect(s.db_path)
    conn.execute(
        "INSERT INTO positions_snapshot (snapshot_utc, symbol, qty, avg_entry_price,"
        " market_value, unrealized_pl) VALUES (?, 'BRK.B', 5, 400, 2100, 100)",
        (db.utcnow(),))
    conn.commit()
    out = tmp_path / "index.html"
    report.build(s, conn, out_path=out)
    html = out.read_text()
    assert "https://finance.yahoo.com/quote/BRK-B" in html   # dot -> dash for yahoo
    assert "title='Berkshire Hathaway · finance'" in html    # tooltip carries entity data


def test_positions_table_units_are_consistent(config_file, tmp_path):
    s = load_settings(config_file("paper"), env=PAPER_ENV)
    conn = db.connect(s.db_path)
    conn.execute(
        "INSERT INTO positions_snapshot (snapshot_utc, symbol, qty, avg_entry_price,"
        " market_value, unrealized_pl) VALUES (?, 'AMD', 4, 471.09, 1888.0, 3.64)",
        (db.utcnow(),))
    conn.commit()
    out = tmp_path / "index.html"
    report.build(s, conn, out_path=out)
    html = out.read_text()
    assert "paid / share" in html and "now / share" in html and "total value" in html
    assert "$471.09" in html          # per-share entry
    assert "$472.00" in html          # per-share now (1888/4)
    assert "+3.64 (+0.19%)" in html   # exact P&L with percent
