"""Alpaca market data via raw REST (IEX feed). Injectable fetchers for tests."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from datetime import date

from bottski.config import Settings

logger = logging.getLogger("bottski.market_data")

DATA_URL = "https://data.alpaca.markets/v2/stocks"
CHUNK = 50


def build_fetcher(settings: Settings):
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_key_id,
        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
    }

    def fetch(path: str, params: dict) -> dict:
        url = f"{DATA_URL}/{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())

    return fetch


def daily_bars(symbols: list[str], start: date, end: date, fetch) -> dict[str, list[dict]]:
    """Daily bars per symbol, ascending by date. Symbols chunked; paginated."""
    out: dict[str, list[dict]] = {}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i : i + CHUNK]
        token = None
        while True:
            params = {
                "symbols": ",".join(chunk),
                "timeframe": "1Day",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": 10000,
                "adjustment": "split",
                "feed": "iex",
            }
            if token:
                params["page_token"] = token
            data = fetch("bars", params)
            for sym, bars in (data.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            token = data.get("next_page_token")
            if not token:
                break
    for bars in out.values():
        bars.sort(key=lambda b: b["t"])
    return out


def latest_quotes(symbols: list[str], fetch) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i : i + CHUNK]
        data = fetch("quotes/latest", {"symbols": ",".join(chunk), "feed": "iex"})
        out.update(data.get("quotes") or {})
    return out
