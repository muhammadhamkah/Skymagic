#!/usr/bin/env python3
"""Cross-exchange MS-RESOLUTION capture: Binance vs MEXC, BTCUSDT.

Run this FROM A LOW-LATENCY BOX in the exchanges' region (e.g. AWS Tokyo).
It opens both websockets, stamps every book update with a high-resolution
LOCAL monotonic clock (shared across both streams in one process), and after
the run measures the lead-lag at millisecond resolution.

Streams:
  * Binance  wss .../btcusdt@bookTicker   -> REAL-TIME per update (JSON)
  * MEXC     wss .../aggre.bookTicker.pb   -> 100ms-AGGREGATED (protobuf)

HONEST RESOLUTION LIMIT: MEXC's public book-ticker is aggregated at 100ms, so
this resolves the lag down to ~100ms, not finer. If the lag is <100ms you will
see "same bin" and CANNOT conclude it's tradeable — you'd then need MEXC's
deals stream (10ms) or incremental-depth stream. This tool answers: "is there a
lag of ~100ms+?" — which is the cheapest decisive cut.

Deps: pip install websocket-client
First run MUST verify the MEXC protobuf fields:
    python3 xcap.py raw        # prints decoded MEXC frames; confirm field map
Then:
    python3 xcap.py 120        # capture 120s -> ~/xcap_data.jsonl, then analyze
    python3 xcap.py analyze    # re-analyze the saved file
"""
import json, math, os, sys, threading, time

try:
    import websocket  # websocket-client
except ImportError:
    sys.exit("pip install websocket-client")
import pbdecode

BINANCE_WS = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"
MEXC_WS = "wss://wbs-api.mexc.com/ws"
MEXC_CHAN = "spot@public.aggre.bookTicker.v3.api.pb@100ms@BTCUSDT"
DATA = os.path.expanduser("~/xcap_data.jsonl")

# MEXC protobuf field map (verified live via `raw`): body is field 315
F_SYMBOL, F_BODY, F_BID, F_ASK = 3, 315, 1, 3

T0 = time.perf_counter_ns()
events = []          # (t_ms, "B"/"M", mid)
lock = threading.Lock()


def t_ms():
    return (time.perf_counter_ns() - T0) / 1e6


def binance_run(stopper):
    def on_msg(ws, msg):
        try:
            d = json.loads(msg)
            mid = (float(d["b"]) + float(d["a"])) / 2
            with lock:
                events.append((t_ms(), "B", mid))
        except Exception:
            pass
    ws = websocket.WebSocketApp(BINANCE_WS, on_message=on_msg)
    stopper.append(ws)
    ws.run_forever(ping_interval=20)


def mexc_extract(buf):
    top = pbdecode.decode(buf)
    body_raw = top.get(F_BODY, [None])[0]
    if not body_raw:
        return None
    body = pbdecode.decode(body_raw)

    def f(fid):
        v = body.get(fid, [None])[0]
        s = pbdecode.as_str(v) if v is not None else None
        try:
            return float(s) if s is not None else None
        except ValueError:
            return None
    bid, ask = f(F_BID), f(F_ASK)
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def mexc_run(stopper, raw=0):
    state = {"raw_seen": 0}

    def on_open(ws):
        ws.send(json.dumps({"method": "SUBSCRIPTION", "params": [MEXC_CHAN]}))

    def on_msg(ws, msg):
        if isinstance(msg, str):
            print(f"  [mexc ctrl] {msg[:160]}")
            return
        if raw:
            state["raw_seen"] += 1
            print(f"\n--- MEXC frame {state['raw_seen']} ({len(msg)} bytes) ---")
            print(json.dumps(pbdecode.tree(msg), indent=2, default=str)[:1500])
            if state["raw_seen"] >= raw:
                ws.close()
            return
        mid = mexc_extract(msg)
        if mid is not None:
            with lock:
                events.append((t_ms(), "M", mid))
    ws = websocket.WebSocketApp(MEXC_WS, on_open=on_open, on_message=on_msg)
    stopper.append(ws)
    ws.run_forever(ping_interval=20, ping_payload=json.dumps({"method": "PING"}))


