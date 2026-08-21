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

NOT_IMPLEMENTED = {
    "collect": "M1",
    "observe": "M3",
    "decide": "M5",
    "execute": "M6",
    "backfill-returns": "M3",
    "reconcile": "M6",
    "report": "M4",
}


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


def cmd_stub(settings: Settings, args: argparse.Namespace) -> int:
    milestone = NOT_IMPLEMENTED[args.command]
    print(f"`{args.command}` is not implemented yet (arrives in {milestone}).")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bottski")
    parser.add_argument("--config", default="config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="mode, kill switch, db counts")
    sub.add_parser("halt", help="engage kill switch")
    sub.add_parser("resume", help="release kill switch")
    for name, ms in NOT_IMPLEMENTED.items():
        sub.add_parser(name, help=f"(not yet implemented — {ms})")

    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    botlog.setup(settings.secret_values())
    logger.info("bottski starting — mode=%s base_url=%s", settings.mode.upper(), settings.base_url)

    handlers = {"status": cmd_status, "halt": cmd_halt, "resume": cmd_resume}
    return handlers.get(args.command, cmd_stub)(settings, args)


if __name__ == "__main__":
    sys.exit(main())
