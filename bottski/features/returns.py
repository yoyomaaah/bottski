"""Forward-return backfill for the observation panel.

Runs strictly after the fact: for each panel row missing forward returns,
compute close-to-close returns from official daily closes. A horizon is filled
only when enough LATER trading days exist — the anchor close is obs_date's
official close and every forward close comes from bars dated after obs_date,
so a row can never contain information from before it was fillable.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bottski.config import Settings
from bottski.broker import market_data
from bottski.store import db

logger = logging.getLogger("bottski.returns")

HORIZONS = (1, 3, 5, 10)
ET = ZoneInfo("America/New_York")


def strip_incomplete_bars(bars: dict[str, list[dict]], now_et: datetime) -> None:
    """Drop today's bar unless the session has closed — an intraday 'daily'
    bar is not a final close and must never enter a forward return."""
    closed = now_et.hour > 16 or (now_et.hour == 16 and now_et.minute >= 5)
    if closed:
        return
    today = now_et.date().isoformat()
    for sym, sym_bars in bars.items():
        bars[sym] = [b for b in sym_bars if b["t"][:10] != today]


def run(settings: Settings, conn: sqlite3.Connection, bars_fetch=None) -> dict[str, int]:
    pending = conn.execute(
        "SELECT id, obs_date, symbol FROM observations"
        " WHERE fwd_ret_10d IS NULL ORDER BY symbol, obs_date"
    ).fetchall()
    if not pending:
        return {"updated": 0, "complete": 0}

    fetch = bars_fetch or market_data.build_fetcher(settings)
    earliest = min(date.fromisoformat(r["obs_date"]) for r in pending)
    symbols = sorted({r["symbol"] for r in pending})
    bars = market_data.daily_bars(
        symbols, earliest - timedelta(days=3), date.today(), fetch)
    strip_incomplete_bars(bars, datetime.now(ET))

    stats = {"updated": 0, "complete": 0}
    for r in pending:
        sym_bars = bars.get(r["symbol"], [])
        dates = [b["t"][:10] for b in sym_bars]
        if r["obs_date"] not in dates:
            continue
        i = dates.index(r["obs_date"])
        anchor = sym_bars[i]["c"]
        if not anchor:
            continue
        updates: dict[str, float] = {}
        for h in HORIZONS:
            if i + h < len(sym_bars):
                updates[f"fwd_ret_{h}d"] = sym_bars[i + h]["c"] / anchor - 1
        if not updates:
            continue
        sets = ", ".join(f"{c} = ?" for c in updates)
        vals = list(updates.values())
        complete = f"fwd_ret_{max(HORIZONS)}d" in updates
        if complete:
            sets += ", fwd_filled_utc = ?"
            vals.append(db.utcnow())
            stats["complete"] += 1
        conn.execute(f"UPDATE observations SET {sets} WHERE id = ?", vals + [r["id"]])
        stats["updated"] += 1
    conn.commit()
    logger.info("returns backfilled: %s", stats)
    return stats
