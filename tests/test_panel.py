"""Panel construction against seeded docs and fake market data."""

from datetime import date

from bottski.config import load_settings
from bottski.extract import tickers as extract_mod
from bottski.features import panel, returns
from bottski.score import vader as score_mod
from bottski.store import db

PAPER_ENV = {"ALPACA_PAPER_KEY_ID": "pk", "ALPACA_PAPER_SECRET_KEY": "sk"}
OBS = date(2026, 8, 20)  # Thursday


def _settings(config_file, tmp_path):
    s = load_settings(config_file("paper"), env=PAPER_ENV)
    s.universe_file.write_text("symbol,name,aliases\nTSLA,Tesla,tesla\nNVDA,NVIDIA,nvidia\nSPY,SPDR,\n")
    return s


def _seed_doc(conn, doc_id, created, title, body=""):
    conn.execute(
        "INSERT INTO raw_documents (id, source, source_id, subreddit_or_publisher,"
        " author_hash, created_utc, fetched_utc, title, body, raw_json)"
        " VALUES (?, 'news', ?, 'benzinga', NULL, ?, ?, ?, ?, '{}')",
        (doc_id, f"n{doc_id}", created, db.utcnow(), title, body),
    )


def fake_bars_factory(n_days=30, base=100.0):
    def fetch(path, params):
        if path != "bars":
            return {}
        symbols = params["symbols"].split(",")
        out = {}
        for sym in symbols:
            bars = []
            # trading days Mon-Fri ending on OBS date
            import datetime as dt
            d = OBS
            days = []
            while len(days) < n_days:
                if d.weekday() < 5:
                    days.append(d)
                d -= dt.timedelta(days=1)
            days.reverse()
            px = base
            for day in days:
                px *= 1.01
                bars.append({"t": day.isoformat() + "T04:00:00Z", "o": px, "h": px * 1.02,
                             "l": px * 0.98, "c": px, "v": 1_000_000})
            out[sym] = bars
        return {"bars": out, "next_page_token": None}
    return fetch


def _pipeline(conn, settings):
    extract_mod.run(settings, conn)
    score_mod.run(settings, conn)


def test_panel_covers_full_universe_including_zero_mention(config_file, tmp_path):
    s = _settings(config_file, tmp_path)
    conn = db.connect(s.db_path)
    # one doc inside the window (Aug 19 20:30 UTC = 16:30 ET Wed, after prev close 16:00)
    _seed_doc(conn, 1, "2026-08-19T21:00:00+00:00", "NVDA soars on huge gains")
    conn.commit()
    _pipeline(conn, s)
    stats = panel.build(s, conn, OBS, bars_fetch=fake_bars_factory(), quotes_fetch=lambda syms: {})
    assert stats["rows"] == 3  # full universe, not just mentioned symbols
    rows = {r["symbol"]: dict(r) for r in conn.execute("SELECT * FROM observations")}
    assert rows["NVDA"]["n_mentions"] == 1 and rows["NVDA"]["score_mean"] > 0
    assert rows["TSLA"]["n_mentions"] == 0 and rows["TSLA"]["score_mean"] is None
    assert rows["SPY"]["close"] is not None and rows["SPY"]["ret_5d"] is not None


def test_docs_outside_window_are_excluded(config_file, tmp_path):
    s = _settings(config_file, tmp_path)
    conn = db.connect(s.db_path)
    _seed_doc(conn, 1, "2026-08-19T15:00:00+00:00", "NVDA soars")  # 11:00 ET Wed — before window
    _seed_doc(conn, 2, "2026-08-20T19:41:00+00:00", "NVDA crashes")  # 15:41 ET Thu — after cutoff
    conn.commit()
    _pipeline(conn, s)
    panel.build(s, conn, OBS, bars_fetch=fake_bars_factory(), quotes_fetch=lambda syms: {})
    row = conn.execute("SELECT * FROM observations WHERE symbol='NVDA'").fetchone()
    assert row["n_mentions"] == 0


