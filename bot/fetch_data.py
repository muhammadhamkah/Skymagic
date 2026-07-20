"""Download real Binance klines to CSV for the harness.

Run this from ANY environment with internet access (your laptop, Google Colab,
or a Claude Code web session whose network policy allows api.binance.com). It
saves one CSV per symbol in the layout ``bot/feeds/csv_feed.py`` auto-detects,
so you can immediately run::

    python -m bot.run_backtest --source csv \
        --leader-csv data/BTCUSDT-1m.csv --laggard-csv data/SOLUSDT-1m.csv \
        --bar-seconds 60 --lookback 1 --hold 8

Why this exists: locked-down sandboxes (e.g. Claude Code on the web with a
restrictive egress policy) block every exchange host, so the data has to be
pulled where the network allows it and then imported.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pandas as pd
import requests

# Public kline endpoints. .com is global; .us works from the United States;
# data.binance.vision serves downloadable historical dumps. No API key needed.
BASES = {
    "com": "https://api.binance.com/api/v3/klines",
    "us": "https://api.binance.us/api/v3/klines",
}
_MAX_PER_CALL = 1000
_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore",
]


def fetch(symbol: str, interval: str, limit: int, base: str) -> pd.DataFrame:
    """Fetch ``limit`` klines for ``symbol``, paginating backwards in time."""
    url = BASES[base]
    rows: list[list] = []
    end_time: int | None = None
    remaining = limit
    while remaining > 0:
        params = {"symbol": symbol, "interval": interval,
                  "limit": min(_MAX_PER_CALL, remaining)}
        if end_time is not None:
            params["endTime"] = end_time
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        page = r.json()
        if not page:
            break
        rows = page + rows
        remaining -= len(page)
        end_time = int(page[0][0]) - 1
        if len(page) < params["limit"]:
            break
        time.sleep(0.2)
    if not rows:
        raise RuntimeError(f"No klines returned for {symbol} {interval}")
    df = pd.DataFrame(rows, columns=_COLUMNS)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Download Binance klines to CSV")
    p.add_argument("--leader", default="BTCUSDT")
    p.add_argument("--laggard", default="ETHUSDT")
    p.add_argument("--interval", default="1m", help="1s, 1m, 5m, ...")
    p.add_argument("--limit", type=int, default=5000, help="bars per symbol")
    p.add_argument("--base", choices=list(BASES), default="com")
    p.add_argument("--out-dir", default="data")
    args = p.parse_args(argv)

    out = Path(args.out_dir)
    os.makedirs(out, exist_ok=True)
    paths = {}
    for symbol in (args.leader, args.laggard):
        print(f"Fetching {symbol} {args.interval} x{args.limit} from {args.base} ...")
        df = fetch(symbol, args.interval, args.limit, args.base)
        path = out / f"{symbol}-{args.interval}.csv"
        df.to_csv(path, index=False)
        paths[symbol] = path
        print(f"  saved {len(df)} rows -> {path}")

    bar_seconds = {"1s": 1, "1m": 60, "3m": 180, "5m": 300, "15m": 900,
                   "1h": 3600}.get(args.interval, 60)
    print("\nNow run the harness on real data:")
    print(
        f"  python -m bot.run_backtest --source csv \\\n"
        f"      --leader-csv {paths[args.leader]} \\\n"
        f"      --laggard-csv {paths[args.laggard]} \\\n"
        f"      --bar-seconds {bar_seconds} --lookback 1 --hold 8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
