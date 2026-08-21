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
