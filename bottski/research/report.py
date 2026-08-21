"""Daily report: data status + signal-quality metrics. Writes a self-contained
HTML file and returns a terminal summary. The panel/signal section is kept
conceptually separate from (future) trading P&L: the panel says whether the
SIGNAL works; the trade log will say whether the BOT works."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bottski.config import Settings
from bottski.research import ic

logger = logging.getLogger("bottski.report")

CSS = """
body{font-family:-apple-system,system-ui,sans-serif;max-width:960px;margin:2rem auto;
padding:0 1rem;color:#1a1a2e;background:#fafafa}
h1,h2{color:#16213e} table{border-collapse:collapse;width:100%;margin:1rem 0;background:#fff}
th,td{border:1px solid #ddd;padding:6px 10px;text-align:right;font-variant-numeric:tabular-nums}
th{background:#16213e;color:#fff} td:first-child,th:first-child{text-align:left}
.good{color:#0a7d33;font-weight:600}.bad{color:#c0392b}.muted{color:#888}
.note{background:#fff8e1;border-left:4px solid #f0a500;padding:8px 12px;margin:1rem 0}
"""


def _fmt(x, pct=False, digits=3):
    if x is None or (isinstance(x, float) and (x != x)):
        return "—"
    return f"{x*100:.2f}%" if pct else f"{x:.{digits}f}"


def _data_status(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "documents": q("SELECT COUNT(*) FROM raw_documents"),
        "docs_by_source": dict(conn.execute(
            "SELECT source, COUNT(*) FROM raw_documents GROUP BY source").fetchall()),
        "scored_pairs": q("SELECT COUNT(*) FROM document_sentiment"),
        "panel_rows": q("SELECT COUNT(*) FROM observations"),
        "panel_days": q("SELECT COUNT(DISTINCT obs_date) FROM observations"),
        "rows_with_fwd_1d": q("SELECT COUNT(*) FROM observations WHERE fwd_ret_1d IS NOT NULL"),
        "external_snapshots": q("SELECT COUNT(DISTINCT fetched_utc) FROM external_sentiment"),
        "first_obs": q("SELECT MIN(obs_date) FROM observations"),
        "last_obs": q("SELECT MAX(obs_date) FROM observations"),
    }


def _equity_svg(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT snapshot_utc, equity FROM equity_curve ORDER BY snapshot_utc").fetchall()
    if len(rows) < 2:
        return "<p class=muted>equity curve appears after a few reconciles</p>"
    vals = [r["equity"] for r in rows]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    W, H, PAD = 900, 160, 8
    pts = " ".join(
        f"{PAD + i * (W - 2*PAD) / (len(vals)-1):.1f},"
        f"{H - PAD - (v - lo) * (H - 2*PAD) / span:.1f}"
        for i, v in enumerate(vals))
    color = "#0a7d33" if vals[-1] >= vals[0] else "#c0392b"
    return (f"<svg viewBox='0 0 {W} {H}' style='width:100%;background:#fff;"
            f"border:1px solid #ddd'><polyline points='{pts}' fill='none' "
            f"stroke='{color}' stroke-width='2'/></svg>"
            f"<p class=muted>{rows[0]['snapshot_utc'][:10]} → {rows[-1]['snapshot_utc'][:10]}"
            f" · start {vals[0]:,.0f} · now <b>{vals[-1]:,.0f}</b>"
            f" ({(vals[-1]/vals[0]-1)*100:+.2f}%)</p>")


def _trading_html(conn: sqlite3.Connection, settings: Settings) -> list[str]:
    from bottski.store import db as _db

    html = ["<h2>Trading (paper)</h2>"]
    killed = _db.kill_switch_engaged(conn, settings.kill_switch_file)
    if killed:
        html.append("<div class=note><b>KILL SWITCH ENGAGED</b> — no new orders "
                    "until `bottski resume`.</div>")
    html.append(_equity_svg(conn))

    latest = conn.execute(
        "SELECT MAX(snapshot_utc) m FROM positions_snapshot").fetchone()["m"]
    html.append("<h3>Open positions</h3>")
    pos = conn.execute(
        "SELECT * FROM positions_snapshot WHERE snapshot_utc = ? ORDER BY symbol",
        (latest,)).fetchall() if latest else []
    if pos:
        html.append("<table><tr><th>symbol</th><th>qty</th><th>entry</th>"
                    "<th>value</th><th>unrealized P&L</th></tr>")
        for r in pos:
            cls = "good" if (r["unrealized_pl"] or 0) >= 0 else "bad"
            html.append(f"<tr><td>{r['symbol']}</td><td>{r['qty']:g}</td>"
                        f"<td>{_fmt(r['avg_entry_price'], digits=2)}</td>"
                        f"<td>{r['market_value']:,.0f}</td>"
                        f"<td class={cls}>{r['unrealized_pl']:+,.0f}</td></tr>")
        html.append(f"</table><p class=muted>as of {latest} UTC</p>")
    else:
        html.append("<p class=muted>flat — no open positions</p>")

    day = conn.execute(
        "SELECT MAX(date(decision_utc)) m FROM decisions WHERE mode != 'dry-run'"
    ).fetchone()["m"]
    html.append(f"<h3>Decisions — {day or 'none yet'}</h3>")
    if day:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE date(decision_utc) = ? AND mode != 'dry-run'"
            " ORDER BY CASE action WHEN 'sell' THEN 0 WHEN 'buy' THEN 1 ELSE 2 END,"
            " blocked_by IS NOT NULL, symbol", (day,)).fetchall()
        acted = [r for r in rows if r["action"] != "hold" or r["blocked_by"]]
        holds = len(rows) - len(acted)
        if acted:
            html.append("<table><tr><th>symbol</th><th>action</th><th>notional</th>"
                        "<th>reason</th><th>score</th><th>mentions</th><th>blocked by</th></tr>")
            import json as _json
            for r in acted:
                panel = _json.loads(r["inputs_json"]).get("panel", {})
                act_cls = "bad" if r["blocked_by"] else ("good" if r["action"] == "buy" else "")
                html.append(
                    f"<tr><td>{r['symbol']}</td><td class='{act_cls}'>{r['action']}</td>"
                    f"<td>{_fmt(r['target_notional'], digits=0)}</td><td>{r['reason_code']}</td>"
                    f"<td>{_fmt(panel.get('score_mean'))}</td>"
                    f"<td>{panel.get('n_mentions') if panel.get('n_mentions') is not None else '—'}</td>"
                    f"<td>{r['blocked_by'] or '—'}</td></tr>")
            html.append("</table>")
        html.append(f"<p class=muted>{holds} hold decisions recorded (full detail in db)</p>")

    fills = conn.execute(
        "SELECT f.filled_utc, o.symbol, o.side, f.qty, f.price FROM fills f"
        " JOIN orders o ON o.id = f.order_id ORDER BY f.filled_utc DESC LIMIT 15"
    ).fetchall()
    html.append("<h3>Recent fills</h3>")
    if fills:
        html.append("<table><tr><th>time (UTC)</th><th>symbol</th><th>side</th>"
                    "<th>qty</th><th>price</th></tr>")
        for r in fills:
            html.append(f"<tr><td>{r['filled_utc'][:16]}</td><td>{r['symbol']}</td>"
                        f"<td>{r['side']}</td><td>{r['qty']:g}</td>"
                        f"<td>{_fmt(r['price'], digits=2)}</td></tr>")
        html.append("</table>")
    else:
        html.append("<p class=muted>no fills yet</p>")
    return html


def build(settings: Settings, conn: sqlite3.Connection,
          out_path: str | Path = "data/www/index.html") -> str:
    status = _data_status(conn)
    results = ic.evaluate_all(conn)
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")

    html = [f"<title>bottski</title><meta http-equiv='refresh' content='300'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<style>{CSS}</style>",
            f"<h1>bottski</h1><p class=muted>generated {now} UTC · PAPER mode ·"
            f" auto-refreshes every 5 min</p>",
            "<h2>Data accrual</h2><table><tr><th>metric</th><th>value</th></tr>"]
    src = ", ".join(f"{k}: {v}" for k, v in status["docs_by_source"].items())
    for label, val in [
        ("raw documents", f"{status['documents']} ({src})"),
        ("scored (doc, ticker) pairs", status["scored_pairs"]),
        ("external provider snapshots", status["external_snapshots"]),
        ("panel rows / days", f"{status['panel_rows']} / {status['panel_days']}"),
        ("panel span", f"{status['first_obs']} → {status['last_obs']}"),
        ("rows with 1d forward return", status["rows_with_fwd_1d"]),
    ]:
        html.append(f"<tr><td>{label}</td><td>{val}</td></tr>")
    html.append("</table>")

    html.extend(_trading_html(conn, settings))

    summary_lines = [f"docs={status['documents']} panel_days={status['panel_days']}"
                     f" fwd_filled={status['rows_with_fwd_1d']}"]
    html.append("<h2>Research</h2>")

    if not results:
        html.append("<div class=note>No filled forward returns yet — signal metrics begin "
                    "once backfill-returns has matured panel rows (first fills ~1 trading "
                    "day after the first observe run). The dataset is accruing; this page "
                    "fills itself in.</div>")
        summary_lines.append("signal metrics: waiting on forward returns")
    else:
        html.append("<h2>Signal quality</h2><p>Sample unit is a trading day (cross-sectional "
                    "IC per day, aggregated). <b>resid IC</b> is the headline: signal "
                    "orthogonalized to 1d/5d returns and liquidity. <b>null %ile</b>: where "
                    f"the real mean IC sits among {ic.NULL_DRAWS} within-day shuffles "
                    "(&gt;97.5 or &lt;2.5 is interesting).</p>")
        for sig, horizons in results.items():
            html.append(f"<h3><code>{sig}</code></h3>")
            html.append("<table><tr><th>horizon</th><th>days</th><th>mean IC</th>"
                        "<th>t</th><th>resid IC</th><th>resid t</th>"
                        "<th>null %ile</th><th>Q5−Q1 fwd ret</th></tr>")
            for h, r in horizons.items():
                cls = ""
                if r.resid_t_stat == r.resid_t_stat:
                    cls = "good" if abs(r.resid_t_stat) > 2 else ""
                html.append(
                    f"<tr><td>{h}d</td><td>{r.n_days}</td><td>{_fmt(r.mean_ic)}</td>"
                    f"<td>{_fmt(r.t_stat, digits=2)}</td>"
                    f"<td class='{cls}'>{_fmt(r.resid_mean_ic)}</td>"
                    f"<td class='{cls}'>{_fmt(r.resid_t_stat, digits=2)}</td>"
                    f"<td>{_fmt(r.null_pctile, digits=1)}</td>"
                    f"<td>{_fmt(r.q_spread_eq, pct=True)}</td></tr>")
            html.append("</table>")
            r1 = horizons.get(1)
            if r1 and r1.n_days:
                summary_lines.append(
                    f"{sig}: 1d IC={_fmt(r1.mean_ic)} (t={_fmt(r1.t_stat, digits=2)}) "
                    f"resid={_fmt(r1.resid_mean_ic)} (t={_fmt(r1.resid_t_stat, digits=2)}) "
                    f"n={r1.n_days}d")
        html.append("<div class=note>Read t-stats as directional until n_days is well past "
                    "30; at daily cadence significance accrues slowly by design.</div>")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(html))
    logger.info("report written to %s", out)
    return "\n".join(summary_lines)
