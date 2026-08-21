import json

from bottski.config import load_settings
from bottski.risk.blacklist import Blacklist
from bottski.risk.rails import AccountState, Candidate, check
from bottski.store import db
from bottski.strategy import decide
from tests.test_risk import LIMITS, ACCT, buy

PAPER_ENV = {"ALPACA_PAPER_KEY_ID": "pk", "ALPACA_PAPER_SECRET_KEY": "sk"}


def test_blacklist_parsing(tmp_path):
    p = tmp_path / "bl.txt"
    p.write_text("# comment\nsector:energy\nAMZN\n\ntsla\nSECTOR:Tobacco\n")
    bl = Blacklist.load(p)
    assert bl.sectors == {"energy", "tobacco"}
    assert bl.symbols == {"AMZN", "TSLA"}
    assert bl.matches("AMZN", "tech")
    assert bl.matches("XOM", "energy")
    assert not bl.matches("NVDA", "semis")


def test_missing_file_is_empty(tmp_path):
    bl = Blacklist.load(tmp_path / "nope.txt")
    assert not bl.matches("AMZN", "tech")
    assert bl.describe() == "(empty)"


def test_rail_blocks_buys_never_sells():
    assert check(buy(blacklisted=True), ACCT, LIMITS, 0, False) == "blacklist"
    sell = buy(side="sell", blacklisted=True)
    assert check(sell, ACCT, LIMITS, 0, False) is None


def test_decide_records_blacklist_counterfactual(config_file, tmp_path):
    s = load_settings(config_file("paper"), env=PAPER_ENV)
    s.universe_file.write_text(
        "symbol,name,aliases,sector\nXOM,Exxon,,energy\nAMZN,Amazon,,tech\nNVDA,NVIDIA,,semis\n")
    object.__setattr__(s, "blacklist_file", tmp_path / "bl.txt")
    s.blacklist_file.write_text("sector:energy\nAMZN\n")
    conn = db.connect(s.db_path)
    for sym in ("XOM", "AMZN", "NVDA"):
        conn.execute(
            "INSERT INTO observations (obs_date, obs_ts_utc, symbol, n_mentions,"
            " score_mean, close, ret_5d, dollar_volume_20d, spread_bps, is_tradable,"
            " is_halted) VALUES ('2026-08-20', ?, ?, 5, 0.5, 100, 0.0, 1e8, 5, 1, 0)",
            (db.utcnow(), sym))
    conn.commit()
    stats = decide.run(s, conn, "2026-08-20", AccountState(equity=100_000, cash=100_000))
    d = {r["symbol"]: dict(r) for r in conn.execute("SELECT * FROM decisions")}
    assert d["XOM"]["blocked_by"] == "blacklist"     # sector exclusion
    assert d["AMZN"]["blocked_by"] == "blacklist"    # symbol exclusion
    assert d["NVDA"]["blocked_by"] is None and d["NVDA"]["action"] == "buy"
    assert stats["blocked"] == 2 and stats["buy"] == 1


def test_universe_sectors_loaded():
    from bottski.extract.tickers import Universe
    u = Universe.load("universe.csv")
    assert u.sectors["XOM"] == "energy"
    assert u.sectors["PM"] == "tobacco"
    assert u.sectors["LMT"] == "defense"
    assert u.sectors["TSLA"] == "auto"
