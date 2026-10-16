"""Synthetic leader/laggard generator with a *known* lead-lag.

This is the harness's own test instrument. Because we construct the laggard to
follow the leader by an exact lag, we know the ground truth, so we can:

  1. confirm the lag detector recovers it, and
  2. demonstrate the central thesis directly — when injected latency exceeds
     the lag, the edge must collapse. A backtester that still shows profit in
     that regime has a lookahead bug.

No network required, fully deterministic given a seed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_lead_lag_series(
    n: int = 20_000,
    lag_bars: int = 5,
    leader_vol: float = 0.0008,
    follow_strength: float = 0.9,
    laggard_noise: float = 0.0004,
    seed: int = 7,
    start_price: float = 100.0,
) -> pd.DataFrame:
    """Return a DataFrame with columns ``leader`` and ``laggard`` (prices).

    The laggard's log-return at time ``t`` is ``follow_strength`` times the
    leader's log-return at ``t - lag_bars`` plus idiosyncratic noise. So the
    laggard genuinely *lags* the leader by ``lag_bars`` — an edge exists, but
    only for someone who can act within that window.

    Args:
        n: number of bars.
        lag_bars: the true lead-lag, in bars.
        leader_vol: per-bar stdev of the leader's log-returns.
        follow_strength: how much of the leader's lagged return the laggard
            inherits (1.0 = full pass-through).
        laggard_noise: per-bar stdev of the laggard's own noise.
        seed: RNG seed for reproducibility.
        start_price: starting price for both series.
    """
    if lag_bars < 0:
        raise ValueError("lag_bars must be >= 0")
    rng = np.random.default_rng(seed)

    leader_ret = rng.normal(0.0, leader_vol, size=n)
    # Laggard inherits the leader's return from `lag_bars` ago.
    lagged = np.zeros(n)
    if lag_bars < n:
        lagged[lag_bars:] = leader_ret[:-lag_bars] if lag_bars > 0 else leader_ret
    laggard_ret = follow_strength * lagged + rng.normal(0.0, laggard_noise, size=n)

    leader_price = start_price * np.exp(np.cumsum(leader_ret))
    laggard_price = start_price * np.exp(np.cumsum(laggard_ret))

    return pd.DataFrame({"leader": leader_price, "laggard": laggard_price})
