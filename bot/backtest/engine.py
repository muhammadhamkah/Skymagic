"""Lookahead-safe, cost-aware lead-lag backtest engine.

Design choices that make the result trustworthy rather than flattering:

  * **Causal signal, delayed fill.** The signal at bar ``t`` uses only data up
    to ``t``. The entry fill cannot occur before ``t + latency_bars``. This is
    the single guard that separates a real edge from the classic lead-lag
    lookahead mirage (filling at the price the signal just predicted).

  * **Costs are crossed adversely.** Every entry and exit pays the taker fee,
    crosses the half-spread, and eats slippage — applied in the direction that
    hurts. The move must beat the full round-trip cost to profit.

  * **Non-overlapping, all-in trades.** A 100 USDT account holds one position
    at a time; we don't open a new trade until the current one exits. No
    fictional parallel capital.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import BacktestConfig
from ..signals.lead_lag import lead_lag_signal


@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    direction: int           # +1 long laggard, -1 short laggard
    entry_price: float
    exit_price: float
    gross_return: float      # before costs, fraction of notional
    cost: float              # round-trip cost, fraction of notional
    net_return: float        # gross_return - cost
    pnl_usdt: float


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    config: BacktestConfig | None = None

    @property
    def n_trades(self) -> int:
        return len(self.trades)


def backtest(df: pd.DataFrame, cfg: BacktestConfig) -> BacktestResult:
    """Run the lead-lag backtest over a leader/laggard price frame.

    Args:
        df: DataFrame with ``leader`` and ``laggard`` price columns.
        cfg: full backtest configuration.

    Returns:
        A :class:`BacktestResult` with the trade list and equity curve.
    """
    sig_cfg = cfg.signal
    signal = lead_lag_signal(df, sig_cfg.lookback_bars, sig_cfg.entry_threshold)
    laggard = df["laggard"].to_numpy()
    sig = signal.to_numpy()
    n = len(df)

    latency = cfg.execution.latency_bars
    hold = sig_cfg.holding_bars
    rt_cost = cfg.cost.round_trip_cost()

    capital = cfg.capital_usdt
    result = BacktestResult(config=cfg)
    result.equity_curve.append(capital)

    t = sig_cfg.lookback_bars  # first bar with a valid trailing return
    while t < n:
        direction = int(sig[t])
        if direction == 0:
            t += 1
            continue

        entry_idx = t + latency
        exit_idx = entry_idx + hold
        if exit_idx >= n:
            break  # not enough future bars to complete the trade

        entry_price = float(laggard[entry_idx])
        exit_price = float(laggard[exit_idx])
        gross = direction * (exit_price / entry_price - 1.0)
        net = gross - rt_cost

        notional = capital * cfg.position_fraction
        pnl = notional * net
        capital += pnl

        result.trades.append(
            Trade(
                entry_idx=entry_idx,
                exit_idx=exit_idx,
                direction=direction,
                entry_price=entry_price,
                exit_price=exit_price,
                gross_return=gross,
                cost=rt_cost,
                net_return=net,
                pnl_usdt=pnl,
            )
        )
        result.equity_curve.append(capital)

        # Non-overlapping: resume scanning only after this trade has exited.
        t = exit_idx + 1

    return result


def latency_sweep(df: pd.DataFrame, cfg: BacktestConfig, latencies_s: list[float]) -> pd.DataFrame:
    """Re-run the backtest across a range of latencies — the money chart.

    Produces a table showing how net PnL behaves as execution latency grows.
    If the edge is real and you are fast enough, low-latency rows are positive;
    as latency crosses the true lag, PnL collapses. A backtest that stays
    positive at absurd latency is telling you it has a lookahead bug.
    """
    from .metrics import summarize

    rows = []
    for lat in latencies_s:
        c = _with_latency(cfg, lat)
        res = backtest(df, c)
        s = summarize(res)
        rows.append(
            {
                "latency_s": lat,
                "latency_bars": c.execution.latency_bars,
                "trades": s["trades"],
                "net_return_pct": s["total_return_pct"],
                "gross_return_pct": s["gross_return_pct"],
                "win_rate_pct": s["win_rate_pct"],
                "avg_net_bps": s["avg_net_bps"],
                "sharpe": s["sharpe"],
                "final_equity": s["final_equity"],
            }
        )
    return pd.DataFrame(rows)


def _with_latency(cfg: BacktestConfig, latency_s: float) -> BacktestConfig:
    import copy

    c = copy.deepcopy(cfg)
    c.execution.latency_seconds = latency_s
    return c
