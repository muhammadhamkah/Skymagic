# Test your own strategy — privately

You don't have to share your edge with anyone. `bot/evaluate.py` runs *your*
signal through the same honest gauntlet that killed every mirage in this project,
entirely on your machine.

## How

1. Open `bot/strategy_template.py`.
2. Put your logic in `my_signal(df)` — return a Series in `{-1, 0, +1}` using
   **only past data** (no `.shift(-1)`, no `.iloc[t+1]`, no whole-series fit
   that leaks the future).
3. Point `load_data()` at your real prices (CSV importer or your own loader).
4. Set `CostModel` to your venue's real fees.
5. Run `python -m bot.strategy_template`.

It prints a latency sweep and a one-line **verdict**.

## The verdict can only be one of:

- **SURVIVES** — causal, positive *median* after costs at realistic latency.
  The only result worth paper-trading. (Still paper-trade before real money.)
- **LOOKAHEAD** — your signal changes when future bars are removed. The backtest
  profit is fake. This is the #1 killer and the evaluator checks it first.
- **SPEED-GATED** — profit only at zero latency. The edge is speed you don't have.
- **FAT-TAILED** — positive mean, negative median. A lottery, not an edge.
- **NO EDGE** — negative after costs at every latency.

## The honest checklist (what the evaluator encodes)

Any strategy claiming to make money must pass ALL of these. Most "profitable"
bots fail at least one:

1. **Is it causal?** Recompute the signal on a shorter slice of history — does
   the past change? If yes, it's peeking. (Auto-checked.)
2. **Does it survive your real latency?** Not a colo'd 1 ms — *your* 50–100 ms.
3. **Does it survive real fees + spread + slippage**, crossed adversely?
4. **Is the MEDIAN trade positive**, or just the mean? Mean-only = tail lottery.
5. **What's the worst 5% (p05)?** Can you survive the bad streak?
6. **Does it need something you lack** — speed, a rebate tier, capital, info?
7. **What's the ruin risk?** Exchange failure, stablecoin de-peg, liquidation —
   the things "delta-neutral" and "backtested" do NOT protect against.

If a strategy can't clear 1–5 in this harness, it will not clear them with real
money — it will just cost you the money to find out. Running it here costs $0.

## What this project already proved (so you don't re-pay for it)

Lead-lag, scalping, new-listing momentum, and retail market-making all FAILED
this gauntlet on real or honest-synthetic data. Funding carry passed at a modest
~9–15% APY (regime-dependent, with real tail risk). If your private strategy is
a variant of the first group, expect the same verdict — and let the harness tell
you for free before the market does for a fee.