def capture(secs, raw=0):
    stoppers = []
    if raw:
        print("verifying MEXC protobuf frames (no Binance)...")
        mexc_run(stoppers, raw=raw)
        return
    print(f"capturing Binance + MEXC BTCUSDT for {secs}s...")
    tb = threading.Thread(target=binance_run, args=(stoppers,), daemon=True)
    tm = threading.Thread(target=mexc_run, args=(stoppers,), daemon=True)
    tb.start(); tm.start()
    t_end = time.time() + secs
    try:
        while time.time() < t_end:
            time.sleep(1)
            with lock:
                nb = sum(1 for e in events if e[1] == "B")
                nm = sum(1 for e in events if e[1] == "M")
            print(f"  binance={nb}  mexc={nm}", end="\r")
    except KeyboardInterrupt:
        pass
    for ws in stoppers:
        try:
            ws.close()
        except Exception:
            pass
    time.sleep(0.5)
    with lock:
        snap = list(events)
    with open(DATA, "w") as f:
        for t, src, mid in snap:
            f.write(json.dumps([t, src, mid]) + "\n")
    print(f"\nsaved {len(snap)} events to {DATA}")
    analyze(snap)


def load():
    out = []
    with open(DATA) as f:
        for line in f:
            t, src, mid = json.loads(line)
            out.append((t, src, mid))
    return out


def grid_series(evs, src, bin_ms, t_lo, t_hi):
    n = int((t_hi - t_lo) / bin_ms) + 1
    g = [None] * n
    for t, s, mid in evs:
        if s == src:
            i = int((t - t_lo) / bin_ms)
            if 0 <= i < n:
                g[i] = mid          # last update in the bin wins
    last = None
    for i in range(n):
        if g[i] is not None:
            last = g[i]
        g[i] = last
    return g


def corr(a, b):
    n = min(len(a), len(b)); a, b = a[:n], b[:n]
    if n < 5:
        return float("nan")
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a); vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return float("nan")
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / math.sqrt(va * vb)


def analyze(evs=None, bin_ms=20, max_lag_bins=15):
    if evs is None:
        evs = load()
    nb = sum(1 for e in evs if e[1] == "B")
    nm = sum(1 for e in evs if e[1] == "M")
    if nb < 50 or nm < 50:
        print(f"too few events (binance={nb}, mexc={nm}) — run longer.")
        return
    t_lo = min(e[0] for e in evs); t_hi = max(e[0] for e in evs)
    B = grid_series(evs, "B", bin_ms, t_lo, t_hi)
    M = grid_series(evs, "M", bin_ms, t_lo, t_hi)
    if None in B or None in M:
        i = next(k for k in range(len(B)) if B[k] is not None and M[k] is not None)
        B, M = B[i:], M[i:]
    rB = [math.log(B[i + 1] / B[i]) for i in range(len(B) - 1)]
    rM = [math.log(M[i + 1] / M[i]) for i in range(len(M) - 1)]
    print(f"\n========  CROSS-EXCHANGE MS LEAD-LAG  ========")
    print(f"binance updates={nb} (real-time)  mexc updates={nm} (100ms-agg)")
    print(f"bin={bin_ms}ms  span={ (t_hi-t_lo)/1000:.0f}s   (k>0 => MEXC follows Binance)\n")
    best = (0, 0.0)
    for k in range(-max_lag_bins, max_lag_bins + 1):
        c = corr(rB[:len(rB) - k], rM[k:]) if k >= 0 else corr(rB[-k:], rM[:len(rM) + k])
        if c == c:
            if abs(c) > abs(best[1]):
                best = (k, c)
            mark = "  <<" if k == best[0] else ""
            print(f"  k={k:+3d} ({k*bin_ms:+5d}ms)  corr={c:+.3f}  {'#'*int(abs(c)*40)}")
    k, c = best
    print(f"\n  peak: k={k} ({k*bin_ms:+d}ms), corr={c:+.3f}")
    if k <= 0:
        print("  => MEXC does NOT lag Binance at >=100ms resolution. No tradeable lag")
        print("     at this cut. (A sub-100ms lag can't be seen here — needs deals stream.)")
    else:
        print(f"  => MEXC appears to lag Binance by ~{k*bin_ms}ms. If that exceeds your")
        print("     measured order latency here, THIS is the real candidate. Verify fills next.")
    print("\n  Caveat: local receive timestamps. Run on the colo box so both feeds")
    print("  arrive with ~equal, single-digit-ms transport; otherwise lag is biased.")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "60"
    if arg == "raw":
        capture(0, raw=int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    elif arg == "analyze":
        analyze()
    else:
        capture(float(arg))
