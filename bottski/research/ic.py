"""Signal evaluation: does any sentiment signal predict forward returns?

Panel in, statistics out. Everything is cross-sectional per day, then
aggregated across days — so the sample unit is a trading day, not a row.

The headline number is the RESIDUALIZED IC: sentiment regressed (per day,
cross-sectionally) on trailing returns and log dollar volume, then the
residual rank-correlated with forward returns. People post AFTER moves; a
signal that dies under residualization is momentum wearing a costume.

The null test shuffles symbols within each day and recomputes — it catches
accidental lookahead, the failure mode that produces beautiful wrong answers.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MIN_CROSS_SECTION = 10   # skip days with fewer scored names than this
NULL_DRAWS = 1000
HORIZONS = (1, 3, 5, 10)

# signal column -> minimum row filter
SIGNALS = {
    "score_mean": "n_mentions >= 2",
    "news_score_mean": "n_news >= 2",
    # NaN != NaN — pandas-query idiom for "not null"
    "ext_sentiment_score": "ext_sentiment_score == ext_sentiment_score",
    "mention_velocity": "mention_velocity == mention_velocity",
}
CONTROLS = ["ret_1d", "ret_5d", "log_dollar_volume"]


def load_panel(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM observations", conn)
    with np.errstate(divide="ignore", invalid="ignore"):
        df["log_dollar_volume"] = np.log(df["dollar_volume_20d"])
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    return df


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    if np.std(ra) == 0 or np.std(rb) == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def residualize_day(day: pd.DataFrame, signal: str) -> np.ndarray | None:
    """Cross-sectional OLS residual of signal on controls, for one day."""
    cols = [signal] + CONTROLS
    d = day.dropna(subset=cols)
    if len(d) < MIN_CROSS_SECTION:
        return None
    X = np.column_stack([np.ones(len(d))] + [d[c].to_numpy() for c in CONTROLS])
    y = d[signal].to_numpy()
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    out = pd.Series(np.nan, index=day.index)
    out.loc[d.index] = resid
    return out.to_numpy()


@dataclass
class HorizonResult:
    horizon: int
    n_days: int = 0
    mean_ic: float = np.nan
    t_stat: float = np.nan
    resid_mean_ic: float = np.nan
    resid_t_stat: float = np.nan
    null_pctile: float = np.nan       # where real mean IC sits in shuffled dist
    q_spread_eq: float = np.nan       # top-bottom quintile fwd ret, equal weight
    daily_ic: pd.Series = field(default_factory=pd.Series)


def _daily_ics(df: pd.DataFrame, signal_col: str, fwd_col: str) -> pd.Series:
    out = {}
    for day, d in df.groupby("obs_date"):
        d = d.dropna(subset=[signal_col, fwd_col])
        if len(d) < MIN_CROSS_SECTION:
            continue
        out[day] = spearman(d[signal_col].to_numpy(), d[fwd_col].to_numpy())
    return pd.Series(out, dtype=float).dropna()


def _t_stat(ics: pd.Series) -> float:
    if len(ics) < 2 or ics.std(ddof=1) == 0:
        return np.nan
    return float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics))))


def _null_pctile(df: pd.DataFrame, signal_col: str, fwd_col: str,
                 real_mean: float, rng: np.random.Generator) -> float:
    """Shuffle signal within each day, recompute mean IC, NULL_DRAWS times."""
    days = []
    for _, d in df.groupby("obs_date"):
        d = d.dropna(subset=[signal_col, fwd_col])
        if len(d) >= MIN_CROSS_SECTION:
            days.append((d[signal_col].to_numpy(), d[fwd_col].to_numpy()))
    if not days or np.isnan(real_mean):
        return np.nan
    null_means = np.empty(NULL_DRAWS)
    for k in range(NULL_DRAWS):
        ics = [spearman(rng.permutation(sig), fwd) for sig, fwd in days]
        null_means[k] = np.nanmean(ics)
    return float((null_means < real_mean).mean() * 100)


def evaluate_signal(df: pd.DataFrame, signal: str, seed: int = 42) -> dict[int, HorizonResult]:
    """Evaluate one signal across horizons. df must be the full panel."""
    rng = np.random.default_rng(seed)
    sub = df.query(SIGNALS[signal]) if SIGNALS.get(signal) else df
    sub = sub.copy()

    # residualized signal column, built day by day
    resid_col = f"_resid_{signal}"
    parts = []
    for _, d in sub.groupby("obs_date"):
        r = residualize_day(d, signal)
        parts.append(pd.Series(r if r is not None else np.nan, index=d.index))
    sub[resid_col] = pd.concat(parts) if parts else np.nan

    results: dict[int, HorizonResult] = {}
    for h in HORIZONS:
        fwd = f"fwd_ret_{h}d"
        res = HorizonResult(horizon=h)
        ics = _daily_ics(sub, signal, fwd)
        res.daily_ic = ics
        res.n_days = len(ics)
        if len(ics):
            res.mean_ic = float(ics.mean())
            res.t_stat = _t_stat(ics)
            res.null_pctile = _null_pctile(sub, signal, fwd, res.mean_ic, rng)
            rics = _daily_ics(sub, resid_col, fwd)
            if len(rics):
                res.resid_mean_ic = float(rics.mean())
                res.resid_t_stat = _t_stat(rics)
            # quintile spread, averaged across days
            spreads = []
            for _, d in sub.groupby("obs_date"):
                d = d.dropna(subset=[signal, fwd])
                if len(d) < MIN_CROSS_SECTION:
                    continue
                try:
                    q = pd.qcut(d[signal].rank(method="first"), 5, labels=False)
                except ValueError:
                    continue
                spreads.append(d[fwd][q == 4].mean() - d[fwd][q == 0].mean())
            if spreads:
                res.q_spread_eq = float(np.mean(spreads))
        results[h] = res
    return results


def evaluate_all(conn: sqlite3.Connection) -> dict[str, dict[int, HorizonResult]]:
    df = load_panel(conn)
    if df.empty or df["fwd_ret_1d"].notna().sum() == 0:
        return {}
    return {sig: evaluate_signal(df, sig) for sig in SIGNALS}
