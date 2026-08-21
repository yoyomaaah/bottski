"""Execution + reconciliation against a FakeBroker enforcing Alpaca's
client_order_id uniqueness — the crash-restart double-order gate."""

import json

from bottski import execution, reconcile
from bottski.broker.alpaca import DuplicateOrderError, Position
from bottski.config import load_settings
from bottski.risk.rails import AccountState
from bottski.store import db

PAPER_ENV = {"ALPACA_PAPER_KEY_ID": "pk", "ALPACA_PAPER_SECRET_KEY": "sk"}


class FakeBroker:
    def __init__(self, positions=None, open_=True, equity=100_000.0):
        self._positions = positions or {}
        self._open = open_
        self._equity = equity
        self.orders: dict[str, dict] = {}   # client_order_id -> order
        self.submissions = 0

    def market_open(self):
        return self._open

    def positions(self):
        return dict(self._positions)

    def account_state(self):
        return AccountState(
            equity=self._equity, cash=self._equity,
            positions={s: p.market_value for s, p in self._positions.items()})

    def _submit(self, symbol, side, qty, client_order_id):
        if client_order_id in self.orders:
            raise DuplicateOrderError(client_order_id)
        self.submissions += 1
        order = {
            "broker_order_id": f"bo-{len(self.orders)+1}", "client_order_id": client_order_id,
            "symbol": symbol, "side": side, "qty": float(qty), "status": "filled",
            "filled_qty": float(qty), "filled_avg_price": 100.0,
            "filled_at": "2026-08-21T19:47:00+00:00",
        }
        self.orders[client_order_id] = order
        return dict(order)

    def submit_buy_with_stop(self, symbol, qty, stop_price, client_order_id):
        assert isinstance(qty, int) and qty >= 1, "brackets need whole shares"
        assert stop_price > 0
        o = self._submit(symbol, "buy", qty, client_order_id)
        self.orders[client_order_id]["stop_price"] = stop_price
        o["stop_price"] = stop_price
        return o

    def submit_sell_market(self, symbol, qty, client_order_id):
        return self._submit(symbol, "sell", qty, client_order_id)

    def get_order_by_client_id(self, client_order_id):
        o = self.orders.get(client_order_id)
        return dict(o) if o else None


def _settings(config_file):
    return load_settings(config_file("paper"), env=PAPER_ENV)


def _seed_decision(conn, symbol, action, qty=20.0, close=100.0, mode="paper",
                   blocked_by=None, strategy_version="v0"):
    inputs = {"panel": {"close": close}, "account": {}, "kill_switch": False}
    cur = conn.execute(
        "INSERT INTO decisions (decision_utc, symbol, action, target_qty,"
        " target_notional, reason_code, inputs_json, blocked_by, strategy_version, mode)"
        " VALUES (datetime('now'),?,?,?,?,?,?,?,?,?)",
        (symbol, action, qty, qty * close, "test", json.dumps(inputs),
         blocked_by, strategy_version, mode))
    conn.commit()
    return cur.lastrowid


