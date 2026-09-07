# Crypto market gap research (Sept 2026)

Goal: find an edge a solo operator can actually capture, after having already tried
cross-exchange arbitrage, bot trading, and fast-exchange-leads-slow-exchange spike following.

## The framing that has to change first

"A method no one has done before" is not a useful filter. Every mechanical price gap on a
liquid venue has been found, because thousands of well-funded teams scan for exactly that.
What has not been found is a gap where the barrier to closing it is **not speed and not
capital**. Those gaps persist for years because the people with speed and capital are
structurally locked out of them.

So the filter is: *what stops Jump, Wintermute, and the HFT desks from closing this?*
If the answer is "nothing", the gap is already gone. If the answer is "identity, geography,
bank rails, regulation, or reasoning", it is a candidate.

The three attempts already made all fail that filter:

| Tried | Why it was already closed |
|---|---|
| Cross-exchange arb | Speed. Colocated firms with maker rebates see the gap first. |
| Bot trading on price signals | Signal is public, every bot sees it. Fees + slippage eat it. |
| Spike on fast venue, follow on slow venue | Same. The "slow" venue's makers pull quotes within ms of the fast venue moving. |

## Candidates, ranked

### 1. Regional fiat premium (Indonesia). Barrier: identity + bank rails.

**What it is.** BTC/IDR and USDT/IDR on Indodax and Tokocrypto trade at a persistent
offset from Binance BTC/USDT times the official USD/IDR rate. This is the Indonesian
analogue of the Korean "kimchi premium".

**Evidence it persists.** In Korea, the same mechanism produced a 35-day discount run
(June 20 to July 24, 2026), a 3.1% discount in early June and a 1% premium by September 1.
Capital controls mean domestic buying shows up as a price gap instead of arbitrage flow.
Korea's gap is well covered. Indonesia's is barely written about at all, which is itself
a signal: nobody has published a measurement.

**Why it survives.** A foreign fund cannot open an Indodax account, cannot hold an IDR
bank account, and cannot move IDR across the border cheaply. A local resident can do all
three. The moat is your passport and your bank, not your latency.

**How to trade it.** Not as a fast round trip. As a slow one:
- When IDR venues are at a discount: buy BTC/USDT locally with IDR, withdraw on-chain,
  sell on Binance for USDT, hold USDT (or hedge with a perp) until the premium flips.
- When at a premium: bring USDT in on-chain, sell for IDR locally.
- Each leg is a stablecoin transfer, so the round trip costs network fees plus two spreads.
  Position holding time is days to weeks, not milliseconds.

**Risks.** OJK now regulates crypto as a financial asset (P2SK amendment in force
June 17, 2026). Check rupiah withdrawal caps, tax on every trade (Indonesia levies income
tax and VAT on crypto trades), and bank flags on repeated large IDR movements. The
premium can widen against you for a month. Do this inside your own account only.

**Measurement first.** `research/spread_logger.py` records the premium every N minutes.
Run it for 2 to 4 weeks before touching capital. The go/no-go numbers are in the script's
docstring.

### 2. Stock-perpetual weekend basis. Barrier: needs a brokerage leg + oracle risk.

**What it is.** Binance, Hyperliquid and others now list perpetuals on US stocks
(SpaceX, Nvidia, etc.). They trade 24/7 while the cash market closes. Prices are
discovered independently on the weekend.

**Evidence.** An August 2026 weekend: four stock perps on Binance traded about
$461M combined, all priced above Friday close on Sunday, all opened lower Monday.
Same name trades 0.15% to 0.75% apart across venues, wider overnight.

**Why it survives.** Equity market makers are not set up to hedge on a crypto perp
venue at 3am Sunday; crypto desks mostly cannot hedge in the cash equity. Retail
dominates the weekend book and is directionally long.

**How to trade it.** Short the perp Sunday evening when it is rich to Friday close
(adjusted for any known weekend news), hedge with the cash equity or a CFD at Monday
open, close both. It is not risk-free: a real weekend news event moves the "fair" price
and the perp is right, not you. Size for that.

**Measurement first.** Log Friday close, Sunday 20:00 UTC perp mark, Monday open.
Ten weekends tells you whether the premium is systematic or was one August anecdote.

### 3. Prediction-market combinatorial arbitrage. Barrier: reasoning, not speed.

**What it is.** On Polymarket, logically dependent markets are priced inconsistently.
Example shape: "Candidate X wins" priced at 0.40 while the sum of the state-level
markets that imply it is priced as if 0.55.

**Evidence.** Saguillo et al., AFT 2025 ("Unravelling the Probabilistic Forest"):
about $40M realized arbitrage profit April 2024 to April 2025, across 86M bets and
7,000+ markets. Only ~$10.6M was simple intra-market rebalancing. The larger share was
combinatorial, across dependent markets. Bots dominate the intra-market kind. The
combinatorial kind needs someone to *notice* that two markets are logically linked.

**Why it survives.** Detecting dependence between free-text market questions is a
reasoning problem. Speed bots do not do it. An LLM pipeline that reads new markets,
proposes logical dependencies, and flags price inconsistencies is a genuinely
under-built tool.

**Risks.** Resolution rules differ subtly between "dependent" markets and a "sure thing"
can resolve the wrong way. Liquidity on long-tail markets is thin. Polymarket geo-blocks
some jurisdictions.

### 4. Long-tail perp funding arbitrage across DEXes. Barrier: operational pain.

Hyperliquid paid on average 7.17% more annualised funding than Binance on BTC
(2023 to 2026) and long-tail perps on new listings run 20 to 60%+ APR. Break-even is
about 1.3 bps per 8h window with maker orders. This is known and increasingly crowded
on majors, but on newly listed long-tail tokens it stays wide because the tail risk
(delisting, liquidation cascades, thin books, hourly vs 8h settlement mismatches) is
real. Only worth it with an automated position manager. Not "untouched", but still
paying.

### 5. Volatility risk premium. Barrier: none. Well known.

Implied minus realised vol on BTC options averages ~12% and selling it has been
consistently profitable. Listed here only for completeness: every options desk does
this. Not a gap.

## Recommendation

Do not pick one on gut. Measure the first three cheaply and in parallel:

1. **Run the spread logger for 30 days** on the Indonesia premium. It costs nothing.
2. **Log ten weekends** of stock-perp vs cash close by hand or with a second logger.
3. **Prototype the dependency finder** for Polymarket with 50 markets and see how many
   real inconsistencies it surfaces per week.

Whichever shows a repeatable gap net of fees after that gets capital. The others get
dropped without regret.

## Sources

- Kimchi premium 2026 data: cryptotimes.io (Sept 1, 2026), Bloomberg (Sept 1, 2026),
  CryptoQuant via cryptonews.net, spotedcrypto.com
- Indonesia regulation: license.aiying.cc (P2SK / OJK transition), lightspark.com,
  liminalcustody.com
- Stock perpetuals weekend behaviour: coingecko.com/learn/tokenized-stock-perpetuals-price-discovery,
  reports.tiger-research.com, altstreet.investments
- Prediction market arbitrage: arXiv 2508.03474, AFT 2025 (LIPIcs.AFT.2025.27),
  arXiv 2605.00864 (Polymarket NBA markets)
- Funding arbitrage: bitmex.com Q2 2026 derivatives report, neuralarb.com (Apr 24, 2026),
  arbitragescanner.io
- Volatility premium: pandabull.io, livevolatile.com
- Market inefficiency literature: arXiv 2602.20771, JFQA "Informational Efficiency of
  Cryptocurrency Markets"
