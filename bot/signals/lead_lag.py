"""Lead-lag signal: use the leader's recent return to predict the laggard.

Two pieces:

  * ``detect_lag`` — a *diagnostic* (lagged cross-correlation) that estimates
    how many bars the laggard trails the leader. This is descriptive, not a
    trade decision, so it may look across the whole sample.

  * ``lead_lag_signal`` — the *tradable* signal. It is strictly causal: the
    signal at bar ``t`` uses only the leader's return up to and including ``t``.
    The backtest engine then enforces that the corresponding fill cannot occur
    until ``t + latency``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def leader_returns(prices: pd.Series, lookback: int) -> pd.Series:
    """Trailing log-return of the leader over ``lookback`` bars, causal."""
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    return np.log(prices).diff(lookback)


def detect_lag(df: pd.DataFrame, max_lag: int = 30) -> tuple[int, float]:
    """Estimate the laggard's lag behind the leader via cross-correlation.

    Correlates the leader's return at ``t`` with the laggard's return at
    ``t + k`` for ``k`` in ``[0, max_lag]`` and returns the ``k`` with the
    highest correlation. A positive ``k`` means the laggard genuinely follows.

    Returns:
        ``(best_lag, best_corr)``.
    """
    lr = np.log(df["leader"]).diff().dropna()
    gr = np.log(df["laggard"]).diff().dropna()
    n = min(len(lr), len(gr))
    lr = lr.iloc[-n:].to_numpy()
    gr = gr.iloc[-n:].to_numpy()

    best_lag, best_corr = 0, -np.inf
    for k in range(0, max_lag + 1):
        if k >= n:
            break
        a = lr[: n - k]          # leader return at t
        b = gr[k:]               # laggard return at t + k
        if a.std() == 0 or b.std() == 0:
            continue
        corr = float(np.corrcoef(a, b)[0, 1])
        if corr > best_corr:
            best_lag, best_corr = k, corr
    return best_lag, best_corr


def lead_lag_signal(df: pd.DataFrame, lookback: int, threshold: float = 0.0) -> pd.Series:
    """Directional signal in {-1, 0, +1} aligned to each bar ``t`` (causal).

    +1 / -1 when the leader's trailing return over ``lookback`` bars exceeds
    ``threshold`` in magnitude; 0 otherwise. The value at ``t`` depends only on
    leader prices at or before ``t``.
    """
    ret = leader_returns(df["leader"], lookback)
    sig = pd.Series(0, index=df.index, dtype=int)
    sig[ret > threshold] = 1
    sig[ret < -threshold] = -1
    return sig