def test_execute_places_buy_with_deterministic_id_and_stop(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    did = _seed_decision(conn, "TSLA", "buy", qty=20.4, close=100.0)
    broker = FakeBroker()
    stats = execution.run(s, conn, broker)
    assert stats["submitted"] == 1
    assert f"v0-{did}" in broker.orders
    o = broker.orders[f"v0-{did}"]
    assert o["qty"] == 20                       # floored to whole shares
    assert o["stop_price"] == 92.0              # 8% below close
    row = conn.execute("SELECT * FROM orders").fetchone()
    assert row["client_order_id"] == f"v0-{did}" and row["stop_loss_price"] == 92.0


def test_execute_is_idempotent_across_runs(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    _seed_decision(conn, "TSLA", "buy")
    broker = FakeBroker()
    execution.run(s, conn, broker)
    stats2 = execution.run(s, conn, broker)
    assert stats2["submitted"] == 0 and broker.submissions == 1


def test_crash_restart_recovers_instead_of_double_ordering(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    did = _seed_decision(conn, "TSLA", "buy")
    broker = FakeBroker()
    # simulate: order reached broker but crash before local record
    broker._submit("TSLA", "buy", 20, f"v0-{did}")
    stats = execution.run(s, conn, broker)
    assert stats["recovered"] == 1 and stats["submitted"] == 0
    assert broker.submissions == 1  # no second order
    row = conn.execute("SELECT * FROM orders").fetchone()
    assert row["broker_order_id"] == "bo-1"


def test_blocked_and_dryrun_decisions_are_never_executed(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    _seed_decision(conn, "AAA", "buy", blocked_by="max_spread")
    _seed_decision(conn, "BBB", "buy", mode="dry-run")
    broker = FakeBroker()
    stats = execution.run(s, conn, broker)
    assert stats["submitted"] == 0 and broker.submissions == 0


def _seed_fill(conn, symbol, qty, side="buy"):
    # historical decision (yesterday) so today's execute query ignores it
    did = conn.execute(
        "INSERT INTO decisions (decision_utc, symbol, action, reason_code,"
        " inputs_json, strategy_version, mode) VALUES"
        " (datetime('now','-1 day'), ?, ?, 'hist', '{}', 'v0', 'paper')",
        (symbol, side)).lastrowid
    cur = conn.execute(
        "INSERT INTO orders (decision_id, client_order_id, submitted_utc, symbol,"
        " side, qty, order_type, status, raw_json) VALUES"
        " (?, ?, datetime('now'), ?, ?, ?, 'market', 'filled', '{}')",
        (did, f"hist-{symbol}-{side}", symbol, side, qty))
    conn.execute(
        "INSERT INTO fills (order_id, filled_utc, qty, price, raw_json)"
        " VALUES (?, datetime('now'), ?, 100.0, '{}')", (cur.lastrowid, qty))
    conn.commit()


def test_sell_uses_broker_qty_and_skips_missing_position(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    _seed_fill(conn, "HELD", 17.0)  # position has a fill history -> reconcile clean
    _seed_decision(conn, "HELD", "sell")
    _seed_decision(conn, "GONE", "sell")
    broker = FakeBroker(positions={
        "HELD": Position("HELD", 17.0, 1700.0, 95.0, 100.0)})
    stats = execution.run(s, conn, broker)
    assert stats["submitted"] == 1 and stats["skipped"] == 1
    sold = [o for o in broker.orders.values() if o["side"] == "sell"]
    assert sold[0]["qty"] == 17.0  # broker truth, not decision qty


def test_kill_switch_halts_execution(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    _seed_decision(conn, "TSLA", "buy")
    db.set_control(conn, "kill_switch", "1")
    stats = execution.run(s, conn, FakeBroker())
    assert stats["halted"] == 1 and stats["submitted"] == 0


def test_market_closed_no_orders(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    _seed_decision(conn, "TSLA", "buy")
    stats = execution.run(s, conn, FakeBroker(open_=False))
    assert stats["submitted"] == 0


def test_reconcile_clean_snapshots_positions_and_equity(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    _seed_decision(conn, "TSLA", "buy", qty=20)
    broker = FakeBroker()
    execution.run(s, conn, broker)                       # buy 20 filled
    broker._positions = {"TSLA": Position("TSLA", 20.0, 2000.0, 100.0, 0.0)}
    result = reconcile.run(s, conn, broker)
    assert result["mismatch"] == 0 and result["positions"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM positions_snapshot").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM equity_curve").fetchone()["c"] == 1


def test_reconcile_mismatch_halts_and_does_not_self_heal(config_file):
    s = _settings(config_file)
    conn = db.connect(s.db_path)
    _seed_decision(conn, "TSLA", "buy", qty=20)
    broker = FakeBroker()
    execution.run(s, conn, broker)                       # fills recorded: 20
    # corrupt the world: broker says 5 shares, we expect 20
    broker._positions = {"TSLA": Position("TSLA", 5.0, 500.0, 100.0, 0.0)}
    result = reconcile.run(s, conn, broker)
    assert result["mismatch"] == 1
    assert db.kill_switch_engaged(conn, s.kill_switch_file)
    # and execution refuses to run
    stats = execution.run(s, conn, broker)
    assert stats["halted"] == 1 and broker.submissions == 1
