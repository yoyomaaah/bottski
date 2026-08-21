"""The most important tests in the repo: live mode must refuse to arm."""

import pytest

from bottski.config import (
    LIVE_BASE_URL,
    LIVE_CONFIRM_ENV,
    LIVE_CONFIRM_VALUE,
    PAPER_BASE_URL,
    LiveModeLockError,
    assert_url_matches_mode,
    load_settings,
)

PAPER_ENV = {"ALPACA_PAPER_KEY_ID": "pk_paper", "ALPACA_PAPER_SECRET_KEY": "sk_paper"}
LIVE_KEYS = {"ALPACA_LIVE_KEY_ID": "pk_live", "ALPACA_LIVE_SECRET_KEY": "sk_live"}


def test_paper_mode_loads_and_uses_paper_url(config_file):
    s = load_settings(config_file("paper"), env=PAPER_ENV)
    assert s.is_paper
    assert s.base_url == PAPER_BASE_URL
    assert s.alpaca_key_id == "pk_paper"


def test_live_refuses_without_confirm_env(config_file):
    with pytest.raises(LiveModeLockError, match=LIVE_CONFIRM_ENV):
        load_settings(config_file("live"), env={**PAPER_ENV, **LIVE_KEYS})


def test_live_refuses_with_wrong_confirm_value(config_file):
    env = {**PAPER_ENV, **LIVE_KEYS, LIVE_CONFIRM_ENV: "yes"}
    with pytest.raises(LiveModeLockError):
        load_settings(config_file("live"), env=env)


def test_live_refuses_without_dedicated_live_keys(config_file):
    env = {**PAPER_ENV, LIVE_CONFIRM_ENV: LIVE_CONFIRM_VALUE}
    with pytest.raises(LiveModeLockError, match="ALPACA_LIVE"):
        load_settings(config_file("live"), env=env)


def test_live_arms_only_with_all_locks_and_never_uses_paper_keys(config_file):
    env = {**PAPER_ENV, **LIVE_KEYS, LIVE_CONFIRM_ENV: LIVE_CONFIRM_VALUE}
    s = load_settings(config_file("live"), env=env)
    assert s.base_url == LIVE_BASE_URL
    assert s.alpaca_key_id == "pk_live"
    assert "paper" not in s.alpaca_key_id


def test_url_mode_mismatch_raises():
    with pytest.raises(LiveModeLockError):
        assert_url_matches_mode(LIVE_BASE_URL, "paper")
    with pytest.raises(LiveModeLockError):
        assert_url_matches_mode(PAPER_BASE_URL, "live")
    assert_url_matches_mode(PAPER_BASE_URL, "paper")
    assert_url_matches_mode(LIVE_BASE_URL, "live")
