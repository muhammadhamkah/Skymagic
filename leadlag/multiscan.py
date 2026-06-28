#!/usr/bin/env python3
"""Multi-exchange lead-lag: who LEADS, who is the slowest FOLLOWER? Pure stdlib.

Polls ~9 exchanges' all-symbols tickers concurrently, builds a lead-lag
leadership ranking, and flags tradeable leader->follower candidates with a
two-window persistence guard. Reports each exchange's RTT so you can tell a
GENUINE lag from a NETWORK-DISTANCE artifact (an exchange far from you looks
"slow" but isn't tradeably slow).

Steps:
  python3 multiscan.py health     # FIRST: which exchanges parse + their RTT
  python3 multiscan.py            # collect ~20min, then full analysis
"""
import json, math, sys, threading, time, urllib.request

QUOTE = "USDT"
DURATION_MIN = 20
CADENCE_S = 0.40            # 9 exchanges/cycle -> a bit slower; ~400ms grid
COST_SLIP_BPS = 1.0
EVENT_PCTL = 0.90
MIN_EXCHANGES = 5          # only study symbols present on >= this many venues
TOP_N = 80                 # cap universe size (pure-python runtime)
MATRIX_SYMS = 40           # cap symbols used for the leadership matrix


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "multiscan/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# --- per-exchange adapters: return list of (raw_symbol, bid, ask) -----------
def _binance(j):   return [(d["symbol"], d["bidPrice"], d["askPrice"]) for d in j]
def _bybit(j):     return [(d["symbol"], d["bid1Price"], d["ask1Price"]) for d in j["result"]["list"]]
def _okx(j):       return [(d["instId"], d["bidPx"], d["askPx"]) for d in j["data"]]
def _kucoin(j):    return [(d["symbol"], d["buy"], d["sell"]) for d in j["data"]["ticker"]]
def _gate(j):      return [(d["currency_pair"], d["highest_bid"], d["lowest_ask"]) for d in j]
def _bitget(j):    return [(d["symbol"], d["bidPr"], d["askPr"]) for d in j["data"]]
def _htx(j):       return [(d["symbol"], d["bid"], d["ask"]) for d in j["data"]]
def _cryptocom(j): return [(d["i"], d["b"], d["k"]) for d in j["result"]["data"]]

ADAPTERS = [
    ("BINANCE",   "https://api.binance.com/api/v3/ticker/bookTicker", _binance),
    ("MEXC",      "https://api.mexc.com/api/v3/ticker/bookTicker",    _binance),
    ("BYBIT",     "https://api.bybit.com/v5/market/tickers?category=spot", _bybit),
    ("OKX",       "https://www.okx.com/api/v5/market/tickers?instType=SPOT", _okx),
    ("KUCOIN",    "https://api.kucoin.com/api/v1/market/allTickers",  _kucoin),
    ("GATE",      "https://api.gateio.ws/api/v4/spot/tickers",        _gate),
    ("BITGET",    "https://api.bitget.com/api/v2/spot/market/tickers", _bitget),
    ("HTX",       "https://api.huobi.pro/market/tickers",             _htx),
    ("CRYPTOCOM", "https://api.crypto.com/v2/public/get-ticker",      _cryptocom),
]
NAMES = [a[0] for a in ADAPTERS]


def normalize(s):
    return s.upper().replace("-", "").replace("_", "").replace("/", "")


def fetch_ex(name, url, parser, out):
    t0 = time.time()
    try:
        j = get(url)
        book = {}
        for raw, b, a in parser(j):
            try:
                bb, aa = float(b), float(a)
                if bb > 0 and aa > 0:
                    book[normalize(raw)] = (bb, aa)
            except (ValueError, TypeError):
                continue
        out[name] = book
        out[name + "_rtt"] = (time.time() - t0) * 1000
    except Exception as e:
        out[name + "_err"] = repr(e)
        out[name + "_rtt"] = (time.time() - t0) * 1000


