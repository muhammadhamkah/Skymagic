"""The hunt: does ANY honest configuration net positive after costs?

Sweeps the levers a real retail lead-lag bot can actually pull —

  * **market regime** (fast/liquid small moves vs. a slower alt with a longer
    lag and bigger moves),
  * **signal selectivity** (entry threshold — only trade strong leader moves),
  * **holding horizon**,
  * **execution latency** (how fast you really are vs. the lag), and
  * **cost scenario** (taker VIP-0 → maker-only + BNB discount),

then ranks every combination by net return after honest costs. The output
tells us whether a capturable edge exists at all, and what it requires.

Run:  python -m bot.hunt
"""

from __future__ import annotations

import itertools

import pandas as pd

from .config import BacktestConfig, CostModel, ExecutionModel, SignalConfig
from .feeds import make_lead_lag_series
from .backtest.engine import backtest
from .backtest.metrics import summarize

pd.set_option("display.width", 140)
pd.set_option("display.max_columns", 30)


# --- Market regimes (synthetic, but each represents a real situation) --------
# leader_vol and follow_strength set how big the captured move is; lag sets how
# much speed you need. A slower, choppier alt gives bigger moves AND a longer
# lag — the only regime where a slow retail bot has room to act.
REGIMES = {
    "fast_liquid":  dict(lag=2,  leader_vol=0.0006, follow=0.80, noise=0.0004),
    "mid":          dict(lag=5,  leader_vol=0.0010, follow=0.85, noise=0.0004),
    "slow_alt":     dict(lag=10, leader_vol=0.0025, follow=0.90, noise=0.0006),
}

# --- Cost scenarios (the fee lever the user wants to explore next) -----------
COSTS = {
    "taker_vip0":   CostModel(taker_fee=0.0010, half_spread=0.0002, slippage=0.0001,
                              mode="taker"),
    "taker_bnb":    CostModel(taker_fee=0.0010, bnb_discount=0.25, half_spread=0.0002,
                              slippage=0.0001, mode="taker"),
    "maker_only":   CostModel(maker_fee=0.0010, half_spread=0.0002, slippage=0.0001,
                              mode="maker", spread_capture=0.5),
    "maker_bnb":    CostModel(maker_fee=0.0010, bnb_discount=0.25, half_spread=0.0002,
                              slippage=0.0001, mode="maker", spread_capture=0.5),
    # USDⓈ-M futures: taker 0.05% / maker 0.02% — half the spot fee, and the
    # perp is very liquid so the spread is tighter. The single biggest fee lever.
    "fut_taker":    CostModel(taker_fee=0.0005, half_spread=0.0001, slippage=0.0001,
                              mode="taker"),
    "fut_maker":    CostModel(maker_fee=0.0002, half_spread=0.0001, slippage=0.0001,
                              mode="maker", spread_capture=0.5),
}

THRESHOLDS = [0.0, 0.0010, 0.0020, 0.0040]   # 0, 10, 20, 40 bps leader move
LATENCIES = [0.5, 2.0]                        # seconds (bar = 1s)
N_BARS = 40_000


def run_hunt() -> pd.DataFrame:
    rows = []
    for regime_name, rp in REGIMES.items():
        df = make_lead_lag_series(
            n=N_BARS, lag_bars=rp["lag"], leader_vol=rp["leader_vol"],
            follow_strength=rp["follow"], laggard_noise=rp["noise"], seed=11,
        )
        lag = rp["lag"]
        for cost_name, threshold, latency in itertools.product(
            COSTS, THRESHOLDS, LATENCIES
        ):
            cfg = BacktestConfig(
                signal=SignalConfig(lookback_bars=1, holding_bars=lag,
                                    entry_threshold=threshold),
                cost=COSTS[cost_name],
                execution=ExecutionModel(latency_seconds=latency, bar_seconds=1.0),
            )
            s = summarize(backtest(df, cfg))
            rows.append({
                "regime": regime_name,
                "cost": cost_name,
                "rt_cost_bps": round(cfg.cost.round_trip_cost() * 1e4, 1),
                "thresh_bps": round(threshold * 1e4, 0),
                "latency_s": latency,
                "lag": lag,
                "trades": s["trades"],
                "net_%": round(s["total_return_pct"], 2),
                "avg_net_bps": round(s["avg_net_bps"], 2),
                "win_%": round(s["win_rate_pct"], 1),
                "sharpe": round(s["sharpe"], 3),
            })
    return pd.DataFrame(rows)


def main() -> int:
    res = run_hunt()
    res = res.sort_values("net_%", ascending=False).reset_index(drop=True)

    total = len(res)
    positive = res[res["net_%"] > 0]
    print(f"\nSwept {total} configurations across "
          f"{len(REGIMES)} regimes x {len(COSTS)} cost models "
          f"x {len(THRESHOLDS)} thresholds x {len(LATENCIES)} latencies.")
    print(f"Net-positive after honest costs: {len(positive)} / {total}\n")

    print("=== Top 12 configurations by net return ===")
    print(res.head(12).to_string(index=False))

    print("\n=== Worst 5 (for contrast) ===")
    print(res.tail(5).to_string(index=False))

    if len(positive):
        print("\n=== What the WINNERS have in common ===")
        print(f"  cost models : {sorted(positive['cost'].unique())}")
        print(f"  regimes     : {sorted(positive['regime'].unique())}")
        print(f"  thresholds  : {sorted(positive['thresh_bps'].unique())} bps")
        print(f"  latency<=lag: "
              f"{(positive['latency_s'] <= positive['lag']).mean()*100:.0f}% of winners")
    else:
        print("\nNo configuration nets positive — the cost floor wins everywhere.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
