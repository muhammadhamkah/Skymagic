"""CLI: run the lead-lag backtest and the latency sweep.

Examples
--------
Offline, fully reproducible (synthetic data with a known 5-bar lag)::

    python -m bot.run_backtest --source synthetic --true-lag 5

Against real Binance data (needs network; caches locally)::

    python -m bot.run_backtest --source binance \
        --leader BTCUSDT --laggard ETHUSDT --interval 1s --limit 5000

The latency sweep is the headline output: it shows net PnL collapsing as
execution latency crosses the true lead-lag — the honest test of whether a
"profitable lead-lag bot" is real or a zero-latency-fill artifact.
"""

from __future__ import annotations

import argparse

import pandas as pd

from .config import BacktestConfig, SignalConfig
from .feeds import make_lead_lag_series, load_pair
from .signals.lead_lag import detect_lag
from .backtest.engine import backtest, latency_sweep
from .backtest.metrics import summarize, format_summary

pd.set_option("display.width", 100)
pd.set_option("display.max_columns", 20)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Lead-lag backtest harness")
    p.add_argument("--source", choices=["synthetic", "binance"], default="synthetic")
    p.add_argument("--leader", default="BTCUSDT")
    p.add_argument("--laggard", default="ETHUSDT")
    p.add_argument("--interval", default="1s")
    p.add_argument("--limit", type=int, default=5000)
    p.add_argument("--true-lag", type=int, default=5, help="synthetic ground-truth lag (bars)")
    p.add_argument("--lookback", type=int, default=3, help="leader-return lookback (bars)")
    p.add_argument("--hold", type=int, default=5, help="holding horizon (bars)")
    p.add_argument("--threshold", type=float, default=0.0, help="entry threshold (fraction)")
    p.add_argument("--bar-seconds", type=float, default=1.0)
    p.add_argument("--latency", type=float, default=0.1, help="execution latency (s)")
    return p


def load_data(args) -> pd.DataFrame:
    if args.source == "synthetic":
        return make_lead_lag_series(n=max(args.limit, 20_000), lag_bars=args.true_lag)
    return load_pair(args.leader, args.laggard, interval=args.interval, limit=args.limit)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    df = load_data(args)

    lag, corr = detect_lag(df)
    print(f"\nData: {len(df)} bars  source={args.source}")
    print(f"Detected lead-lag: {lag} bars (max cross-corr {corr:.3f})")

    cfg = BacktestConfig(
        signal=SignalConfig(
            leader=args.leader,
            laggard=args.laggard,
            lookback_bars=args.lookback,
            holding_bars=args.hold,
            entry_threshold=args.threshold,
        ),
    )
    cfg.execution.bar_seconds = args.bar_seconds
    cfg.execution.latency_seconds = args.latency

    print(f"\nRound-trip cost assumption: {cfg.cost.round_trip_cost()*1e4:.1f} bps per trade")
    print(f"\n=== Single run @ latency={args.latency}s "
          f"({cfg.execution.latency_bars} bars) ===")
    res = backtest(df, cfg)
    print(format_summary(summarize(res)))

    print("\n=== Latency sweep (the honest test) ===")
    sweep = latency_sweep(
        df, cfg,
        latencies_s=[0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0],
    )
    print(sweep.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(
        "\nRead it like this: if net PnL is only positive at near-zero latency "
        "and\ncollapses once latency crosses the detected lag, the 'edge' "
        "belonged to\nspeed you don't have — not to a strategy a 100 USDT "
        "retail bot can run.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
