"""Thin wrapper around pandas-market-calendars for NYSE trading days.

All bot logic asks these functions; nothing else imports the calendar lib.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

ET = ZoneInfo("America/New_York")


@lru_cache(maxsize=1)
def _nyse():
    return mcal.get_calendar("NYSE")


def _schedule(start: date, end: date):
    return _nyse().schedule(start_date=start.isoformat(), end_date=end.isoformat())


def is_trading_day(d: date) -> bool:
    return not _schedule(d, d).empty


def previous_trading_day(d: date) -> date:
    sched = _schedule(d - timedelta(days=10), d - timedelta(days=1))
    return sched.index[-1].date()


def next_trading_day(d: date) -> date:
    sched = _schedule(d + timedelta(days=1), d + timedelta(days=10))
    return sched.index[0].date()


def sentiment_window(obs_date: date) -> tuple[datetime, datetime]:
    """Accumulation window: previous trading day 16:00 ET -> obs_date 15:40 ET."""
    prev = previous_trading_day(obs_date)
    start = datetime(prev.year, prev.month, prev.day, 16, 0, tzinfo=ET)
    end = datetime(obs_date.year, obs_date.month, obs_date.day, 15, 40, tzinfo=ET)
    return start, end
