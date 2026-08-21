"""Reconciliation: the broker is the truth; the bot must agree with it.

1. Sync order statuses/fills from the broker for our known orders.
2. Compute expected position qty per symbol from recorded fills.
3. Compare with the broker's actual positions.
On mismatch beyond tolerance: ENGAGE THE KILL SWITCH AND HALT. Never
self-heal — a silent correction hides exactly the bug you need to see.
Also snapshots positions and the equity curve.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from bottski.config import Settings
from bottski.store import db

logger = logging.getLogger("bottski.reconcile")

QTY_TOLERANCE = 1e-6
TERMINAL = {"filled", "canceled", "expired", "rejected", "skipped"}


def _sync_orders(conn: sqlite3.Connection, broker) -> int:
    # non-terminal orders, plus filled orders whose fill row is missing
    # (an order can return already-filled at submission time)
    open_orders = conn.execute(
        "SELECT * FROM orders WHERE status NOT IN ({}) OR (status = 'filled'"
        " AND id NOT IN (SELECT order_id FROM fills))".format(
            ",".join(f"'{s}'" for s in TERMINAL))
    ).fetchall()
    synced = 0
    for o in open_orders:
        remote = broker.get_order_by_client_id(o["client_order_id"])
        if not remote:
            continue
        conn.execute(
            "UPDATE orders SET status = ?, broker_order_id = ?, raw_json = ? WHERE id = ?",
            (remote["status"], remote["broker_order_id"], json.dumps(remote), o["id"]))
        if remote["status"] == "filled" and remote.get("filled_qty"):
            existing = conn.execute(
                "SELECT 1 FROM fills WHERE order_id = ?", (o["id"],)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO fills (order_id, filled_utc, qty, price, raw_json)"
                    " VALUES (?,?,?,?,?)",
                    (o["id"], remote.get("filled_at") or db.utcnow(),
                     remote["filled_qty"], remote.get("filled_avg_price") or 0,
                     json.dumps(remote)))
        synced += 1
    conn.commit()
    return synced


def expected_positions(conn: sqlite3.Connection) -> dict[str, float]:
    rows = conn.execute(
        """
        SELECT o.symbol, SUM(CASE WHEN o.side='buy' THEN f.qty ELSE -f.qty END) qty
        FROM fills f JOIN orders o ON o.id = f.order_id GROUP BY o.symbol
        """
    ).fetchall()
    return {r["symbol"]: r["qty"] for r in rows if abs(r["qty"]) > QTY_TOLERANCE}


def run(settings: Settings, conn: sqlite3.Connection, broker) -> dict:
    _sync_orders(conn, broker)
    expected = expected_positions(conn)
    actual = broker.positions()

    mismatches = []
    for sym in sorted(set(expected) | set(actual)):
        exp = expected.get(sym, 0.0)
        act = actual[sym].qty if sym in actual else 0.0
        if abs(exp - act) > QTY_TOLERANCE:
            mismatches.append(f"{sym}: expected {exp}, broker has {act}")

    if mismatches:
        logger.error("POSITION MISMATCH — engaging kill switch. %s", "; ".join(mismatches))
        db.set_control(conn, "kill_switch", "1")
        settings.kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
        settings.kill_switch_file.touch()
        return {"mismatch": len(mismatches), "detail": mismatches}

    now = db.utcnow()
    for p in actual.values():
        conn.execute(
            "INSERT INTO positions_snapshot (snapshot_utc, symbol, qty,"
            " avg_entry_price, market_value, unrealized_pl) VALUES (?,?,?,?,?,?)",
            (now, p.symbol, p.qty, p.avg_entry_price, p.market_value, p.unrealized_pl))
    acct = broker.account_state()
    conn.execute(
        "INSERT OR IGNORE INTO equity_curve (snapshot_utc, equity, cash,"
        " gross_exposure) VALUES (?,?,?,?)",
        (now, acct.equity, acct.cash, acct.gross_exposure))
    conn.commit()
    logger.info("reconcile clean: %d positions, equity %.2f", len(actual), acct.equity)
    return {"mismatch": 0, "positions": len(actual), "equity": acct.equity}
