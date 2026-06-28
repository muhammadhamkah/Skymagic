#!/usr/bin/env python3
"""Breadth scan: which of the top-N pairs show a tradeable Binance->MEXC lag?

Pure stdlib. One all-symbols request per exchange per cycle (so 150 pairs cost
2 requests, not 300). For each pair it measures event-conditioned +1-bucket
follow-through net of MEXC cost, computed SEPARATELY in the first and second
half of the run. Only pairs that win in BOTH halves are credible — this is the
guard against the false positives you'd otherwise get scanning 150 pairs.

Run: python3 scan.py
"""
import json, math, os, sys, threading, time, urllib.request

BINANCE_BOOK = "https://api.binance.com/api/v3/ticker/bookTicker"
BINANCE_24H = "https://api.binance.com/api/v3/ticker/24hr"
MEXC_BOOK = "https://api.mexc.com/api/v3/ticker/bookTicker"
QUOTE = "USDT"
TOP_N = 150
DURATION_MIN = 20
CADENCE_S = 0.30
COST_SLIP_BPS = 1.0
EVENT_PCTL = 0.90


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "scan/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def build_universe():
    print("ranking top pairs by Binance 24h volume, intersecting with MEXC...")
    b24 = get(BINANCE_24H)
    bvol = {d["symbol"]: float(d["quoteVolume"]) for d in b24 if d["symbol"].endswith(QUOTE)}
    mset = {d["symbol"] for d in get(MEXC_BOOK)}
    common = sorted((s for s in bvol if s in mset), key=lambda s: -bvol[s])
    uni = common[:TOP_N]
    print(f"universe: {len(uni)} pairs common to both exchanges (top by volume)")
    return uni


def fetch_book(url, out, key):
    try:
        out[key] = {d["symbol"]: (float(d["bidPrice"]), float(d["askPrice"])) for d in get(url, 15)}
    except Exception as e:
        out[key + "_err"] = str(e)


def snapshot(universe):
    out = {}
    tb = threading.Thread(target=fetch_book, args=(BINANCE_BOOK, out, "B"))
    tm = threading.Thread(target=fetch_book, args=(MEXC_BOOK, out, "M"))
    tb.start(); tm.start(); tb.join(); tm.join()
    ts = time.time()
    if "B" not in out or "M" not in out:
        return ts, None
    snap = {}
    for s in universe:
        if s in out["B"] and s in out["M"]:
            snap[s] = (out["B"][s], out["M"][s])
    return ts, snap


def collect(universe):
    print("connectivity check...")
    _, snap = snapshot(universe)
    if not snap:
        print("ERROR: could not read both exchanges. If Binance is blocked, tell me.")
        sys.exit(1)
    print(f"OK — {len(snap)} pairs live. collecting every {CADENCE_S*1000:.0f}ms "
          f"for {DURATION_MIN}min (Ctrl+C to stop early)...\n")
    out = []
    dead = time.time() + DURATION_MIN * 60
    try:
        while time.time() < dead:
            t0 = time.time()
            ts, sn = snapshot(universe)
            if sn:
                out.append((ts, sn))
                if len(out) % 100 == 0:
                    print(f"  {len(out)} samples...")
            dt = CADENCE_S - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        print("\nstopped early.")
    return out


def lr(xs):
    return [math.log(xs[i + 1] / xs[i]) for i in range(len(xs) - 1)]


def corr(a, b):
    n = min(len(a), len(b)); a, b = a[:n], b[:n]
    if n < 5:
        return float("nan")
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a); vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return float("nan")
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / math.sqrt(va * vb)


def followthrough(lb, lm, lo, hi, thr, cost):
    """mean +1-bucket MEXC follow-through (in BTC-move direction) minus cost, over [lo,hi)."""
    ev = [i for i in range(lo, hi - 1) if abs(lb[i]) > thr]
    vals = [(1 if lb[i] > 0 else -1) * lm[i + 1] * 1e4 for i in ev if i + 1 < len(lm)]
    if not vals:
        return None, 0
    return sum(vals) / len(vals) - cost, len(vals)


