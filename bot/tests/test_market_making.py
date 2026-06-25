"""Tests for the Avellaneda-Stoikov market-making harness.

Pins the mechanics that make the conclusion trustworthy: inventory-skewed
quotes, a real spread income under benign flow, monotonic decay under adverse
selection, and the pro-beats-retail separation that is the whole point.
"""

from __future__ import annotations

from bot.market_making import MMParams, as_quotes, run_montecarlo


def test_quotes_skew_to_offload_inventory():
    p = MMParams()
    # Flat inventory: quotes are symmetric around mid.
    b0, a0, _ = as_quotes(100.0, 0, 1.0, p)
    assert abs((100.0 - b0) - (a0 - 100.0)) < 1e-9
    # Long inventory: reservation price drops, so the ASK moves closer to mid
    # (sell faster) and the BID moves away (buy slower) — offloading.
    bL, aL, _ = as_quotes(100.0, 10, 1.0, p)
    assert (aL - 100.0) < (a0 - 100.0)
    assert (100.0 - bL) > (100.0 - b0)


def test_benign_flow_earns_the_spread():
    p = MMParams(toxicity=0.0)
    r = run_montecarlo(p, n_episodes=100)
    assert r["mean_pnl_bps"] > 0
    assert r["pnl_per_fill_bps"] > 0  # you keep part of the spread per fill


def test_adverse_selection_monotonically_hurts():
    base = run_montecarlo(MMParams(toxicity=0.5, adverse=0.02), n_episodes=120)
    worse = run_montecarlo(MMParams(toxicity=0.5, adverse=0.15), n_episodes=120)
    assert worse["pnl_per_fill_bps"] < base["pnl_per_fill_bps"]
    assert worse["pnl_per_fill_bps"] < 0  # heavy pick-off flips it negative


def test_pro_keeps_spread_retail_bleeds():
    pro = run_montecarlo(MMParams(fee=-0.0001, toxicity=0.3, adverse=0.02), n_episodes=150)
    retail = run_montecarlo(MMParams(fee=0.0001, toxicity=0.6, adverse=0.12), n_episodes=150)
    assert pro["pnl_per_fill_bps"] > 0
    assert retail["pnl_per_fill_bps"] < 0
    assert pro["pct_profitable"] > retail["pct_profitable"]
