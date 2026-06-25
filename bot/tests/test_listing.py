"""Tests for the new-listing momentum harness.

Asserts the mechanics that make the result trustworthy — decaying spread, costs
reducing returns, fat-tailed long side (mean > median), and that the real-CSV
loader produces events the backtest accepts unchanged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.listing import (
    ListingModel, make_dataset, make_event, backtest_side,
    load_event_csv, load_dataset_csv,
)


def test_spread_decays_from_listing():
    ev = make_event(ListingModel(), np.random.default_rng(0))
    assert ev["half_spread"].iloc[0] > ev["half_spread"].iloc[-1]
    assert ev["half_spread"].iloc[0] > 0.005  # wide at listing (>50 bps)
    assert ev["half_spread"].iloc[-1] < 0.002  # narrow later


def test_costs_reduce_returns():
    events = make_dataset(200, ListingModel(), seed=1)
    cheap = backtest_side(events, "long", 5, 30, fee=0.0, slippage=0.0)
    pricey = backtest_side(events, "long", 5, 30, fee=0.0010, slippage=0.0005)
    assert pricey["mean_%"] < cheap["mean_%"]


def test_long_side_is_fat_tailed():
    # The pump is a lottery: a few big winners pull the mean above the median.
    events = make_dataset(400, ListingModel(), seed=2)
    s = backtest_side(events, "long", 1, 15)
    assert s["mean_%"] > s["median_%"]


def test_real_csv_roundtrips_into_backtest(tmp_path):
    # Write a tiny OHLC CSV like fetch_listings would, and confirm it loads and
    # runs through the same backtest path.
    idx = np.arange(60)
    close = 1.0 + 0.3 * np.exp(-idx / 10) * (idx / 5)  # a little hump
    df = pd.DataFrame({
        "open": close, "high": close * 1.02, "low": close * 0.98, "close": close,
    })
    p = tmp_path / "NEWUSDT.csv"
    df.to_csv(p, index=False)

    ev = load_event_csv(str(p))
    assert list(ev.columns) == ["price", "half_spread"]
    assert (ev["half_spread"] >= 0.0005).all()  # floored

    events = load_dataset_csv(str(tmp_path))
    s = backtest_side(events, "long", 1, 15)
    assert s["events"] == 1 and "mean_%" in s
