# Findings: Can a 100 USDT lead-lag bot make money?

A self-contained record of the investigation, from "why doesn't HFT work with
100 USDT" to a real-data test of a lead-lag strategy on Binance.

**Short answer: no — not because the lead-lag *signal* is fake, but because the
tradable edge is destroyed by latency and fees, and on real liquid pairs at
tradable resolutions there is barely any edge to begin with.**

---

## The question

HFT doesn't work with 100 USDT (see `hft-with-100-usdt.md`). But what about
lead-lag — one asset (e.g. BTC) leading another (e.g. SOL)? "Profitable
lead-lag bots" are a common claim. We built a harness to test it honestly.

## The method

A lookahead-safe, cost-aware backtester (`bot/`) with three guards that
separate a real edge from the usual mirage:

1. **Causal signal** — the decision at bar `t` uses only data up to `t`.
2. **Delayed fill** — the fill cannot land before `t + latency`. This is the
   guard that kills the classic "fill at the price you just predicted" bug.
3. **Honest costs** — every entry/exit pays fees, crosses the spread, and eats
   slippage, applied adversely.

We validated the harness on synthetic data with a *known* lag, then swept
latency, costs, signal selectivity, and maker adverse selection, and finally
ran it on **real Binance data**.

## What we found

### 1. The signal is real (synthetic + real cross-correlation)
Lead-lag genuinely exists — synthetic detection recovered the injected lag
exactly (corr 0.87); real BTC/SOL returns are correlated 0.66.

### 2. Latency is the gate
Across the configuration sweep, **100% of net-positive configs had
latency ≤ lag.** If you can't act inside the lag window, no edge survives,
regardless of fees. Retail latency (50–100 ms+) sits at or past that cliff for
liquid pairs.

### 3. Fees are a 1:1 lever — but only on a fat edge
Every basis point of cost saved adds ~1 bp to per-trade profit. The cost floor
ranges from **~26 bps** (spot taker, VIP-0) down to **~5 bps** (futures maker +
BNB). But the maker adverse-selection stress test showed this only matters when
the gross edge is fat: a **thin** edge (a few bps) is already negative at zero
adverse selection. **Cutting fees cannot rescue an edge that isn't there.**

### 4. Real data: the edge isn't there (BTC→SOL, 1-minute)

Run via Google Colab against live Binance.US klines (3000 × 1-minute bars):

```
BTCUSDT->SOLUSDT 1m: detected lag = 0 bars, corr = 0.664

                 gross      spot taker 26bps   fut maker 5bps
 latency  0 bars  +1.26 bps      -24.74 bps        -3.74 bps
 latency  1 bar   +0.19          -25.81            -4.81
 latency  2 bars  -0.67          -26.67            -5.67
 latency  5 bars  +0.28          -25.72            -4.72
 latency 10 bars  +0.31          -25.69            -4.69
```

- **Lag = 0** → no tradable lead. The 0.66 correlation is *contemporaneous*
  (they move in the same minute); there is no window to step into.
- **Gross ≈ 0** → the per-trade edge wobbles in ±1 bp noise with no structure
  and no latency decay. Even a zero-fee, zero-latency trader barely breaks even.
- **After costs**: futures-maker bleeds −4 to −6 bps/trade; spot-taker −25
  bps/trade at ~3% win rate. Every configuration is underwater.

This matches the literature: lead-lag relationships **disappear at larger time
scales**, and majors are efficient at the minute level.

## Conclusion

| Claim | Verdict |
|-------|---------|
| Lead-lag signal exists | ✅ true (but sub-minute for majors) |
| A 100 USDT bot can monetize it | ❌ no |
| Why | latency > lag, and gross edge ≈ 0 at tradable resolution; fees finish it |

The honest takeaway from the whole thread (HFT → scalping → funding carry →
lead-lag): **at 100 USDT the reachable strategies are structurally
unprofitable. The value here is the harness and the discipline to prove it,
not a money printer.**

## Where an edge could still hide (not yet tested)

The only unexplored corner is **sub-minute** data (`1s`) on a **slower,
smaller-cap** laggard, where the lag is longest and BTC's lead strongest. Even
there the latency gate is brutal for retail. To test it:

```python
# in the Colab cell:
LEADER, LAGGARD, INTERVAL, LIMIT = "BTCUSDT", "DOGEUSDT", "1s", 1000
```

Or with the repo harness on a machine with network access:

```bash
python -m bot.fetch_data --leader BTCUSDT --laggard DOGEUSDT --interval 1s --limit 1000
python -m bot.run_backtest --source csv \
    --leader-csv data/BTCUSDT-1s.csv --laggard-csv data/DOGEUSDT-1s.csv \
    --bar-seconds 1 --lookback 1 --hold 5
```

## Reproduce

- Offline (synthetic): `python -m bot.run_backtest --source synthetic --true-lag 5`
- Config sweep: `python -m bot.hunt`
- Maker stress: `python -m bot.stress_maker`
- Real data: `python -m bot.fetch_data ...` then `--source csv` (see `bot/README.md`)
