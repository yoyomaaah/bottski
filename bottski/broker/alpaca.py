"""Alpaca trading API access. In M5 this is READ-ONLY (account + positions);
order placement arrives in M6 behind the same URL/mode assertions."""

from __future__ import annotations

import logging

from bottski.config import Settings, assert_url_matches_mode
from bottski.risk.rails import AccountState

logger = logging.getLogger("bottski.broker")


def get_account_state(settings: Settings) -> AccountState:
    from alpaca.trading.client import TradingClient  # lazy

    assert_url_matches_mode(settings.base_url, settings.mode)
    client = TradingClient(
        settings.alpaca_key_id, settings.alpaca_secret_key, paper=settings.is_paper)
    acct = client.get_account()
    positions = {p.symbol: float(p.market_value) for p in client.get_all_positions()}
    return AccountState(
        equity=float(acct.equity),
        cash=float(acct.cash),
        positions=positions,
        day_start_equity=float(acct.last_equity) if acct.last_equity else None,
    )
