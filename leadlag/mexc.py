"""Thin MEXC public-API client. Market data only — no API key required.

Docs: https://mexcdevelop.github.io/apidocs/spot_v3_en/
All endpoints used here are public (book ticker, depth, recent trades).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

BASE = "https://api.mexc.com"


@dataclass
class BookTick:
    symbol: str
    ts_local_ms: int  # local clock when we received the response
    bid: float
    bid_qty: float
    ask: float
    ask_qty: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_bps(self) -> float:
        return (self.spread / self.mid) * 1e4 if self.mid else float("nan")


class MexcPublic:
    def __init__(self, base: str = BASE, timeout: float = 10.0):
        self.base = base
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "leadlag-research/0.1"})

    def ping(self) -> bool:
        try:
            r = self.session.get(f"{self.base}/api/v3/ping", timeout=self.timeout)
        except requests.RequestException:
            return False
        return r.status_code == 200

    def server_time_ms(self) -> int:
        r = self.session.get(f"{self.base}/api/v3/time", timeout=self.timeout)
        r.raise_for_status()
        return int(r.json()["serverTime"])

    def book_ticker(self, symbol: str) -> BookTick:
        """Best bid/ask for one symbol. Cheapest, fastest endpoint for lead-lag."""
        r = self.session.get(
            f"{self.base}/api/v3/ticker/bookTicker",
            params={"symbol": symbol},
            timeout=self.timeout,
        )
        r.raise_for_status()
        d = r.json()
        return BookTick(
            symbol=symbol,
            ts_local_ms=int(time.time() * 1000),
            bid=float(d["bidPrice"]),
            bid_qty=float(d["bidQty"]),
            ask=float(d["askPrice"]),
            ask_qty=float(d["askQty"]),
        )

    def book_tickers(self, symbols: list[str]) -> list[BookTick]:
        """One request returns the whole market; we filter to the symbols we want.

        Using the all-symbols form means a single round-trip timestamp covers
        every symbol -> no inter-symbol skew from sequential per-symbol calls.
        """
        r = self.session.get(f"{self.base}/api/v3/ticker/bookTicker", timeout=self.timeout)
        r.raise_for_status()
        ts = int(time.time() * 1000)
        wanted = set(symbols)
        out: list[BookTick] = []
        for d in r.json():
            if d["symbol"] in wanted:
                out.append(
                    BookTick(
                        symbol=d["symbol"],
                        ts_local_ms=ts,
                        bid=float(d["bidPrice"]),
                        bid_qty=float(d["bidQty"]),
                        ask=float(d["askPrice"]),
                        ask_qty=float(d["askQty"]),
                    )
                )
        return out
