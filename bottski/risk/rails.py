"""Hard risk rails. Every proposed order passes through check() before it can
become an executable decision; the first violated rail names the block.

Pure functions over explicit state — no I/O, no hidden config — so every rail
is unit-testable by constructing a violating candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bottski.config import RiskLimits


@dataclass
class AccountState:
    equity: float
    cash: float
    positions: dict[str, float] = field(default_factory=dict)  # symbol -> market value
    day_start_equity: float | None = None

    @property
    def gross_exposure(self) -> float:
        return sum(abs(v) for v in self.positions.values())


@dataclass
class Candidate:
    symbol: str
    side: str                 # 'buy' | 'sell'
    notional: float
    dollar_volume_20d: float | None
    spread_bps: float | None
    is_tradable: bool = True
    is_halted: bool = False


def check(
    c: Candidate,
    account: AccountState,
    limits: RiskLimits,
    orders_today: int,
    kill_switch: bool,
) -> str | None:
    """Return the name of the first violated rail, or None if clear.
    Sells are exempt from entry-quality rails (liquidity/extension) but never
    from the kill switch or halt status — you must always be able to exit,
    unless trading is globally halted."""
    if kill_switch:
        return "kill_switch"
    if c.is_halted:
        return "halted"
    if not c.is_tradable:
        return "not_tradable"
    if orders_today >= limits.max_orders_per_day:
        return "max_orders_per_day"

    if c.side == "sell":
        return None

    if account.day_start_equity:
        loss_pct = (account.day_start_equity - account.equity) / account.day_start_equity * 100
        if loss_pct >= limits.max_daily_loss_pct:
            return "max_daily_loss"
    if c.notional > limits.max_order_notional:
        return "max_order_notional"
    if account.equity > 0 and c.notional / account.equity * 100 > limits.max_position_pct:
        return "max_position_pct"
    if account.equity > 0 and (
        (account.gross_exposure + c.notional) / account.equity * 100
        > limits.max_gross_exposure_pct
    ):
        return "max_gross_exposure"
    if c.symbol not in account.positions and len(account.positions) >= limits.max_open_positions:
        return "max_open_positions"
    if c.dollar_volume_20d is not None and c.dollar_volume_20d < limits.min_dollar_volume_20d:
        return "min_dollar_volume"
    if c.dollar_volume_20d is None:
        return "min_dollar_volume"  # unknown liquidity = untradeable, not a pass
    if c.spread_bps is not None and c.spread_bps > limits.max_spread_bps:
        return "max_spread"
    return None
