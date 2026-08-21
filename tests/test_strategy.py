"""Strategy proposals + decide end-to-end on a seeded panel."""

from bottski.config import load_settings
from bottski.risk.rails import AccountState
from bottski.store import db
from bottski.strategy import core, decide

PAPER_ENV = {"ALPACA_PAPER_KEY_ID": "pk", "ALPACA_PAPER_SECRET_KEY": "sk"}


def _settings(config_file):
    return load_settings(config_file("paper"), env=PAPER_ENV)


def row(symbol, score=0.5, mentions=5, close=100.0, ret5=0.0, dv=1e8, **kw):
    base = dict(symbol=symbol, score_mean=score, n_mentions=mentions, close=close,
                ret_5d=ret5, dollar_volume_20d=dv, spread_bps=5,
                is_tradable=1, is_halted=0)
    base.update(kw)
    return base


def props_by_symbol(props):
    return {p.symbol: p for p in props}


def test_entry_requires_score_and_mentions(config_file):
    s = _settings(config_file)
    panel = [row("AAA"), row("BBB", score=0.1), row("CCC", mentions=1)]
    p = props_by_symbol(core.propose(s, panel, {}, {}, 100_000))
    assert p["AAA"].action == "buy"
    assert p["BBB"].action == "hold" and p["BBB"].reason_code == "score_below_entry"
    assert "CCC" not in p  # below mention floor: no signal, not even a hold row


def test_extended_move_is_not_bought(config_file):
    s = _settings(config_file)
    p = props_by_symbol(core.propose(s, [row("AAA", ret5=0.15)], {}, {}, 100_000))
    assert p["AAA"].action == "hold" and p["AAA"].reason_code == "extended_move"


def test_daily_cap_ranks_by_conviction(config_file):
    s = _settings(config_file)
    panel = [row(f"S{i}", score=0.3 + i * 0.05) for i in range(5)]  # S4 best
    props = core.propose(s, panel, {}, {}, 100_000)
    buys = [p.symbol for p in props if p.action == "buy"]
    assert buys == ["S4", "S3", "S2"]  # top 3 by score
    capped = [p for p in props if p.reason_code == "ranked_below_daily_cap"]
    assert {p.symbol for p in capped} == {"S0", "S1"}


def test_sizing_respects_position_pct_and_notional_cap(config_file):
    s = _settings(config_file)
    p = props_by_symbol(core.propose(s, [row("AAA", close=50.0)], {}, {}, 100_000))
    # min(2000, 100k*5%) = 2000 -> 40 shares
    assert p["AAA"].target_qty == 40.0 and p["AAA"].target_notional == 2000.0
    p2 = props_by_symbol(core.propose(s, [row("AAA", close=50.0)], {}, {}, 10_000))
    # min(2000, 10k*5%=500) = 500 -> 10 shares
    assert p2["AAA"].target_notional == 500.0


def test_exits_bearish_flip_and_time_stop(config_file):
    s = _settings(config_file)
    panel = [row("HELD1", score=-0.5), row("HELD2", score=0.4)]
    positions = {"HELD1": 2000.0, "HELD2": 2000.0, "HELD3": 2000.0}
    ages = {"HELD1": 1, "HELD2": 7, "HELD3": 2}
    p = props_by_symbol(core.propose(s, panel, positions, ages, 100_000))
    assert p["HELD1"].action == "sell" and p["HELD1"].reason_code == "sentiment_flip_bearish"
    assert p["HELD2"].action == "sell" and p["HELD2"].reason_code == "max_hold_days"
    assert p["HELD3"].action == "hold"  # no panel signal, young position


def _seed_panel(conn, rows, obs_date="2026-08-20"):
    for r in rows:
        conn.execute(
            "INSERT INTO observations (obs_date, obs_ts_utc, symbol, n_mentions,"
            " score_mean, close, ret_5d, dollar_volume_20d, spread_bps, is_tradable,"
            " is_halted) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (obs_date, db.utcnow(), r["symbol"], r["n_mentions"], r["score_mean"],
             r["close"], r["ret_5d"], r["dollar_volume_20d"], r["spread_bps"],
             r["is_tradable"], r["is_halted"]))
    conn.commit()


def test_decide_writes_decisions_with_blocks_and_is_deterministic(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    _seed_panel(conn, [
        row("GOOD"),
        row("THIN", score=0.6, dv=1e5),      # will hit min_dollar_volume
        row("WEAK", score=0.05),
    ])
    acct = AccountState(equity=100_000, cash=100_000, day_start_equity=100_000)

    stats = decide.run(s, conn, "2026-08-20", acct)
    assert stats["buy"] == 1 and stats["blocked"] == 1

    d = {r["symbol"]: dict(r) for r in conn.execute("SELECT * FROM decisions")}
    assert d["GOOD"]["action"] == "buy" and d["GOOD"]["blocked_by"] is None
    assert d["THIN"]["action"] == "buy" and d["THIN"]["blocked_by"] == "min_dollar_volume"
    assert d["WEAK"]["action"] == "hold"
    assert d["GOOD"]["mode"] == "dry-run" and d["GOOD"]["strategy_version"] == "v0"
    import json
    inputs = json.loads(d["GOOD"]["inputs_json"])
    assert inputs["panel"]["score_mean"] == 0.5 and inputs["account"]["equity"] == 100_000

    # determinism: replay -> identical decision tuples
    first = conn.execute(
        "SELECT symbol, action, target_qty, reason_code, blocked_by FROM decisions"
        " ORDER BY symbol").fetchall()
    decide.run(s, conn, "2026-08-20", acct)
    rows = conn.execute(
        "SELECT symbol, action, target_qty, reason_code, blocked_by FROM decisions"
        " ORDER BY id").fetchall()
    second = sorted([tuple(r) for r in rows[len(first):]])
    assert sorted([tuple(r) for r in first]) == second


def test_decide_kill_switch_blocks_all_trades(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    _seed_panel(conn, [row("AAA"), row("BBB", score=0.4)])
    db.set_control(conn, "kill_switch", "1")
    acct = AccountState(equity=100_000, cash=100_000)
    stats = decide.run(s, conn, "2026-08-20", acct)
    assert stats["buy"] == 0
    blocked = [r["blocked_by"] for r in conn.execute(
        "SELECT blocked_by FROM decisions WHERE action='buy'")]
    assert blocked and all(b == "kill_switch" for b in blocked)


def test_decide_without_panel_reports_error(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    stats = decide.run(s, conn, "2026-08-20", AccountState(equity=1, cash=1))
    assert stats == {"error_no_panel": 1}
