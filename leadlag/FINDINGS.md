# Lead-Lag Investigation — Findings

**Question:** Is there a tradeable cross-exchange lead-lag (Binance leads, a
follower like MEXC lags) on liquid crypto pairs that a low-latency trader could
capture — and does MEXC's near-zero fees make it profitable?

**Answer: No.** Across five independent tests escalating from a $0 home script
to a colocated AWS Tokyo box at 15–20ms RTT, no tradeable lead-lag was found.
The near-zero fees were never the binding constraint; the lag simply isn't
there at a resolution that can be traded.

## The thesis and its refinements (all tested)
1. "0 fees → lead-lag is highly profitable."
2. "Only trade when the move clears the spread."
3. "Only trade lags that converge in >250ms."
4. "Single-digit-ms / colocated latency makes it work."
5. "Find the slowest-following exchange across many venues."

## The tests
| # | Test | Vantage | Result |
|---|------|---------|--------|
| 1 | Intra-MEXC BTC→ETH/SOL cross-correlation | laptop | no lead-lag (peaks at k≤0) |
| 2 | Binance→MEXC, top-144 pairs, 2-window persistence guard | laptop | **0** credible lags |
| 3 | Multi-exchange (9 venues) leadership matrix | laptop | unmeasurable — RTT (37–3857ms) dominates any real lag |
| 4 | Binance→MEXC bookTicker, ms-resolution | **Tokyo colo** | apparent ~50ms lag = MEXC's 100ms feed aggregation, not price lag |
| 5 | Binance→MEXC **real-time trades** (deals stream) | **Tokyo colo** | **no lag** — confirms #4 was a feed artifact |

## Why it fails (the mechanism, not just the result)
- **Feed-aggregation mirage:** MEXC's public bookTicker publishes every 100ms.
  A coarse measurement sees a ~50ms "lag" that is purely the publish delay —
  the actual price (and the market makers quoting it) are synced to Binance in
  real time. Test #5 (real-time trades) removed the aggregation and the lag
  vanished.
- **Liquidity ⟺ sync tradeoff:** Any venue liquid enough to trade is liquid
  enough that cross-exchange MMs keep it synced (no lag). Any venue that lags
  is illiquid (wide spreads, thin/stale books, counterparty risk) — i.e. not
  tradeable. The "tradeable AND laggy" region is empty by construction, which
  is why scanning more exchanges (test #3/#5 logic) doesn't help.
- **Latency reality:** A free AWS Tokyo box reaches the venues at ~15–20ms RTT
  (not the single-digit ms of true cross-connect colocation). A measured
  mid-price lag would still need to beat the full observe→fill loop AND survive
  adverse selection (MMs pull stale quotes exactly when the move is real).

## Tooling (this directory)
- `leadlag_oneshot.py` — zero-dependency REST screen (single pair)
- `scan.py` — top-N pair scan, Binance→MEXC, false-discovery (half-split) guard
- `multiscan.py` — 9-exchange leadership matrix with RTT-bias reporting
- `xcap.py` — cross-exchange ms-resolution capture (bookTicker websockets)
- `xdeals.py` — real-time deals-stream disambiguation (the decisive test)
- `pbdecode.py` — dependency-free MEXC protobuf decoder
- `TOKYO_EXPERIMENT.md` — colo-box runbook

## Takeaway
The investigation succeeded: it returned a **definitive, mechanism-backed "no"**
for the cost of an afternoon and ~$0 in cloud credits — instead of the slow
capital bleed of finding out live. Fees were a red herring; the binding
constraints are feed aggregation, the liquidity/lag tradeoff, and latency.