def test_reobserve_preserves_forward_returns(config_file, tmp_path):
    s = _settings(config_file, tmp_path)
    conn = db.connect(s.db_path)
    panel.build(s, conn, OBS, bars_fetch=fake_bars_factory(), quotes_fetch=lambda syms: {})
    conn.execute("UPDATE observations SET fwd_ret_1d = 0.123 WHERE symbol='TSLA'")
    conn.commit()
    panel.build(s, conn, OBS, bars_fetch=fake_bars_factory(), quotes_fetch=lambda syms: {})
    row = conn.execute("SELECT fwd_ret_1d FROM observations WHERE symbol='TSLA'").fetchone()
    assert row["fwd_ret_1d"] == 0.123  # upsert must not clobber backfilled returns


def test_non_trading_day_is_skipped(config_file, tmp_path):
    s = _settings(config_file, tmp_path)
    conn = db.connect(s.db_path)
    stats = panel.build(s, conn, date(2026, 8, 22), bars_fetch=fake_bars_factory())  # Saturday
    assert stats == {"skipped": 1}
    assert conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"] == 0


def test_backfill_fills_only_available_horizons(config_file, tmp_path):
    s = _settings(config_file, tmp_path)
    conn = db.connect(s.db_path)
    obs = date(2026, 8, 13)  # Thursday, 5 trading days before OBS(=Aug 20)
    conn.execute(
        "INSERT INTO observations (obs_date, obs_ts_utc, symbol) VALUES (?, ?, 'TSLA')",
        (obs.isoformat(), db.utcnow()),
    )
    conn.commit()
    stats = returns.run(s, conn, bars_fetch=fake_bars_factory())
    row = conn.execute("SELECT * FROM observations").fetchone()
    assert row["fwd_ret_1d"] is not None and row["fwd_ret_5d"] is not None
    assert abs(row["fwd_ret_1d"] - 0.01) < 1e-9   # bars grow 1%/day
    assert abs(row["fwd_ret_5d"] - (1.01 ** 5 - 1)) < 1e-9
    assert row["fwd_ret_10d"] is None             # only 5 later days exist
    assert row["fwd_filled_utc"] is None          # not complete yet
    assert stats["complete"] == 0


def test_backfill_completes_and_stamps_when_10d_available(config_file, tmp_path):
    s = _settings(config_file, tmp_path)
    conn = db.connect(s.db_path)
    obs = date(2026, 8, 4)  # >10 trading days before OBS
    conn.execute(
        "INSERT INTO observations (obs_date, obs_ts_utc, symbol) VALUES (?, ?, 'TSLA')",
        (obs.isoformat(), db.utcnow()),
    )
    conn.commit()
    stats = returns.run(s, conn, bars_fetch=fake_bars_factory())
    row = conn.execute("SELECT * FROM observations").fetchone()
    assert row["fwd_ret_10d"] is not None and row["fwd_filled_utc"] is not None
    assert stats["complete"] == 1


def test_strip_incomplete_bars_guard():
    from datetime import datetime
    from bottski.features.returns import ET, strip_incomplete_bars

    bars = {"TSLA": [{"t": "2026-08-20T04:00:00Z", "c": 1},
                     {"t": "2026-08-21T04:00:00Z", "c": 2}]}
    midday = datetime(2026, 8, 21, 12, 0, tzinfo=ET)
    strip_incomplete_bars(bars, midday)
    assert [b["t"][:10] for b in bars["TSLA"]] == ["2026-08-20"]

    bars2 = {"TSLA": [{"t": "2026-08-21T04:00:00Z", "c": 2}]}
    after_close = datetime(2026, 8, 21, 16, 10, tzinfo=ET)
    strip_incomplete_bars(bars2, after_close)
    assert len(bars2["TSLA"]) == 1
