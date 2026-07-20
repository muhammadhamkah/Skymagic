"""Download real historical funding rates and evaluate carry on them.

Funding history needs no API key. Run from a network-permitted environment
(laptop / Colab / a session whose policy allows the exchange). Binance USDⓈ-M:
``/fapi/v1/fundingRate``. The script prints what 1x cross-margined carry would
have earned, net of fees, over the real funding regime — the live go/no-go.

    python -m bot.fetch_funding --symbol BTCUSDT --limit 1000
"""

from __future__ import annotations

import argparse

import pandas as pd
import requests

from .funding_carry import CarryParams, backtest_real_funding

BASES = {
    "com": "https://fapi.binance.com/fapi/v1/fundingRate",
    "us":  "https://fapi.binance.us/fapi/v1/fundingRate",
}


def fetch_funding(symbol: str, limit: int, base: str) -> pd.Series:
    """Fetch up to ``limit`` historical 8h funding rates for ``symbol``."""
    rows: list[dict] = []
    end = None
    while len(rows) < limit:
        params = {"symbol": symbol, "limit": min(1000, limit - len(rows))}
        if end:
            params["endTime"] = end
        d = requests.get(BASES[base], params=params, timeout=20).json()
        if not isinstance(d, list) or not d:
            break
        rows = d + rows
        end = int(d[0]["fundingTime"]) - 1
        if len(d) < params["limit"]:
            break
    if not rows:
        raise RuntimeError(f"No funding history for {symbol}")
    return pd.Series([float(r["fundingRate"]) for r in rows])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch funding history and score carry")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--limit", type=int, default=1000, help="number of 8h periods (~333d at 1000)")
    ap.add_argument("--base", choices=list(BASES), default="com")
    ap.add_argument("--cost-bps", type=float, default=11.0, help="round-trip cost, bps")
    args = ap.parse_args(argv)

    f = fetch_funding(args.symbol, args.limit, args.base)
    p = CarryParams(roundtrip_cost=args.cost_bps / 1e4)
    r = backtest_real_funding(f, p)
    print(f"\n{args.symbol}: {r['periods']} funding periods (~{r['days']} days)")
    print(f"  mean funding   : {r['mean_funding_bps_8h']} bps / 8h "
          f"({r['pct_periods_positive']}% of periods positive)")
    print(f"  gross carry    : {r['gross_%']}%")
    print(f"  net (1x, -fees): {r['net_%']}%   ->   {r['apy_%']}% APY")
    print("\n(positive net = carry would have paid in this real regime; "
          "negative = it would have bled.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
