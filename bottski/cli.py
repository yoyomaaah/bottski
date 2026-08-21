"""bottski CLI. One subcommand per scheduled job, each its own crash domain."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from bottski import log as botlog
from bottski.config import Settings, load_settings
from bottski.store import db

logger = logging.getLogger("bottski")




def cmd_collect(settings: Settings, args: argparse.Namespace) -> int:
    """Pull Reddit + news into raw_documents. Each source is its own crash
    domain: one failing must not stop the other. Non-zero exit if any failed,
    so the scheduler's healthcheck ping is withheld."""
    conn = db.connect(settings.db_path)
    ok = True
    if not settings.reddit_client_id:
        logger.warning("reddit: skipped — no credentials configured (awaiting API approval)")
    else:
        try:
            from bottski.collect import reddit as reddit_collector

            stats = reddit_collector.collect(settings, conn)
            logger.info("reddit done: %s", stats)
            ok = ok and stats.get("errors", 0) == 0
        except Exception:
            logger.exception("reddit collection failed")
            ok = False
    try:
        from bottski.collect import news as news_collector

        stats = news_collector.collect(settings, conn)
        logger.info("news done: %s", stats)
    except Exception:
        logger.exception("news collection failed")
        ok = False
    from bottski.collect import external

    for name, fn in (("apewisdom", external.collect_apewisdom),
                     ("tradestie", external.collect_tradestie)):
        try:
            fn(settings, conn)
        except Exception:
            logger.exception("%s collection failed", name)
            ok = False
    try:
        from bottski.extract import tickers as extract_tickers

        extract_tickers.run(settings, conn)
        from bottski.score import vader as scorer

        scorer.run(settings, conn)
    except Exception:
        logger.exception("extraction/scoring failed")
        ok = False
    return 0 if ok else 1


def cmd_observe(settings: Settings, args: argparse.Namespace) -> int:
    from datetime import date
    from zoneinfo import ZoneInfo

    from bottski.features import panel

    conn = db.connect(settings.db_path)
    obs_date = (
        date.fromisoformat(args.date)
        if args.date
        else datetime.now(ZoneInfo("America/New_York")).date()
    )
    stats = panel.build(settings, conn, obs_date)
    print(stats)
    return 0


def _position_ages(conn, held: set[str]) -> dict[str, int]:
    """Trading days each held symbol has been open, from the latest buy fill
    (conservative: resets on adds). Panel dates stand in for trading days."""
    ages: dict[str, int] = {}
    for sym in held:
        first = conn.execute(
            "SELECT MAX(f.filled_utc) m FROM fills f JOIN orders o ON o.id = f.order_id"
            " WHERE o.symbol = ? AND o.side = 'buy'", (sym,)).fetchone()["m"]
        if not first:
            continue
        ages[sym] = conn.execute(
            "SELECT COUNT(DISTINCT obs_date) c FROM observations WHERE obs_date > ?",
            (first[:10],)).fetchone()["c"]
    return ages


def cmd_decide(settings: Settings, args: argparse.Namespace) -> int:
    """Strategy decisions for the latest (or given) panel date. Reads the
    broker (read-only) for account state; never places orders itself."""
    from bottski.broker.alpaca import get_account_state
    from bottski.strategy import decide

    conn = db.connect(settings.db_path)
    obs_date = args.date or (
        conn.execute("SELECT MAX(obs_date) m FROM observations").fetchone()["m"])
    if not obs_date:
        print("no panel yet — run observe first")
        return 2
    account = get_account_state(settings)
    mode = "dry-run" if args.dry_run else settings.mode
    ages = _position_ages(conn, set(account.positions))
    stats = decide.run(settings, conn, obs_date, account, position_age_days=ages, mode=mode)
    print(f"{obs_date} ({mode}): {stats}")
    return 0


def cmd_execute(settings: Settings, args: argparse.Namespace) -> int:
    """Place orders for today's unblocked decisions. Reconciles first; halts
    on mismatch or kill switch."""
    from bottski import execution
    from bottski.broker.alpaca import Broker

    conn = db.connect(settings.db_path)
    stats = execution.run(settings, conn, Broker(settings))
    print(stats)
    return 1 if stats.get("halted") else 0


