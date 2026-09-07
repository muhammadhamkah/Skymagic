"""Log the Indonesia rupiah crypto premium against the global price.

Premium = local_price_idr / (global_price_usdt * usd_idr) - 1

Two premia are recorded each tick:
  btc_premium  : Indodax BTC/IDR vs Binance BTC/USDT * USD/IDR
  usdt_premium : Indodax USDT/IDR vs USD/IDR (the stablecoin rail itself)

Two ways to run it for 30 days:

  a) GitHub Actions (no machine needed). `.github/workflows/idr_premium.yml` runs
     one sample every 15 minutes and commits the row to
     `research/data/idr_premium.csv`. Scheduled workflows only fire on the repo's
     default branch, so merge the PR first; until then use "Run workflow" in the
     Actions tab to test it on this branch.

  b) Your own machine:

         python research/spread_logger.py run --interval 600

Either way, read the result with:

    python research/spread_logger.py summary --csv research/data/idr_premium.csv
    python research/spread_logger.py summary            # SQLite variant

Go / no-go after 30 days (all figures net of nothing, so subtract your own costs):
  - mean |premium| under 0.5%              -> no gap, drop it
  - |premium| above 1.5% for 20%+ of ticks -> worth a small live test
  - premium sign flips at least twice      -> a round trip exists (buy discount,
                                              sell premium) without needing IDR out
  - persistent one-sided premium           -> you need an IDR-in or IDR-out leg;
                                              check bank and OJK limits before sizing

Expected round-trip cost to beat: Indodax taker fee (~0.3%) x2, Binance taker (0.1%),
on-chain USDT transfer (a few USD on Tron/Solana), plus the USDT/IDR spread.
Roughly 0.8% to 1.0% all in, so a 1.5% gap is the floor of interest.
"""

import argparse
import csv
import json
import sqlite3
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Optional

import requests

INDODAX_TICKER = "https://indodax.com/api/ticker/{pair}"
BINANCE_BOOK = "https://data-api.binance.vision/api/v3/ticker/bookTicker?symbol={symbol}"
BINANCE_BOOK_FALLBACK = "https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol}"
FX_RATES = "https://open.er-api.com/v6/latest/USD"

SCHEMA = """
CREATE TABLE IF NOT EXISTS idr_premium (
    ts TEXT PRIMARY KEY,
    indodax_btc_bid REAL,
    indodax_btc_ask REAL,
    indodax_usdt_bid REAL,
    indodax_usdt_ask REAL,
    binance_btc_bid REAL,
    binance_btc_ask REAL,
    usd_idr REAL,
    btc_premium REAL,
    usdt_premium REAL
);
"""

Fetcher = Callable[[str], dict]


def _get_json(url: str, timeout: int = 20) -> dict:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.json()


def fetch_indodax(pair: str, get: Fetcher = _get_json) -> tuple[float, float]:
    """Return (bid, ask) for an Indodax pair like 'btcidr'."""
    data = get(INDODAX_TICKER.format(pair=pair))["ticker"]
    return float(data["buy"]), float(data["sell"])


def fetch_binance(symbol: str, get: Fetcher = _get_json) -> tuple[float, float]:
    try:
        data = get(BINANCE_BOOK.format(symbol=symbol))
    except Exception:  # the .vision host is rarely down; fall back to the main API
        data = get(BINANCE_BOOK_FALLBACK.format(symbol=symbol))
    return float(data["bidPrice"]), float(data["askPrice"])


def fetch_usd_idr(get: Fetcher = _get_json) -> float:
    return float(get(FX_RATES)["rates"]["IDR"])


def compute_premia(
    indodax_btc: tuple[float, float],
    indodax_usdt: tuple[float, float],
    binance_btc: tuple[float, float],
    usd_idr: float,
) -> tuple[float, float]:
    """Mid-price premia as fractions. Positive = Indonesia is expensive."""
    idr_btc_mid = (indodax_btc[0] + indodax_btc[1]) / 2
    usdt_btc_mid = (binance_btc[0] + binance_btc[1]) / 2
    idr_usdt_mid = (indodax_usdt[0] + indodax_usdt[1]) / 2
    btc_premium = idr_btc_mid / (usdt_btc_mid * usd_idr) - 1
    usdt_premium = idr_usdt_mid / usd_idr - 1
    return btc_premium, usdt_premium


