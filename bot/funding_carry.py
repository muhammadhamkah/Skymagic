"""Funding-rate carry: the one "constant small income" not gated by speed.

Delta-neutral cash-and-carry: hold spot long and an equal short perpetual. Price
moves cancel (long spot gain ≈ short perp loss), so the PnL is the **funding**
the short collects every 8h, minus the two-leg trading costs — and minus the
things that actually go wrong:

  * **Funding flips negative.** In bull/neutral regimes longs pay shorts (you
    earn). In bear regimes you pay. Carry is a bet that funding stays positive.
  * **Fees front-load the loss.** You pay entry+exit on BOTH legs before any
    funding accrues, so you must hold long enough to clear the hurdle.
  * **Liquidation if you lever up.** "Delta neutral" only protects you if the
    legs are truly cross-margined. With isolated margin or two venues, a price
    spike can liquidate the short before the spot gain rescues it — and leverage
    sets how small a spike does it.

This harness models all four so the *net* APY — and its left tail — are honest,
not the "~15% APY!" headline that ignores regime risk and liquidation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

pd.set_option("display.width", 140)
pd.set_option("display.max_columns", 30)

PERIODS_PER_DAY = 3  # funding settles every 8h


@dataclass
class CarryParams:
    capital: float = 2000.0
    leverage: float = 1.0          # notional = capital * leverage
    hold_days: int = 30
    # Round-trip trading cost across BOTH legs (spot + perp, entry + exit), as a
    # fraction of notional. Default ~ careful maker on a low-fee venue.
    roundtrip_cost: float = 0.0011  # 11 bps
    # Funding regime: mean & sd of the 8h funding rate (fraction of notional).
    funding_mean: float = 0.00010   # +1 bp / 8h  (~11% APY) — neutral-bull
    funding_sd: float = 0.00015
    funding_phi: float = 0.92       # autocorrelation (regimes persist)
    # Price process (for liquidation risk on the short leg).
    daily_vol: float = 0.035        # 3.5%/day crypto vol
    # Liquidation: short is wiped if price rises past ~1/leverage (minus a
    # maintenance buffer). cross_margined=True means the spot hedge covers it
    # and there is effectively no liquidation.
    cross_margined: bool = True
    maint_buffer: float = 0.5       # fraction of 1/leverage kept as buffer
    liq_penalty: float = 0.05       # cost of a forced unwind / lost hedge (frac of capital)


def carry_episode(rng: np.random.Generator, p: CarryParams) -> dict:
    """Simulate one carry holding period. Returns net return on capital + APY."""
    n = p.hold_days * PERIODS_PER_DAY
    notional = p.capital * p.leverage

    # Autocorrelated funding path around the regime mean (OU-ish).
    f = np.empty(n)
    f[0] = p.funding_mean
    for i in range(1, n):
        f[i] = (p.funding_mean
                + p.funding_phi * (f[i - 1] - p.funding_mean)
                + rng.normal(0, p.funding_sd) * (1 - p.funding_phi))
    funding_income = float(f.sum()) * notional  # short collects funding when >0

    # Price path (per-period), to check the short-leg liquidation tail.
    per_period_vol = p.daily_vol / np.sqrt(PERIODS_PER_DAY)
    rets = rng.normal(0, per_period_vol, n)
    cum_up = np.maximum.accumulate(np.cumsum(rets))  # worst (highest) adverse move for a short
    liquidated = False
    if not p.cross_margined and p.leverage > 1:
        liq_level = (1.0 / p.leverage) * p.maint_buffer
        liquidated = bool((cum_up >= liq_level).any())

    fees = p.roundtrip_cost * notional
    pnl = funding_income - fees
    if liquidated:
        pnl -= p.liq_penalty * p.capital  # forced unwind cost; hedge broke

    net_return = pnl / p.capital
    apy = net_return * (365.0 / p.hold_days)
    return {
        "net_return": net_return,
        "apy": apy,
        "funding_income": funding_income,
        "fees": fees,
        "liquidated": liquidated,
    }


def run_regime(p: CarryParams, n_eps: int = 400, seed: int = 23) -> dict:
    rng = np.random.default_rng(seed)
    eps = [carry_episode(rng, p) for _ in range(n_eps)]
    apy = np.array([e["apy"] for e in eps])
    nr = np.array([e["net_return"] for e in eps])
    return {
        "mean_apy_%": float(apy.mean() * 100),
        "median_apy_%": float(np.median(apy) * 100),
        "pct_profitable": float((nr > 0).mean() * 100),
        "p05_apy_%": float(np.percentile(apy, 5) * 100),
        "liq_rate_%": float(np.mean([e["liquidated"] for e in eps]) * 100),
    }


def backtest_real_funding(funding_rates, p: CarryParams) -> dict:
    """Carry on a REAL series of 8h funding rates (fractions of notional).

    net return = leverage · (Σ funding − round-trip cost), annualised by the
    number of 8h periods. This answers the only question that matters: would
    carry have paid, net of fees, in the regime that actually happened?
    """
    f = np.asarray(funding_rates, dtype=float)
    periods = len(f)
    days = periods / PERIODS_PER_DAY
    gross = float(f.sum())
    net_return = p.leverage * (gross - p.roundtrip_cost)
    apy = net_return * (365.0 / days) if days > 0 else 0.0
    return {
        "periods": periods,
        "days": round(days, 1),
        "mean_funding_bps_8h": round(float(f.mean()) * 1e4, 3),
        "pct_periods_positive": round(float((f > 0).mean()) * 100, 1),
        "gross_%": round(gross * 100, 2),
        "net_%": round(net_return * 100, 2),
        "apy_%": round(apy * 100, 1),
    }


REGIMES = {
    "bull   (+2bp/8h)":    dict(funding_mean=0.00020, funding_sd=0.00020),
    "neutral(+1bp/8h)":    dict(funding_mean=0.00010, funding_sd=0.00015),
    "flat   (+0.3bp/8h)":  dict(funding_mean=0.00003, funding_sd=0.00015),
    "bear   (-0.5bp/8h)":  dict(funding_mean=-0.00005, funding_sd=0.00020),
}


def main() -> int:
    base = CarryParams()
    print(f"Funding carry — capital ${base.capital:.0f}, hold {base.hold_days}d, "
          f"round-trip cost {base.roundtrip_cost*1e4:.0f} bps, 1x (cross-margined).\n")

    print("=== Net APY across funding regimes (1x, properly hedged) ===")
    rows = []
    for name, kw in REGIMES.items():
        r = run_regime(CarryParams(**{**base.__dict__, **kw}))
        rows.append({"regime": name, **{k: round(v, 1) for k, v in r.items()}})
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n=== Leverage, ISOLATED margin (hedge can break) — neutral regime ===")
    lev_rows = []
    for lev in (1, 2, 3, 5):
        r = run_regime(CarryParams(**{**base.__dict__, "leverage": lev,
                                      "cross_margined": False}))
        lev_rows.append({"leverage": f"{lev}x", **{k: round(v, 1) for k, v in r.items()}})
    print(pd.DataFrame(lev_rows).to_string(index=False))

    print(
        "\nRead it: in positive-funding regimes, 1x cross-margined carry earns a "
        "modest,\nmostly-positive APY — real 'constant small income'. It turns "
        "NEGATIVE in a bear\nfunding regime, and leverage on isolated margin "
        "buys higher APY at the price of a\nliquidation tail that can erase "
        "months of carry in one squeeze. Cross-margin at\nlow leverage is the "
        "only sane way to run it.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
