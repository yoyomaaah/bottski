import pytest


@pytest.fixture
def config_file(tmp_path):
    """A config.toml factory writing into tmp_path."""

    def make(mode: str = "paper") -> str:
        p = tmp_path / "config.toml"
        p.write_text(f'''
mode = "{mode}"
db_path = "{tmp_path}/test.db"
kill_switch_file = "{tmp_path}/KILL"
universe_file = "{tmp_path}/universe.csv"

[collect]
subreddits = ["wallstreetbets"]

[strategy]
version = "v0"

[risk]
max_position_pct = 5.0
max_gross_exposure_pct = 50.0
max_open_positions = 10
max_order_notional = 2000.0
max_orders_per_day = 10
max_daily_loss_pct = 3.0
min_dollar_volume_20d = 5000000
max_spread_bps = 50
stop_loss_pct = 8.0
''')
        return str(p)

    return make
