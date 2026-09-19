# Findings: Is there a tradable edge in new-listing momentum?

The one gap where *being small is an advantage* — a fresh listing is too tiny
and chaotic for big funds to touch — and where the killer is **not speed**
(a listing plays out over minutes–hours). Tested with the `bot/listing.py`
harness on real Binance.US data.

**Short answer: no clean mechanical edge on the data we could reach. Long-the-
pump is a losing lottery (negative median, −80% tail); short-the-fade is
~break-even and untradable (you can't short a coin on day one). The one
exciting number — a +24,000% short "return" — was a formula bug, not money.**

---

## Method

`bot/listing.py` models a population of listing events (pump→bleed price path +
a wide-at-listing, decaying spread) and backtests entry-timing / holding / side
with honest, time-varying costs. `bot/fetch_listings.py` pulls each symbol from
`startTime=0` (its first trading day) so the harness runs on real listing paths.

Real run: 23 Binance.US symbols, first 240 one-minute bars each, fees 10 bps +
slippage 5 bps + a high-low spread proxy.

## Result (robust columns — median / win / p05)

| Play | Median/trade | Win rate | p05 | Verdict |
|------|-------------|----------|-----|---------|
| Long the pump (all entry/hold) | −0.4% to −1.3% | 9–26% | −35% to −80% | losing lottery |
| Short the fade | −0.4% to +0.5% | up to 57% | −2% to −5% | ~break-even, untradable |

- **Long**: negative median everywhere, single-digit win rates, catastrophic
  −80% tail. The occasional positive *mean* is a couple of moonshots; the
  typical trade bleeds after paying the ~98 bps early-entry spread.
- **Short**: median ≈ 0. The 57% win rate at one setting doesn't clear costs,
  and shorting is impossible on a coin's first day (no borrow, perp lists later).

## The bug that almost fooled us

The first run showed a short **mean of +24,713%**. That was a real bug: the
short P&L used `entry/exit − 1`, which is unbounded and explodes when a coin
collapses near zero. A real short is **capped at +100%** (price floors at zero).
Fixed to `1 − exit/entry`; regression test added. Lesson: when the headline is a
mean wildly above the median, suspect the tail — or the formula — before
believing the edge.

## Caveat: the data isn't the ideal test

Most of the 23 symbols are *established* coins that migrated to Binance.US, not
true first-day price-discovery debuts. The genuine meme-launch pump lives on
Binance.com (geo-blocked from Colab) or in 2026 micro-cap listings not on
Binance.US. So this rules out a *mechanical* edge on accessible liquid coins; it
does not fully test a discretionary "be first to a hot new token" play — which
is informational/event-driven, not something a 100 USDT mechanical bot captures.

## Where this leaves the gap hunt

Tested so far: HFT, scalping, lead-lag (real data: dead), new-listing momentum
(real data: no clean edge). Each mechanical retail strategy fails the honest
test for the same family of reasons — costs, tails, and structural access.

Still untested and speed-free: **statistical arbitrage / pairs trading**
(mean-reversion on a cointegrated spread). It is the remaining candidate that
neither needs speed nor depends on hard-to-reach data.

## Reproduce

```bash
python -m bot.listing                         # synthetic
python -m bot.fetch_listings --symbols A,B,C   # real (network-permitted env)
python -m bot.listing --csv-dir data/listings  # real analysis
```
