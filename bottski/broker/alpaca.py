"""Alpaca trading API access behind a thin wrapper so tests can fake it.

Every construction path runs assert_url_matches_mode; order submission adds
whole-share and client_order_id discipline. Paper vs live is decided ONLY by
Settings (mode + live locks), never here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from bottski.config import Settings, assert_url_matches_mode
from bottski.risk.rails import AccountState

logger = logging.getLogger("bottski.broker")


@dataclass
class Position:
    symbol: str
    qty: float
    market_value: float
    avg_entry_price: float
    unrealized_pl: float


class DuplicateOrderError(Exception):
    """client_order_id already used — the order exists at the broker."""


class Broker:
    """Real Alpaca implementation. Tests provide an object with the same
    methods; execution/reconcile code depends only on this surface."""

    def __init__(self, settings: Settings):
        from alpaca.trading.client import TradingClient  # lazy

        assert_url_matches_mode(settings.base_url, settings.mode)
        self._settings = settings
        self._client = TradingClient(
            settings.alpaca_key_id, settings.alpaca_secret_key, paper=settings.is_paper)

    def account_state(self) -> AccountState:
        acct = self._client.get_account()
        return AccountState(
            equity=float(acct.equity),
            cash=float(acct.cash),
            positions={p.symbol: float(p.market_value) for p in self._client.get_all_positions()},
            day_start_equity=float(acct.last_equity) if acct.last_equity else None,
        )

    def positions(self) -> dict[str, Position]:
        return {
            p.symbol: Position(
                symbol=p.symbol, qty=float(p.qty), market_value=float(p.market_value),
                avg_entry_price=float(p.avg_entry_price),
                unrealized_pl=float(p.unrealized_pl or 0))
            for p in self._client.get_all_positions()
        }

    def market_open(self) -> bool:
        return bool(self._client.get_clock().is_open)

    def submit_buy_with_stop(self, symbol: str, qty: int, stop_price: float,
                             client_order_id: str) -> dict:
        from alpaca.common.exceptions import APIError
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest, StopLossRequest

        req = MarketOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
            order_class=OrderClass.OTO,
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
            client_order_id=client_order_id,
        )
        try:
            order = self._client.submit_order(req)
        except APIError as e:
            if "client_order_id must be unique" in str(e) or getattr(e, "code", None) == 42210000:
                raise DuplicateOrderError(client_order_id) from e
            raise
        return _order_dict(order)

    def submit_sell_market(self, symbol: str, qty: float, client_order_id: str) -> dict:
        from alpaca.common.exceptions import APIError
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        req = MarketOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        try:
            order = self._client.submit_order(req)
        except APIError as e:
            if "client_order_id must be unique" in str(e) or getattr(e, "code", None) == 42210000:
                raise DuplicateOrderError(client_order_id) from e
            raise
        return _order_dict(order)

    def get_order_by_client_id(self, client_order_id: str) -> dict | None:
        from alpaca.common.exceptions import APIError

        try:
            order = self._client.get_order_by_client_id(client_order_id)
        except APIError:
            return None
        return _order_dict(order)


def _order_dict(order) -> dict:
    return {
        "broker_order_id": str(order.id),
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "side": str(order.side.value if hasattr(order.side, "value") else order.side),
        "qty": float(order.qty) if order.qty else None,
        "status": str(order.status.value if hasattr(order.status, "value") else order.status),
        "filled_qty": float(order.filled_qty) if order.filled_qty else 0.0,
        "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
        "filled_at": order.filled_at.isoformat() if getattr(order, "filled_at", None) else None,
    }


def get_account_state(settings: Settings) -> AccountState:
    return Broker(settings).account_state()
