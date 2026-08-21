"""One-time historical export from Massive (ex-Polygon), Stocks Developer plan.

Access window ends 2026-09-21. Everything lands in data/massive/ (gitignored),
private, personal research use only — never redistributed.

Subcommands:
  bars       grouped-daily OHLCV, FULL US market incl. delisted, per trading day
  reference  all tickers, active + inactive (paginated snapshot)
  actions    all splits + dividends
  details    per-ticker details (SIC sector, market cap) for the bottski universe
  all        everything above, in order

Resumable: bars tracks completed dates in state_bars.txt; reference/actions
overwrite atomically; safe to re-run any piece.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from bottski.market_calendar import _nyse  # noqa: E402

OUT = Path("data/massive")
BASE = "https://api.polygon.io"
PACE_S = 0.13
# Developer plan exposes a rolling ~10y window (probed: 2016-09-22 OK,
# 2016-08-22 NOT_AUTHORIZED on 2026-08-21). Start inside it with margin.
BARS_START = date(2016, 10, 1)


def _key() -> str:
    from dotenv import load_dotenv

    load_dotenv()
    k = os.environ.get("MASSIVE_API_KEY", "")
    if not k:
        sys.exit("MASSIVE_API_KEY missing from .env")
    return k


def _get(url: str, params: dict | None = None, key: str | None = None, retries: int = 5):
    params = dict(params or {})
    if key:
        params["apiKey"] = key
    full = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(full, timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001 — pace + retry on anything transient
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return None


def export_bars(key: str) -> None:
    out = OUT / "grouped_daily"
    out.mkdir(parents=True, exist_ok=True)
    state_file = OUT / "state_bars.txt"
    done = set(state_file.read_text().split()) if state_file.exists() else set()

    sched = _nyse().schedule(start_date=BARS_START.isoformat(),
                             end_date=date.today().isoformat())
    days = [d.date().isoformat() for d in sched.index]
    todo = [d for d in days if d not in done]
    print(f"bars: {len(days)} trading days, {len(todo)} to fetch", flush=True)

    buf: dict[str, list] = {}
    for i, day in enumerate(todo):
        data = _get(f"{BASE}/v2/aggs/grouped/locale/us/market/stocks/{day}",
                    {"adjusted": "true"}, key)
        rows = data.get("results") or []
        year = day[:4]
        buf.setdefault(year, []).extend(
            {"date": day, "ticker": r.get("T"), "o": r.get("o"), "h": r.get("h"),
             "l": r.get("l"), "c": r.get("c"), "v": r.get("v"),
             "vw": r.get("vw"), "n": r.get("n")} for r in rows)
        done.add(day)
        if (i + 1) % 50 == 0 or i == len(todo) - 1:
            for year, rows_ in list(buf.items()):
                f = out / f"{year}.parquet"
                df = pd.DataFrame(rows_)
                if f.exists():
                    df = pd.concat([pd.read_parquet(f), df], ignore_index=True)
                    df = df.drop_duplicates(subset=["date", "ticker"])
                df.to_parquet(f, index=False)
            buf.clear()
            state_file.write_text("\n".join(sorted(done)))
            print(f"bars: {i+1}/{len(todo)} days done (through {day})", flush=True)
        time.sleep(PACE_S)
    print("bars: complete", flush=True)


def _paginate(url: str, params: dict, key: str, label: str) -> list[dict]:
    rows, page = [], 0
    data = _get(url, {**params, "limit": 1000}, key)
    while True:
        rows.extend(data.get("results") or [])
        page += 1
        if page % 20 == 0:
            print(f"{label}: {len(rows)} rows...", flush=True)
        nxt = data.get("next_url")
        if not nxt:
            break
        time.sleep(PACE_S)
        data = _get(nxt, {}, key)
    return rows


def export_reference(key: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frames = []
    for active in ("true", "false"):
        rows = _paginate(f"{BASE}/v3/reference/tickers",
                         {"market": "stocks", "active": active}, key,
                         f"reference(active={active})")
        df = pd.DataFrame(rows)
        df["was_active_at_export"] = active == "true"
        frames.append(df)
        print(f"reference: active={active} -> {len(df)} tickers", flush=True)
    pd.concat(frames, ignore_index=True).to_parquet(OUT / "tickers.parquet", index=False)
    print("reference: complete", flush=True)


def export_actions(key: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, path in (("splits", "v3/reference/splits"),
                       ("dividends", "v3/reference/dividends")):
        rows = _paginate(f"{BASE}/{path}", {}, key, name)
        pd.DataFrame(rows).to_parquet(OUT / f"{name}.parquet", index=False)
        print(f"{name}: {len(rows)} rows, complete", flush=True)


def export_details(key: str) -> None:
    import csv

    OUT.mkdir(parents=True, exist_ok=True)
    with open("universe.csv", newline="") as f:
        symbols = [r["symbol"] for r in csv.DictReader(f)]
    import urllib.error

    rows = []
    for i, sym in enumerate(symbols):
        try:
            data = _get(f"{BASE}/v3/reference/tickers/{urllib.parse.quote(sym)}", {}, key)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                rows.append({"ticker": sym, "name": None, "sic_code": None,
                             "sic_description": None, "market_cap": None,
                             "total_employees": None, "list_date": None,
                             "description": "NOT_FOUND"})
                continue
            raise
        r = data.get("results") or {}
        rows.append({"ticker": sym, "name": r.get("name"),
                     "sic_code": r.get("sic_code"),
                     "sic_description": r.get("sic_description"),
                     "market_cap": r.get("market_cap"),
                     "total_employees": r.get("total_employees"),
                     "list_date": r.get("list_date"),
                     "description": (r.get("description") or "")[:500]})
        if (i + 1) % 50 == 0:
            print(f"details: {i+1}/{len(symbols)}", flush=True)
        time.sleep(PACE_S)
    pd.DataFrame(rows).to_parquet(OUT / "universe_details.parquet", index=False)
    print("details: complete", flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    key = _key()
    steps = {"bars": export_bars, "reference": export_reference,
             "actions": export_actions, "details": export_details}
    if cmd == "all":
        for name, fn in steps.items():
            print(f"=== {name} ===", flush=True)
            fn(key)
    else:
        steps[cmd](key)
