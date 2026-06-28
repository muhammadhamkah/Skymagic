#!/usr/bin/env python3
"""Self-contained lead-lag screen for MEXC — pure standard library, no pip installs.

Polls best bid/ask for a leader + laggards via the public REST API, then measures
the cross-correlation lead-lag and a round-trip cost reality check. One file, no
repo, no protobuf, no dependencies beyond what ships with Python 3.

Run:   python3 leadlag.py
Stop early any time with Ctrl+C — it analyzes whatever it has collected.
"""

import json
import math
import sys
import threading
import time
import urllib.request

# ---- config (edit if you like) ------------------------------------------
LEADER = "BTCUSDT"
LAGGARDS = ["ETHUSDT", "SOLUSDT"]
DURATION_MIN = 20
CADENCE_S = 0.20            # ~200ms grid; resolves lags >= ~200ms
COST_SLIP_BPS = 1.0        # per-side slippage; taker fee assumed 0 (0-fee pair)
# -------------------------------------------------------------------------

URL = "https://api.mexc.com/api/v3/ticker/bookTicker?symbol={}"
WANTED = [LEADER] + LAGGARDS


def fetch_one(sym, out):
    try:
        req = urllib.request.Request(URL.format(sym), headers={"User-Agent": "leadlag/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        out[sym] = (float(d["bidPrice"]), float(d["askPrice"]))
    except Exception:
        pass


def snapshot():
    """Fetch all wanted symbols concurrently so they share one near-instant timestamp."""
    out = {}
    threads = [threading.Thread(target=fetch_one, args=(s, out)) for s in WANTED]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return time.time(), out


def collect():
    print("checking connectivity...")
    ts, snap = snapshot()
    if LEADER not in snap:
        print("\nERROR: could not read MEXC market data.")
        print("If this hangs or errors, MEXC may be geo-blocked from your location")
        print("(common in some countries). In that case you need a server in another")
        print("region. Otherwise check your internet connection.")
        sys.exit(1)
    print(f"connected. collecting {WANTED} every {CADENCE_S*1000:.0f}ms for "
          f"{DURATION_MIN} min (Ctrl+C to stop early)...\n")
    samples = []
    deadline = time.time() + DURATION_MIN * 60
    try:
        while time.time() < deadline:
            t0 = time.time()
            ts, snap = snapshot()
            if all(s in snap for s in WANTED):
                samples.append((ts, snap))
                if len(samples) % 200 == 0:
                    print(f"  {len(samples)} samples...")
            dt = CADENCE_S - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        print("\nstopped early.")
    return samples


def mids(samples, sym):
    return [(s[1][sym][0] + s[1][sym][1]) / 2 for s in samples]


def logrets(xs):
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
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / math.sqrt(va * vb)


def analyze(samples):
    if len(samples) < 60:
        print(f"\nonly {len(samples)} samples — run longer for a reliable read.")
        return
    dts = sorted(samples[i + 1][0] - samples[i][0] for i in range(len(samples) - 1))
    grid_ms = dts[len(dts) // 2] * 1000.0
    lead = logrets(mids(samples, LEADER))
    print(f"\n================  RESULT  ================")
    print(f"samples={len(samples)}   median cadence={grid_ms:.0f}ms")
    for lagg in LAGGARDS:
        lr = logrets(mids(samples, lagg))
        print(f"\n=== {LEADER} -> {lagg} (k>0 means {lagg} FOLLOWS {LEADER}) ===")
        best = (0, 0.0)
        for k in range(-5, 6):
            if k >= 0:
                c = corr(lead[: len(lead) - k], lr[k:])
            else:
                c = corr(lead[-k:], lr[: len(lr) + k])
            if c == c:  # not nan
                if abs(c) > abs(best[1]):
                    best = (k, c)
                bar = "#" * int(abs(c) * 40)
                print(f"  k={k:+d} ({k*grid_ms:+5.0f}ms)  corr={c:+.3f}  {bar}")
        k, c = best
        spreads = sorted(
            (samples[i][1][lagg][1] - samples[i][1][lagg][0])
            / ((samples[i][1][lagg][0] + samples[i][1][lagg][1]) / 2) * 1e4
            for i in range(len(samples))
        )
        medspread = spreads[len(spreads) // 2]
        sigma = math.sqrt(sum(x * x for x in lr) / len(lr)) * 1e4
        capture = abs(c) * sigma
        cost = medspread + 2 * COST_SLIP_BPS
        good = k > 0 and capture > cost
        print(f"  peak: k={k} ({k*grid_ms:+.0f}ms), corr={c:+.3f}")
        print(f"  laggard spread~{medspread:.1f}bps | capture~{capture:.1f}bps | "
              f"round-trip cost~{cost:.1f}bps")
        print(f"  -> {'POSSIBLE lead at this resolution (worth a finer look)' if good else 'NO edge after round-trip cost'}")
    print(f"\nNOTE: ~{grid_ms:.0f}ms REST screen. Detects lags >= one step, i.e. the")
    print("~250ms regime you're betting on. A k<=0 peak means the leader does NOT lead.")
    print("To resolve sub-100ms lags you'd still need the websocket collector.")


if __name__ == "__main__":
    analyze(collect())
