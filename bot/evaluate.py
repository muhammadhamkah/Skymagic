"""Generic strategy evaluator — plug in YOUR signal, keep it private.

You don't have to share your edge with anyone. Write your strategy as a single
causal function `signal(df) -> Series in {-1, 0, +1}` (or position sizes), hand
it to `evaluate_strategy`, and it runs the same honest gauntlet that killed every
mirage in this project:

  1. **Lookahead check** — recomputes your signal on a truncated history and
     verifies the prefix is identical. If it changes, your signal peeks at the
     future and any backtest profit is fake. This is the #1 killer.
  2. **Latency sweep** — delays the fill by 0..N bars. If profit only exists at
     near-zero latency, the edge belonged to speed you don't have.
  3. **Honest costs** — fees + spread + slippage crossed adversely on every
     entry and exit.
  4. **Mean vs median** — a positive mean with a negative median is a fat-tailed
     lottery, not a repeatable edge.

It prints a verdict. It does not tell you to trade — it tells you whether the
thing survives contact with reality.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import CostModel


@dataclass
class EvalResult:
    lookahead_safe: bool
    table: pd.DataFrame
    verdict: str


def _backtest(price: np.ndarray, sig: np.ndarray, latency: int, hold: int,
              rt_cost: float) -> dict:
    """Non-overlapping, all-in backtest with delayed fills and round-trip cost."""
    nets = []
    n = len(price)
    t = 1
    while t < n:
        d = sig[t]
        if d == 0:
            t += 1
            continue
        e = t + latency
        x = e + hold
        if x >= n:
            break
        gross = d * (price[x] / price[e] - 1.0)
        nets.append(gross - rt_cost)
        t = x + 1
    nets = np.array(nets) if nets else np.array([0.0])
    return {
        "trades": int((sig != 0).sum() and len(nets)),
        "mean_bps": float(nets.mean() * 1e4),
        "median_bps": float(np.median(nets) * 1e4),
        "win_%": float((nets > 0).mean() * 100),
        "p05_bps": float(np.percentile(nets, 5) * 1e4),
        "sharpe": float(nets.mean() / nets.std()) if nets.std() > 0 else 0.0,
    }


def check_lookahead(df: pd.DataFrame, signal_fn, frac: float = 0.6) -> bool:
    """True if the signal is causal: its value at each bar must not change when
    later bars are removed. The single most important test."""
    m = int(len(df) * frac)
    full = np.asarray(signal_fn(df))[:m]
    prefix = np.asarray(signal_fn(df.iloc[:m]))
    if len(prefix) != m:
        return False
    return bool(np.allclose(np.nan_to_num(full), np.nan_to_num(prefix)))


def evaluate_strategy(
    df: pd.DataFrame,
    signal_fn,
    price_col: str = "price",
    hold: int = 5,
    cost: CostModel | None = None,
    latencies=(0, 1, 2, 5, 10),
) -> EvalResult:
    """Run a private signal through the honest gauntlet.

    Args:
        df: market data; must contain ``price_col``.
        signal_fn: ``df -> array/Series`` in {-1,0,+1}, CAUSAL.
        hold: holding horizon in bars.
        cost: cost model (defaults to Binance VIP-0 taker).
        latencies: fill delays (bars) to sweep.
    """
    cost = cost or CostModel()
    rt = cost.round_trip_cost()
    price = df[price_col].to_numpy()
    sig = np.asarray(signal_fn(df))

    safe = check_lookahead(df, signal_fn)

    rows = []
    for lat in latencies:
        r = _backtest(price, sig, lat, hold, rt)
        rows.append({"latency": lat, **r})
    table = pd.DataFrame(rows)

    # Verdict logic — the mirage flags.
    flags = []
    realistic = table[table["latency"] >= 1]
    if not safe:
        flags.append("LOOKAHEAD: signal changes when future bars are removed — backtest is fake")
    if table.iloc[0]["mean_bps"] > 0 and (realistic["mean_bps"] <= 0).all():
        flags.append("SPEED-GATED: profit only at zero latency — needs speed you lack")
    net_pos = realistic[realistic["mean_bps"] > 0]
    if len(net_pos) and (net_pos["median_bps"] <= 0).any():
        flags.append("FAT-TAILED: positive mean but negative median — a lottery, not an edge")
    if (table["mean_bps"] <= 0).all():
        flags.append("NO EDGE: negative after costs at every latency")

    if not flags and safe and (realistic["median_bps"] > 0).any():
        verdict = "SURVIVES: causal, positive median after costs at realistic latency. Worth paper-trading."
    else:
        verdict = "FAILS — " + " | ".join(flags) if flags else "INCONCLUSIVE"

    return EvalResult(lookahead_safe=safe, table=table, verdict=verdict)


def report(result: EvalResult) -> None:
    print("Lookahead-safe:", "YES" if result.lookahead_safe else "NO")
    print(result.table.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print("\nVERDICT:", result.verdict)
