# Volume-Profile Backtester

A small, honest harness for answering one question: **would a "buy the dip /
sell the rip" rule built on a volume profile (POC / Value Area) actually make
money after fees?**

It grew out of a look at the DGT *Pivot Anchored Volume Profile* TradingView
indicator and the natural follow-up — "can we just turn that into a profitable
bot?"

## TL;DR finding

No — not on its own. The indicator is a **descriptive lens** (where volume has
already traded), not a **predictive edge**. Tested across 40 random-walk seeds,
both shipped strategies have **negative expected edge vs. buy-and-hold** once a
0.1% fee is applied. Any single run that beats hold is noise, not alpha.

```
revert_poc  | mean edge  -9.35% | beat-hold 20/40 seeds   (coin flip)
breakout    | mean edge -23.27% | beat-hold 12/40 seeds
```

A backtester can't *manufacture* an edge. It can only tell you honestly whether
one exists — and here, on data with no real pattern, it correctly says it
doesn't.

## Why it doesn't cheat (the important part)

The DGT indicator anchors its profile to **pivot highs/lows**, and
`ta.pivothigh(L, L)` only confirms a pivot **L bars after it happened**. On a
chart that lag is invisible; in a backtest it is **lookahead bias** — the
single most common reason a backtested bot prints money it can never make live.

This harness deliberately avoids that:

- Profiles are computed over a **rolling window of already-closed bars**
  (`[t-window .. t-1]`), never a pivot — so every decision uses only
  information available at that moment.
- Signals at bar `t` are **filled at bar `t+1`'s open**, never the same bar.
- Fees are charged on **every** buy and sell.
- Results are split **in-sample / out-of-sample**; the OOS number is the one
  that counts. An edge that only shows in-sample is overfitting.

## Usage

```bash
# Offline demo on synthetic random data (shows the fee drag):
python -m backtest.run --synthetic --strategy both

# Real data: a raw Binance-klines CSV or a headered OHLCV CSV.
# Download where you have network access (the API is allowlisted out of CI):
#   https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=1000
#   https://data.binance.vision   (bulk historical dumps)
python -m backtest.run --csv data/BTCUSDT_1h.csv --strategy revert_poc

# Inspect the expected CSV schema:
python -m backtest.run --make-sample data/sample.csv
```

## Strategies

- `revert_poc` — buy when close dips below Value Area Low, sell on revert to
  POC. Mean-reversion.
- `breakout` — buy when close crosses above Value Area High, sell on fall back
  through POC. Momentum.

Spot only: long/flat, no shorting, no leverage.

## Layout

| file | role |
|------|------|
| `data.py` | load candles from CSV; synthetic GBM generator |
| `volume_profile.py` | POC / VAH / VAL from a window of closed bars |
| `engine.py` | signal generation, fee-aware fills, metrics, in/out-of-sample |
| `run.py` | CLI |

## What would come next (with real risk)

A genuine edge has to come from a real, persistent pattern + risk management —
not from the structure of "buy low, sell high," which everyone already has.
Next honest steps: real tick data (not OHLC-approximated volume), parameter
robustness sweeps, transaction-cost stress tests, and walk-forward validation.
