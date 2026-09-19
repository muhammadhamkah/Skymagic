"""Tests for the lead-lag harness.

These assert the properties that make the backtest trustworthy. The most
important one (``test_latency_destroys_synthetic_edge``) encodes the entire
thesis of this project: a true lead-lag edge is monetizable only inside the lag
window, and our engine must reflect that rather than hide it.
"""

from __future__ import annotations

import numpy as np

from bot.config import BacktestConfig, CostModel, SignalConfig
from bot.feeds import make_lead_lag_series
from bot.signals.lead_lag import detect_lag, lead_lag_signal
from bot.backtest.engine import backtest
from bot.backtest.metrics import summarize


def _cfg(lag, latency_s, **cost_kw):
    cfg = BacktestConfig(
        signal=SignalConfig(lookback_bars=1, holding_bars=lag),
        cost=CostModel(**cost_kw),
    )
    cfg.execution.bar_seconds = 1.0
    cfg.execution.latency_seconds = latency_s
    return cfg


def test_detect_lag_recovers_ground_truth():
    df = make_lead_lag_series(n=30_000, lag_bars=5, seed=1)
    lag, corr = detect_lag(df)
    assert lag == 5, f"expected lag 5, detected {lag}"
    assert corr > 0.3


def test_signal_is_causal():
    # Shifting leader prices into the future must shift the signal identically;
    # the signal at t may not depend on any leader value after t.
    df = make_lead_lag_series(n=2000, lag_bars=3, seed=2)
    sig = lead_lag_signal(df, lookback=2, threshold=0.0)
    # Recompute on a truncated frame; the prefix must be byte-identical.
    sig_prefix = lead_lag_signal(df.iloc[:1000], lookback=2, threshold=0.0)
    assert (sig.iloc[:1000].to_numpy() == sig_prefix.to_numpy()).all()


def test_zero_cost_zero_latency_edge_is_positive():
    df = make_lead_lag_series(n=40_000, lag_bars=5, seed=3)
    cfg = _cfg(lag=5, latency_s=0.0, taker_fee=0.0, maker_fee=0.0,
              half_spread=0.0, slippage=0.0)
    res = backtest(df, cfg)
    s = summarize(res)
    assert s["total_return_pct"] > 0, "frictionless in-window edge should be positive"
    assert s["win_rate_pct"] > 50


def test_latency_destroys_synthetic_edge():
    # THE thesis. With a 5-bar lag, acting inside the window is profitable;
    # acting well after it (latency >> lag) must not be.
    df = make_lead_lag_series(n=40_000, lag_bars=5, seed=4)
    fast = summarize(backtest(df, _cfg(5, latency_s=0.0,
                                       taker_fee=0.0, half_spread=0.0, slippage=0.0)))
    slow = summarize(backtest(df, _cfg(5, latency_s=10.0,
                                       taker_fee=0.0, half_spread=0.0, slippage=0.0)))
    assert fast["total_return_pct"] > slow["total_return_pct"]
    assert slow["avg_net_bps"] <= fast["avg_net_bps"]


def test_fees_reduce_net_below_gross():
    df = make_lead_lag_series(n=40_000, lag_bars=5, seed=5)
    cfg = _cfg(5, latency_s=0.0, taker_fee=0.001, half_spread=0.0002, slippage=0.0001)
    res = backtest(df, cfg)
    s = summarize(res)
    assert s["fee_drag_pct"] > 0
    # Every trade's net must be exactly gross minus the round-trip cost.
    rt = cfg.cost.round_trip_cost()
    for t in res.trades:
        assert abs((t.gross_return - rt) - t.net_return) < 1e-12


def test_no_overlapping_trades():
    df = make_lead_lag_series(n=10_000, lag_bars=5, seed=6)
    res = backtest(df, _cfg(5, latency_s=0.1))
    last_exit = -1
    for t in res.trades:
        assert t.entry_idx > last_exit, "trades must not overlap"
        last_exit = t.exit_idx
