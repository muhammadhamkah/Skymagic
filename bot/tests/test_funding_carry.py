"""Tests for the funding-carry harness.

Pins the honest mechanics: positive-funding regimes pay and bear regimes bleed,
fees reduce the return, cross-margin removes the liquidation tail that isolated
leverage introduces, and the real-funding scorer sums correctly.
"""

from __future__ import annotations

import numpy as np

from bot.funding_carry import CarryParams, run_regime, backtest_real_funding


def test_positive_regime_pays_bear_regime_bleeds():
    bull = run_regime(CarryParams(funding_mean=0.00020))
    bear = run_regime(CarryParams(funding_mean=-0.00005))
    assert bull["mean_apy_%"] > 0 and bull["pct_profitable"] > 90
    assert bear["mean_apy_%"] < 0 and bear["pct_profitable"] < 10


def test_fees_reduce_return():
    cheap = run_regime(CarryParams(roundtrip_cost=0.0005))
    pricey = run_regime(CarryParams(roundtrip_cost=0.0030))
    assert cheap["mean_apy_%"] > pricey["mean_apy_%"]


def test_cross_margin_has_no_liquidations():
    r = run_regime(CarryParams(leverage=5, cross_margined=True))
    assert r["liq_rate_%"] == 0.0


def test_isolated_leverage_introduces_liquidation_tail():
    r1 = run_regime(CarryParams(leverage=1, cross_margined=False))
    r5 = run_regime(CarryParams(leverage=5, cross_margined=False))
    assert r5["liq_rate_%"] > r1["liq_rate_%"]
    assert r5["p05_apy_%"] < r1["p05_apy_%"]  # fatter left tail with leverage


def test_real_funding_scorer():
    # 90 periods (~30 days) of a steady +1 bp/8h, 11 bps round trip.
    f = np.full(90, 0.0001)
    r = backtest_real_funding(f, CarryParams(roundtrip_cost=0.0011))
    # gross = 90 * 1bp = 90 bps = 0.90%; net = 0.90% - 0.11% = 0.79%
    assert abs(r["gross_%"] - 0.90) < 1e-6
    assert abs(r["net_%"] - 0.79) < 1e-6
    assert r["apy_%"] > 0
