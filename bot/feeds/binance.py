"""Binance public market data (klines) with local CSV caching.

Uses the public REST endpoint — no API key required for historical klines.
Results are cached under ``bot/_cache/`` (git-ignored) so repeated backtests
don't re-hit the network. If the network is unavailable, prefer the synthetic
feed; this module raises a clear error rather than silently returning nothing.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import requests

_BASE = "https://api.binance.com/api/v3/klines"
_CACHE_DIR = Path(__file__).resolve().parent.parent / "_cache"
_MAX_PER_CALL = 1000

# Binance kline column layout.
_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_base", "taker_quote", "ignore",
]


def fetch_klines(symbol: str, interval: str = "1s", limit: int = 5000) -> pd.DataFrame:
    """Fetch up to ``limit`` recent klines for ``symbol``, paginating as needed.

    Args:
        symbol: e.g. ``"BTCUSDT"``.
        interval: Binance interval string (``"1s"``, ``"1m"``, ``"5m"``, ...).
        limit: total number of bars wanted (paginated in 1000-bar pages).

    Returns:
        DataFrame indexed by close timestamp with float OHLCV columns.
    """
    rows: list[list] = []
    end_time: int | None = None
    remaining = limit

    while remaining > 0:
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(_MAX_PER_CALL, remaining),
        }
        if end_time is not None:
            params["endTime"] = end_time
        resp = requests.get(_BASE, params=params, timeout=20)
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        rows = page + rows  # prepend older data
        remaining -= len(page)
        # Walk backwards: next page ends just before this page's first bar.
        end_time = int(page[0][0]) - 1
        if len(page) < params["limit"]:
            break
        time.sleep(0.2)  # be polite to the public endpoint

    if not rows:
        raise RuntimeError(f"No klines returned for {symbol} {interval}")

    df = pd.DataFrame(rows, columns=_COLUMNS)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    return df.set_index("close_time")[["open", "high", "low", "close", "volume"]]


def load_pair(
    leader: str,
    laggard: str,
    interval: str = "1s",
    limit: int = 5000,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Load aligned leader/laggard close prices into one DataFrame.

    Returns a DataFrame with ``leader`` and ``laggard`` close-price columns,
    inner-joined on timestamp so both series are time-aligned.
    """
    frames = {}
    for role, symbol in (("leader", leader), ("laggard", laggard)):
        cache = _CACHE_DIR / f"{symbol}_{interval}_{limit}.csv"
        if use_cache and cache.exists():
            frames[role] = pd.read_csv(cache, index_col=0, parse_dates=True)["close"]
        else:
            df = fetch_klines(symbol, interval=interval, limit=limit)
            if use_cache:
                os.makedirs(_CACHE_DIR, exist_ok=True)
                df.to_csv(cache)
            frames[role] = df["close"]

    out = pd.DataFrame({"leader": frames["leader"], "laggard": frames["laggard"]})
    return out.dropna()
