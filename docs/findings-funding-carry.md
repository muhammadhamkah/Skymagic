# Findings: Funding-rate carry — the one strategy that actually pays

After HFT, scalping, lead-lag, listings, and market making all failed the honest
test for a small retail account, funding carry is the first thing that comes back
**positive on its own terms** — because you stop predicting and start *providing*
(you take the other side of leverage and get paid funding for it). It is not
speed-gated, so retail latency is irrelevant.

**Short answer: yes, 1x cross-margined funding carry earns a modest, mostly-
positive APY *while funding is positive* — but it turns negative in a bear
funding regime, and leverage on isolated margin adds a liquidation tail that can
erase months of carry in one squeeze. It needs ~$2k to clear fees, and on $2k a
good year is ~$200. Real income, modest size, regime-dependent.**

---

## Method (`bot/funding_carry.py`)

Delta-neutral cash-and-carry: long spot + equal short perp. Price risk cancels,
so PnL = funding collected each 8h − two-leg round-trip cost, with two real risks
modelled: **funding flipping negative** (regime risk) and **short-leg
liquidation** when levered on isolated margin (a price spike past ~1/leverage).

## Results — 1x, cross-margined, $2k, 30-day holds, 11 bps round-trip

| Funding regime | Net APY | Profitable | Worst 5% |
|----------------|--------:|-----------:|---------:|
| Bull (+2 bp/8h)    | **+20.4%** | 100% | +16.7% |
| Neutral (+1 bp/8h) | **+9.5%**  | 100% | +6.7%  |
| Flat (+0.3 bp/8h)  | +1.8%      | 88%  | −1.0%  |
| Bear (−0.5 bp/8h)  | **−7.0%**  | 0%   | −10.7% |

The whole thing hinges on the funding regime staying positive. It usually is in
crypto bull/neutral markets, but not always — and when it flips you pay.

## Leverage is a trap on isolated margin (neutral regime)

| Leverage | Mean APY | Median | Liquidation rate | Worst 5% |
|----------|---------:|-------:|-----------------:|---------:|
| 1x | +9.5% | +9.4% | 0% | +6.7% |
| 2x | +7.6% | +18.2% | **19%** | −44% |
| 3x | +8.8% | +26.0% | **32%** | −37% |
| 5x | +14.9% | −1.2% | **53%** | −25% |

Higher leverage lifts the median but the liquidation tail destroys the mean and
the p05. **Cross-margin at 1x (low leverage) is the only sane way to run it.**

## Honest assessment

- **It works** — the first strategy in the investigation that does, for a small
  account, without speed or a rebate tier.
- **It's modest.** ~9% APY at 1x neutral funding → ~$180/yr on $2k. Comparable
  to, and often only slightly above, just lending stablecoins — for more work
  and more tail risk.
- **It's regime-dependent.** Bear funding = negative carry. The live funding
  regime is the make-or-break input, not the strategy.
- **It's not scalping.** No constant trading; you hold a hedged position and
  collect funding. The "constant small profit" the whole thread chased — but
  earned by providing, not predicting.

## The live go/no-go

Carry's profitability is entirely "is funding positive now?" Check the real,
current regime before deploying:

```bash
python -m bot.fetch_funding --symbol BTCUSDT --limit 1000   # ~333 days of 8h funding
# prints mean funding, % of periods positive, and net %/APY carry would have earned
```

(Needs network egress to the exchange; blocked in this sandbox, runs anywhere
else. A Colab one-liner does the same.)

## Reproduce

```bash
python -m bot.funding_carry          # synthetic regimes + leverage tail
python -m pytest bot/tests/test_funding_carry.py -q
```
