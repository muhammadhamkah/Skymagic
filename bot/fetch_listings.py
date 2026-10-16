"""Download the first N minutes of newly-listed symbols, from their listing.

Binance klines start at a symbol's listing moment, so fetching with the
earliest available data gives the real pump/bleed path. Run from a network-
permitted environment (laptop / Colab / a session whose policy allows
api.binance.com).

Get the symbol list from Binance's "New Cryptocurrency Listings" announcements,
then::

    python -m bot.fetch_listings --symbols XUSDT,YUSDT,ZUSDT --minutes 240
    python -m bot.listing --csv-dir data/listings

Saved CSVs carry OHLC so the harness can approximate the (unknown) spread from
each bar's high-low range — a rough but real proxy, documented in bot/listing.py.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd
import requests

BASES = {
    "us": "https://api.binance.us/api/v3/klines",
    "com": "https://api.binance.com/api/v3/klines",
}
_COLS = ["open_time", "open", "high", "low", "close", "volume",
         "close_time", "qv", "n", "tb", "tq", "ig"]


def fetch_from_listing(symbol: str, minutes: int, base: str) -> pd.DataFrame:
    """Fetch the first ``minutes`` of 1-minute klines from the symbol's start."""
    url = BASES[base]
    rows: list[list] = []
    start = 0  # earliest available == listing
    while len(rows) < minutes:
        p = {"symbol": symbol, "interval": "1m",
             "limit": min(1000, minutes - len(rows)), "startTime": start}
        d = requests.get(url, params=p, timeout=20).json()
        if not isinstance(d, list):
            raise RuntimeError(f"{symbol}: error response {d}")
        if not d:
            break
        rows += d
        start = int(d[-1][0]) + 1
        if len(d) < p["limit"]:
            break
    if not rows:
        raise RuntimeError(f"{symbol}: no klines")
    df = pd.DataFrame(rows, columns=_COLS).iloc[:minutes]
    return df[["open", "high", "low", "close"]].astype(float)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download new-listing klines from listing")
    ap.add_argument("--symbols", required=True, help="comma-separated, e.g. XUSDT,YUSDT")
    ap.add_argument("--minutes", type=int, default=240)
    ap.add_argument("--base", choices=list(BASES), default="us")
    ap.add_argument("--out-dir", default="data/listings")
    args = ap.parse_args(argv)

    out = Path(args.out_dir)
    os.makedirs(out, exist_ok=True)
    ok = 0
    for sym in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        try:
            df = fetch_from_listing(sym, args.minutes, args.base)
            path = out / f"{sym}.csv"
            df.to_csv(path, index=False)
            print(f"  {sym}: {len(df)} bars -> {path}")
            ok += 1
        except Exception as e:  # keep going if one symbol fails
            print(f"  {sym}: SKIPPED ({e})")
    print(f"\nFetched {ok} listings. Now run:\n  python -m bot.listing --csv-dir {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
