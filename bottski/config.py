"""Configuration and secrets loading.

Secrets come only from the environment (.env); config.toml is non-secret.
Live mode is behind a double lock — see load_settings().
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_BASE_URL = "https://api.alpaca.markets"
LIVE_CONFIRM_ENV = "BOTTSKI_LIVE_CONFIRM"
LIVE_CONFIRM_VALUE = "I-UNDERSTAND-THIS-TRADES-REAL-MONEY"


class LiveModeLockError(RuntimeError):
    """Raised when live mode is requested without every lock in place."""


class CollectConfig(BaseModel):
    subreddits: list[str]
    posts_per_subreddit: int = 50
    comments_per_post: int = 200


class StrategyConfig(BaseModel):
    version: str = "v0"
    min_mentions: int = 3
    min_score: float = 0.25
    exit_score: float = -0.25
    max_new_positions_per_day: int = 3
    max_hold_days: int = 5
    extended_ret_5d: float = 0.10


class RiskLimits(BaseModel):
    max_position_pct: float
    max_gross_exposure_pct: float
    max_open_positions: int
    max_order_notional: float
    max_orders_per_day: int
    max_daily_loss_pct: float
    min_dollar_volume_20d: float
    max_spread_bps: float
    stop_loss_pct: float


class Settings(BaseModel):
    mode: Literal["paper", "live"]
    db_path: Path
    kill_switch_file: Path
    universe_file: Path
    collect: CollectConfig
    strategy: StrategyConfig
    risk: RiskLimits

    alpaca_key_id: str
    alpaca_secret_key: str
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = ""

    @property
    def base_url(self) -> str:
        return LIVE_BASE_URL if self.mode == "live" else PAPER_BASE_URL

    @property
    def is_paper(self) -> bool:
        return self.mode == "paper"

    def secret_values(self) -> list[str]:
        """Every value that must never appear in logs."""
        return [
            s
            for s in (
                self.alpaca_key_id,
                self.alpaca_secret_key,
                self.reddit_client_secret,
            )
            if s
        ]


def assert_url_matches_mode(url: str, mode: str) -> None:
    """Final guard before any trading client is constructed."""
    expected = LIVE_BASE_URL if mode == "live" else PAPER_BASE_URL
    if url != expected:
        raise LiveModeLockError(
            f"base URL {url!r} does not match mode {mode!r} (expected {expected!r})"
        )


def load_settings(
    config_path: str | Path = "config.toml", env: dict[str, str] | None = None
) -> Settings:
    """Load config + secrets. `env` overrides os.environ (for tests).

    Live-mode locks, all required simultaneously:
      1. mode = "live" in config.toml
      2. BOTTSKI_LIVE_CONFIRM env set to the exact confirmation string
      3. dedicated ALPACA_LIVE_* keys present (paper keys can never reach the live URL)
    """
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    if env is None:
        load_dotenv()
        env = dict(os.environ)

    mode = raw.get("mode", "paper")
    if mode == "live":
        if env.get(LIVE_CONFIRM_ENV) != LIVE_CONFIRM_VALUE:
            raise LiveModeLockError(
                f"mode=live requires env {LIVE_CONFIRM_ENV}={LIVE_CONFIRM_VALUE!r}"
            )
        key_id = env.get("ALPACA_LIVE_KEY_ID", "")
        secret = env.get("ALPACA_LIVE_SECRET_KEY", "")
        if not key_id or not secret:
            raise LiveModeLockError(
                "mode=live requires ALPACA_LIVE_KEY_ID and ALPACA_LIVE_SECRET_KEY"
            )
    else:
        key_id = env.get("ALPACA_PAPER_KEY_ID", "")
        secret = env.get("ALPACA_PAPER_SECRET_KEY", "")

    return Settings(
        mode=mode,
        db_path=Path(raw["db_path"]),
        kill_switch_file=Path(raw["kill_switch_file"]),
        universe_file=Path(raw["universe_file"]),
        collect=CollectConfig(**raw["collect"]),
        strategy=StrategyConfig(**raw["strategy"]),
        risk=RiskLimits(**raw["risk"]),
        alpaca_key_id=key_id,
        alpaca_secret_key=secret,
        reddit_client_id=env.get("REDDIT_CLIENT_ID", ""),
        reddit_client_secret=env.get("REDDIT_CLIENT_SECRET", ""),
        reddit_user_agent=env.get("REDDIT_USER_AGENT", ""),
    )
