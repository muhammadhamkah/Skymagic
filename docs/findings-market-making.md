# Findings: Market making — the legit "scalp tiny profits, constantly"

The honest version of the thing the whole investigation kept chasing. A market
maker doesn't predict — it posts a bid and an ask and earns the spread on every
round trip, thousands of times. That *is* "tiny profits, constantly." So can a
**retail** maker do it?

**Short answer: market making is a real, constant edge — but it belongs to
whoever is fast enough to avoid being picked off and gets paid a maker rebate.
The exact same Avellaneda–Stoikov strategy is steady income for a pro and a
steady bleed for a slow, fee-paying retail maker. The edge is the speed and the
rebate, not the strategy.**

---

## Method (`bot/market_making.py`)

Avellaneda–Stoikov optimal quoting, used the way practitioners actually use it:

- **Reservation price** `r = s − q·γ·σ²·(T−t)` skews quotes away from the side
  that would grow inventory (long → quote lower → sell faster).
- **Spread width** is pinned to the *competitive* market spread (a few bps),
  NOT the textbook A-S "optimal" width — that formula assumes a monopolist and
  yields absurd 70+ bps quotes nobody could actually post.
- **Fills** are Poisson order flow whose intensity decays with distance from
  mid. A tunable fraction are **toxic**: after they hit you, price drifts
  against your new inventory (adverse selection).

Monte Carlo over 300 sessions; PnL = cash + inventory marked to the final mid.

## Results (5 bps competitive half-spread)

**1. Adverse selection sets the breakeven (toxicity 0.5):**

| Picked off by (per fill) | PnL per fill | Sessions profitable |
|---|---|---|
| 2 bps | +2.62 bps | 69% |
| 6 bps | +1.30 bps | 59% |
| **10 bps** | **−0.02 bps** | **50%** |
| 15 bps | −1.67 bps | 41% |

A 5 bps spread cannot survive being picked off for more than ~8 bps a fill. And
**how hard you get picked off is a function of speed** — a fast maker reprices
before stale quotes are hit; a slow one is run over.

**2. Same strategy, two operators:**

| Operator | PnL per fill | Sessions profitable |
|---|---|---|
| **Pro** (fast, −1 bp rebate, low toxicity, 2 bps adverse) | **+4.86 bps** | 80% |
| **Retail** (slow, +1 bp fee, high toxicity, 12 bps adverse) | **−1.49 bps** | 44% |

Identical quoting logic. Opposite outcomes. The pro keeps the spread; the retail
maker hands it to the informed traders who pick off its stale quotes — and pays
a fee for the privilege instead of earning a rebate.

## Why this is the punchline of the whole investigation

Every gap we tested died for the same reason in different clothes:

| Strategy | What it really needed |
|---|---|
| HFT | speed |
| Scalping / lead-lag | speed (act inside the window) |
| New-listing momentum | information / luck (fat tails) |
| **Market making** | **speed (avoid pick-off) + rebate (volume tier)** |

The thing that makes "constant tiny profits" work is never a clever signal —
it's a **structural position**: being fast enough, or large enough for a
rebate, or holding a risk premium others won't. A 100 USDT retail account has
none of those. That is the real, final answer to "where is the edge we can
constantly scalp": the edge is real, it is structural, and it is held by the
people on the other side of the trade from retail.

## What this leaves actually open for retail

Not scalping. The only honest "constant small income" left standing is the
**risk-premium / provider** family that does NOT require speed:

- **Funding-rate carry** (delta-neutral, collect funding) — real ~8–20% APY,
  wants ~$2k+, has tail risk. Untested here; the natural next build.
- Lending / LP fees — provider income with their own risks (impermanent loss,
  smart-contract risk).

These pay you for providing capital and bearing risk, not for predicting or
out-running anyone — which is the only door a small account can walk through.

## Reproduce

```bash
python -m bot.market_making        # toxicity sweep, adverse sweep, pro vs retail
python -m pytest bot/tests/test_market_making.py -q
```
