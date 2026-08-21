"""The observation panel: one row per (obs_date, symbol) for every universe
symbol — zero-mention symbols included (the control group) — plus tickers
promoted by mention volume.

Rules that keep the panel honest:
- Sentiment features come only from documents CREATED inside the accumulation
  window (prev trading day 16:00 ET -> obs day 15:40 ET).
- External provider features use the latest snapshot AT OR BEFORE window end.
- Forward-return columns are never written here; backfill_returns owns them,
  and the upsert never touches them.
"""

from __future__ import annotations

import logging
import sqlite3
import statistics
from datetime import date, datetime, timedelta, timezone

from bottski.config import Settings
from bottski.broker import market_data
from bottski.extract.tickers import EXTRACTOR_VERSION, Universe
from bottski.market_calendar import is_trading_day, sentiment_window
from bottski.score.vader import SCORER_VERSION
from bottski.store import db

logger = logging.getLogger("bottski.panel")

PROMOTION_MIN_MENTIONS = 5
UNIVERSE_VERSION = "u2"
BARS_LOOKBACK_DAYS = 45  # calendar days; yields ~30 trading bars


def _sentiment_features(conn, start_utc, end_utc) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT s.symbol, s.compound, d.source, d.author_hash
        FROM document_sentiment s
        JOIN raw_documents d ON d.id = s.document_id
        WHERE s.scorer_version = ? AND d.created_utc >= ? AND d.created_utc < ?
        """,
        (SCORER_VERSION, start_utc, end_utc),
    ).fetchall()
    feats: dict[str, dict] = {}
    for r in rows:
        f = feats.setdefault(r["symbol"], {"scores": [], "authors": set(), "news_scores": []})
        f["scores"].append(r["compound"])
        if r["author_hash"]:
            f["authors"].add(r["author_hash"])
        if r["source"] == "news":
            f["news_scores"].append(r["compound"])
    return feats


def _external_features(conn, end_utc) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for provider, cols in (("apewisdom", ("mentions", "rank")), ("tradestie", ("sentiment_score",))):
        latest = conn.execute(
            "SELECT MAX(fetched_utc) m FROM external_sentiment WHERE provider = ? AND fetched_utc <= ?",
            (provider, end_utc),
        ).fetchone()["m"]
        if not latest:
            continue
        for r in conn.execute(
            "SELECT * FROM external_sentiment WHERE provider = ? AND fetched_utc = ?",
            (provider, latest),
        ):
            d = out.setdefault(r["symbol"], {})
            if provider == "apewisdom":
                d["ext_mentions"] = r["mentions"]
                d["ext_rank"] = r["rank"]
            else:
                d["ext_sentiment_score"] = r["sentiment_score"]
    return out


def _price_features(bars: list[dict]) -> dict:
    if not bars:
        return {}
    closes = [b["c"] for b in bars]
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    vols = [b["v"] for b in bars]
    out = {"close": closes[-1]}
    if len(closes) >= 2:
        out["ret_1d"] = closes[-1] / closes[-2] - 1
    if len(closes) >= 6:
        out["ret_5d"] = closes[-1] / closes[-6] - 1
    if len(closes) >= 21:
        out["ret_20d"] = closes[-1] / closes[-21] - 1
        out["dist_from_20d_high"] = closes[-1] / max(highs[-20:]) - 1
        out["dollar_volume_20d"] = statistics.fmean(
            c * v for c, v in zip(closes[-20:], vols[-20:])
        )
    if len(closes) >= 15:
        trs = []
        for i in range(len(bars) - 14, len(bars)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        out["atr14"] = statistics.fmean(trs)
    return out


def _mention_velocity(conn, symbol: str, obs_date: str, n_mentions: int) -> float | None:
    rows = conn.execute(
        "SELECT n_mentions FROM observations WHERE symbol = ? AND obs_date < ?"
        " ORDER BY obs_date DESC LIMIT 20",
        (symbol, obs_date),
    ).fetchall()
    if len(rows) < 5:
        return None
    avg = statistics.fmean(r["n_mentions"] for r in rows)
    if avg <= 0:
        return None
    return n_mentions / avg


def build(settings: Settings, conn: sqlite3.Connection, obs_date: date,
          bars_fetch=None, quotes_fetch=None) -> dict[str, int]:
    if not is_trading_day(obs_date):
        logger.info("%s is not a trading day — skipping observe", obs_date)
        return {"skipped": 1}

    start_et, end_et = sentiment_window(obs_date)
    start_utc = start_et.astimezone(timezone.utc).isoformat(timespec="seconds")
    end_utc = end_et.astimezone(timezone.utc).isoformat(timespec="seconds")

    universe = Universe.load(settings.universe_file)
    feats = _sentiment_features(conn, start_utc, end_utc)
    ext = _external_features(conn, end_utc)

    promoted = {
        sym for sym, f in feats.items()
        if sym not in universe.symbols and len(f["scores"]) >= PROMOTION_MIN_MENTIONS
    }
    symbols = sorted(universe.symbols | promoted)

    fetch = bars_fetch
    if fetch is None:
        fetch = market_data.build_fetcher(settings)
    bars = market_data.daily_bars(
        symbols, obs_date - timedelta(days=BARS_LOOKBACK_DAYS), obs_date, fetch)
    quotes = {}
    if quotes_fetch is not None:
        quotes = quotes_fetch(symbols)
    elif obs_date == datetime.now(timezone.utc).date():
        try:
            quotes = market_data.latest_quotes(symbols, fetch)
        except Exception:
            logger.exception("quote fetch failed — spread_bps will be NULL")

    obs_ts = db.utcnow()
    stats = {"rows": 0, "promoted": len(promoted), "no_price": 0}
    for sym in symbols:
        f = feats.get(sym, {"scores": [], "authors": set(), "news_scores": []})
        scores = f["scores"]
        price = _price_features(bars.get(sym, []))
        if not price:
            stats["no_price"] += 1
        q = quotes.get(sym) or {}
        bid, ask = q.get("bp"), q.get("ap")
        spread_bps = None
        if bid and ask and ask > bid > 0:
            spread_bps = (ask - bid) / ((ask + bid) / 2) * 1e4
        e = ext.get(sym, {})
        row = {
            "obs_date": obs_date.isoformat(),
            "obs_ts_utc": obs_ts,
            "symbol": sym,
            "n_mentions": len(scores),
            "n_unique_authors": len(f["authors"]),
            "score_mean": statistics.fmean(scores) if scores else None,
            "score_sum": sum(scores) if scores else None,
            "score_std": statistics.pstdev(scores) if len(scores) > 1 else None,
            "mention_velocity": _mention_velocity(conn, sym, obs_date.isoformat(), len(scores)),
            "n_news": len(f["news_scores"]),
            "news_score_mean": statistics.fmean(f["news_scores"]) if f["news_scores"] else None,
            "close": price.get("close"),
            "ret_1d": price.get("ret_1d"),
            "ret_5d": price.get("ret_5d"),
            "ret_20d": price.get("ret_20d"),
            "atr14": price.get("atr14"),
            "dist_from_20d_high": price.get("dist_from_20d_high"),
            "dollar_volume_20d": price.get("dollar_volume_20d"),
            "spread_bps": spread_bps,
            "is_halted": 0,
            "is_tradable": 1,
            "extractor_version": EXTRACTOR_VERSION,
            "scorer_version": SCORER_VERSION,
            "universe_version": UNIVERSE_VERSION,
            "ext_mentions": e.get("ext_mentions"),
            "ext_rank": e.get("ext_rank"),
            "ext_sentiment_score": e.get("ext_sentiment_score"),
        }
        cols = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        updates = ", ".join(
            f"{c} = excluded.{c}" for c in row if c not in ("obs_date", "symbol")
        )
        # ON CONFLICT update lists ONLY feature columns — fwd_ret_* and
        # fwd_filled_utc are never in `row`, so re-observing can't cause lookahead.
        conn.execute(
            f"INSERT INTO observations ({cols}) VALUES ({placeholders})"
            f" ON CONFLICT (obs_date, symbol) DO UPDATE SET {updates}",
            list(row.values()),
        )
        stats["rows"] += 1
    conn.commit()
    logger.info("panel built for %s: %s", obs_date, stats)
    return stats
