# The Decisive Experiment — Tokyo Box, MS-Resolution Lead-Lag

Goal: settle, with real data from a **low-latency** vantage point, whether MEXC
lags Binance by enough to trade. Everything before this was either too coarse
(360ms REST) or measured from too far (your Mac, 37–314ms RTT). This removes
both objections.

**Honest odds going in: ~5–15%.** The prior is against us (most-arbitraged
relationship; colocated incumbents; adverse selection). But it is *not*
disproven, and this is the cheap, definitive cut. Budget: a few hours and
~$5–20 of cloud time.

---

## Step 1 — Spin up a box in the exchanges' region
Most major exchanges (incl. Binance, MEXC) run in/near **AWS Tokyo
`ap-northeast-1`**. We'll verify empirically in Step 3.

1. AWS console → EC2 → **Launch instance**. Region (top-right) → **Tokyo
   ap-northeast-1**.
2. AMI **Ubuntu 22.04**, type **t3.small** (fine for *measurement*; a real bot
   later would want a non-burstable `c`-class).
3. Key pair → download `.pem`. Security group → allow SSH from your IP.
4. Launch, copy the **Public IPv4**.

```bash
chmod 400 key.pem
ssh -i key.pem ubuntu@<PUBLIC_IP>
sudo apt update && sudo apt install -y python3-pip git
pip3 install websocket-client
git clone -b claude/hello-x4kako https://github.com/muhammadhamkah/Skymagic.git
cd Skymagic/leadlag
```

## Step 2 — THE GATING CHECK: is this box actually low-latency to both?
If the box isn't single-digit ms to **both** venues, the experiment can't work
— stop and relocate the region. Run:

```bash
python3 - <<'EOF'
import urllib.request, time
for n,u in [("BINANCE","https://api.binance.com/api/v3/ping"),
            ("MEXC","https://api.mexc.com/api/v3/ping")]:
    ts=[]
    for _ in range(10):
        t=time.time()
        try: urllib.request.urlopen(u,timeout=5).read()
        except Exception as e: print(n,"err",e); break
        ts.append((time.time()-t)*1000)
    if ts: print(f"{n:8} min {min(ts):5.1f}ms  median {sorted(ts)[len(ts)//2]:5.1f}ms")
EOF
```

- **Both ≤ ~5–10ms** → great, proceed.
- **One is tens of ms** → that venue isn't in Tokyo. Note which, and we pick a
  different region (or accept it can't be co-located with the other — which is
  itself a partial answer: you *can't* be close to both).

## Step 3 — Verify the MEXC protobuf field map
I couldn't test this live. Confirm the decoded frame has bid/ask where expected:

```bash
python3 xcap.py raw
```
You should see frames with numeric strings under field 4. If the field numbers
differ, tell me and I'll adjust `F_BID/F_ASK/F_BODY` (one-line fix).

## Step 4 — Capture + analyze
```bash
python3 xcap.py 300        # 5 minutes of Binance(real-time) + MEXC(100ms)
```
It prints the ms-resolution cross-correlation and a peak lag.

## Step 5 — Read the result
- **Peak at `k ≤ 0`, or all at +0ms** → MEXC moves with Binance at ≥100ms
  resolution. No tradeable lag at this cut. (A sub-100ms lag still can't be
  ruled out — that needs the deals stream; ask me to add it. But the bar for it
  being tradeable also drops.)
- **Peak at `k > 0` (e.g. +100ms) with real correlation** → MEXC genuinely lags.
  Compare that lag to your Step 2 latency. If lag ≫ latency, **this is the real
  candidate** — and the next step is verifying you can actually get *filled*
  (adverse-selection test), not just that the mid moved.

## Important honesty notes
- Numbers use **local receive timestamps**; valid only because, on the colo
  box, both feeds arrive with ~equal single-digit-ms transport. (This is exactly
  why your Mac couldn't do it.)
- MEXC's 100ms aggregation **smears** the apparent lag (a 60ms true lag shows
  near +100ms). So a peak at +100ms may be a *smaller* true lag — finer
  resolution (deals stream) needed to pin it, and to know if it's beatable.
- A measured mid-price lag is **necessary but not sufficient**: the MEXC quote
  is run by MMs who pull when the move is real. Fills ≠ mids. That's the next
  test if Step 5 is promising.
