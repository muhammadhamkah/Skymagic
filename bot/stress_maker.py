"""Stress test: does the maker edge survive adverse selection?

The hunt's best configs were maker-only — but they assumed you *earn* half the
spread on every passive fill (spread_capture=0.5, no adverse selection). That is
the optimistic case. In reality a resting order tends to fill precisely when the
market is running against it, so the fill is worse than mid. This script turns
that knob up and watches the edge.

For the winning regime (slow_alt, trade only strong moves), it sweeps the
adverse-selection penalty per leg and reports the net edge per trade for both
spot-maker and futures-maker, with the taker variants as fixed reference lines
(takers cross the book, so they don't carry this particular penalty).

Run:  python -m bot.stress_maker
"""

from __future__ import annotations

import pandas as pd

from .config import BacktestConfig, CostModel, ExecutionModel, SignalConfig
from .feeds import make_lead_lag_series
from .backtest.engine import backtest
from .backtest.metrics import summarize

pd.set_option("display.width", 140)
pd.set_option("display.max_columns", 30)

# Two regimes, same long (10-bar) lag so latency is never the issue here — the
# ONLY difference is how fat the gross edge is. "generous" is the optimistic
# synthetic case the hunt liked; "thin" is what real, noisy markets look like
# (weak pass-through, lots of idiosyncratic noise → small captured move).
REGIMES = {
    "generous": dict(lag=10, leader_vol=0.0025, follow=0.90, noise=0.0006,
                     threshold=0.0040),
    "thin":     dict(lag=10, leader_vol=0.0008, follow=0.35, noise=0.0010,
                     threshold=0.0010),
}
LATENCY = 2.0               # realistic-ish 2s; still < the 10-bar lag
ADVERSE_BPS = [0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0]   # per-leg, in bps


def _summary(df, cost: CostModel, lag: int, threshold: float):
    cfg = BacktestConfig(
        signal=SignalConfig(lookback_bars=1, holding_bars=lag,
                            entry_threshold=threshold),
        cost=cost,
        execution=ExecutionModel(latency_seconds=LATENCY, bar_seconds=1.0),
    )
    s = summarize(backtest(df, cfg))
    return cfg.cost.round_trip_cost() * 1e4, s


def _run_regime(name: str, rp: dict) -> None:
    df = make_lead_lag_series(
        n=40_000, lag_bars=rp["lag"], leader_vol=rp["leader_vol"],
        follow_strength=rp["follow"], laggard_noise=rp["noise"], seed=11,
    )
    lag, thr = rp["lag"], rp["threshold"]

    # The frictionless gross edge — the size of the prize before any cost.
    _, gross_s = _summary(
        df, CostModel(taker_fee=0.0, maker_fee=0.0, half_spread=0.0, slippage=0.0,
                      mode="taker"), lag, thr)
    print(f"\n================  REGIME: {name}  ================")
    print(f"  frictionless gross edge = {gross_s['avg_gross_bps']:+.2f} bps/trade "
          f"over {gross_s['trades']} trades (the prize)")

    for label, cost in [
        ("spot taker VIP0", CostModel(taker_fee=0.0010, half_spread=0.0002,
                                      slippage=0.0001, mode="taker")),
        ("futures taker",   CostModel(taker_fee=0.0005, half_spread=0.0001,
                                      slippage=0.0001, mode="taker")),
    ]:
        rt, s = _summary(df, cost, lag, thr)
        print(f"  ref {label:16s} rt={rt:5.1f}bps  "
              f"net/trade={s['avg_net_bps']:+6.2f}bps  win={s['win_rate_pct']:.1f}%")

    rows = []
    for adverse in ADVERSE_BPS:
        a = adverse / 1e4
        spot = CostModel(maker_fee=0.0010, half_spread=0.0002, slippage=0.0001,
                         mode="maker", spread_capture=0.0, adverse_selection=a)
        fut = CostModel(maker_fee=0.0002, half_spread=0.0001, slippage=0.0001,
                        mode="maker", spread_capture=0.0, adverse_selection=a)
        rt_s, s_spot = _summary(df, spot, lag, thr)
        rt_f, s_fut = _summary(df, fut, lag, thr)
        rows.append({
            "adverse_bps": adverse,
            "spot_rt_bps": round(rt_s, 1),
            "spot_net/trade": round(s_spot["avg_net_bps"], 2),
            "fut_rt_bps": round(rt_f, 1),
            "fut_net/trade": round(s_fut["avg_net_bps"], 2),
        })
    out = pd.DataFrame(rows)
    print(out.to_string(index=False))

    def death(col):
        neg = out[out[col] <= 0]
        return None if neg.empty else float(neg["adverse_bps"].iloc[0])

    for col, who in [("spot_net/trade", "spot maker"), ("fut_net/trade", "futures maker")]:
        d = death(col)
        if d is None:
            verdict = "survives the whole sweep"
        elif d == 0.0:
            verdict = "already negative even with ZERO adverse selection"
        else:
            verdict = f"dies at {d:.0f} bps adverse selection"
        print(f"  {who:14s}: {verdict}")


def main() -> int:
    print("Stress: maker fills with NO spread credit, rising adverse selection.")
    print("Same 10-bar lag in both regimes — only the gross edge size differs.")
    for name, rp in REGIMES.items():
        _run_regime(name, rp)
    print("\nTakeaway: the maker edge survives adverse selection only in "
          "proportion to the\ngross edge. Where the prize is fat it shrugs it "
          "off; where the prize is thin —\nwhich is what real markets usually "
          "offer — a few bps of adverse selection is\nenough to flip it "
          "negative. Cutting fees cannot save a thin edge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
