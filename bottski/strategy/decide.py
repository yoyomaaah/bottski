"""The decide job: panel + account state -> decisions written to the DB.

Never places orders. Records EVERY proposal — including holds and trades
blocked by a risk rail (with the rail named) — so the counterfactual is
reviewable. inputs_json snapshots exactly what the decision saw.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from bottski.config import Settings
from bottski.extract.tickers import Universe
from bottski.risk.blacklist import Blacklist
from bottski.risk.rails import AccountState, Candidate, check
from bottski.store import db
from bottski.strategy import core

logger = logging.getLogger("bottski.decide")


def run(
    settings: Settings,
    conn: sqlite3.Connection,
    obs_date: str,
    account: AccountState,
    position_age_days: dict[str, int] | None = None,
    mode: str = "dry-run",
) -> dict[str, int]:
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM observations WHERE obs_date = ?", (obs_date,))]
    if not rows:
        logger.warning("no panel rows for %s — did observe run?", obs_date)
        return {"error_no_panel": 1}

    kill = db.kill_switch_engaged(conn, settings.kill_switch_file)
    blacklist = Blacklist.load(settings.blacklist_file)
    try:
        sectors = Universe.load(settings.universe_file).sectors
    except FileNotFoundError:
        sectors = {}
    proposals = core.propose(
        settings, rows, account.positions, position_age_days or {}, account.equity)

    obs_ids = {r["symbol"]: r["id"] for r in rows}
    by_symbol = {r["symbol"]: r for r in rows}
    stats = {"buy": 0, "sell": 0, "hold": 0, "blocked": 0}
    orders_today = conn.execute(
        "SELECT COUNT(*) c FROM decisions WHERE decision_utc >= ? AND action != 'hold'"
        " AND blocked_by IS NULL AND mode = ?",
        (obs_date, mode),
    ).fetchone()["c"]

    for p in proposals:
        blocked_by = None
        if p.action in ("buy", "sell"):
            row = by_symbol.get(p.symbol, {})
            cand = Candidate(
                symbol=p.symbol,
                side=p.action,
                notional=p.target_notional or 0.0,
                dollar_volume_20d=row.get("dollar_volume_20d"),
                spread_bps=row.get("spread_bps"),
                is_tradable=bool(row.get("is_tradable", True)),
                is_halted=bool(row.get("is_halted", False)),
                blacklisted=blacklist.matches(p.symbol, sectors.get(p.symbol)),
            )
            blocked_by = check(cand, account, settings.risk, orders_today, kill)
            if blocked_by is None:
                orders_today += 1
            else:
                stats["blocked"] += 1
        stats[p.action] = stats.get(p.action, 0) + (0 if blocked_by else 1)

        inputs = {
            "panel": {k: by_symbol.get(p.symbol, {}).get(k) for k in (
                "n_mentions", "score_mean", "score_std", "n_news", "news_score_mean",
                "ext_mentions", "ext_sentiment_score", "close", "ret_1d", "ret_5d",
                "dist_from_20d_high", "dollar_volume_20d", "spread_bps")},
            "account": {"equity": account.equity,
                        "gross_exposure": account.gross_exposure,
                        "n_positions": len(account.positions),
                        "held": p.symbol in account.positions},
            "kill_switch": kill,
        }
        conn.execute(
            "INSERT INTO decisions (decision_utc, obs_id, symbol, action, target_qty,"
            " target_notional, reason_code, inputs_json, blocked_by, strategy_version,"
            " mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (db.utcnow(), obs_ids.get(p.symbol), p.symbol, p.action, p.target_qty,
             p.target_notional, p.reason_code, json.dumps(inputs, sort_keys=True),
             blocked_by, settings.strategy.version, mode),
        )
    conn.commit()
    logger.info("decide %s (%s): %s", obs_date, mode, stats)
    return stats
