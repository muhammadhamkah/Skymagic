"""Collect timestamped best-bid/ask snapshots for a set of MEXC symbols.

Run this locally (needs network access to api.mexc.com). It polls the
all-symbols bookTicker endpoint at a fixed cadence and appends each snapshot
to a SQLite file for later analysis by analyze.py.

Example:
    python collect.py --symbols BTCUSDT ETHUSDT SOLUSDT \
        --interval-ms 250 --duration-min 30 --db ticks.db

Notes / honesty:
  * REST polling resolution is bounded by network round-trip (~50-200ms typ).
    True sub-100ms lead-lag needs a websocket feed; this is a first pass.
  * Local timestamps are used (consistent clock across symbols). Good enough
    for *relative* lead-lag between symbols on the SAME exchange.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time

from mexc import MexcPublic

SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    ts_local_ms INTEGER NOT NULL,
    symbol      TEXT    NOT NULL,
    bid         REAL    NOT NULL,
    bid_qty     REAL    NOT NULL,
    ask         REAL    NOT NULL,
    ask_qty     REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ticks_sym_ts ON ticks(symbol, ts_local_ms);
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect MEXC bookTicker snapshots")
    ap.add_argument("--symbols", nargs="+", required=True, help="e.g. BTCUSDT ETHUSDT")
    ap.add_argument("--interval-ms", type=int, default=250, help="poll cadence")
    ap.add_argument("--duration-min", type=float, default=30.0)
    ap.add_argument("--db", default="ticks.db")
    args = ap.parse_args()

    client = MexcPublic()
    if not client.ping():
        print("ERROR: cannot reach MEXC (ping failed). Check network/region.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA)

    deadline = time.time() + args.duration_min * 60
    interval = args.interval_ms / 1000.0
    n = 0
    print(f"collecting {args.symbols} every {args.interval_ms}ms for {args.duration_min}min -> {args.db}")
    try:
        while time.time() < deadline:
            t0 = time.time()
            try:
                ticks = client.book_tickers(args.symbols)
                conn.executemany(
                    "INSERT INTO ticks VALUES (?,?,?,?,?,?,?)",
                    [(t.ts_local_ms, t.symbol, t.bid, t.bid_qty, t.ask, t.ask_qty) for t in ticks],
                )
                conn.commit()
                n += len(ticks)
                if n % (len(args.symbols) * 40) == 0:
                    print(f"  {n} rows...")
            except Exception as e:  # keep collecting through transient errors
                print(f"  warn: {e}", file=sys.stderr)
            # pace to target interval
            sleep = interval - (time.time() - t0)
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        print("\nstopped by user")
    finally:
        conn.close()
    print(f"done. {n} rows written to {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
