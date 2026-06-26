"""Websocket tick collector for MEXC spot (ms-resolution best-bid/ask).

Why this exists: the REST collector polls at ~250ms, which can't resolve the
sub-second lead-lag we care about. This subscribes to MEXC's push websocket so
each book update is stamped with a high-precision LOCAL receive time.

IMPORTANT resolution caveat (read before trusting results):
  MEXC's PUBLIC websocket AGGREGATES updates. The book-ticker stream's finest
  interval is 100ms; the deals (trade) stream's finest is 10ms. So from public
  data you can resolve lead-lag down to ~10-100ms, NOT single-digit ms. True
  sub-10ms structure is only visible on raw/colocated feeds that retail does
  not get. Keep that in mind when interpreting a "breakeven latency".

Frames are protobuf (channels end in `.pb`). This file was written WITHOUT the
ability to test against the live endpoint (the build network blocks MEXC), so:

  1. Run `python ws_collect.py --symbols BTCUSDT --raw` FIRST. It prints the
     decoded field tree of the first few frames. Confirm which field numbers
     hold symbol / bidPrice / bidQty / askPrice / askQty.
  2. If they differ from the defaults below, pass --field-* flags (no code edit
     needed) and then run for real to write into the SQLite db.

Output schema matches the REST collector exactly, so analyze.py / event_study.py
work unchanged.

Deps: pip install websocket-client
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time

try:
    import websocket  # websocket-client
except ImportError:
    sys.exit("pip install websocket-client")

import pbdecode

WS_URL = "wss://wbs-api.mexc.com/ws"

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


def chan(symbol: str, interval_ms: int) -> str:
    # aggregated protobuf book-ticker; 100ms is the finest MEXC offers here
    return f"spot@public.aggre.bookTicker.v3.api.pb@{interval_ms}ms@{symbol}"


class Collector:
    def __init__(self, args):
        self.args = args
        self.conn = sqlite3.connect(args.db)
        self.conn.executescript(SCHEMA)
        self.deadline = time.time() + args.duration_min * 60
        self.last_ping = time.time()
        self.n = 0
        self.raw_seen = 0

    def extract(self, msg: bytes):
        """Pull (symbol, bid, bid_qty, ask, ask_qty) from a protobuf frame.

        Field-number paths are configurable via --field-* because the live
        schema must be confirmed with --raw. Defaults follow MEXC's documented
        PushDataV3ApiWrapper -> PublicBookTicker layout (verify on first run).
        """
        a = self.args
        top = pbdecode.decode(msg)
        sym_raw = top.get(a.field_symbol, [None])[0]
        symbol = pbdecode.as_str(sym_raw) if sym_raw else None
        body_raw = top.get(a.field_body, [None])[0]
        if not body_raw:
            return None
        body = pbdecode.decode(body_raw)

        def fnum(fid):
            v = body.get(fid, [None])[0]
            s = pbdecode.as_str(v) if v is not None else None
            try:
                return float(s) if s is not None else None
            except ValueError:
                return None

        bid = fnum(a.field_bid)
        bidq = fnum(a.field_bidqty)
        ask = fnum(a.field_ask)
        askq = fnum(a.field_askqty)
        if None in (symbol, bid, ask):
            return None
        return (symbol, bid, bidq or 0.0, ask, askq or 0.0)

    def on_message(self, ws, message):
        # control acks arrive as JSON text; data arrives as binary protobuf
        if isinstance(message, str):
            print(f"  [ctrl] {message[:200]}")
            return
        ts = int(time.time() * 1000)
        if self.args.raw:
            self.raw_seen += 1
            print(f"\n--- frame {self.raw_seen} ({len(message)} bytes) ---")
            print(json.dumps(pbdecode.tree(message), indent=2, default=str)[:2000])
            if self.raw_seen >= self.args.raw:
                ws.close()
            return
        row = self.extract(message)
        if row is None:
            return
        symbol, bid, bidq, ask, askq = row
        self.conn.execute(
            "INSERT INTO ticks VALUES (?,?,?,?,?,?)",
            (ts, symbol, bid, bidq, ask, askq),
        )
        self.n += 1
        if self.n % 500 == 0:
            self.conn.commit()
            print(f"  {self.n} ticks...")
        if time.time() > self.deadline:
            ws.close()

    def on_open(self, ws):
        params = [chan(s, self.args.interval_ms) for s in self.args.symbols]
        ws.send(json.dumps({"method": "SUBSCRIPTION", "params": params}))
        print(f"subscribed: {params}")

    def on_error(self, ws, error):
        print(f"  ws error: {error}", file=sys.stderr)

    def on_close(self, ws, *a):
        self.conn.commit()
        self.conn.close()
        print(f"closed. {self.n} ticks written to {self.args.db}")

    def run(self):
        ws = websocket.WebSocketApp(
            WS_URL,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        # MEXC expects a PING within 60s; send one every 20s
        ws.run_forever(ping_interval=20, ping_payload=json.dumps({"method": "PING"}))


def main() -> int:
    ap = argparse.ArgumentParser(description="MEXC websocket tick collector")
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--interval-ms", type=int, default=100, choices=[100],
                    help="book-ticker aggregation (MEXC public floor is 100ms)")
    ap.add_argument("--duration-min", type=float, default=30.0)
    ap.add_argument("--db", default="ticks_ws.db")
    ap.add_argument("--raw", type=int, default=0, metavar="N",
                    help="print decoded field tree for first N frames, then exit "
                         "(use this to confirm field numbers before collecting)")
    # field-number paths (confirm with --raw; defaults per documented schema)
    ap.add_argument("--field-symbol", type=int, default=3)
    ap.add_argument("--field-body", type=int, default=4, help="the bookTicker sub-message field")
    ap.add_argument("--field-bid", type=int, default=1)
    ap.add_argument("--field-bidqty", type=int, default=2)
    ap.add_argument("--field-ask", type=int, default=3)
    ap.add_argument("--field-askqty", type=int, default=4)
    args = ap.parse_args()
    Collector(args).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
