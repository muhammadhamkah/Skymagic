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

from . import data, engine


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
                   help="per-side fee fraction (0.001 = 0.1%% taker)")
    p.add_argument("--drift", type=float, default=0.0,
                   help="synthetic per-bar drift (0 = no upward bias)")
    p.add_argument("--bars", type=int, default=4000, help="synthetic bar count")
    p.add_argument("--no-split", action="store_true",
                   help="report one run instead of in/out-of-sample")
    args = p.parse_args()

    if args.make_sample:
        data.write_sample_csv(args.make_sample, data.synthetic(n=args.bars))
        print(f"wrote synthetic sample -> {args.make_sample}")
        return

    if args.csv:
        candles = data.load_csv(args.csv)
        source = args.csv
    else:
        candles = data.synthetic(n=args.bars, drift=args.drift)
        source = (f"SYNTHETIC random walk (drift={args.drift}, {args.bars} bars) "
                  "— no real edge exists in this data by construction")

    print(f"\nData: {source}")
    print(f"Loaded {len(candles)} candles.\n")

    strategies = ["revert_poc", "breakout"] if args.strategy == "both" else [args.strategy]
    common = dict(window=args.window, rows=args.rows,
                  value_area=args.value_area, fee=args.fee)

    for strat in strategies:
        if args.no_split:
            print(engine.run(candles, strategy=strat, label="all", **common).summary())
        else:
            in_s, out_s = engine.run_split(candles, strat, **common)
            print(in_s.summary())
            print(out_s.summary())
        print("-" * 60)

    print(
        "\nReminder: a positive in-sample edge that vanishes (or goes negative)\n"
        "out-of-sample is overfitting, not alpha. On synthetic random data the\n"
        "honest expectation is: strategy return <= buy & hold once fees apply.\n"
    )


if __name__ == "__main__":
    main()
