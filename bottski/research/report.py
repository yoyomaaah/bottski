"""The dashboard/report page: one self-contained HTML file, written for a
reader who is NOT a quant. Every number is accompanied by what it means; the
statistics get verdict badges with the raw table behind a disclosure.

Design follows the dataviz reference palette (single-series line chart, status
colors always icon+label, text never wears series color, light+dark modes).
The panel/signal section stays conceptually separate from trading P&L: the
panel says whether the SIGNAL works; the trade log says whether the BOT works.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

STHLM = ZoneInfo("Europe/Stockholm")
NY = ZoneInfo("America/New_York")


def _sthlm(ts: str | None, with_date: bool = True) -> str:
    """Render a stored UTC timestamp string in Stockholm local time."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(STHLM)
        return local.strftime("%Y-%m-%d %H:%M" if with_date else "%H:%M")
    except ValueError:
        return ts


def _decision_time_local() -> str:
    """What 15:45 New York is in Stockholm today (DST-safe)."""
    today = datetime.now(NY).date()
    ny = datetime(today.year, today.month, today.day, 15, 45, tzinfo=NY)
    return ny.astimezone(STHLM).strftime("%H:%M")

from bottski.config import Settings
from bottski.market_calendar import sentiment_window
from bottski.research import ic
from bottski.score.vader import SCORER_VERSION

logger = logging.getLogger("bottski.report")

CSS = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e;
  --muted: #898781; --grid: #e1e0d9; --baseline: #c3c2b7;
  --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --up: #006300; --down: #d03b3b;
  --good: #0ca30c; --warn: #fab219; --serious: #ec835a; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7;
    --muted: #898781; --grid: #2c2c2a; --baseline: #383835;
    --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --up: #0ca30c; --down: #e66767;
  }
}
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  margin: 0; background: var(--page); color: var(--ink); line-height: 1.5; }
main { max-width: 980px; margin: 0 auto; padding: 1.2rem 1rem 3rem; }
h1 { font-size: 1.5rem; margin: 0; }
h2 { font-size: 1.15rem; margin: 2.2rem 0 0.3rem; }
.sub { color: var(--ink-2); font-size: 0.85rem; margin: 0.1rem 0 0; }
.lede { color: var(--ink-2); font-size: 0.9rem; margin: 0.2rem 0 0.8rem; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px; margin-top: 1.1rem; }
.tile { background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 10px 14px; }
.tile .label { font-size: 0.78rem; color: var(--ink-2); }
.tile .value { font-size: 1.5rem; font-weight: 600; }
.tile .delta { font-size: 0.8rem; }
.tile .note { font-size: 0.75rem; color: var(--muted); }
.card { background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 14px; margin-top: 0.5rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.88rem; }
th { text-align: left; color: var(--ink-2); font-weight: 600;
  border-bottom: 1px solid var(--baseline); padding: 5px 8px; }
