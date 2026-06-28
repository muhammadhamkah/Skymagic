#!/usr/bin/env python3
"""Cross-exchange lead-lag screen: does Binance LEAD MEXC on the same asset?

Pure stdlib, no installs. Polls BTCUSDT best bid/ask on BOTH exchanges
concurrently, then measures cross-correlation lead-lag + event-conditioned
follow-through + round-trip cost (on MEXC, where you'd execute).

IMPORTANT measurement caveat: cross-exchange timing picks up your RELATIVE
latency to each venue. From a home machine this is a screen (does a lag exist?),
not the exact tradeable number — that must be re-measured from your colo box.

Run:        python3 xexch.py
Re-analyze: python3 xexch.py reanalyze     (uses saved ~/xexch_data.json)
Stop early with Ctrl+C.
"""
import json, math, os, sys, threading, time, urllib.request

SYMBOL = "BTCUSDT"
SOURCES = {
    "BINANCE": "https://api.binance.com/api/v3/ticker/bookTicker?symbol={}",
    "MEXC":    "https://api.mexc.com/api/v3/ticker/bookTicker?symbol={}",
}
LEADER, LAGGARD = "BINANCE", "MEXC"     # hypothesis: Binance leads, MEXC follows
DURATION_MIN = 20
CADENCE_S = 0.20
COST_SLIP_BPS = 1.0
EVENT_PCTL = 0.90

ORDER = [LEADER, LAGGARD]


def fetch_one(name, out):
    try:
        req = urllib.request.Request(SOURCES[name].format(SYMBOL),
                                     headers={"User-Agent": "xexch/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        out[name] = (float(d["bidPrice"]), float(d["askPrice"]))
    except Exception as e:
        out[name + "_err"] = str(e)


def snapshot():
    out = {}
    ts = [threading.Thread(target=fetch_one, args=(n, out)) for n in ORDER]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return time.time(), out


def collect():
    print("checking connectivity to both exchanges...")
    _, snap = snapshot()
    for name in ORDER:
        if name in snap:
            print(f"  {name}: OK")
        else:
            print(f"  {name}: UNREACHABLE — {snap.get(name + '_err', 'no data')}")
    if not all(n in snap for n in ORDER):
        print("\nCannot reach both exchanges. If BINANCE is blocked from your region,")
        print("tell me and we'll swap the leader to OKX/Bybit or move to a cloud box.")
        sys.exit(1)
    print(f"\ncollecting {SYMBOL} on {ORDER} every {CADENCE_S*1000:.0f}ms for "
          f"{DURATION_MIN}min (Ctrl+C to stop early)...\n")
    out = []
    dead = time.time() + DURATION_MIN * 60
    try:
        while time.time() < dead:
            t0 = time.time()
            ts, sn = snapshot()
            if all(n in sn for n in ORDER):
                out.append((ts, {n: sn[n] for n in ORDER}))
                if len(out) % 200 == 0:
                    print(f"  {len(out)} samples...")
            dt = CADENCE_S - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        print("\nstopped early.")
    json.dump(out, open(os.path.expanduser("~/xexch_data.json"), "w"))
    print("raw data saved to ~/xexch_data.json")
    return out


def mids(s, name):
    return [(x[1][name][0] + x[1][name][1]) / 2 for x in s]


def lr(xs):
    return [math.log(xs[i + 1] / xs[i]) for i in range(len(xs) - 1)]


def corr(a, b):
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    if n < 5:
        return float("nan")
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return float("nan")
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / math.sqrt(va * vb)


def analyze(samples):
    if len(samples) < 100:
        print(f"only {len(samples)} samples — run longer.")
        return
    grid = sorted(samples[i + 1][0] - samples[i][0]
                  for i in range(len(samples) - 1))[len(samples) // 2] * 1000
    lead = lr(mids(samples, LEADER))
    lag = lr(mids(samples, LAGGARD))
    print(f"\n================  RESULT  ================")
    print(f"samples={len(samples)}  cadence={grid:.0f}ms   {LEADER} -> {LAGGARD}  ({SYMBOL})")
    print(f"(k>0 means {LAGGARD} FOLLOWS {LEADER} — the tradeable direction)\n")
    best = (0, 0.0)
    for k in range(-5, 6):
        c = corr(lead[:len(lead) - k], lag[k:]) if k >= 0 else corr(lead[-k:], lag[:len(lag) + k])
        if c == c:
            if abs(c) > abs(best[1]):
                best = (k, c)
            print(f"  k={k:+d} ({k*grid:+5.0f}ms)  corr={c:+.3f}  {'#'*int(abs(c)*40)}")
    k, c = best
    print(f"  peak: k={k} ({k*grid:+.0f}ms), corr={c:+.3f}")

    # event-conditioned follow-through (MEXC side, the executable leg)
    al = sorted(abs(x) for x in lead)
    thr = al[int(len(al) * EVENT_PCTL)]
    events = [i for i in range(len(lead)) if abs(lead[i]) > thr]
    sp = sorted((samples[i][1][LAGGARD][1] - samples[i][1][LAGGARD][0])
                / ((samples[i][1][LAGGARD][0] + samples[i][1][LAGGARD][1]) / 2) * 1e4
                for i in range(len(samples)))
    spread = sp[len(sp) // 2]
    cost = spread + 2 * COST_SLIP_BPS
    print(f"\nbig {LEADER} moves (top {(1-EVENT_PCTL)*100:.0f}%): {len(events)} events, "
          f"threshold {thr*1e4:.1f}bps;  {LAGGARD} spread ~{spread:.2f}bps")
    for h, name in [(0, f"same bucket (k=0, {LAGGARD} already moved — NO lag)"),
                    (1, "next bucket (+1, the tradeable lag)"),
                    (2, "+2 buckets")]:
        vals = [(1 if lead[i] > 0 else -1) * lag[i + h] * 1e4 for i in events if i + h < len(lag)]
        if vals:
            m = sum(vals) / len(vals)
            extra = ""
            if h == 1:
                extra = f"   - cost {cost:.1f}bps = NET {m-cost:+.2f}bps  [{'EDGE' if m-cost > 0 else 'no edge'}]"
            print(f"  {name:46} follow-through {m:+6.2f}bps{extra}")
    print(f"\nRead: a real, tradeable lag = positive corr peak at k>0 AND positive NET on")
    print(f"the +1 line. If the move is all in the 'same bucket', {LAGGARD} already moved")
    print(f"with {LEADER} at this resolution — nothing to trade above ~{grid:.0f}ms.")
    print("Caveat: home measurement includes your relative latency to each venue.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "reanalyze":
        analyze(json.load(open(os.path.expanduser("~/xexch_data.json"))))
    else:
        analyze(collect())
