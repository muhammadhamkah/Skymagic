# Volume-Profile Backtester

A small, honest harness for answering one question: **would a "buy the dip /
sell the rip" rule built on a volume profile (POC / Value Area) actually make
money after fees?**

It grew out of a look at the DGT *Pivot Anchored Volume Profile* TradingView
indicator and the natural follow-up — "can we just turn that into a profitable
bot?"

## TL;DR finding

No — not on its own. The indicator is a **descriptive lens** (where volume has
already traded), not a **predictive edge**. The reproducible robustness check:

```
$ python -m backtest.run --synthetic --seeds 40

  revert_poc  | mean -31.86% | median -20.82% | std 59.63% | beat-hold 14/40 | t=-3.38
  breakout    | mean -42.03% | median -35.61% | std 50.24% | beat-hold  7/40 | t=-5.29
```

Across 40 random-walk seeds, both shipped strategies have **negative expected
edge vs. buy-and-hold** once 0.1% commission + 5bps slippage are applied — and
the negativity is statistically significant (|t| > 3). Any single run that
beats hold is noise (usually just sitting in cash during a drop), not alpha.

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
- **Commission + slippage** are charged on every buy and sell, adversarially
  (buy above the open, sell below it). For a Convert-style venue set `--fee 0`
  and `--slippage` to the quoted spread.
- It reports what makes a comparison fair: **time-in-market** (the strategy
  sits in cash, so its risk ≠ hold's), **hold's drawdown** alongside the
  strategy's, and a **±95% band on win rate** (win rate ≠ profit).

### Walk-forward validation (the honest test)

`--walk-forward` is the validation to actually trust. It **tunes parameters on
a trailing in-sample window, then trades the next segment forward with those
frozen params**, rolling through the whole series and stitching the forward
segments into one equity curve. Params are only ever chosen from the past, so
the result estimates what an *adaptive* bot would really have done live.

```bash
python -m backtest.run --synthetic --bars 5000 --walk-forward --train 1500 --test 300
```

It prints a per-fold table; watch the chosen params **churn** fold-to-fold —
that instability is the overfitting an ordinary split can't surface. On
synthetic (edgeless) data the stitched out-of-sample return correctly loses to
buy-and-hold.

### Honest limitations (flagged by review, not yet fixed)

- The plain in/out-of-sample split (`run_split`) is only a **two-period
  stability check, not an overfitting test** — use `--walk-forward` for real
  validation.
- The walk-forward objective maximizes **net return** on the training window;
  swapping in a risk-adjusted objective (Sharpe) would be a reasonable variant.
- Fills are assumed **full, immediate and impact-free** beyond the flat
  slippage term; `volume` is loaded but not used to cap fill size.
- Synthetic GBM is a clean **null hypothesis** (no edge to find), not a
  realistic stress test — it lacks fat tails and volatility clustering.

## Usage

```bash
# Offline demo on synthetic random data (shows the fee+slippage drag):
python -m backtest.run --synthetic --strategy both

# Reproducible robustness check — the number to actually trust:
python -m backtest.run --synthetic --seeds 40

# Inspect the expected CSV schema:
python -m backtest.run --make-sample data/sample.csv
```

### Real Binance data

`backtest.fetch` pulls live klines into a CSV the backtester reads. **It must
run where api.binance.com is reachable** — sandboxed/CI environments usually
allowlist it out (you'll get a clear "Host not in allowlist" / 403 error).

```bash
# 1. Fetch (run on a machine with network access). Pages back automatically
#    past the 1000-bars-per-request cap.
python -m backtest.fetch --symbol BTCUSDT --interval 1h --bars 5000 \
    --out data/BTCUSDT_1h.csv

# 2. Backtest the real candles:
python -m backtest.run --csv data/BTCUSDT_1h.csv --strategy both

# Already have a CSV? Any raw Binance-klines dump (e.g. from
# https://data.binance.vision) or a headered OHLCV file works directly.
```

`data/` and `*.csv` are gitignored, so fetched history is never committed.

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
| `engine.py` | signals, fee+slippage fills, metrics, split + walk-forward |
| `fetch.py` | pull real Binance klines into a CSV (run where network allows) |
| `run.py` | CLI |

## What would come next (with real risk)

A genuine edge has to come from a real, persistent pattern + risk management —
not from the structure of "buy low, sell high," which everyone already has.
Next honest steps: real tick data (not OHLC-approximated volume), parameter
robustness sweeps, transaction-cost stress tests, and walk-forward validation.