td { padding: 5px 8px; border-bottom: 1px solid var(--grid); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.up { color: var(--up); font-weight: 600; }
.down { color: var(--down); font-weight: 600; }
.badge { display: inline-block; padding: 1px 9px; border-radius: 999px;
  font-size: 0.78rem; font-weight: 600; border: 1px solid var(--border); }
.b-good { background: color-mix(in srgb, var(--good) 14%, var(--surface)); }
.b-warn { background: color-mix(in srgb, var(--warn) 18%, var(--surface)); }
.b-crit { background: color-mix(in srgb, var(--critical) 14%, var(--surface)); }
.b-muted { background: var(--page); color: var(--ink-2); }
details { margin-top: 0.6rem; }
summary { cursor: pointer; color: var(--ink-2); font-size: 0.88rem; }
details[open] summary { margin-bottom: 0.5rem; }
.muted { color: var(--muted); }
.explain { font-size: 0.83rem; color: var(--ink-2); }
svg text { font-family: inherit; }
.pill-kill { border-color: var(--critical); }
a.sym { color: inherit; font-weight: 600; text-decoration: none;
  border-bottom: 1px dotted var(--muted); }
a.sym:hover { border-bottom-style: solid; }
.explain a { color: inherit; }
dl.gloss dt { font-weight: 600; margin-top: 0.6rem; }
dl.gloss dd { margin: 0; color: var(--ink-2); font-size: 0.88rem; }
.card { overflow-x: auto; }           /* wide tables scroll inside their card */
th, td { white-space: nowrap; }
td.explain, td:has(details) { white-space: normal; min-width: 220px; }
@media (max-width: 640px) {
  main { padding: 0.8rem 0.6rem 2.5rem; }
  h1 { font-size: 1.25rem; }
  h1 .badge { display: block; width: fit-content; margin-top: 4px; }
  .tiles { grid-template-columns: 1fr 1fr; }
  .tile .value { font-size: 1.25rem; }
  table { font-size: 0.8rem; }
  th, td { padding: 4px 6px; }
}
"""

# --- plain-language mappings -------------------------------------------------

REASONS = {
    "sentiment_entry": "chatter was strongly positive",
    "sentiment_flip_bearish": "chatter turned negative — selling",
    "max_hold_days": "held for the 5-day limit — time-based exit",
    "extended_move": "skipped: already up >10% this week (don't chase)",
    "score_below_entry": "chatter not positive enough",
    "ranked_below_daily_cap": "positive, but not in today's top 3",
    "no_price": "no price data available",
    "position_within_rules": "keeping the position",
}
RAILS = {
    "kill_switch": "kill switch is engaged",
    "min_dollar_volume": "stock trades too thinly",
    "max_spread": "bid/ask spread too wide",
    "max_position_pct": "position-size limit",
    "max_gross_exposure": "total-exposure limit",
    "max_open_positions": "too many open positions",
    "max_order_notional": "order-size limit",
    "max_orders_per_day": "daily order limit",
    "max_daily_loss": "daily loss limit was hit",
    "halted": "trading is halted in this stock",
    "blacklist": "on your never-buy list",
    "not_tradable": "not tradable at the broker",
}
SIGNAL_NAMES = {
    "score_mean": "Our sentiment score",
    "news_score_mean": "News-only sentiment",
    "ext_sentiment_score": "WSB sentiment (Tradestie)",
    "mention_velocity": "Mention spikes",
    "n_mentions": "Attention level (mention count)",
    "ext_adanos_sentiment": "Multi-subreddit sentiment (Adanos)",
}


_UNI_META: dict[str, tuple[str, str]] = {}


def _load_uni_meta(settings: Settings) -> None:
    """symbol -> (company name, sector) for tooltips; best-effort."""
    global _UNI_META
    try:
        from bottski.extract.tickers import Universe

        u = Universe.load(settings.universe_file)
        _UNI_META = {sym: (u.names.get(sym, ""), u.sectors.get(sym, ""))
                     for sym in u.symbols}
    except FileNotFoundError:
        _UNI_META = {}


def _sym(symbol: str) -> str:
    """Ticker rendered as a link to its Yahoo Finance page, with a native
    tooltip carrying company name + sector from universe.csv."""
    name, sector = _UNI_META.get(symbol, ("", ""))
    tip = " · ".join(x for x in (name, sector) if x) or symbol
    y = symbol.replace(".", "-")
    return (f"<a class=sym href='https://finance.yahoo.com/quote/{y}'"
            f" target='_blank' rel='noopener' title='{tip}'>{symbol}</a>")


def _fmt(x, pct=False, digits=3):
    if x is None or (isinstance(x, float) and (x != x)):
        return "—"
    return f"{x*100:.2f}%" if pct else f"{x:.{digits}f}"


def _money(x):
    return f"${x:,.0f}" if x is not None else "—"


def _reason(code):
    return REASONS.get(code, code)


def _verdict(r: "ic.HorizonResult") -> tuple[str, str]:
    """(badge css class, text) for one signal's 1d result, in plain language."""
    if r is None or not r.n_days:
        return "b-muted", "no data yet"
    if r.n_days < 10:
        return "b-muted", f"too early to tell — day {r.n_days} of ~30 needed"
    t = r.resid_t_stat
    if t != t:  # nan
        return "b-muted", f"not measurable yet ({r.n_days} days)"
    if abs(t) < 1:
        return "b-muted", f"no evidence so far ({r.n_days} days)"
    if abs(t) < 2:
        direction = "positive" if t > 0 else "inverse"
        return "b-warn", f"weak {direction} hint — not yet reliable ({r.n_days} days)"
    direction = "predicts returns" if t > 0 else "predicts returns INVERSELY"
    return "b-good", f"evidence it {direction} ({r.n_days} days)"


# --- sections ----------------------------------------------------------------

def _status(conn) -> dict:
    q = lambda sql, *a: conn.execute(sql, a).fetchone()
    docs = q("SELECT COUNT(*) c FROM raw_documents")["c"]
    eq = q("SELECT equity, snapshot_utc FROM equity_curve ORDER BY snapshot_utc DESC LIMIT 1")
    eq0 = q("SELECT equity FROM equity_curve ORDER BY snapshot_utc LIMIT 1")
    pos_ts = q("SELECT MAX(snapshot_utc) m FROM positions_snapshot")["m"]
    positions = conn.execute(
        "SELECT * FROM positions_snapshot WHERE snapshot_utc = ? ORDER BY symbol",
        (pos_ts,)).fetchall() if pos_ts else []
    day = q("SELECT MAX(date(decision_utc)) m FROM decisions WHERE mode != 'dry-run'")["m"]
    decisions = conn.execute(
        "SELECT d.*, ifnull((SELECT obs_date FROM observations o WHERE o.id = d.obs_id),"
        " date(d.decision_utc)) AS obs_date"
        " FROM decisions d WHERE date(d.decision_utc) = ? AND d.mode != 'dry-run'"
        " ORDER BY CASE d.action WHEN 'sell' THEN 0 WHEN 'buy' THEN 1 ELSE 2 END,"
        " d.blocked_by IS NOT NULL, d.symbol", (day,)).fetchall() if day else []
    return {
        "docs": docs,
        "panel_days": q("SELECT COUNT(DISTINCT obs_date) c FROM observations")["c"],
        "fwd_filled": q("SELECT COUNT(*) c FROM observations WHERE fwd_ret_1d IS NOT NULL")["c"],
        "equity": eq["equity"] if eq else None,
        "equity_ts": eq["snapshot_utc"] if eq else None,
        "equity_start": eq0["equity"] if eq0 else None,
        "positions": positions,
        "positions_ts": pos_ts,
        "decision_day": day,
        "decisions": decisions,
        "fills": conn.execute(
            "SELECT f.filled_utc, o.symbol, o.side, f.qty, f.price FROM fills f"
            " JOIN orders o ON o.id = f.order_id ORDER BY f.filled_utc DESC LIMIT 15"
        ).fetchall(),
    }


def _tiles(st, killed) -> str:
    eq, eq0 = st["equity"], st["equity_start"]
    if eq is not None and eq0:
        d = eq / eq0 - 1
        cls = "up" if (eq - eq0) >= 0 else "down"
        delta = (f"<div class='delta {cls}'>{eq - eq0:+,.2f} ({d*100:+.2f}%) since start</div>"
                 f"<div class=note>account value as reported by the broker at "
                 f"{_sthlm(st.get('equity_ts'))} · auto-refreshes each US trading "
                 f"day ~22:05 Stockholm time</div>")
        eq_html = f"<div class=value>{_money(eq)}</div>{delta}"
    else:
        eq_html = "<div class=value>$100,000</div><div class=note>starting value — first snapshot lands after the first trading close</div>"
    n_pos = len(st["positions"])
    upl = sum(p["unrealized_pl"] or 0 for p in st["positions"])
    pos_delta = ""
    if n_pos:
        cls = "up" if upl >= 0 else "down"
        pos_delta = f"<div class='delta {cls}'>{upl:+,.0f} unrealized</div>"
    acted = [d for d in st["decisions"] if d["action"] != "hold" and not d["blocked_by"]]
    buys = sum(1 for d in acted if d["action"] == "buy")
    sells = sum(1 for d in acted if d["action"] == "sell")
    blocked = sum(1 for d in st["decisions"] if d["blocked_by"])
    if st["decisions"]:
        today_val = f"{buys} buy · {sells} sell"
        today_note = f"{blocked} stopped by safety rails" if blocked else "no rail blocks"
    else:
        today_val, today_note = "—", f"decisions run {_decision_time_local()} Stockholm time (15:45 New York)"
    system = ("<span class='badge b-crit'>⛔ halted</span>" if killed
              else "<span class='badge b-good'>✓ running</span>")
    return f"""
<div class=tiles>
  <div class=tile><div class=label>Account value (paper money)</div>{eq_html}</div>
  <div class=tile><div class=label>Open positions</div>
    <div class=value>{n_pos}</div>{pos_delta or "<div class=note>currently flat</div>"}</div>
  <div class=tile><div class=label>Today's trades</div>
    <div class=value>{today_val}</div><div class=note>{today_note}</div></div>
  <div class=tile><div class=label>System</div>
    <div class=value>{system}</div>
    <div class=note>{st['docs']:,} documents collected</div></div>
</div>"""


def _equity_chart(conn) -> str:
    rows = conn.execute(
        "SELECT snapshot_utc, equity FROM equity_curve ORDER BY snapshot_utc").fetchall()
    if len(rows) < 2:
        return ("<div class=card><p class=explain>The account-value chart appears "
                "once there are a few days of history (one point per trading day). "
                "Everything here is simulated “paper” money — no real "
                "money is at risk.</p></div>")
    vals = [r["equity"] for r in rows]
    lo, hi = min(vals), max(vals)
    pad_v = (hi - lo) * 0.15 or max(vals[0] * 0.002, 1)
    lo, hi = lo - pad_v, hi + pad_v
    W, H, L, B = 920, 200, 56, 22
    def x(i): return L + i * (W - L - 14) / (len(vals) - 1)
    def y(v): return (H - B) - (v - lo) * (H - B - 10) / (hi - lo)
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    area = f"{L},{y(vals[0]):.1f} {pts} {x(len(vals)-1):.1f},{H-B} {L},{H-B}"
    # clean gridlines: 3 round levels
    grid, labels = [], []
    for frac in (0.0, 0.5, 1.0):
        v = lo + (hi - lo) * frac
        yy = y(v)
        grid.append(f"<line x1='{L}' y1='{yy:.1f}' x2='{W-14}' y2='{yy:.1f}'"
                    f" stroke='var(--grid)' stroke-width='1'/>")
        labels.append(f"<text x='{L-6}' y='{yy+4:.1f}' text-anchor='end'"
                      f" font-size='11' fill='var(--muted)'>{v:,.0f}</text>")
    ex, ey = x(len(vals) - 1), y(vals[-1])
    hover = "".join(
        f"<circle cx='{x(i):.1f}' cy='{y(v):.1f}' r='10' fill='transparent'>"
        f"<title>{rows[i]['snapshot_utc'][:10]}: ${v:,.0f}</title></circle>"
        for i, v in enumerate(vals))
    d = vals[-1] / vals[0] - 1
    cls = "up" if d >= 0 else "down"
    return f"""
<div class=card>
  <svg viewBox='0 0 {W} {H}' style='width:100%;display:block'>
    {''.join(grid)}{''.join(labels)}
    <polygon points='{area}' fill='var(--series-1)' opacity='0.10'/>
    <polyline points='{pts}' fill='none' stroke='var(--series-1)'
      stroke-width='2' stroke-linejoin='round' stroke-linecap='round'/>
    <circle cx='{ex:.1f}' cy='{ey:.1f}' r='4.5' fill='var(--series-1)'
      stroke='var(--surface)' stroke-width='2'/>
    <text x='{ex-6:.1f}' y='{ey-9:.1f}' text-anchor='end' font-size='12'
      font-weight='600' fill='var(--ink)'>${vals[-1]:,.0f}</text>
    {hover}
  </svg>
  <p class=explain>{rows[0]['snapshot_utc'][:10]} → {rows[-1]['snapshot_utc'][:10]}
   · started at {_money(vals[0])} · <span class='{cls}'>{d*100:+.2f}%</span>
   overall. Hover a point for the exact value.</p>
</div>"""


def _positions_html(st) -> str:
    if not st["positions"]:
        return ("<div class=card><p class=explain>The bot holds nothing right now. "
                "It buys at most 3 stocks per day, only when online chatter about "
                "them is strongly positive, and sells within 5 trading days.</p></div>")
    rows = []
    for p in st["positions"]:
        pl = p["unrealized_pl"] or 0
        cls = "up" if pl >= 0 else "down"
        qty = p["qty"] or 0
        now_per_share = (p["market_value"] / qty) if qty else None
        cost = (p["avg_entry_price"] or 0) * qty
        pct = f" ({pl / cost * 100:+.2f}%)" if cost else ""
        rows.append(
            f"<tr><td>{_sym(p['symbol'])}</td><td class=num>{qty:g}</td>"
            f"<td class=num>${p['avg_entry_price']:,.2f}</td>"
            f"<td class=num>{'$' + format(now_per_share, ',.2f') if now_per_share else '—'}</td>"
            f"<td class=num>{_money(p['market_value'])}</td>"
            f"<td class='num {cls}'>{pl:+,.2f}{pct}</td></tr>")
    return (f"<div class=card><table><tr><th>stock</th><th class=num>shares</th>"
            f"<th class=num>paid / share</th><th class=num>now / share</th>"
            f"<th class=num>total value</th>"
            f"<th class=num>gain/loss</th></tr>{''.join(rows)}</table>"
            f"<p class=explain>as of {_sthlm(st['positions_ts'])} Stockholm time · every position "
            f"has an automatic stop-loss 8% below entry, held at the broker</p></div>")


def _blacklist_desc(settings: Settings) -> str:
    from bottski.risk.blacklist import Blacklist

    return Blacklist.load(settings.blacklist_file).describe()


def _blacklist_html(settings: Settings) -> str:
    from bottski.risk.blacklist import Blacklist

    bl = Blacklist.load(settings.blacklist_file)
    if not bl.sectors and not bl.symbols:
        return ""
    chips = []
    for sec in sorted(bl.sectors):
        chips.append(f"<span class='badge b-crit'>sector: {sec}</span>")
    for sym in sorted(bl.symbols):
        chips.append(f"<span class='badge b-crit'>{_sym(sym)}</span>")
    return (f"<h2>Excluded from trading</h2>"
            f"<p class=lede>Names and sectors the bot will never buy, by your choice. "
            f"They still appear in the research data — excluded from the portfolio, "
            f"not the experiment. Blocked buys show up in the decisions log above.</p>"
            f"<div class=card style='display:flex;flex-wrap:wrap;gap:6px'>"
            f"{''.join(chips)}</div>")


def _rules_html(settings: Settings) -> str:
    c, r = settings.strategy, settings.risk
    return f"""
<details><summary>How the bot decides (the actual current rules)</summary>
<div class=card><ol class=explain style='margin:0.2rem 0 0.2rem 1.2rem;padding:0'>
<li>Look at every stock with at least <b>{c.min_mentions} mentions</b> in the last day.</li>
<li>Consider buying when average sentiment is <b>≥ {c.min_score:+.2f}</b>
    (scale −1…+1); skip anything already up more than
    <b>{c.extended_ret_5d*100:.0f}%</b> this week (don't chase).</li>
<li>Rank the rest by sentiment; buy at most the <b>top {c.max_new_positions_per_day}</b>,
    about <b>{_money(min(r.max_order_notional, 100000 * r.max_position_pct / 100))}</b> each
    (never more than {r.max_position_pct:g}% of the account per stock).</li>
<li>Every buy gets an automatic <b>stop-loss {r.stop_loss_pct:g}% below entry</b>, held at the broker.</li>
<li>Sell when sentiment flips below <b>{c.exit_score:+.2f}</b>, or after
    <b>{c.max_hold_days} trading days</b>, whichever comes first.</li>
<li>Never buys anything on the exclusion list: <b>{_blacklist_desc(settings)}</b>
    (excluded names stay in the research data — they just can't be bought).</li>
<li>Hard safety rails can refuse any trade regardless of the strategy:
    liquidity ≥ {_money(r.min_dollar_volume_20d)}/day traded, spread ≤ {r.max_spread_bps:g} bps,
    ≤ {r.max_open_positions} open positions, ≤ {r.max_orders_per_day} orders/day,
    daily loss ≤ {r.max_daily_loss_pct:g}% (else the kill switch trips).</li>
</ol></div></details>"""


def _check(label, value_txt, ok) -> str:
    mark = "✓" if ok else ("✗" if ok is False else "·")
    cls = "up" if ok else ("down" if ok is False else "muted")
    return (f"<tr><td>{label}</td><td class=num>{value_txt}</td>"
            f"<td class='num {cls}'>{mark}</td></tr>")


def _evidence_html(d, settings: Settings, conn=None) -> str:
    """The expandable 'what the bot saw' block for one decision."""
    inputs = json.loads(d["inputs_json"])
    p = inputs.get("panel", {})
    c, r = settings.strategy, settings.risk
    score, mentions = p.get("score_mean"), p.get("n_mentions")
    ret5, dv, spread = p.get("ret_5d"), p.get("dollar_volume_20d"), p.get("spread_bps")
    rows = []
    rows.append(_check(f"mentions in window (need ≥ {c.min_mentions})",
                       mentions if mentions is not None else "—",
                       None if mentions is None else mentions >= c.min_mentions))
    rows.append(_check(f"avg sentiment (need ≥ {c.min_score:+.2f})",
                       _fmt(score, digits=2) if score is not None else "—",
                       None if score is None else score >= c.min_score))
    rows.append(_check(f"5-day move (must be ≤ +{c.extended_ret_5d*100:.0f}%)",
                       _fmt(ret5, pct=True) if ret5 is not None else "—",
                       None if ret5 is None else ret5 <= c.extended_ret_5d))
    rows.append(_check(f"daily $ traded (need ≥ {_money(r.min_dollar_volume_20d)})",
                       _money(dv) if dv is not None else "—",
                       None if dv is None else dv >= r.min_dollar_volume_20d))
    rows.append(_check(f"bid/ask spread (max {r.max_spread_bps:g} bps)",
                       f"{spread:.0f} bps" if spread is not None else "not measured",
                       None if spread is None else spread <= r.max_spread_bps))
    extra = []
    if p.get("n_news") is not None and mentions:
        extra.append(f"{p['n_news']} of {mentions} mentions were news articles"
                     + (f" (news sentiment {_fmt(p.get('news_score_mean'), digits=2)})"
                        if p.get("news_score_mean") is not None else ""))
    if p.get("ext_sentiment_score") is not None:
        extra.append(f"WSB sentiment (Tradestie): {_fmt(p['ext_sentiment_score'], digits=2)}")
    if p.get("ext_mentions") is not None:
        extra.append(f"WSB mention count (ApeWisdom): {p['ext_mentions']}")
    if p.get("close") is not None:
        px = f"price {_money(p['close'])}"
        if p.get("ret_1d") is not None:
            px += f", {p['ret_1d']*100:+.1f}% today"
        if p.get("dist_from_20d_high") is not None:
            px += f", {p['dist_from_20d_high']*100:+.1f}% vs 20-day high"
        extra.append(px)
    acct = inputs.get("account", {})
    if acct:
        extra.append(f"account at decision time: {_money(acct.get('equity'))} equity, "
                     f"{acct.get('n_positions', 0)} positions open")
    extra_html = ("<p class=explain style='margin:0.4rem 0 0'>" +
                  " · ".join(extra) + "</p>") if extra else ""
    receipts = _receipts_html(conn, d["symbol"], d["obs_date"] if "obs_date" in d.keys() else None)
    return (f"<details style='margin:0.2rem 0 0'><summary style='font-size:0.8rem'>"
            f"what the bot saw</summary>"
            f"<table style='margin-top:4px'><tr><th>check</th>"
            f"<th class=num>value</th><th class=num></th></tr>"
            f"{''.join(rows)}</table>{extra_html}{receipts}</details>")


def _receipts_html(conn, symbol: str, obs_date: str | None) -> str:
    """The actual documents behind a symbol's score in its decision window."""
    if not obs_date or conn is None:
        return ""
    try:
        from datetime import date as _date
        start_et, end_et = sentiment_window(_date.fromisoformat(obs_date))
    except Exception:
        return ""
    start = start_et.astimezone(timezone.utc).isoformat(timespec="seconds")
    end = end_et.astimezone(timezone.utc).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT d.title, d.body, d.source, d.subreddit_or_publisher pub,
                  d.url, s.compound
           FROM document_sentiment s JOIN raw_documents d ON d.id = s.document_id
           WHERE s.symbol = ? AND s.scorer_version = ?
             AND d.created_utc >= ? AND d.created_utc < ?
           ORDER BY ABS(s.compound) DESC LIMIT 6""",
        (symbol, SCORER_VERSION, start, end)).fetchall()
    if not rows:
        return ""
    items = []
    for r in rows:
        text = (r["title"] or (r["body"] or "")[:90] or "(no text)").strip()
        if len(text) > 110:
            text = text[:107] + "…"
        cls = "up" if r["compound"] >= 0.05 else ("down" if r["compound"] <= -0.05 else "muted")
        link = f"<a href='{r['url']}' target='_blank' rel='noopener'>{text}</a>" if r["url"] else text
        items.append(f"<li><span class='{cls}'>{r['compound']:+.2f}</span> "
                     f"<span class=muted>{r['pub'] or r['source']}</span> · {link}</li>")
    return ("<p class=explain style='margin:0.5rem 0 0.1rem'><b>the actual text behind "
            "the score</b> (strongest first):</p><ul class=explain style='margin:0.1rem 0 "
            "0.2rem 1.1rem;padding:0'>" + "".join(items) + "</ul>")


def _decisions_html(st, settings: Settings, conn=None) -> str:
    if not st["decisions"]:
        return ("<div class=card><p class=explain>No decisions yet today. The bot "
                "decides once per day at 15:45 New York time (about 15 minutes "
                "before the market closes), based on the last 24 hours of chatter."
                "</p></div>") + _rules_html(settings)
    acted = [d for d in st["decisions"] if d["action"] != "hold" or d["blocked_by"]]

    def _score(d):
        s = json.loads(d["inputs_json"]).get("panel", {}).get("score_mean")
        return s if s is not None else -99

    holds = [d for d in st["decisions"] if d["action"] == "hold" and not d["blocked_by"]]
    holds_with_signal = sorted(
        (d for d in holds
         if json.loads(d["inputs_json"]).get("panel", {}).get("n_mentions")),
        key=_score, reverse=True)[:12]
    quiet_holds = len(holds) - len(holds_with_signal)

    rows = []
    for d in acted + holds_with_signal:
        panel = json.loads(d["inputs_json"]).get("panel", {})
        why = _reason(d["reason_code"])
        score, mentions = panel.get("score_mean"), panel.get("n_mentions")
        if score is not None and mentions:
            why += f" (score {score:+.2f} across {mentions} mentions)"
        if d["blocked_by"]:
            act = "<span class='badge b-warn'>🛡 blocked</span>"
            why += f" — <b>stopped by a safety rail:</b> {RAILS.get(d['blocked_by'], d['blocked_by'])}"
        elif d["action"] == "buy":
            act = "<span class='badge b-good'>bought</span>"
        elif d["action"] == "sell":
            act = "<span class='badge b-crit'>sold</span>"
        else:
            act = "<span class='badge b-muted'>passed</span>"
        amount = _money(d["target_notional"]) if d["target_notional"] else "—"
        rows.append(f"<tr><td>{_sym(d['symbol'])}</td><td>{act}</td>"
                    f"<td class=num>{amount}</td>"
                    f"<td class=explain>{why}{_evidence_html(d, settings, conn)}</td></tr>")
    body = (f"<table><tr><th>stock</th><th>action</th><th class=num>amount</th>"
            f"<th>why — expand each row for the evidence</th></tr>{''.join(rows)}</table>") if rows else \
        "<p class=explain>Nothing met the bar today — the bot did not trade.</p>"
    return (f"<div class=card>{body}<p class=explain>{quiet_holds} more stocks had "
            f"little or no chatter and were left alone. “Blocked” rows are trades "
            f"the strategy wanted but a hard safety rule refused — recorded on "
            f"purpose, so we can later judge whether the rails helped. “Passed” "
            f"rows are the near-misses: stocks with chatter that didn't clear the "
            f"bar.</p></div>") + _rules_html(settings)


def _fills_html(st) -> str:
    if not st["fills"]:
        return ""
    rows = "".join(
        f"<tr><td>{_sthlm(f['filled_utc'])}</td><td>{_sym(f['symbol'])}</td>"
        f"<td>{f['side']}</td><td class=num>{f['qty']:g}</td>"
        f"<td class=num>{_money(f['price'])}</td></tr>" for f in st["fills"])
    return (f"<details><summary>Recent executed orders ({len(st['fills'])})</summary>"
            f"<div class=card><table><tr><th>time (Stockholm)</th><th>stock</th><th>side</th>"
            f"<th class=num>shares</th><th class=num>price</th></tr>{rows}</table></div></details>")


def _chatter_html(conn) -> str:
    day = conn.execute("SELECT MAX(obs_date) m FROM observations").fetchone()["m"]
    if not day:
        return ""
    rows = conn.execute(
        """SELECT symbol, n_mentions, score_mean, ext_sentiment_score, ext_rank
           FROM observations WHERE obs_date = ? AND n_mentions > 0
           ORDER BY n_mentions DESC LIMIT 10""", (day,)).fetchall()
    if not rows:
        return ""
    trs = []
    for r in rows:
        ours, wsb = r["score_mean"], r["ext_sentiment_score"]
        if ours is not None and wsb is not None and abs(ours - wsb) > 0.3:
            note = "<span class='badge b-warn'>disagree</span>"
        elif wsb is None:
            note = "<span class=muted>—</span>"
        else:
            note = "<span class=muted>agree</span>"
        trs.append(
            f"<tr><td>{_sym(r['symbol'])}</td><td class=num>{r['n_mentions']}</td>"
            f"<td class=num>{_fmt(ours, digits=2)}</td>"
            f"<td class=num>{_fmt(wsb, digits=2)}</td>"
            f"<td class=num>{r['ext_rank'] if r['ext_rank'] is not None else '—'}</td>"
            f"<td>{note}</td></tr>")
    return (f"<h2>What the market is talking about</h2>"
            f"<p class=lede>Most-mentioned stocks in the latest observation "
            f"({day}), whether or not the bot acted. <b>Our score</b> is computed "
            f"by this bot from collected news text (we don't read Reddit directly "
            f"yet — API approval pending). <b>WSB score</b> is Tradestie's published "
            f"reading of r/wallstreetbets — their collection, their sentiment model. "
            f"When the two disagree strongly, the news and the WSB crowd are saying "
            f"different things about the same stock — or one of the models is "
            f"misreading. Either way, worth a click.</p>"
            f"<div class=card><table><tr><th>stock</th><th class=num>mentions</th>"
            f"<th class=num>our score</th><th class=num>WSB score</th>"
            f"<th class=num>WSB rank</th><th></th></tr>{''.join(trs)}</table></div>")


def _source_health_html(conn) -> str:
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    rows = []

    def _row(name, last_iso, count_24h, expect_hours):
        if last_iso:
            last_dt = datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            age_h = (now - last_dt).total_seconds() / 3600
            if age_h <= expect_hours:
                badge = "<span class='badge b-good'>✓ fresh</span>"
            elif age_h <= expect_hours * 4:
                badge = "<span class='badge b-warn'>⚠ lagging</span>"
            else:
                badge = "<span class='badge b-crit'>✗ stale</span>"
            last_txt = _sthlm(last_iso)
        else:
            badge, last_txt = "<span class='badge b-muted'>never</span>", "—"
        rows.append(f"<tr><td>{name}</td><td>{last_txt}</td>"
                    f"<td class=num>{count_24h}</td><td>{badge}</td></tr>")

    cutoff = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    for source, label in (("news", "News (Alpaca/Benzinga)",),
                          ("reddit_post", "Reddit posts"), ("reddit_comment", "Reddit comments")):
        r = conn.execute(
            "SELECT MAX(fetched_utc) m, SUM(fetched_utc >= ?) c FROM raw_documents"
            " WHERE source = ?", (cutoff, source)).fetchone()
        if source.startswith("reddit") and not r["m"]:
            continue  # awaiting API approval; don't render a scary 'never'
        _row(label, r["m"], r["c"] or 0, expect_hours=1)
    for provider, label, expect in (("apewisdom", "ApeWisdom (WSB mentions)", 1),
                            ("tradestie", "Tradestie (WSB sentiment)", 1),
                            ("adanos", "Adanos (multi-subreddit, daily)", 26)):
        r = conn.execute(
            "SELECT MAX(fetched_utc) m, COUNT(DISTINCT fetched_utc) c FROM external_sentiment"
            " WHERE provider = ? AND fetched_utc >= ?", (provider, cutoff)).fetchone()
        last = conn.execute("SELECT MAX(fetched_utc) m FROM external_sentiment"
                            " WHERE provider = ?", (provider,)).fetchone()["m"]
        _row(label, last, r["c"] or 0, expect_hours=expect)
    return (f"<details><summary>Data-source health</summary><div class=card>"
            f"<table><tr><th>source</th><th>last successful fetch (Stockholm)</th>"
            f"<th class=num>items last 24h</th><th>status</th></tr>{''.join(rows)}</table>"
            f"<p class=explain>“fresh” = fetched within the last hour (collection "
            f"runs every 20 min). A stale source means the pipeline runs but that "
            f"feed is returning nothing — the failure emails can't see this.</p>"
            f"</div></details>")


def _research_html(conn, summary_lines) -> str:
    results = ic.evaluate_all(conn)
    html = ["<h2>Is the sentiment signal actually predictive?</h2>",
            "<p class=lede>This is the real experiment. Each day we record how "
            "positive the chatter is about every stock — including stocks nobody "
            "mentions, as a control group — then later check whether high-sentiment "
            "stocks went on to beat low-sentiment ones. Trading profits alone can't "
            "answer this; this table can, given enough days.</p>"]
    if not results:
        html.append("<div class=card><p class=explain>⏳ Waiting for the first "
                    "forward returns — the first verdicts appear a few trading days "
                    "after data collection began, and firm up over ~2–3 months. "
                    "This section fills itself in; nothing needs to be done.</p></div>")
        summary_lines.append("signal metrics: waiting on forward returns")
        return "\n".join(html)

    html.append("<div class=card><table><tr><th>signal</th><th>verdict so far</th></tr>")
    for sig, horizons in results.items():
        r1 = horizons.get(1)
        cls, text = _verdict(r1)
        html.append(f"<tr><td>{SIGNAL_NAMES.get(sig, sig)}</td>"
                    f"<td><span class='badge {cls}'>{text}</span></td></tr>")
        if r1 and r1.n_days:
            summary_lines.append(
                f"{sig}: 1d IC={_fmt(r1.mean_ic)} (t={_fmt(r1.t_stat, digits=2)}) "
                f"resid={_fmt(r1.resid_mean_ic)} (t={_fmt(r1.resid_t_stat, digits=2)}) "
                f"n={r1.n_days}d")
    html.append("</table><p class=explain>“Verdict” is based on the "
                "signal <i>after</i> removing what recent price moves already "
                "explain — people often post about stocks <i>because</i> they "
                "moved, and that must not count as prediction.</p></div>")

    html.append("<details><summary>Full statistics (Signal quality — for the curious)"
                "</summary><div class=card>")
    for sig, horizons in results.items():
        html.append(f"<h3 style='font-size:1rem'>{SIGNAL_NAMES.get(sig, sig)}"
                    f" <span class=muted>({sig})</span></h3>")
        html.append("<table><tr><th>horizon</th><th class=num>days</th>"
                    "<th class=num>mean IC</th><th class=num>t</th>"
                    "<th class=num>resid IC</th><th class=num>resid t</th>"
                    "<th class=num>null %ile</th><th class=num>Q5−Q1</th></tr>")
        for h, r in horizons.items():
            html.append(
                f"<tr><td>{h}d</td><td class=num>{r.n_days}</td>"
                f"<td class=num>{_fmt(r.mean_ic)}</td>"
                f"<td class=num>{_fmt(r.t_stat, digits=2)}</td>"
                f"<td class=num>{_fmt(r.resid_mean_ic)}</td>"
                f"<td class=num>{_fmt(r.resid_t_stat, digits=2)}</td>"
                f"<td class=num>{_fmt(r.null_pctile, digits=1)}</td>"
                f"<td class=num>{_fmt(r.q_spread_eq, pct=True)}</td></tr>")
        html.append("</table>")
    html.append("</div></details>")
    return "\n".join(html)


GLOSSARY = """
<details><summary>Glossary — what the terms mean</summary><div class=card><dl class=gloss>
<dt>Paper trading</dt><dd>Trading with simulated money against real market prices.
No real money is involved anywhere in this system.</dd>
<dt>Sentiment score</dt><dd>How positive or negative the collected text about a
stock reads, from −1 (very negative) to +1 (very positive).</dd>
<dt>Mentions</dt><dd>How many collected posts/articles referred to the stock in
the last 24 hours.</dd>
<dt>Safety rail</dt><dd>A hard-coded limit (position size, liquidity, daily loss…)
that can refuse a trade no matter what the strategy wants.</dd>
<dt>Kill switch</dt><dd>A manual/automatic full stop: while engaged, the bot
places no orders at all.</dd>
<dt>IC (information coefficient)</dt><dd>Rank correlation between the signal one
day and returns the next: +1 = perfect prediction, 0 = useless, −1 = perfectly
backwards. Real trading signals rarely exceed ~0.05.</dd>
<dt>Residualized (resid)</dt><dd>The signal after subtracting what recent price
action already explains — the honest version of the test.</dd>
<dt>t-statistic</dt><dd>How many standard errors the average sits from zero.
Rule of thumb: |t| &gt; 2 starts to be believable; below that is noise.</dd>
<dt>Null %ile</dt><dd>Where the real result ranks among 1000 shuffled fakes.
&gt;97.5 means the real signal beats almost all random shuffles.</dd>
<dt>Q5−Q1</dt><dd>Average next-day return of the most-positive fifth of stocks
minus the most-negative fifth.</dd>
</dl></div></details>"""


def build(settings: Settings, conn: sqlite3.Connection,
          out_path: str | Path = "data/www/index.html") -> str:
    from bottski.store import db as _db

    st = _status(conn)
    _load_uni_meta(settings)
    killed = _db.kill_switch_engaged(conn, settings.kill_switch_file)
    now = datetime.now(STHLM).strftime("%Y-%m-%d %H:%M")
    summary_lines = [f"docs={st['docs']} panel_days={st['panel_days']}"
                     f" fwd_filled={st['fwd_filled']}"]

    html = [f"<title>bottski</title><meta http-equiv='refresh' content='300'>",
            "<meta name='viewport' content='width=device-width, initial-scale=1'>",
            f"<style>{CSS}</style><main>",
            "<h1>bottski <span class='badge b-muted'>PAPER — simulated money</span></h1>",
            f"<p class=sub>An experiment: does online chatter predict stock moves? "
            f"· updated {now} (Stockholm) · refreshes itself every 5 min</p>"]
    if killed:
        html.append("<div class='card pill-kill'><b>⛔ Kill switch engaged</b> "
                    "<span class=explain>— all trading stopped until manually resumed "
                    "(<code>bottski resume</code> on the server).</span></div>")
    html.append(_tiles(st, killed))

    html.append("<details><summary>What am I looking at?</summary><div class=card>"
                "<p class=explain>This bot reads financial news and social-media "
                "chatter around the clock, scores how positive each stock's chatter "
                "is, and once a day buys up to three stocks people are unusually "
                "positive about — using <b>simulated money only</b>. The goal isn't "
                "profit: it's to find out, with honest statistics, whether the "
                "chatter predicts anything at all. Expect months, not days, for an "
                "answer.</p></div></details>")

    html.append("<h2>Account value</h2>"
                "<p class=lede>Simulated account, one snapshot per trading day.</p>")
    html.append(_equity_chart(conn))

    html.append(f"<h2>What the bot owns</h2>")
    html.append(_positions_html(st))

    html.append(_blacklist_html(settings))

    day = st["decision_day"] or "today"
    html.append(f"<h2>Decisions — {day}</h2>")
    html.append(_decisions_html(st, settings, conn))
    html.append(_fills_html(st))

    html.append(_chatter_html(conn))

    html.append(_research_html(conn, summary_lines))

    html.append("<h2>Data collection</h2>")
    src = dict(conn.execute(
        "SELECT source, COUNT(*) FROM raw_documents GROUP BY source").fetchall())
    ext = conn.execute(
        "SELECT COUNT(DISTINCT fetched_utc) c FROM external_sentiment").fetchone()["c"]
    html.append(
        f"<div class=card><p class=explain>Every 20 minutes the bot collects "
        f"financial news and social sentiment. So far: "
        f"<b>{st['docs']:,}</b> documents ({', '.join(f'{k}: {v:,}' for k, v in src.items())}), "
        f"<b>{ext:,}</b> sentiment-provider snapshots, and <b>{st['panel_days']}</b> "
        f"daily observation snapshots across ~220 stocks. "
        f"{st['fwd_filled']:,} observations have their follow-up returns filled in. "
        f"This dataset is the project's real asset — it grows whether or not the "
        f"bot trades.</p></div>")

    html.append(_source_health_html(conn))
    html.append(GLOSSARY)
    html.append("</main>")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(html))
    logger.info("report written to %s", out)
    return "\n".join(summary_lines)