def snapshot():
    out = {}
    ts = [threading.Thread(target=fetch_ex, args=(n, u, p, out)) for n, u, p in ADAPTERS]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return time.time(), out


def health():
    print("polling all exchanges once...\n")
    _, out = snapshot()
    print(f"  {'exchange':<12}{'status':<10}{'symbols':>9}{'rtt_ms':>9}")
    print("  " + "-" * 40)
    ok = []
    for n in NAMES:
        if n in out:
            print(f"  {n:<12}{'OK':<10}{len(out[n]):>9}{out[n+'_rtt']:>9.0f}")
            ok.append(n)
        else:
            err = out.get(n + "_err", "?")[:40]
            print(f"  {n:<12}{'FAIL':<10}{'-':>9}{out.get(n+'_rtt',0):>9.0f}   {err}")
    print(f"\n  {len(ok)}/{len(NAMES)} exchanges live.")
    print("  RTT = network distance from THIS machine. Big RTT spread means the")
    print("  lead-lag matrix will be biased: far exchanges look 'slow' but aren't")
    print("  tradeably slow. Tell me any FAILs and I'll fix that adapter.")
    return ok


def build_universe(out):
    present = {}
    for n in NAMES:
        for s in out.get(n, {}):
            if s.endswith(QUOTE):
                present[s] = present.get(s, 0) + 1
    common = [s for s, c in present.items() if c >= MIN_EXCHANGES]
    common.sort(key=lambda s: -present[s])
    return common[:TOP_N]


