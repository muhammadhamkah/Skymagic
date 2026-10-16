"""Tests for the generic strategy evaluator.

Proves it catches the two failure modes that fooled us earlier: a lookahead
(future-peeking) signal, and a real-but-honest signal that the gauntlet passes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.config import CostModel
from bot.evaluate import evaluate_strategy, check_lookahead
from bot.feeds import make_lead_lag_series


def _df():
    df = make_lead_lag_series(n=20_000, lag_bars=5, seed=3)
    df["price"] = df["laggard"]
    return df


def causal_signal(df):
    ret = np.log(df["leader"]).diff(1)        # uses only past leader data
    s = pd.Series(0, index=df.index, dtype=int)
    s[ret > 0] = 1
    s[ret < 0] = -1
    return s


def cheating_signal(df):
    # Peeks at the NEXT bar of the laggard — the classic lookahead bug.
    fut = np.log(df["laggard"]).shift(-1) - np.log(df["laggard"])
    s = pd.Series(0, index=df.index, dtype=int)
    s[fut > 0] = 1
    s[fut < 0] = -1
    return s


def test_detects_lookahead():
    df = _df()
    assert check_lookahead(df, causal_signal) is True
    assert check_lookahead(df, cheating_signal) is False


def test_cheating_signal_is_flagged_not_celebrated():
    df = _df()
    res = evaluate_strategy(df, cheating_signal, hold=5)
    assert not res.lookahead_safe
    assert "LOOKAHEAD" in res.verdict


def test_zero_cost_causal_signal_runs_and_reports():
    df = _df()
    cheap = CostModel(taker_fee=0.0, half_spread=0.0, slippage=0.0)
    res = evaluate_strategy(df, causal_signal, hold=5, cost=cheap)
    assert res.lookahead_safe
    assert set(["latency", "mean_bps", "median_bps"]).issubset(res.table.columns)


def test_high_cost_kills_thin_edge():
    df = _df()
    pricey = CostModel(taker_fee=0.005, half_spread=0.002, slippage=0.001)  # huge
    res = evaluate_strategy(df, causal_signal, hold=5, cost=pricey)
    assert "FAILS" in res.verdict or "NO EDGE" in res.verdict
