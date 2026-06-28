"""Measure your REAL latency to MEXC from wherever you run it (e.g. your colo box).

The whole lead-lag question hinges on one number: how long from "I observe the
leader move" to "my order is filled on the laggard". People quote their PING,
but that is only the floor. This probe measures the components you actually
control or observe, and reports the distribution (median is a lie; the tail is
what trades into).

What it measures (no API key needed):
  * REST round-trip latency to a light endpoint  -> gateway + network RTT
  * REST jitter (p50/p90/p99/max)                -> the part that kills you
  * websocket message inter-arrival + staleness  -> feed aggregation delay

What it CANNOT measure without keys (and why it matters):
  * order placement -> match -> ack latency. This is usually the largest and
    jitteriest piece. Treat the REST RTT below as a LOWER BOUND on your true
    observe->fill latency; real fills are slower.

Usage:
    python latency_probe.py --rest-samples 300            # REST RTT distribution
    python latency_probe.py --ws --symbols BTCUSDT --secs 60   # feed staleness
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time

import requests

BASE = "https://api.mexc.com"
WS_URL = "wss://wbs-api.mexc.com/ws"


def pctl(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def probe_rest(samples: int) -> None:
    s = requests.Session()
    s.headers.update({"User-Agent": "leadlag-latency/0.1"})
    # warm up TLS/conn
    try:
        s.get(f"{BASE}/api/v3/ping", timeout=5)
    except Exception as e:
        sys.exit(f"cannot reach MEXC: {e}")
    rtts = []
    for i in range(samples):
        t0 = time.perf_counter()
        try:
            s.get(f"{BASE}/api/v3/ping", timeout=5)
        except Exception:
            continue
        rtts.append((time.perf_counter() - t0) * 1000.0)
        time.sleep(0.05)
    if not rtts:
        sys.exit("no successful samples")
    print("\n=== REST round-trip latency (ms) ===")
    print(f"  samples : {len(rtts)}")
    print(f"  min     : {min(rtts):7.2f}")
    print(f"  p50     : {pctl(rtts,0.50):7.2f}")
    print(f"  p90     : {pctl(rtts,0.90):7.2f}")
    print(f"  p99     : {pctl(rtts,0.99):7.2f}")
    print(f"  max     : {max(rtts):7.2f}")
    print(f"  stdev   : {st.pstdev(rtts):7.2f}  (jitter)")
    print("\n  This is a LOWER BOUND on observe->fill latency. Add feed-aggregation")
    print("  delay (below) + order match/ack time (not measured) for the real number.")


def probe_ws(symbols: list[str], secs: float) -> None:
    try:
        import websocket  # websocket-client
    except ImportError:
        sys.exit("pip install websocket-client")
    import pbdecode  # noqa: F401  (kept for parity; staleness uses arrival gaps)

    arrivals: list[float] = []
    state = {"last": None}

    def on_open(ws):
        params = [f"spot@public.aggre.bookTicker.v3.api.pb@100ms@{s}" for s in symbols]
        ws.send(json.dumps({"method": "SUBSCRIPTION", "params": params}))
        state["deadline"] = time.time() + secs

    def on_message(ws, msg):
        now = time.perf_counter()
        if isinstance(msg, str):
            return  # control ack
        if state["last"] is not None:
            arrivals.append((now - state["last"]) * 1000.0)
        state["last"] = now
        if time.time() > state.get("deadline", 0):
            ws.close()

    ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message)
    ws.run_forever(ping_interval=20, ping_payload=json.dumps({"method": "PING"}))

    if not arrivals:
        sys.exit("no data frames received (check field/channel or symbols)")
    print("\n=== websocket inter-arrival gaps (ms) ===")
    print(f"  frames  : {len(arrivals)+1}")
    print(f"  p50 gap : {pctl(arrivals,0.50):7.2f}")
    print(f"  p90 gap : {pctl(arrivals,0.90):7.2f}")
    print(f"  max gap : {max(arrivals):7.2f}")
    print("\n  Gaps near ~100ms confirm the public book-ticker aggregation floor:")
    print("  new information reaches you in buckets this size, NOT continuously.")
    print("  Your reaction latency cannot be smaller than how often data arrives.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure real latency to MEXC")
    ap.add_argument("--rest-samples", type=int, default=300)
    ap.add_argument("--ws", action="store_true", help="also probe websocket feed staleness")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT"])
    ap.add_argument("--secs", type=float, default=60.0)
    args = ap.parse_args()
    probe_rest(args.rest_samples)
    if args.ws:
        probe_ws(args.symbols, args.secs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
