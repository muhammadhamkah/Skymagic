"""Market making (Avellaneda–Stoikov): the legit version of "scalp constantly".

A market maker doesn't predict direction — it posts a bid and an ask and earns
the spread on every round trip, thousands of times. That IS "tiny profits,
constantly." The question this harness answers honestly: can a *retail* maker
keep any of that spread once **adverse selection** is modelled — the fact that
your resting order fills precisely when an informed trader is running you over?

We implement the Avellaneda–Stoikov (2008) optimal quotes:

    reservation price r = s - q · γ · σ² · (T - t)      # skews away from inventory
    optimal spread     = γ · σ² · (T - t) + (2/γ)·ln(1 + γ/k)
    bid = r - spread/2 ,  ask = r + spread/2

and simulate fills as Poisson order flow whose intensity decays with distance
from mid (λ = A·e^(−k·δ)). A tunable fraction of fills are **toxic**: after they
hit you, the price drifts against your new inventory. Sweeping that toxicity is
the whole point — it shows where the constant spread income turns into a
constant bleed, and how much a maker rebate buys back.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

pd.set_option("display.width", 140)
pd.set_option("display.max_columns", 30)


@dataclass
class MMParams:
    s0: float = 100.0          # starting mid price
    T: float = 1.0             # session length (abstract units)
    steps: int = 2000          # time steps per episode
    sigma: float = 2.0         # volatility (price units per sqrt(time))
    gamma: float = 0.001       # inventory risk aversion (drives the skew)
    k: float = 20.0            # order-book liquidity decay (per price unit)
    A: float = 160.0           # base order-arrival rate
    # The competitive spread you must quote at — set by other makers, NOT by the
    # A-S "optimal" formula (which assumes a monopolist and yields absurdly wide,
    # un-competitive quotes). 0.05 on a price of 100 == 5 bps half-spread.
    market_half: float = 0.05
    fee: float = 0.0001        # maker fee per fill (negative = rebate)
    max_inv: int = 25          # hard inventory cap
    toxicity: float = 0.0      # fraction of fills that are informed/toxic
    adverse: float = 0.03      # adverse price drift after a toxic fill (price units, 3 bps)


def as_quotes(s: float, q: int, tau: float, p: MMParams):
    """Quote at the competitive spread, skewed by inventory the A-S way.

    The reservation price r = s - q·γ·σ²·(T-t) shifts quotes away from the side
    that would grow inventory; the *width* is the market's competitive spread,
    not the (un-realisable) A-S optimal width. This is how A-S is actually used
    in practice — for the skew, with the spread pinned to competition.
    """
    skew = q * p.gamma * p.sigma ** 2 * tau
    r = s - skew
    return r - p.market_half, r + p.market_half, 2.0 * p.market_half


def simulate_episode(rng: np.random.Generator, p: MMParams) -> dict:
    """One market-making session. Returns PnL and its decomposition.

    PnL = cash + inventory marked at the final mid. We also track the gross
    spread captured vs. the adverse-selection cost paid, so the two forces are
    visible separately rather than hidden in one number.
    """
    s, q, cash = p.s0, 0, 0.0
    fills = 0
    spread_earned = 0.0
    adverse_paid = 0.0
    dt = p.T / p.steps
    half_spreads = []

    for i in range(p.steps):
        tau = max(p.T - i * dt, 1e-6)
        bid, ask, spread = as_quotes(s, q, tau, p)
        half_spreads.append(spread / 2.0)
        db = max(s - bid, 1e-9)
        da = max(ask - s, 1e-9)
        p_b = 1.0 - math.exp(-p.A * math.exp(-p.k * db) * dt)
        p_a = 1.0 - math.exp(-p.A * math.exp(-p.k * da) * dt)

        drift = 0.0
        if rng.random() < p_b and q < p.max_inv:        # someone sold into our bid
            cash -= bid * (1.0 + p.fee)
            q += 1
            fills += 1
            spread_earned += (s - bid)
            if rng.random() < p.toxicity:
                drift -= p.adverse                       # price keeps falling
                adverse_paid += p.adverse
        if rng.random() < p_a and q > -p.max_inv:        # someone bought our ask
            cash += ask * (1.0 - p.fee)
            q -= 1
            fills += 1
            spread_earned += (ask - s)
            if rng.random() < p.toxicity:
                drift += p.adverse                       # price keeps rising
                adverse_paid += p.adverse

        s = s + p.sigma * math.sqrt(dt) * rng.standard_normal() + drift

    pnl = cash + q * s
    return {
        "pnl": pnl,
        "pnl_bps": pnl / p.s0 * 1e4,
        "fills": fills,
        "final_inv": q,
        "half_spread_bps": float(np.mean(half_spreads)) / p.s0 * 1e4,
        "spread_earned_bps": spread_earned / p.s0 * 1e4,
        "adverse_paid_bps": adverse_paid / p.s0 * 1e4,
    }


def run_montecarlo(p: MMParams, n_episodes: int = 300, seed: int = 17) -> dict:
    rng = np.random.default_rng(seed)
    eps = [simulate_episode(rng, p) for _ in range(n_episodes)]
    pnl = np.array([e["pnl_bps"] for e in eps])
    fills = np.array([e["fills"] for e in eps])
    return {
        "episodes": n_episodes,
        "mean_pnl_bps": float(pnl.mean()),
        "median_pnl_bps": float(np.median(pnl)),
        "pct_profitable": float((pnl > 0).mean() * 100),
        "mean_fills": float(fills.mean()),
        "pnl_per_fill_bps": float(pnl.sum() / max(fills.sum(), 1)),
        "half_spread_bps": float(np.mean([e["half_spread_bps"] for e in eps])),
        "adverse_bps_per_ep": float(np.mean([e["adverse_paid_bps"] for e in eps])),
    }


def toxicity_sweep(p: MMParams, toxicities, fees=None) -> pd.DataFrame:
    """The headline: PnL vs adverse-selection toxicity, at one or more fee levels."""
    fees = fees if fees is not None else [p.fee]
    rows = []
    for fee in fees:
        for tox in toxicities:
            q = MMParams(**{**p.__dict__, "toxicity": tox, "fee": fee})
            r = run_montecarlo(q)
            rows.append({
                "fee_bps": round(fee * 1e4, 1),
                "toxicity": tox,
                "mean_pnl_bps": round(r["mean_pnl_bps"], 1),
                "median_pnl_bps": round(r["median_pnl_bps"], 1),
                "pct_profit": round(r["pct_profitable"], 0),
                "pnl_per_fill_bps": round(r["pnl_per_fill_bps"], 3),
                "fills": round(r["mean_fills"], 0),
            })
    return pd.DataFrame(rows)


def adverse_sweep(p: MMParams, adverses_bps, toxicity=0.5) -> pd.DataFrame:
    """PnL per fill as the per-pick-off cost rises — i.e. as you get SLOWER.

    A fast pro reprices before stale quotes are hit (small adverse); a slow
    retail maker is run over (large adverse). This sweep finds where the spread
    income flips to a bleed.
    """
    rows = []
    for adv_bps in adverses_bps:
        q = MMParams(**{**p.__dict__, "toxicity": toxicity, "adverse": adv_bps / 1e4 * p.s0})
        r = run_montecarlo(q)
        rows.append({
            "adverse_bps": adv_bps,
            "toxicity": toxicity,
            "pnl_per_fill_bps": round(r["pnl_per_fill_bps"], 2),
            "mean_pnl_bps": round(r["mean_pnl_bps"], 0),
            "pct_profit": round(r["pct_profitable"], 0),
        })
    return pd.DataFrame(rows)


def main() -> int:
    p = MMParams()
    base = run_montecarlo(p)
    print("Avellaneda–Stoikov market maker — Monte Carlo over "
          f"{base['episodes']} sessions.")
    print(f"Competitive half-spread quoted: {base['half_spread_bps']:.1f} bps "
          f"(what you earn per fill, before fees & adverse selection)\n")

    print("=== 1. Toxicity sweep (fee 1 bp, adverse 3 bps per toxic fill) ===")
    print(toxicity_sweep(p, toxicities=[0.0, 0.1, 0.25, 0.5, 0.75]).to_string(index=False))

    print("\n=== 2. How hard you get picked off when SLOW (toxicity 0.5) ===")
    print(adverse_sweep(p, adverses_bps=[2, 4, 6, 10, 15]).to_string(index=False))
    print("  ^ a 5 bps spread cannot survive being picked off for >~8 bps a "
          "fill. Speed sets how big that number is.")

    print("\n=== 3. Same strategy, two operators ===")
    pro = MMParams(**{**p.__dict__, "fee": -0.0001, "toxicity": 0.3, "adverse": 0.02})
    retail = MMParams(**{**p.__dict__, "fee": 0.0001, "toxicity": 0.6, "adverse": 0.12})
    rp, rr = run_montecarlo(pro), run_montecarlo(retail)
    print(f"  PRO    (fast, rebate -1bp, low toxicity, 2 bps adverse) : "
          f"{rp['pnl_per_fill_bps']:+.2f} bps/fill, {rp['pct_profitable']:.0f}% sessions profitable")
    print(f"  RETAIL (slow, fee +1bp, high toxicity, 12 bps adverse)  : "
          f"{rr['pnl_per_fill_bps']:+.2f} bps/fill, {rr['pct_profitable']:.0f}% sessions profitable")

    print(
        "\nThe verdict: market making IS 'tiny profits, constantly' — but the "
        "spread is kept\nby whoever is fast enough to avoid being picked off and "
        "gets paid a rebate. The\nEXACT SAME A-S strategy is a steady income for "
        "the pro and a steady bleed for the\nslow, fee-paying retail maker. The "
        "edge was never the strategy — it's the speed\nand the rebate, and "
        "retail has neither.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