def eval_pair(samples, pair):
    s = [x for x in samples if pair in x[1]]
    if len(s) < 200:
        return None
    bmid = [(x[1][pair][0][0] + x[1][pair][0][1]) / 2 for x in s]
    mmid = [(x[1][pair][1][0] + x[1][pair][1][1]) / 2 for x in s]
    lb, lm = lr(bmid), lr(mmid)
    if len(lb) < 200:
        return None
    spreads = sorted((x[1][pair][1][1] - x[1][pair][1][0]) /
                     ((x[1][pair][1][0] + x[1][pair][1][1]) / 2) * 1e4 for x in s)
    spread = spreads[len(spreads) // 2]
    cost = spread + 2 * COST_SLIP_BPS
    al = sorted(abs(x) for x in lb)
    thr = al[int(len(al) * EVENT_PCTL)]
    mid = len(lb) // 2
    net_all, n = followthrough(lb, lm, 0, len(lb), thr, cost)
    net_h1, _ = followthrough(lb, lm, 0, mid, thr, cost)
    net_h2, _ = followthrough(lb, lm, mid, len(lb), thr, cost)
    # peak lag of cross-correlation
    best = (0, 0.0)
    for k in range(0, 4):
        c = corr(lb[:len(lb) - k], lm[k:])
        if c == c and abs(c) > abs(best[1]):
            best = (k, c)
    if net_all is None or net_h1 is None or net_h2 is None:
        return None
    return dict(pair=pair, net=net_all, h1=net_h1, h2=net_h2, n=n,
                spread=spread, peakk=best[0], peakc=best[1])


def analyze(samples):
    if len(samples) < 200:
        print(f"only {len(samples)} samples — run longer."); return
    grid = sorted(samples[i + 1][0] - samples[i][0]
                  for i in range(len(samples) - 1))[len(samples) // 2] * 1000
    pairs = sorted({p for _, sn in samples for p in sn})
    rows = [r for r in (eval_pair(samples, p) for p in pairs) if r]
    rows.sort(key=lambda r: -r["net"])
    persistent = [r for r in rows if r["h1"] > 0 and r["h2"] > 0 and r["peakk"] > 0]
    pos_all = [r for r in rows if r["net"] > 0]
    print(f"\n================  SCAN RESULT  ================")
    print(f"samples={len(samples)}  cadence={grid:.0f}ms  pairs evaluated={len(rows)}")
    print(f"positive overall: {len(pos_all)}   |   PERSISTENT (both halves + lag>0): {len(persistent)}")
    print(f"(by chance ~{0.05*len(rows):.0f} pairs would look positive in one window — "
          "ignore those; trust only persistent)\n")
    print(f"  {'pair':<14}{'net+1':>8}{'half1':>8}{'half2':>8}{'peak':>6}{'spread':>8}{'evts':>6}")
    print("  " + "-" * 58)
    for r in (persistent if persistent else rows[:15]):
        flag = " *" if (r["h1"] > 0 and r["h2"] > 0 and r["peakk"] > 0) else ""
        print(f"  {r['pair']:<14}{r['net']:>+7.2f}{r['h1']:>+8.2f}{r['h2']:>+8.2f}"
              f"{r['peakk']*grid:>+5.0f}{r['spread']:>7.1f}{r['n']:>6}{flag}")
    if not persistent:
        print("\n  (showing top 15 by overall net; NONE persisted in both halves —)")
        print("  (that means no credible tradeable lag at this resolution.)")
    else:
        print(f"\n  * = credible: positive in BOTH halves AND MEXC follows at +{grid:.0f}ms.")
        print("  These are the only ones worth a finer (websocket) look + latency test.")
    print("\nCaveat: home measurement carries relative-latency bias; numbers are bps.")


if __name__ == "__main__":
    analyze(collect(build_universe()))
