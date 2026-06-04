"""Fetch real OHLCV candles from Binance into a CSV the backtester reads.

Binance is often allowlisted out of sandboxed/CI environments (you'll see
"Host not in allowlist" or a connection error). Run this where you DO have
network access to api.binance.com, then point `backtest.run --csv` at the
output file.

    python -m backtest.fetch --symbol BTCUSDT --interval 1h --bars 5000 \
        --out data/BTCUSDT_1h.csv

    python -m backtest.run --csv data/BTCUSDT_1h.csv --strategy both
    python -m backtest.run --csv data/BTCUSDT_1h.csv --no-split

The klines endpoint caps at 1000 bars per request, so this pages backwards
from the most recent bar until it has `--bars` of them.
"""

from __future__ import annotations

import argparse
import sys
import time

import requests

from .data import Candle, write_sample_csv

BASE = "https://api.binance.com/api/v3/klines"
MAX_PER_REQ = 1000
VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}


def fetch(symbol: str, interval: str, bars: int,
          max_retries: int = 4, backoff: float = 2.0) -> list[Candle]:
    """Page backwards through Binance klines until `bars` candles are gathered."""
    if interval not in VALID_INTERVALS:
        raise ValueError(f"interval {interval!r} not in {sorted(VALID_INTERVALS)}")

    session = requests.Session()
    collected: list[Candle] = []
    end_time: int | None = None  # ms; None = most recent

    while len(collected) < bars:
        want = min(MAX_PER_REQ, bars - len(collected))
        params = {"symbol": symbol.upper(), "interval": interval, "limit": want}
        if end_time is not None:
            params["endTime"] = end_time

        rows = _get(session, params, max_retries, backoff)
        if not rows:
            break  # ran out of history

        # kline row: [open_time, open, high, low, close, volume, ...]
        batch = [
            Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]),
                   float(r[4]), float(r[5]))
            for r in rows
        ]
        collected = batch + collected
        end_time = batch[0].open_time - 1  # step strictly before this batch
        if len(batch) < want:
            break  # no more history available
        time.sleep(0.25)  # be polite to the rate limiter

    collected.sort(key=lambda c: c.open_time)
    # de-dup any overlap at page boundaries, keep last `bars`
    seen: set[int] = set()
    deduped: list[Candle] = []
    for c in collected:
        if c.open_time not in seen:
            seen.add(c.open_time)
            deduped.append(c)
    return deduped[-bars:]


def _get(session: requests.Session, params: dict,
         max_retries: int, backoff: float) -> list:
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = session.get(BASE, params=params, timeout=30)
            if resp.status_code == 429:  # rate limited
                wait = backoff ** (attempt + 2)
                print(f"  rate limited (429); sleeping {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_err = e
            wait = backoff ** attempt
            print(f"  request failed ({e}); retry in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(
        f"failed to reach Binance after {max_retries} tries: {last_err}\n"
        "If this is 'Host not in allowlist' or a connection error, the network "
        "blocks api.binance.com here — run this where you have network access."
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Fetch Binance klines to CSV")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="1h",
                   help=f"one of {sorted(VALID_INTERVALS)}")
    p.add_argument("--bars", type=int, default=5000)
    p.add_argument("--out", required=True, help="output CSV path")
    args = p.parse_args()

    print(f"Fetching {args.bars} x {args.interval} candles for {args.symbol} ...")
    candles = fetch(args.symbol, args.interval, args.bars)
    if not candles:
        print("No candles returned.", file=sys.stderr)
        sys.exit(1)

    write_sample_csv(args.out, candles)
    span_h = (candles[-1].open_time - candles[0].open_time) / 3_600_000
    print(f"Wrote {len(candles)} candles -> {args.out}  "
          f"(~{span_h / 24:.1f} days of history)")
    print(f"Now run: python -m backtest.run --csv {args.out} --strategy both")


if __name__ == "__main__":
    main()
