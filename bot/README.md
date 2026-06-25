# Lead-Lag Bot — Backtest Harness (Milestone 1)

A lead-lag trading bot for Binance, **built backtest-first**. Before any live
order is placed, this harness answers the only question that matters for a
100 USDT account: *does the lead-lag edge survive realistic latency and fees,
or does it only exist in a zero-latency-fill backtest?*

> Context: see `../docs/hft-with-100-usdt.md` for why HFT, scalping, and naive
> lead-lag bots fail at small size. This harness is the tool that proves it on
> data instead of asserting it.

## What it does

- **Lead-lag signal** — the leader's (e.g. BTC) recent return predicts the
  laggard (e.g. ETH). Strictly causal: the signal at bar `t` uses only data up
  to `t`.
- **Lookahead-safe, cost-aware backtest** — the fill cannot land before
  `t + latency`, and every entry/exit pays the taker fee, crosses the spread,
  and eats slippage (Binance VIP-0 defaults).
- **Latency sweep** — the headline output. Re-runs the backtest across a range
  of execution latencies so you can *watch* the edge collapse once latency
  crosses the true lag. A backtest that stays profitable at absurd latency has
  a lookahead bug.
- **Lag detector** — lagged cross-correlation to estimate the actual lead-lag
  duration of a pair.

## Quick start

Offline, fully reproducible (synthetic data with a known 5-bar lag — no
network, no API key):

```bash
pip install -r requirements.txt
python -m bot.run_backtest --source synthetic --true-lag 5 --lookback 1 --hold 5
```

Against real Binance data (requires outbound network to `api.binance.com`;
caches to `bot/_cache/`):

```bash
python -m bot.run_backtest --source binance \
    --leader BTCUSDT --laggard ETHUSDT --interval 1s --limit 5000
```

> Note: in network-restricted sandboxes (including Claude Code on the web with
> a locked-down egress policy) `api.binance.com` may be blocked — the synthetic
> mode is designed to run anywhere and exercises the same engine.

## Reading the output

```
Detected lead-lag: 5 bars (max cross-corr 0.873)
Round-trip cost assumption: 26.0 bps per trade
=== Latency sweep (the honest test) ===
 latency_s  latency_bars  trades  net_return_pct  gross_return_pct  ...
     0.000             0    3333         -99.875           199.607  ...
     5.000             5    1818         -99.122             0.044  ...
```

Two lessons fall straight out of the numbers:

1. **The signal is real** — gross return is strongly positive at low latency
   (the cross-correlation is 0.87). The leader genuinely predicts the laggard.
2. **It is not monetizable at 100 USDT** — the ~6 bps gross edge per trade is
   buried by the ~26 bps round-trip cost, and the gross edge itself evaporates
   once latency reaches the 5-bar lag. "Tiny profits constantly" becomes "tiny
   losses constantly" the moment costs and latency are modeled honestly.

To find a *capturable* regime, push the harness: lower costs (better fee tier,
maker-only), larger/cleaner moves (a genuinely slower laggard where the lag
exceeds your latency), or a higher entry threshold so you only trade strong
signals. If net PnL can't be made positive here, it won't be positive live.

## Layout

```
bot/
  config.py            # CostModel, ExecutionModel, SignalConfig, BacktestConfig
  feeds/
    synthetic.py       # known-lag generator (the harness's test instrument)
    binance.py         # public klines + CSV cache
  signals/
    lead_lag.py        # detect_lag (diagnostic) + lead_lag_signal (causal)
  backtest/
    engine.py          # lookahead-safe engine + latency_sweep
    metrics.py         # gross vs net, fee drag, Sharpe, drawdown
  run_backtest.py      # CLI entrypoint
  tests/               # property tests incl. "latency destroys the edge"
```

## Tests

```bash
python -m pytest bot/tests/ -q
```

The suite encodes the harness's guarantees: the lag detector recovers ground
truth, the signal is causal, fees reduce net below gross exactly, trades never
overlap, and — the thesis — **injected latency past the lag destroys the
synthetic edge**.

## Roadmap (not yet built)

1. ✅ **Backtest harness** (this milestone).
2. ⏳ Paper-trading runner against Binance testnet (live signal, simulated
   fills, same cost model) — only worth building if the backtest finds a
   capturable regime.
3. ⏳ Live execution with hard risk limits — gated on paper-trading results.