def sample(get: Fetcher = _get_json) -> dict:
    btc_idr = fetch_indodax("btcidr", get)
    usdt_idr = fetch_indodax("usdtidr", get)
    btc_usdt = fetch_binance("BTCUSDT", get)
    usd_idr = fetch_usd_idr(get)
    btc_p, usdt_p = compute_premia(btc_idr, usdt_idr, btc_usdt, usd_idr)
    return {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "indodax_btc_bid": btc_idr[0],
        "indodax_btc_ask": btc_idr[1],
        "indodax_usdt_bid": usdt_idr[0],
        "indodax_usdt_ask": usdt_idr[1],
        "binance_btc_bid": btc_usdt[0],
        "binance_btc_ask": btc_usdt[1],
        "usd_idr": usd_idr,
        "btc_premium": btc_p,
        "usdt_premium": usdt_p,
    }


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def insert(conn: sqlite3.Connection, row: dict) -> None:
    cols = ",".join(row)
    marks = ",".join("?" for _ in row)
    conn.execute(f"INSERT OR REPLACE INTO idr_premium ({cols}) VALUES ({marks})", tuple(row.values()))
    conn.commit()


CSV_COLUMNS = [
    "ts", "indodax_btc_bid", "indodax_btc_ask", "indodax_usdt_bid", "indodax_usdt_ask",
    "binance_btc_bid", "binance_btc_ask", "usd_idr", "btc_premium", "usdt_premium",
]


def append_csv(path: str, row: dict) -> None:
    """Append one sample; write the header if the file is new. Git-friendly storage."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists() or p.stat().st_size == 0
    with p.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if new:
            w.writeheader()
        w.writerow({k: row[k] for k in CSV_COLUMNS})


def load_csv_premia(path: str) -> list[tuple[float, float]]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="") as fh:
        return [(float(r["btc_premium"]), float(r["usdt_premium"])) for r in csv.DictReader(fh)]


def summary_from_rows(rows: list[tuple[float, float]]) -> dict:
    if not rows:
        return {"n": 0}
    btc = [r[0] for r in rows]
    usdt = [r[1] for r in rows]

    def stats(xs: list[float]) -> dict:
        n = len(xs)
        mean = sum(xs) / n
        mean_abs = sum(abs(x) for x in xs) / n
        above_1_5 = sum(1 for x in xs if abs(x) >= 0.015) / n
        flips = sum(1 for a, b in zip(xs, xs[1:]) if (a >= 0) != (b >= 0))
        return {
            "mean_pct": round(mean * 100, 3),
            "mean_abs_pct": round(mean_abs * 100, 3),
            "min_pct": round(min(xs) * 100, 3),
            "max_pct": round(max(xs) * 100, 3),
            "share_abs_over_1.5pct": round(above_1_5, 3),
            "sign_flips": flips,
        }

    return {"n": len(rows), "btc": stats(btc), "usdt": stats(usdt)}


def summary(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT btc_premium, usdt_premium FROM idr_premium ORDER BY ts").fetchall()
    return summary_from_rows([(r[0], r[1]) for r in rows])


def cmd_run(args) -> None:
    conn = None if args.csv else open_db(args.db)
    while True:
        try:
            row = sample()
            if args.csv:
                append_csv(args.csv, row)
            else:
                insert(conn, row)
            print(
                f"{row['ts']}  btc {row['btc_premium'] * 100:+.3f}%  "
                f"usdt {row['usdt_premium'] * 100:+.3f}%  usd/idr {row['usd_idr']:.0f}"
            )
        except Exception as exc:  # keep logging through transient failures
            print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}  ERROR {exc}", file=sys.stderr)
            if args.once:
                sys.exit(1)  # a one-shot (cron) run should show red, not silently skip
        if args.once:
            return
        time.sleep(args.interval)


def cmd_summary(args) -> None:
    if args.csv:
        result = summary_from_rows(load_csv_premia(args.csv))
    else:
        result = summary(open_db(args.db))
    print(json.dumps(result, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def storage_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--db", default="data/idr_premium.db", help="SQLite path (default storage)")
        sp.add_argument("--csv", help="Append to / read from this CSV instead of SQLite")

    r = sub.add_parser("run", help="Sample forever (or once with --once)")
    r.add_argument("--interval", type=int, default=600, help="Seconds between samples")
    r.add_argument("--once", action="store_true")
    storage_args(r)
    r.set_defaults(func=cmd_run)
    s = sub.add_parser("summary", help="Print stats over everything logged so far")
    storage_args(s)
    s.set_defaults(func=cmd_summary)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
