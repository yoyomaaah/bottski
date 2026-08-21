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


def build(settings: Settings, conn: sqlite3.Connection,
          out_path: str | Path = "data/report.html") -> str:
    status = _data_status(conn)
    results = ic.evaluate_all(conn)
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")

    html = [f"<title>bottski report</title><style>{CSS}</style>",
            f"<h1>bottski — research report</h1><p class=muted>generated {now} UTC · paper mode</p>",
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

    summary_lines = [f"docs={status['documents']} panel_days={status['panel_days']}"
                     f" fwd_filled={status['rows_with_fwd_1d']}"]

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
