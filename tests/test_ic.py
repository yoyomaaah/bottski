"""Harness validation on synthetic panels with known ground truth."""

import numpy as np
import pandas as pd

from bottski.research import ic


def make_panel(n_days=30, n_syms=40, seed=7):
    """Synthetic panel. fwd_ret_1d = signal*0.01 + noise; momo_signal = ret_1d."""
    rng = np.random.default_rng(seed)
    rows = []
    dates = pd.bdate_range("2026-01-05", periods=n_days).strftime("%Y-%m-%d")
    for d in dates:
        sig = rng.normal(size=n_syms)
        ret1 = rng.normal(0, 0.02, size=n_syms)
        ret5 = rng.normal(0, 0.04, size=n_syms)
        dv = np.abs(rng.normal(1e7, 3e6, size=n_syms)) + 1e5
        fwd = sig * 0.01 + rng.normal(0, 0.005, size=n_syms)
        for i in range(n_syms):
            rows.append({
                "obs_date": d, "symbol": f"S{i:03d}",
                "n_mentions": 5, "n_news": 5,
                "score_mean": sig[i],                # true signal
                "news_score_mean": ret1[i],          # pure momentum in disguise
                "ext_sentiment_score": rng.normal(), # pure noise
                "mention_velocity": np.nan,
                "ret_1d": ret1[i], "ret_5d": ret5[i],
                "dollar_volume_20d": dv[i],
                "log_dollar_volume": np.log(dv[i]),
                "fwd_ret_1d": fwd[i],
                "fwd_ret_3d": fwd[i] * 0.5 + rng.normal(0, 0.01),
                "fwd_ret_5d": rng.normal(0, 0.02),   # signal decayed to nothing
                "fwd_ret_10d": rng.normal(0, 0.03),
            })
    return pd.DataFrame(rows)


PANEL = make_panel()


def test_true_signal_has_strong_ic_and_survives_residualization():
    res = ic.evaluate_signal(PANEL, "score_mean")[1]
    assert res.n_days == 30
    assert res.mean_ic > 0.5
    assert res.t_stat > 10
    assert res.resid_mean_ic > 0.5      # orthogonal to controls -> survives
    assert res.null_pctile > 99         # far outside the shuffle distribution
    assert res.q_spread_eq > 0.01       # top quintile clearly beats bottom


def test_momentum_in_disguise_dies_under_residualization():
    # news_score_mean IS ret_1d; fwd is independent of ret_1d, so raw IC ~0 too,
    # but the key property: residual is ~constant -> resid IC nan/near-zero.
    res = ic.evaluate_signal(PANEL, "news_score_mean")[1]
    assert abs(res.mean_ic) < 0.15
    assert not (res.resid_mean_ic > 0.2)  # nothing left after controls


def test_noise_signal_sits_inside_null_distribution():
    res = ic.evaluate_signal(PANEL, "ext_sentiment_score")[1]
    assert abs(res.mean_ic) < 0.1
    assert 1 < res.null_pctile < 99


def test_decay_curve_shows_horizon_structure():
    res = ic.evaluate_signal(PANEL, "score_mean")
    assert res[1].mean_ic > res[3].mean_ic > abs(res[5].mean_ic) - 0.1
    assert abs(res[10].mean_ic) < 0.15


def test_determinism():
    a = ic.evaluate_signal(PANEL, "score_mean")[1]
    b = ic.evaluate_signal(PANEL, "score_mean")[1]
    assert a.null_pctile == b.null_pctile and a.mean_ic == b.mean_ic


def test_too_small_cross_sections_are_skipped():
    small = PANEL[PANEL["symbol"].isin([f"S{i:03d}" for i in range(5)])]
    res = ic.evaluate_signal(small, "score_mean")[1]
    assert res.n_days == 0


def test_spearman_basics():
    assert ic.spearman(np.array([1, 2, 3]), np.array([10, 20, 30])) == 1.0
    assert ic.spearman(np.array([1, 2, 3]), np.array([30, 20, 10])) == -1.0
