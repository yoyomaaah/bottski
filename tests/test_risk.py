"""Every rail gets a constructed violation. First-violation-wins ordering."""

from bottski.config import RiskLimits
from bottski.risk.rails import AccountState, Candidate, check

LIMITS = RiskLimits(
    max_position_pct=5.0, max_gross_exposure_pct=50.0, max_open_positions=10,
    max_order_notional=2000.0, max_orders_per_day=10, max_daily_loss_pct=3.0,
    min_dollar_volume_20d=5_000_000, max_spread_bps=50, stop_loss_pct=8.0,
)
ACCT = AccountState(equity=100_000, cash=100_000, day_start_equity=100_000)


def buy(**kw):
    base = dict(symbol="TSLA", side="buy", notional=1000,
                dollar_volume_20d=1e8, spread_bps=5)
    base.update(kw)
    return Candidate(**base)


def test_clear_candidate_passes():
    assert check(buy(), ACCT, LIMITS, 0, False) is None


def test_kill_switch_blocks_everything_including_sells():
    assert check(buy(), ACCT, LIMITS, 0, True) == "kill_switch"
    assert check(buy(side="sell"), ACCT, LIMITS, 0, True) == "kill_switch"


def test_halted_blocks():
    assert check(buy(is_halted=True), ACCT, LIMITS, 0, False) == "halted"


def test_not_tradable_blocks():
    assert check(buy(is_tradable=False), ACCT, LIMITS, 0, False) == "not_tradable"


def test_max_orders_per_day():
    assert check(buy(), ACCT, LIMITS, 10, False) == "max_orders_per_day"


def test_sell_exempt_from_entry_rails_but_not_order_cap():
    s = buy(side="sell", dollar_volume_20d=100, spread_bps=999)
    assert check(s, ACCT, LIMITS, 0, False) is None
    assert check(s, ACCT, LIMITS, 10, False) == "max_orders_per_day"


def test_max_daily_loss_trips():
    acct = AccountState(equity=96_500, cash=0, day_start_equity=100_000)
    assert check(buy(), acct, LIMITS, 0, False) == "max_daily_loss"


def test_max_order_notional():
    assert check(buy(notional=2500), ACCT, LIMITS, 0, False) == "max_order_notional"


def test_max_position_pct():
    acct = AccountState(equity=10_000, cash=10_000)
    assert check(buy(notional=1000), acct, LIMITS, 0, False) == "max_position_pct"


def test_max_gross_exposure():
    acct = AccountState(equity=100_000, cash=0,
                        positions={f"S{i}": 5000 for i in range(10)})
    # 50k held + 1k new = 51% > 50%; give it an 11th-position-free slot check
    limits = LIMITS.model_copy(update={"max_open_positions": 20})
    assert check(buy(notional=1000), acct, limits, 0, False) == "max_gross_exposure"


def test_max_open_positions():
    acct = AccountState(equity=1_000_000, cash=0,
                        positions={f"S{i}": 100 for i in range(10)})
    assert check(buy(), acct, LIMITS, 0, False) == "max_open_positions"


def test_adding_to_existing_position_is_not_a_new_slot():
    acct = AccountState(equity=1_000_000, cash=0,
                        positions={"TSLA": 100, **{f"S{i}": 100 for i in range(9)}})
    assert check(buy(), acct, LIMITS, 0, False) is None


def test_min_dollar_volume_and_unknown_liquidity():
    assert check(buy(dollar_volume_20d=1e6), ACCT, LIMITS, 0, False) == "min_dollar_volume"
    assert check(buy(dollar_volume_20d=None), ACCT, LIMITS, 0, False) == "min_dollar_volume"


def test_max_spread():
    assert check(buy(spread_bps=80), ACCT, LIMITS, 0, False) == "max_spread"
    assert check(buy(spread_bps=None), ACCT, LIMITS, 0, False) is None  # unknown ok