def cmd_reconcile(settings: Settings, args: argparse.Namespace) -> int:
    from bottski import reconcile
    from bottski.broker.alpaca import Broker

    conn = db.connect(settings.db_path)
    result = reconcile.run(settings, conn, Broker(settings))
    print(result)
    return 1 if result.get("mismatch") else 0


def cmd_report(settings: Settings, args: argparse.Namespace) -> int:
    from bottski.research import report

    conn = db.connect(settings.db_path)
    summary = report.build(settings, conn)
    print(summary)
    return 0


def cmd_backfill_returns(settings: Settings, args: argparse.Namespace) -> int:
    from bottski.features import returns

    conn = db.connect(settings.db_path)
    stats = returns.run(settings, conn)
    print(stats)
    return 0


def cmd_extract(settings: Settings, args: argparse.Namespace) -> int:
    from bottski.extract import tickers as extract_tickers

    conn = db.connect(settings.db_path)
    stats = extract_tickers.run(settings, conn)
    print(stats)
    return 0


def cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    conn = db.connect(settings.db_path)
    killed = db.kill_switch_engaged(conn, settings.kill_switch_file)
    n_docs = conn.execute("SELECT COUNT(*) c FROM raw_documents").fetchone()["c"]
    n_obs = conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"]
    n_dec = conn.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"]
    print(f"mode:          {settings.mode.upper()}")
    print(f"base_url:      {settings.base_url}")
    print(f"kill switch:   {'ENGAGED — no new orders' if killed else 'off'}")
    print(f"db:            {settings.db_path}")
    print(f"raw documents: {n_docs}")
    print(f"observations:  {n_obs}")
    print(f"decisions:     {n_dec}")
    print(f"utc now:       {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    return 0


def cmd_halt(settings: Settings, args: argparse.Namespace) -> int:
    conn = db.connect(settings.db_path)
    db.set_control(conn, "kill_switch", "1")
    settings.kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
    settings.kill_switch_file.touch()
    print("kill switch ENGAGED (db flag + file). No new orders until `bottski resume`.")
    return 0


def cmd_resume(settings: Settings, args: argparse.Namespace) -> int:
    conn = db.connect(settings.db_path)
    db.set_control(conn, "kill_switch", "0")
    settings.kill_switch_file.unlink(missing_ok=True)
    print("kill switch off.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bottski")
    parser.add_argument("--config", default="config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("collect", help="pull news + external sentiment, then extract tickers")
    sub.add_parser("extract", help="run ticker extraction on unprocessed documents")
    p_obs = sub.add_parser("observe", help="build the observation panel for a trading day")
    p_obs.add_argument("--date", help="ET trading date (default: today)")
    sub.add_parser("backfill-returns", help="fill forward returns on past panel rows")
    sub.add_parser("report", help="write data/report.html with data status + signal metrics")
    p_dec = sub.add_parser("decide", help="strategy decisions (never places orders itself)")
    p_dec.add_argument("--date", help="panel date (default: latest)")
    p_dec.add_argument("--dry-run", action="store_true",
                       help="record decisions as dry-run so execute ignores them")
    sub.add_parser("execute", help="place orders for today's unblocked decisions (reconciles first)")
    sub.add_parser("reconcile", help="sync fills, compare positions vs broker; halt on mismatch")
    sub.add_parser("status", help="mode, kill switch, db counts")
    sub.add_parser("halt", help="engage kill switch")
    sub.add_parser("resume", help="release kill switch")
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    botlog.setup(settings.secret_values())
    logger.info("bottski starting — mode=%s base_url=%s", settings.mode.upper(), settings.base_url)

    handlers: dict[str, object] = {
        "collect": cmd_collect,
        "extract": cmd_extract,
        "observe": cmd_observe,
        "backfill-returns": cmd_backfill_returns,
        "report": cmd_report,
        "decide": cmd_decide,
        "execute": cmd_execute,
        "reconcile": cmd_reconcile,
        "status": cmd_status,
        "halt": cmd_halt,
        "resume": cmd_resume,
    }
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
