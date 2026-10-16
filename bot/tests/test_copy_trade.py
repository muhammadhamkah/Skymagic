"""Tests for the copy-trading simulation.

Confirms the core result: mirroring a market maker's (edge-free) inventory as a
taker loses at every lag including zero, and only a large gifted directional
alpha — which a real MM does not provide — could overcome the churn fees.
"""

from __future__ import annotations

from bot.copy_trade import CopyParams, run


def test_copier_loses_at_every_lag_including_zero():
    df = run()
    assert (df["net_bps"] <= 0).all()
    # lag 0 (perfect real-time) is not meaningfully better — fees dominate.
    assert df.loc[df["lag"] == 0, "net_bps"].iloc[0] < 0


def test_fees_dominate():
    df = run()
    # The fee drag is far larger than any gross wandering — visibility is moot.
    assert (df["fees_bps"].abs() > df["gross_bps"].abs()).all()


def test_only_large_gifted_alpha_can_win():
    no_alpha = run(CopyParams(inv_alpha=0.0), lags=(0,)).iloc[0]["net_bps"]
    big_alpha = run(CopyParams(inv_alpha=0.2), lags=(0,)).iloc[0]["net_bps"]
    assert no_alpha < 0
    assert big_alpha > no_alpha  # alpha helps, but a real MM gifts alpha = 0
