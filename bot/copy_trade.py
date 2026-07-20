"""Copy-trading a market maker: can you 'latch on' by mirroring its positions?

Even with a PERFECT real-time tracker (Hyperliquid streams fills live over
WebSocket), copying a market maker fails — and this simulation shows why even at
**zero lag**:

  1. A market maker's position is **transient inventory**, not a directional
     bet. It is (by construction) uncorrelated with future price — the MM earns
     the spread, not the move. So mirroring the position captures **noise**.
  2. You mirror as a **taker**, paying a fee on every position change the MM
     makes — and an MM churns its inventory constantly. The fees are a certain
     loss; the position has no expected gain to offset them.

We simulate an MM whose inventory mean-reverts (kept near flat by quoting) and a
copier that mirrors that inventory with a chosen lag, paying taker costs. Output:
the copier bleeds at every lag, including 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class CopyParams:
    steps: int = 20_000
    price_vol: float = 0.0015      # per-step price vol (~15 bps)
    inv_revert: float = 0.15       # how fast MM inventory mean-reverts to flat
    inv_scale: float = 1.0         # MM inventory magnitude
    # The MM's inventory has, by construction, NO predictive value for future
    # price (it's the other side of liquidity). This knob lets us even GIFT the
    # copier some fake directional alpha to show how much it would take to win.
    inv_alpha: float = 0.0
    taker_cost: float = 0.0005     # 5 bps per side when copier changes position


def simulate(rng: np.random.Generator, p: CopyParams):
    """Return (price_returns, mm_position) — the things a tracker would see."""
    n = p.steps
    # MM inventory as a mean-reverting (OU) process around 0.
    inv = np.zeros(n)
    for i in range(1, n):
        inv[i] = inv[i - 1] * (1 - p.inv_revert) + rng.normal(0, p.inv_scale)
    # Price returns. inv_alpha (default 0) optionally lets inventory weakly
    # predict the next return — a gift the real MM does not give you.
    noise = rng.normal(0, p.price_vol, n)
    rets = noise.copy()
    if p.inv_alpha:
        rets[1:] += p.inv_alpha * p.price_vol * np.sign(inv[:-1])
    return rets, inv


def copier_pnl(rets, inv, lag: int, p: CopyParams) -> dict:
    """PnL of mirroring the MM's net position with `lag` steps of delay."""
    n = len(rets)
    # Copier holds sign of MM inventory observed `lag` steps ago.
    target = np.zeros(n)
    if lag < n:
        target[lag:] = np.sign(inv[:n - lag])
    # Gross: hold target[t] across return rets[t] (next-step).
    gross = float(np.sum(target[:-1] * rets[1:]))
    # Costs: pay taker_cost whenever the copied position changes.
    changes = np.abs(np.diff(target))
    fees = float(np.sum(changes) * p.taker_cost)
    net = gross - fees
    return {
        "lag": lag,
        "gross_bps": gross * 1e4,
        "fees_bps": -fees * 1e4,
        "net_bps": net * 1e4,
        "position_changes": int(changes.sum()),
    }


def run(p: CopyParams | None = None, lags=(0, 1, 5, 20), seed: int = 31) -> pd.DataFrame:
    p = p or CopyParams()
    rng = np.random.default_rng(seed)
    rets, inv = simulate(rng, p)
    return pd.DataFrame([copier_pnl(rets, inv, lag, p) for lag in lags])


def main() -> int:
    print("Copy-trading a market maker — even a PERFECT real-time tracker:\n")
    df = run()
    print(df.to_string(index=False, float_format=lambda v: f"{v:.1f}"))
    verdict = "LOSES at every lag" if (df["net_bps"] <= 0).all() else "check"
    print(f"\nVerdict: copier {verdict} — including lag 0.")
    print("Reason: the MM's position has no directional edge (gross ~ 0 noise), "
          "but\nmirroring it as a taker pays a fee on every churn. Real-time data "
          "doesn't help\nbecause the problem was never visibility — it's that you "
          "copy inventory, not edge.\n")

    print("How much fake alpha would the copier need to overcome the fees? "
          "(lag 0)")
    rows = []
    for a in (0.0, 0.05, 0.1, 0.2):
        d = run(CopyParams(inv_alpha=a), lags=(0,)).iloc[0]
        rows.append({"gifted_alpha": a, "net_bps": round(d["net_bps"], 1)})
    print(pd.DataFrame(rows).to_string(index=False))
    print("...and a real market maker's inventory gifts you alpha = 0.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
