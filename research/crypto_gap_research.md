# Crypto market gap research (Sept 2026)

Goal: find an edge a solo operator can actually capture, after two years of attempts
across several branches of this repo. This memo first records what was already tested
so nobody pays for it twice, then proposes where to look next and how to measure each
candidate for free before committing capital.

## What has already been tested in this repo

Every branch below carried an honest harness (causal signals, delayed fills, adversarial
costs) and a written verdict. The verdicts are consistent.

| Branch | Strategy | Verdict | Why |
|---|---|---|---|
| `claude/hft-100-usdt-vr89v7` | HFT / scalping | Dead | Fee drag beats a sub-bp edge; no rebate at retail volume. `docs/hft-with-100-usdt.md` |
| same | Lead-lag (BTC leads SOL etc.) | Dead on real data | Lag = 0 bars at 1m; gross edge ±1 bp; every cost tier underwater. `docs/findings-leadlag.md` |
| same | New-listing momentum | No mechanical edge | Long the pump: negative median, −80% tail. Short: untradable day one. `docs/findings-listings.md` |
| same | Avellaneda-Stoikov market making | Dead for retail | Same quoting logic: pro +4.86 bps/fill, retail −1.49. Speed and rebate decide, not fees. `docs/findings-market-making.md` |
| same | Zero-fee / Hyperliquid variant of the above | Still dead | At literally zero fees the slow maker still loses. |
| same | Copy-trading a market maker's inventory | Dead even at zero lag | You copy inventory, not edge, and pay taker on every churn. |
| same | **Funding-rate carry, 1x cross-margin** | **Pays, modestly** | +9.5% APY neutral regime, +20% bull, −7% bear. Needs ~$2k to clear fees. `docs/findings-funding-carry.md` |
| `claude/hft-bot-work-u6axP` | Order-book imbalance on Lighter; MA / momentum / Donchian tournament | Rust harness built, walk-forward added | No profitable finding recorded. |
| `claude/spot-binance-fees-4Bw1z` | Volume-profile POC / value-area rules | Dead | Negative vs buy-and-hold over 40 seeds, |t| > 3. `backtest/README.md` |
| `claude/crypto-arb-bot-phase1-aA04j` | Cross-exchange arb scaffold (Rust) | Abandoned at phase 1 | Never reached a live spread measurement. |

The prior work's own summary is the right one: the edge behind "constant tiny profits"
is never a signal, it is a **structural position**: speed, size (rebate tier), or
holding a risk others won't. Funding carry is the one strategy that passed because it
is the third kind: you provide, you don't predict.

One candidate the earlier docs flagged as still untested: **statistical arbitrage /
pairs trading** on a cointegrated spread. Addressed in candidate 5 below.

Assumed account size, carried over from that work: **100 USDT to about $2k.** Every
number below should be read at that scale.

## The framing for the next round

"A method no one has done before" is not a useful filter. Every mechanical price gap on
a liquid venue is already scanned by thousands of teams. The prior work proved that the
hard way for speed-gated strategies.

The useful filter extends the "structural position" idea one step: *what stops Jump,
Wintermute, and the HFT desks from closing this particular gap?* If the answer is
"nothing", it is gone. If the answer is **identity, geography, bank rails, regulation,
or reasoning**, it can persist for years, because the people with speed and capital are
locked out of it and the people inside it are mostly not traders.

## Candidates, ranked

### 1. Regional fiat premium (Indonesia). Barrier: identity + bank rails.

**What it is.** BTC/IDR and USDT/IDR on Indodax and Tokocrypto trade at a persistent
offset from Binance BTC/USDT times the official USD/IDR rate. It is the Indonesian
analogue of the Korean "kimchi premium".

**Evidence it persists.** In Korea the same mechanism produced a 35-day discount run
(June 20 to July 24, 2026), a 3.1% discount in early June, and a 1% premium by
September 1. Capital controls turn domestic buying into a price gap instead of an
arbitrage flow. Korea's gap is well covered in the press. Indonesia's is almost never
written about, which itself says nobody has published a measurement.

**Why it survives.** A foreign fund cannot open an Indodax account, cannot hold an IDR
bank account, and cannot move IDR across the border cheaply. A resident can do all
three. The moat is a passport and a bank account, not latency. It is also not
speed-gated: the gap moves over days.

**How to trade it.** As a slow round trip, not a fast one.
- IDR venues at a discount: buy USDT locally with IDR, withdraw on-chain, hold USDT
  (or park it in the funding-carry position) until the premium flips.
- IDR venues at a premium: bring USDT in on-chain, sell for IDR locally.
- If the premium only ever sits one side, you need a real IDR-in or IDR-out leg, and
  that is where bank and regulator limits bite. Measure first.

**Cost floor to beat.** Indodax taker ~0.3% per side, Indonesian per-trade crypto tax
(historically 0.1 to 0.2%, check the current PMK rate), on-chain USDT transfer, and the
USDT/IDR spread. Call it 1.0% round trip. So a 1.5% gap is the floor of interest and
2%+ is where it becomes real money. At $2k a 2% swing is $40 per round trip, which
beats funding carry's ~$180 a year if it flips a few times a year.

**Risks.** OJK now regulates crypto as a financial asset (P2SK amendment in force
June 17, 2026). Check rupiah withdrawal caps and bank flags on repeated large IDR
movements. The premium can widen against you for a month. Do it in your own account
only; never touch other people's money for this.

**Measurement.** `research/spread_logger.py` records the premium every N minutes to
SQLite. Run it for 30 days. Go / no-go thresholds are in the script's docstring.

### 2. Stock-perpetual weekend basis. Barrier: needs an equity leg + oracle risk.

