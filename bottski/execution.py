"""The execute job: turn today's unblocked decisions into paper orders.

Safety properties:
- Kill switch checked immediately before ANY submission.
- Reconcile runs first; a position mismatch halts execution entirely.
- client_order_id = f"{strategy_version}-{decision_id}" — deterministic, so a
  crash-restart resubmission is rejected by the broker and recovered by
  fetching the existing order instead of double-ordering.
- Every buy carries a server-side stop (OTO bracket leg) at stop_loss_pct
  below the decision close. A stop in bot memory is not a stop.
- Whole shares only (Alpaca requires it for bracket orders); qty floors down,
  zero-share results are recorded as skipped, not silently dropped.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from bottski.config import Settings
from bottski import reconcile as reconcile_mod
from bottski.broker.alpaca import DuplicateOrderError
from bottski.store import db

logger = logging.getLogger("bottski.execute")


def _record_order(conn, decision_id, client_order_id, order: dict | None,
                  symbol, side, qty, stop_price=None, status="skipped", note=""):
    conn.execute(
        "INSERT OR IGNORE INTO orders (decision_id, client_order_id, broker_order_id,"
        " submitted_utc, symbol, side, qty, order_type, stop_loss_price, status,"
        " raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (decision_id, client_order_id,
         order["broker_order_id"] if order else None,
         db.utcnow(), symbol, side, qty, "market",
         stop_price, order["status"] if order else status,
         json.dumps(order or {"note": note})),
    )


def run(settings: Settings, conn: sqlite3.Connection, broker) -> dict[str, int]:
    stats = {"submitted": 0, "recovered": 0, "skipped": 0, "halted": 0}

    rec = reconcile_mod.run(settings, conn, broker)
    if rec.get("mismatch"):
        logger.error("reconcile mismatch — execution halted")
        stats["halted"] = 1
        return stats

    if db.kill_switch_engaged(conn, settings.kill_switch_file):
        logger.warning("kill switch engaged — no orders will be placed")
        stats["halted"] = 1
        return stats

    if not broker.market_open():
        logger.info("market closed — nothing to execute")
        return stats

    decisions = conn.execute(
        """
        SELECT d.* FROM decisions d
        LEFT JOIN orders o ON o.decision_id = d.id
        WHERE date(d.decision_utc) = date('now')
          AND d.action IN ('buy', 'sell') AND d.blocked_by IS NULL
          AND d.mode = ? AND o.id IS NULL
        ORDER BY d.action DESC, d.id  -- sells first
        """,
        (settings.mode,),
    ).fetchall()

    positions = broker.positions()
    for d in decisions:
        # the last line of defense runs per order, not per run
        if db.kill_switch_engaged(conn, settings.kill_switch_file):
            logger.warning("kill switch engaged mid-run — stopping")
            stats["halted"] = 1
            break
        client_order_id = f"{d['strategy_version']}-{d['id']}"
        try:
            if d["action"] == "sell":
                pos = positions.get(d["symbol"])
                if not pos or pos.qty <= 0:
                    _record_order(conn, d["id"], client_order_id, None, d["symbol"],
                                  "sell", 0, note="no position at broker")
                    stats["skipped"] += 1
                    continue
                order = broker.submit_sell_market(d["symbol"], pos.qty, client_order_id)
                _record_order(conn, d["id"], client_order_id, order, d["symbol"],
                              "sell", pos.qty)
            else:
                qty = int(d["target_qty"] or 0)
                inputs = json.loads(d["inputs_json"])
                close = inputs["panel"].get("close")
                if qty < 1 or not close:
                    _record_order(conn, d["id"], client_order_id, None, d["symbol"],
                                  "buy", qty, note="sub-share qty or no price")
                    stats["skipped"] += 1
                    continue
                stop_price = close * (1 - settings.risk.stop_loss_pct / 100)
                order = broker.submit_buy_with_stop(
                    d["symbol"], qty, stop_price, client_order_id)
                _record_order(conn, d["id"], client_order_id, order, d["symbol"],
                              "buy", qty, stop_price=round(stop_price, 2))
            stats["submitted"] += 1
        except DuplicateOrderError:
            # crash-restart path: order already exists at broker; adopt it
            order = broker.get_order_by_client_id(client_order_id)
            if order:
                _record_order(conn, d["id"], client_order_id, order, d["symbol"],
                              d["action"], order.get("qty") or 0)
                stats["recovered"] += 1
            else:
                logger.error("duplicate id %s but order not found", client_order_id)
        except Exception:
            logger.exception("order failed for decision %s (%s)", d["id"], d["symbol"])
        conn.commit()
    conn.commit()
    logger.info("execute done: %s", stats)
    return stats
