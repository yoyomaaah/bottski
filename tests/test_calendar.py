from datetime import date

from bottski.market_calendar import ET, is_trading_day, previous_trading_day, sentiment_window


def test_weekend_is_not_trading_day():
    assert not is_trading_day(date(2026, 8, 22))  # Saturday
    assert not is_trading_day(date(2026, 8, 23))  # Sunday
    assert is_trading_day(date(2026, 8, 21))      # Friday


def test_holiday_is_not_trading_day():
    assert not is_trading_day(date(2026, 7, 3))   # July 4th observed (Sat 4th -> Fri 3rd)
    assert not is_trading_day(date(2026, 12, 25))


def test_sentiment_window_spans_prev_close_to_1540():
    start, end = sentiment_window(date(2026, 8, 24))  # Monday
    assert start.date() == date(2026, 8, 21)          # previous Friday
    assert (start.hour, start.minute) == (16, 0)
    assert (end.hour, end.minute) == (15, 40)
    assert start.tzinfo is ET and end.tzinfo is ET