**What it is.** Binance, Hyperliquid and others list perpetuals on US stocks. They
trade 24/7 while the cash market closes. Weekend prices are discovered independently,
mostly by retail, mostly long.

**Evidence.** One August 2026 weekend: four stock perps on Binance traded about $461M
combined, all priced above Friday close on Sunday, all opened lower Monday. The same
name trades 0.15% to 0.75% apart across venues, wider overnight.

**Why it survives.** Equity market makers are not set up to hedge on a crypto venue
at 3am Sunday. Crypto desks mostly cannot hedge in the cash equity. Nobody is
structurally positioned to sit in the middle.

**How to trade it.** Short the perp Sunday evening when it is rich to Friday close
(net of any real weekend news), close at Monday cash open or hedge with a CFD. It is
not risk-free: if real news lands, the perp is right and you are wrong. Size for that.
At $2k this is one small position per weekend.

**Access check.** Binance.com is not licensed in Indonesia; Hyperliquid stock perps
are reachable. Confirm which venue lists them and whether you can hold a position over
the weekend without an outsized funding charge.

**Measurement.** Log Friday close, Sunday 20:00 UTC perp mark, Monday cash open.
Ten weekends tells you if the premium is systematic or an August anecdote.

### 3. Prediction-market combinatorial arbitrage. Barrier: reasoning, not speed.

**What it is.** On Polymarket, logically dependent markets get priced inconsistently.
Example shape: an outcome priced at 0.40 while the set of sub-markets that jointly
imply it is priced as if 0.55.

**Evidence.** Saguillo et al., AFT 2025 ("Unravelling the Probabilistic Forest"):
about $40M realized arbitrage profit April 2024 to April 2025 across 86M bets and
7,000+ markets. Only ~$10.6M was simple intra-market rebalancing, which bots dominate.
The larger share was combinatorial, across dependent markets, and needs someone to
*notice* the dependence.

**Why it survives.** Detecting that two free-text market questions are logically
linked is a reasoning problem. Speed bots do not do it. An LLM pipeline that reads
new markets, proposes dependencies, and flags price inconsistencies is a genuinely
under-built tool and fits the skill set in this repo.

**Risks.** Resolution rules differ subtly between "dependent" markets and a sure thing
resolves the wrong way. Long-tail liquidity is thin. Check whether Polymarket is
reachable from Indonesia without a VPN, and whether that breaks their terms.

**Measurement.** Pull 50 to 100 active markets, run a dependency finder, count real
inconsistencies per week net of the spread. Zero trades needed to know if it works.

### 4. Long-tail perp funding across DEXes. Barrier: operational pain. Extends carry.

Hyperliquid paid on average 7.17% more annualised funding than Binance on BTC
(2023 to 2026). Long-tail perps on new listings run 20 to 60%+ APR while break-even
is ~1.3 bps per 8h with maker orders. This is the same trade the repo already proved
works, moved to where it pays more. It is not "untouched" but it is still paying,
because the tail risk (delisting, liquidation cascades, thin books, hourly vs 8h
settlement mismatches) scares off size. Only worth it with the position manager
from `bot/funding_carry.py` extended to watch liquidation distance automatically.

### 5. Pairs trading / stat-arb. The prior docs' "still untested" item.

Honest read: on majors at minute resolution it is crowded and the spreads are
arbitraged by the same desks that killed lead-lag. It is not speed-gated, so it does
not fail for the same reason, but the expected edge after costs on liquid pairs is
small and the cointegration breaks without warning. Worth one pass through the
existing `bot/evaluate.py` gauntlet on a handful of mid-cap pairs, no more. Rank it
below the three above because it lacks a structural moat.

### 6. Volatility risk premium. No barrier. Not a gap.

Implied minus realised vol on BTC options averages ~12% and selling it has been
consistently profitable. Every options desk does it. Listed only so nobody re-tests it.

## Recommendation

Measure the top three in parallel. All three cost nothing to measure and none needs
speed, a rebate tier, or more than $2k.

1. **Indonesia premium**: run the spread logger for 30 days. This is the one with the
   strongest structural moat and the only one where being in Indonesia is the edge.
2. **Stock-perp weekend basis**: log ten weekends.
3. **Polymarket dependency finder**: prototype on 50 markets, count inconsistencies.

Keep the funding-carry position running underneath as the base yield while these run.
Whichever candidate shows a repeatable gap net of fees after 30 days gets capital. The
others get dropped without regret, the same way the earlier branches dropped theirs.

## Sources

- Prior work in this repo: branches `claude/hft-100-usdt-vr89v7` (docs/ folder),
  `claude/hft-bot-work-u6axP`, `claude/spot-binance-fees-4Bw1z`,
  `claude/crypto-arb-bot-phase1-aA04j`
- Kimchi premium 2026: cryptotimes.io (Sept 1, 2026), Bloomberg (Sept 1, 2026),
  CryptoQuant via cryptonews.net, spotedcrypto.com
- Indonesia regulation: license.aiying.cc (P2SK / OJK transition), lightspark.com,
  liminalcustody.com
- Stock perpetuals: coingecko.com/learn/tokenized-stock-perpetuals-price-discovery,
  reports.tiger-research.com, altstreet.investments
- Prediction markets: arXiv 2508.03474 / AFT 2025 (LIPIcs.AFT.2025.27),
  arXiv 2605.00864
- Funding arbitrage: bitmex.com Q2 2026 derivatives report, neuralarb.com (Apr 24, 2026),
  arbitragescanner.io
- Volatility premium: pandabull.io, livevolatile.com
- Market inefficiency literature: arXiv 2602.20771, JFQA "Informational Efficiency of
  Cryptocurrency Markets"
