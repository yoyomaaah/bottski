"""Strategy v0: transparent rule-based sentiment entries with price sanity.

Entry: mean sentiment >= min_score on >= min_mentions scored mentions, and the
name is NOT already extended (5d return below extended_ret_5d — "don't buy
into an already-extended move"). Rank by conviction, take at most
max_new_positions_per_day.

Exit: sentiment flips bearish (<= exit_score on >= min_mentions), or the
position has been held max_hold_days trading days (time stop). The hard
stop-loss lives at the broker as a bracket order (M6), not here.

Deterministic by construction: pure function of (panel rows, positions,
config); ties broken by symbol. Every emitted decision carries a reason_code
and the full input snapshot; risk rails run downstream in decide().
"""

from __future__ import annotations

from dataclasses import dataclass

from bottski.config import Settings


@dataclass
class Proposal:
    symbol: str
    action: str          # 'buy' | 'sell' | 'hold'
    reason_code: str
    target_notional: float | None = None
    target_qty: float | None = None


def propose(
    settings: Settings,
    panel_rows: list[dict],
    positions: dict[str, float],       # symbol -> market value
    position_age_days: dict[str, int], # symbol -> trading days held
    equity: float,
) -> list[Proposal]:
    cfg = settings.strategy
    risk = settings.risk
    by_symbol = {r["symbol"]: r for r in panel_rows}
    proposals: list[Proposal] = []

    # --- exits first: they free capacity and must never be crowded out ------
    for sym in sorted(positions):
        row = by_symbol.get(sym)
        age = position_age_days.get(sym, 0)
        bearish = (
            row is not None
            and row.get("n_mentions", 0) >= cfg.min_mentions
            and row.get("score_mean") is not None
            and row["score_mean"] <= cfg.exit_score
        )
        if bearish:
            proposals.append(Proposal(sym, "sell", "sentiment_flip_bearish"))
        elif age >= cfg.max_hold_days:
            proposals.append(Proposal(sym, "sell", "max_hold_days"))
        else:
            proposals.append(Proposal(sym, "hold", "position_within_rules"))

    # --- entries ------------------------------------------------------------
    sizing = min(risk.max_order_notional, equity * risk.max_position_pct / 100)
    candidates = []
    for r in panel_rows:
        sym = r["symbol"]
        if sym in positions:
            continue
        score = r.get("score_mean")
        if score is None or r.get("n_mentions", 0) < cfg.min_mentions:
            continue
        if score < cfg.min_score:
            proposals.append(Proposal(sym, "hold", "score_below_entry"))
            continue
        if r.get("close") is None:
            proposals.append(Proposal(sym, "hold", "no_price"))
            continue
        if r.get("ret_5d") is not None and r["ret_5d"] > cfg.extended_ret_5d:
            proposals.append(Proposal(sym, "hold", "extended_move"))
            continue
        candidates.append((score, sym, r))

    candidates.sort(key=lambda t: (-t[0], t[1]))
    for i, (score, sym, r) in enumerate(candidates):
        if i < cfg.max_new_positions_per_day:
            qty = round(sizing / r["close"], 4)
            proposals.append(Proposal(sym, "buy", "sentiment_entry",
                                      target_notional=round(qty * r["close"], 2),
                                      target_qty=qty))
        else:
            proposals.append(Proposal(sym, "hold", "ranked_below_daily_cap"))
    return proposals
