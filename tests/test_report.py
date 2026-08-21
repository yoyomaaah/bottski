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
    assert "bottski" in html and "No filled forward returns yet" in html


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