def collect():
    print("connectivity check...")
    _, out = snapshot()
    live = [n for n in NAMES if n in out]
    if len(live) < 3:
        print("Too few exchanges reachable:", {n: out.get(n + "_err") for n in NAMES if n not in out})
        sys.exit(1)
    uni = build_universe(out)
    print(f"{len(live)} exchanges live: {live}")
    print(f"universe: {len(uni)} symbols on >= {MIN_EXCHANGES} venues")
    print(f"collecting every {CADENCE_S*1000:.0f}ms for {DURATION_MIN}min (Ctrl+C to stop)...\n")
    rtt = {n: [] for n in NAMES}
    samples = []
    dead = time.time() + DURATION_MIN * 60
    try:
        while time.time() < dead:
            t0 = time.time()
            ts, out = snapshot()
            row = {}
            for n in NAMES:
                if n in out:
                    rtt[n].append(out[n + "_rtt"])
                    for s in uni:
                        if s in out[n]:
                            row.setdefault(s, {})[n] = out[n][s]
            samples.append((ts, row))
            if len(samples) % 50 == 0:
                print(f"  {len(samples)} samples...")
            dt = CADENCE_S - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        print("\nstopped early.")
    med_rtt = {n: (sorted(rtt[n])[len(rtt[n]) // 2] if rtt[n] else float("nan")) for n in NAMES}
    return samples, uni, med_rtt


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


def series(samples, sym, ex):
    out = []
    for _, row in samples:
        if sym in row and ex in row[sym]:
            bid, ask = row[sym][ex]
            out.append((bid + ask) / 2)
        else:
            out.append(None)
    # forward-fill
    last = None
    ff = []
    for x in out:
        if x is not None:
            last = x
        ff.append(last)
    return ff if all(v is not None for v in ff) else None


def analyze(bundle):
    samples, uni, med_rtt = bundle
    if len(samples) < 150:
        print(f"only {len(samples)} samples — run longer.")
        return
    grid = sorted(samples[i + 1][0] - samples[i][0]
                  for i in range(len(samples) - 1))[len(samples) // 2] * 1000

    # leadership matrix: lead[A][B] = how much A leads B (sum over symbols of
    # [corr(A_t, B_{t+1}) - corr(A_{t+1}, B_t)])
    lead = {a: {b: 0.0 for b in NAMES} for a in NAMES}
    counts = {a: {b: 0 for b in NAMES} for a in NAMES}
    for sym in uni[:MATRIX_SYMS]:
        ser = {n: series(samples, sym, n) for n in NAMES}
        rets = {n: lr(ser[n]) for n in NAMES if ser[n]}
        exs = list(rets)
        for i in range(len(exs)):
            for j in range(i + 1, len(exs)):
                A, B = exs[i], exs[j]
                ab = corr(rets[A][:-1], rets[B][1:])   # A leads B
                ba = corr(rets[B][:-1], rets[A][1:])    # B leads A
                if ab == ab and ba == ba:
                    lead[A][B] += ab - ba
                    lead[B][A] += ba - ab
                    counts[A][B] += 1
                    counts[B][A] += 1

    score = {}
    for a in NAMES:
        tot = sum(lead[a][b] for b in NAMES)
        n = sum(counts[a][b] for b in NAMES)
        score[a] = tot / n if n else float("nan")
    ranked = sorted((a for a in NAMES if score[a] == score[a]), key=lambda a: -score[a])

    print(f"\n================  MULTI-EXCHANGE RESULT  ================")
    print(f"samples={len(samples)}  cadence={grid:.0f}ms  symbols={len(uni)}")
    print(f"\nLEADERSHIP (higher = leads others; lower = follows). RTT from your machine:")
    print(f"  {'exchange':<12}{'lead_score':>12}{'rtt_ms':>9}")
    print("  " + "-" * 33)
    for a in ranked:
        print(f"  {a:<12}{score[a]:>+12.4f}{med_rtt[a]:>9.0f}")
    if len(ranked) >= 2:
        leader, follower = ranked[0], ranked[-1]
        print(f"\n  apparent LEADER: {leader}   apparent SLOWEST FOLLOWER: {follower}")
        print(f"  ⚠ check RTT: if {follower} just has the largest RTT, its 'lag' is")
        print(f"    network distance from you, NOT a tradeable edge.")
        tradeable_check(samples, uni, leader, follower, grid)


def ft(lL, lF, lo, hi, thr, cost):
    ev = [i for i in range(lo, hi - 1) if abs(lL[i]) > thr]
    vals = [(1 if lL[i] > 0 else -1) * lF[i + 1] * 1e4 for i in ev if i + 1 < len(lF)]
    return (sum(vals) / len(vals) - cost, len(vals)) if vals else (None, 0)


def tradeable_check(samples, uni, leader, follower, grid):
    print(f"\n  tradeable check: {leader} -> {follower}, +1 bucket, persistence guard")
    print(f"  {'symbol':<14}{'net+1':>8}{'half1':>8}{'half2':>8}{'evts':>6}")
    print("  " + "-" * 44)
    hits = 0
    for sym in uni:
        sl = series(samples, sym, leader)
        sf = series(samples, sym, follower)
        if not sl or not sf:
            continue
        lL, lF = lr(sl), lr(sf)
        if len(lL) < 200:
            continue
        # follower spread for cost
        sp = []
        for _, row in samples:
            if sym in row and follower in row[sym]:
                bid, ask = row[sym][follower]
                sp.append((ask - bid) / ((ask + bid) / 2) * 1e4)
        spread = sorted(sp)[len(sp) // 2] if sp else 5.0
        cost = spread + 2 * COST_SLIP_BPS
        al = sorted(abs(x) for x in lL)
        thr = al[int(len(al) * EVENT_PCTL)]
        mid = len(lL) // 2
        na, n = ft(lL, lF, 0, len(lL), thr, cost)
        h1, _ = ft(lL, lF, 0, mid, thr, cost)
        h2, _ = ft(lL, lF, mid, len(lL), thr, cost)
        if na is None or h1 is None or h2 is None:
            continue
        if h1 > 0 and h2 > 0:   # persistent only
            hits += 1
            print(f"  {sym:<14}{na:>+7.2f}{h1:>+8.2f}{h2:>+8.2f}{n:>6} *")
    if hits == 0:
        print("  (none persisted in both halves — no credible tradeable lag)")
    print("\n  numbers in bps. * = positive in BOTH halves. Caveat: home-measured,")
    print("  so subtract the leader/follower RTT gap before believing any lag.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "health":
        health()
    else:
        analyze(collect())
