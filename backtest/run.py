"""CLI for the volume-profile backtester.

Examples:
    # Run on synthetic data (works offline; demonstrates the fee drag):
    python -m backtest.run --synthetic --strategy revert_poc

    # Run on real candles you downloaded somewhere with network access.
    # CSV = raw Binance klines, or a headered OHLCV file. Get klines from
    # https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=1000
    # or the bulk dumps at https://data.binance.vision
    python -m backtest.run --csv data/BTCUSDT_1h.csv --strategy breakout

    # Write a synthetic sample CSV to inspect the expected format:
    python -m backtest.run --make-sample data/sample.csv
"""

from __future__ import annotations

import argparse
import statistics as st

from . import data, engine


def _seed_sweep(strategies: list[str], n_seeds: int, bars: int, drift: float,
                common: dict) -> None:
    """Reproducible robustness check: run each strategy over many random seeds.

    A single seed is one lucky/unlucky path; the distribution across seeds is
    the honest signal. Reports mean, median, spread and how often the strategy
    beats hold (~50% with a negative mean = no edge, just coin-flip noise).
    """
    print(f"Robustness sweep: {n_seeds} random seeds, {bars} bars each, drift={drift}\n")
    for strat in strategies:
        edges = []
        for seed in range(n_seeds):
            candles = data.synthetic(n=bars, drift=drift, seed=seed)
            r = engine.run(candles, strategy=strat, **common)
            edges.append(r.strategy_return - r.buy_hold_return)
        beat = sum(1 for e in edges if e > 0)
        stdev = st.pstdev(edges) if len(edges) > 1 else 0.0
        # t-stat for "mean edge != 0"
        tstat = (st.mean(edges) / (stdev / len(edges) ** 0.5)) if stdev else 0.0
        print(
            f"  {strat:11s} | mean {st.mean(edges):+7.2%} | median {st.median(edges):+7.2%} "
            f"| std {stdev:6.2%} | beat-hold {beat:2d}/{n_seeds} | t={tstat:+.2f}"
        )
    print(
        "\n  Read: beat-hold ~= half the seeds AND mean edge <= 0 means no edge —\n"
        "  the wins are noise. |t| < ~2 means even the mean isn't distinguishable\n"
        "  from zero at this sample size.\n"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Volume-profile spot backtester")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--csv", help="path to OHLCV / Binance-kline CSV")
    src.add_argument("--synthetic", action="store_true",
                     help="use generated random-walk candles (offline)")
    p.add_argument("--make-sample", metavar="PATH",
                   help="write a synthetic sample CSV and exit")

    p.add_argument("--strategy", choices=["revert_poc", "breakout", "both"],
                   default="both")
    p.add_argument("--window", type=int, default=48,
                   help="rolling profile window, in bars")
    p.add_argument("--rows", type=int, default=25, help="profile price levels")
    p.add_argument("--value-area", type=float, default=0.68)
    p.add_argument("--fee", type=float, default=0.001,
                   help="per-side commission fraction (0.001 = 0.1%% taker)")
    p.add_argument("--slippage", type=float, default=0.0005,
                   help="per-side spread+slippage fraction (0.0005 = 5 bps)")
    p.add_argument("--drift", type=float, default=0.0,
                   help="synthetic per-bar drift (0 = no upward bias)")
    p.add_argument("--bars", type=int, default=4000, help="synthetic bar count")
    p.add_argument("--seed", type=int, default=7, help="synthetic RNG seed")
    p.add_argument("--seeds", type=int, metavar="N",
                   help="synthetic-only: sweep N seeds and report the edge "
                        "distribution (the reproducible robustness check)")
    p.add_argument("--no-split", action="store_true",
                   help="report one run instead of in/out-of-sample")
    p.add_argument("--walk-forward", action="store_true",
                   help="rolling re-optimization, forward-only evaluation "
                        "(the honest validation; tunes params per fold)")
    p.add_argument("--train", type=int, default=1500,
                   help="walk-forward: in-sample bars per fold")
    p.add_argument("--test", type=int, default=300,
                   help="walk-forward: forward (out-of-sample) bars per fold")
    args = p.parse_args()

    if args.make_sample:
        data.write_sample_csv(args.make_sample, data.synthetic(n=args.bars))
        print(f"wrote synthetic sample -> {args.make_sample}")
        return

    strategies = ["revert_poc", "breakout"] if args.strategy == "both" else [args.strategy]
    common = dict(window=args.window, rows=args.rows,
                  value_area=args.value_area, fee=args.fee, slippage=args.slippage)

    if args.seeds:
        if args.csv:
            p.error("--seeds works with synthetic data only (omit --csv)")
        _seed_sweep(strategies, args.seeds, args.bars, args.drift, common)
        return

    if args.csv:
        candles = data.load_csv(args.csv)
        source = args.csv
    else:
        candles = data.synthetic(n=args.bars, drift=args.drift, seed=args.seed)
        source = (f"SYNTHETIC random walk (drift={args.drift}, {args.bars} bars, "
                  f"seed={args.seed}) — no real edge exists by construction. One "
                  "seed is one path; use --seeds for the honest picture.")

    print(f"\nData: {source}")
    print(f"Loaded {len(candles)} candles.\n")

    if args.walk_forward:
        need = args.train + args.test
        if len(candles) < need:
            p.error(f"need >= {need} candles for --train {args.train} "
                    f"--test {args.test}; have {len(candles)}")
        wf = engine.walk_forward(candles, train=args.train, test=args.test,
                                 rows=args.rows, fee=args.fee, slippage=args.slippage)
        print(wf.summary())
        print(
            "\nThis is the number to trust for 'would the bot work': params are\n"
            "re-tuned only on past bars and judged only on later ones. A positive\n"
            "stitched OOS edge here is real evidence; a negative one means the\n"
            "in-sample tuning was fitting noise.\n"
        )
        return

    for strat in strategies:
        if args.no_split:
            print(engine.run(candles, strategy=strat, label="all", **common).summary())
        else:
            in_s, out_s = engine.run_split(candles, strat, **common)
            print(in_s.summary())
            print(out_s.summary())
        print("-" * 60)

    print(
        "\nNote: the in/out-of-sample split here is a two-period STABILITY check,\n"
        "not an overfitting test — there is no parameter optimization to overfit.\n"
        "A single seed/period can beat hold by luck (often just by sitting in cash\n"
        "during a drop). Run --seeds N for the reproducible, distribution-level\n"
        "verdict, which is the number to trust.\n"
    )


if __name__ == "__main__":
    main()
