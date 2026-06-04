"""Candle loading: local Binance-kline CSV, or a synthetic generator.

The Binance public API is often unreachable (network allowlists, geoblocks,
sandboxed CI). So the primary path is a CSV you supply; the synthetic path
exists so the engine is runnable anywhere and so you can sanity-check that a
rule with *no* real edge makes *no* money once fees are applied.
"""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Candle:
    open_time: int  # ms epoch
    open: float
    high: float
    low: float
    close: float
    volume: float


def load_csv(path: str) -> list[Candle]:
    """Load OHLCV candles from CSV.

    Accepts the raw Binance klines schema (12 columns, no header) where the
    first six columns are open_time, open, high, low, close, volume. Also
    accepts a headered CSV exposing those six names in any order.
    """
    rows = list(csv.reader(open(path, newline="", encoding="utf-8")))
    if not rows:
        raise ValueError(f"{path} is empty")

    header = rows[0]
    has_header = any(c.strip().lower() in {"open", "close", "open_time"} for c in header)

    candles: list[Candle] = []
    if has_header:
        idx = {name.strip().lower(): i for i, name in enumerate(header)}
        required = ["open_time", "open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in idx]
        if missing:
            raise ValueError(f"{path} missing columns: {missing}")
        for r in rows[1:]:
            candles.append(
                Candle(
                    int(float(r[idx["open_time"]])),
                    float(r[idx["open"]]),
                    float(r[idx["high"]]),
                    float(r[idx["low"]]),
                    float(r[idx["close"]]),
                    float(r[idx["volume"]]),
                )
            )
    else:
        for r in rows:
            candles.append(
                Candle(int(float(r[0])), float(r[1]), float(r[2]),
                       float(r[3]), float(r[4]), float(r[5]))
            )

    candles.sort(key=lambda c: c.open_time)
    return candles


def synthetic(
    n: int = 4000,
    start_price: float = 30_000.0,
    drift: float = 0.0,
    vol: float = 0.01,
    seed: int = 7,
    interval_ms: int = 3_600_000,
) -> list[Candle]:
    """Generate synthetic OHLCV via geometric Brownian motion.

    This is *random* by construction — there is no exploitable pattern in it.
    A strategy that "wins" on this data is overfitting noise; an honest one
    should roughly match buy-and-hold before fees and lose to it after.

    drift: per-bar expected log return (0.0 = no upward bias).
    vol:   per-bar log-return standard deviation.
    """
    rng = random.Random(seed)
    candles: list[Candle] = []
    price = start_price
    t0 = 1_600_000_000_000  # fixed epoch so runs are reproducible
    for i in range(n):
        ret = rng.gauss(drift, vol)
        new_price = price * math.exp(ret)
        o = price
        c = new_price
        # intrabar wick: a fraction of the bar's move plus noise
        wick = abs(c - o) + price * abs(rng.gauss(0, vol / 2))
        hi = max(o, c) + wick * rng.random()
        lo = min(o, c) - wick * rng.random()
        # volume loosely correlated with absolute move (busier on big bars)
        v = 100.0 * (1 + abs(ret) / vol) * (0.5 + rng.random())
        candles.append(Candle(t0 + i * interval_ms, o, hi, lo, c, v))
        price = new_price
    return candles


def write_sample_csv(path: str, candles: list[Candle]) -> None:
    """Persist candles in the headered schema load_csv() reads back."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["open_time", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.open_time, c.open, c.high, c.low, c.close, c.volume])
