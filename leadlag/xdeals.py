#!/usr/bin/env python3
"""Disambiguation run: Binance bookTicker vs MEXC DEALS (trades) stream.

The aggregated bookTicker is 100ms-published, so a measured ~50ms lag could be
pure feed delay, not a real price lag. MEXC's deals stream is ~10ms-aggregated
(near real-time trade prints) — if MEXC's actual trades lag Binance's mid moves,
that's a REAL lag; if they're synced, the bookTicker lag was just aggregation.

Run on the Tokyo box.
  python3 xdeals.py raw      # verify the deals protobuf field map FIRST
  python3 xdeals.py 120      # capture + ms analysis
"""
import json, math, os, sys, threading, time
try:
    import websocket
except ImportError:
    sys.exit("pip install websocket-client")
import pbdecode

BINANCE_WS = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"
MEXC_WS = "wss://wbs-api.mexc.com/ws"
MEXC_CHAN = "spot@public.aggre.deals.v3.api.pb@10ms@BTCUSDT"
DATA = os.path.expanduser("~/xdeals_data.jsonl")

# deals protobuf field map — VERIFY with `raw`. Body likely a high field number
# holding a repeated 'deals' message; each deal has a price string somewhere.
F_BODY = 315          # adjust after `raw`
F_DEAL = 1            # repeated deal entries inside body
F_PRICE = 1          # price field inside a deal

T0 = time.perf_counter_ns()
events = []
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


def mexc_last_price(buf):
    """Extract the last trade price from a deals frame (best-effort, configurable)."""
    top = pbdecode.decode(buf)
    body_raw = top.get(F_BODY, [None])[0]
    if not body_raw:
        return None
    body = pbdecode.decode(body_raw)
    deals = body.get(F_DEAL, [])
    last = None
    for draw in deals:
        if isinstance(draw, bytes):
            d = pbdecode.decode(draw)
            v = d.get(F_PRICE, [None])[0]
            s = pbdecode.as_str(v) if v is not None else None
            try:
                if s is not None:
                    last = float(s)
            except ValueError:
                pass
    return last


def mexc_run(stopper, raw=0):
    state = {"n": 0}

    def on_open(ws):
        ws.send(json.dumps({"method": "SUBSCRIPTION", "params": [MEXC_CHAN]}))

    def on_msg(ws, msg):
        if isinstance(msg, str):
            print(f"  [mexc ctrl] {msg[:160]}")
            return
        if raw:
            state["n"] += 1
            print(f"\n--- MEXC deals frame {state['n']} ({len(msg)} bytes) ---")
            print(json.dumps(pbdecode.tree(msg), indent=2, default=str)[:2500])
            if state["n"] >= raw:
                ws.close()
            return
        p = mexc_last_price(msg)
        if p is not None:
            with lock:
                events.append((t_ms(), "M", p))
    ws = websocket.WebSocketApp(MEXC_WS, on_open=on_open, on_message=on_msg)
    stopper.append(ws)
    ws.run_forever(ping_interval=20, ping_payload=json.dumps({"method": "PING"}))


def capture(secs, raw=0):
    stoppers = []
    if raw:
        print("verifying MEXC deals frames...")
        mexc_run(stoppers, raw=raw)
        return
    print(f"capturing Binance bookTicker + MEXC deals for {secs}s...")
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
            print(f"  binance={nb}  mexc_trades={nm}", end="\r")
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


def grid_series(evs, src, bin_ms, t_lo, t_hi):
    n = int((t_hi - t_lo) / bin_ms) + 1
    g = [None] * n
    for t, s, v in evs:
        if s == src:
            i = int((t - t_lo) / bin_ms)
            if 0 <= i < n:
                g[i] = v
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


def analyze(evs=None, bin_ms=10, max_lag_bins=20):
    if evs is None:
        evs = [tuple(json.loads(l)) for l in open(DATA)]
    nb = sum(1 for e in evs if e[1] == "B")
    nm = sum(1 for e in evs if e[1] == "M")
    if nb < 50 or nm < 50:
        print(f"too few events (binance={nb}, mexc_trades={nm}) — run longer or busier hours.")
        return
    t_lo = min(e[0] for e in evs); t_hi = max(e[0] for e in evs)
    B = grid_series(evs, "B", bin_ms, t_lo, t_hi)
    M = grid_series(evs, "M", bin_ms, t_lo, t_hi)
    if None in B or None in M:
        i = next(k for k in range(len(B)) if B[k] is not None and M[k] is not None)
        B, M = B[i:], M[i:]
    rB = [math.log(B[i + 1] / B[i]) if B[i] > 0 and B[i+1] > 0 else 0.0 for i in range(len(B) - 1)]
    rM = [math.log(M[i + 1] / M[i]) if M[i] > 0 and M[i+1] > 0 else 0.0 for i in range(len(M) - 1)]
    print(f"\n====  BINANCE-mid  vs  MEXC-TRADES  (ms lead-lag)  ====")
    print(f"binance updates={nb}  mexc trades={nm}  bin={bin_ms}ms  span={(t_hi-t_lo)/1000:.0f}s")
    print("(k>0 => MEXC trades follow Binance. This is REAL-TIME, no 100ms feed delay)\n")
    best = (0, 0.0)
    for k in range(-max_lag_bins, max_lag_bins + 1):
        c = corr(rB[:len(rB) - k], rM[k:]) if k >= 0 else corr(rB[-k:], rM[:len(rM) + k])
        if c == c:
            if abs(c) > abs(best[1]):
                best = (k, c)
            if abs(c) > 0.03:
                print(f"  k={k:+3d} ({k*bin_ms:+5d}ms)  corr={c:+.3f}  {'#'*int(abs(c)*40)}")
    k, c = best
    print(f"\n  peak: k={k} ({k*bin_ms:+d}ms), corr={c:+.3f}")
    if k <= 1:
        print("  => MEXC trades move ~together with Binance (<=~10ms). The bookTicker")
        print("     lag WAS feed aggregation, not a tradeable price lag. Final no.")
    else:
        print(f"  => MEXC trades genuinely lag Binance by ~{k*bin_ms}ms. If that beats your")
        print("     ~20-40ms reaction loop, THIS is real — next test is whether you get filled.")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "60"
    if arg == "raw":
        capture(0, raw=int(sys.argv[2]) if len(sys.argv) > 2 else 3)
    elif arg == "analyze":
        analyze()
    else:
        capture(float(arg))
