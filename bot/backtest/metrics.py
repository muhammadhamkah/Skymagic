"""Performance metrics for a backtest result.

Reports both gross and net so the *fee drag* — the gap that kills small-account
scalping — is explicit rather than hidden inside a single PnL number.
"""

from __future__ import annotations

import numpy as np

from .engine import BacktestResult


def summarize(result: BacktestResult) -> dict:
    """Compute headline metrics for a :class:`BacktestResult`."""
    trades = result.trades
    start = result.config.capital_usdt if result.config else 100.0

    if not trades:
        return {
            "trades": 0,
            "total_return_pct": 0.0,
            "gross_return_pct": 0.0,
            "fee_drag_pct": 0.0,
            "win_rate_pct": 0.0,
            "avg_net_bps": 0.0,
            "avg_gross_bps": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "final_equity": start,
        }

    net = np.array([t.net_return for t in trades])
    gross = np.array([t.gross_return for t in trades])
    equity = np.array(result.equity_curve)

    final = float(equity[-1])
    total_return = (final / start - 1.0) * 100.0
    # Sum of per-trade gross returns, in % of starting capital terms (approx).
    gross_return = float(gross.sum() * 100.0)
    fee_drag = gross_return - total_return

    wins = int((net > 0).sum())
    win_rate = wins / len(trades) * 100.0

    # Per-trade Sharpe (unannualized) — comparable across runs on same data.
    sharpe = float(net.mean() / net.std()) if net.std() > 0 else 0.0

    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    max_dd = float(drawdown.min() * 100.0)

    return {
        "trades": len(trades),
        "total_return_pct": total_return,
        "gross_return_pct": gross_return,
        "fee_drag_pct": fee_drag,
        "win_rate_pct": win_rate,
        "avg_net_bps": float(net.mean() * 1e4),
        "avg_gross_bps": float(gross.mean() * 1e4),
        "sharpe": sharpe,
        "max_drawdown_pct": max_dd,
        "final_equity": final,
    }


def format_summary(summary: dict) -> str:
    """Human-readable one-block summary."""
    return (
        f"  trades            {summary['trades']}\n"
        f"  net return        {summary['total_return_pct']:+.2f}%  "
        f"(final equity {summary['final_equity']:.2f} USDT)\n"
        f"  gross return      {summary['gross_return_pct']:+.2f}%\n"
        f"  fee/cost drag     {summary['fee_drag_pct']:.2f}%\n"
        f"  win rate          {summary['win_rate_pct']:.1f}%\n"
        f"  avg net / trade   {summary['avg_net_bps']:+.2f} bps  "
        f"(gross {summary['avg_gross_bps']:+.2f} bps)\n"
        f"  per-trade Sharpe  {summary['sharpe']:.3f}\n"
        f"  max drawdown      {summary['max_drawdown_pct']:.2f}%"
    )
