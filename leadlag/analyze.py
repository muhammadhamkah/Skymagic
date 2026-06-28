"""Lead-lag analysis + honest cost model for collected MEXC ticks.

Answers two questions:
  1. Is there a real lead-lag relationship? (cross-correlation of mid returns)
  2. Does any signal survive realistic round-trip cost? (spread + taker fee + slippage)

Usage:
    python analyze.py --db ticks.db --leader BTCUSDT --laggard SOLUSDT \
        --grid-ms 250 --max-lag 20 --taker-bps 5

The cost model is deliberately conservative. A positive gross correlation is
NOT a profit; it must beat the round-trip cost on the laggard to be tradable.
"""

from __future__ import annotations

import argparse
import sqlite3

import numpy as np
import pandas as pd


def load_series(conn: sqlite3.Connection, symbol: str, grid_ms: int) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT ts_local_ms, bid, ask FROM ticks WHERE symbol = ? ORDER BY ts_local_ms",
        conn,
        params=(symbol,),
    )
    if df.empty:
        raise SystemExit(f"no rows for {symbol} in db")
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread_bps"] = (df["ask"] - df["bid"]) / df["mid"] * 1e4
    df["ts"] = pd.to_datetime(df["ts_local_ms"], unit="ms")
    df = df.set_index("ts")
    # forward-fill onto a uniform grid so both symbols share a clock
    grid = df[["mid", "spread_bps"]].resample(f"{grid_ms}ms").last().ffill()
    return grid


def cross_correlation(leader_ret: pd.Series, laggard_ret: pd.Series, max_lag: int):
    """corr(leader[t], laggard[t+k]) for k in -max_lag..max_lag.

    k > 0  => laggard moves AFTER leader  => leader genuinely leads (tradable).
    k < 0  => laggard moves first         => your 'leader' actually lags.
    """
    a = leader_ret.to_numpy()
    b = laggard_ret.to_numpy()
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    rows = []
    for k in range(-max_lag, max_lag + 1):
        if k >= 0:
            x, y = a[: n - k], b[k:]
        else:
            x, y = a[-k:], b[: n + k]
        if len(x) > 5 and x.std() > 0 and y.std() > 0:
            corr = float(np.corrcoef(x, y)[0, 1])
        else:
            corr = float("nan")
        rows.append((k, corr))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Lead-lag + cost analysis")
    ap.add_argument("--db", default="ticks.db")
    ap.add_argument("--leader", required=True)
    ap.add_argument("--laggard", required=True)
    ap.add_argument("--grid-ms", type=int, default=250)
    ap.add_argument("--max-lag", type=int, default=20, help="in grid steps")
    ap.add_argument("--taker-bps", type=float, default=5.0,
                    help="round-trip taker fee in bps (MEXC spot taker ~5bps; use 0 if truly 0-fee pair)")
    ap.add_argument("--slippage-bps", type=float, default=1.0, help="assumed slippage per side")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    lead = load_series(conn, args.leader, args.grid_ms)
    lag = load_series(conn, args.laggard, args.grid_ms)
    conn.close()

    joined = lead.join(lag, lsuffix="_lead", rsuffix="_lag", how="inner").dropna()
    if len(joined) < 50:
        raise SystemExit(f"only {len(joined)} aligned points; collect more data")

    lead_ret = np.log(joined["mid_lead"]).diff().dropna()
    lag_ret = np.log(joined["mid_lag"]).diff().dropna()
    lead_ret, lag_ret = lead_ret.align(lag_ret, join="inner")

    xc = cross_correlation(lead_ret, lag_ret, args.max_lag)
    valid = [(k, c) for k, c in xc if not np.isnan(c)]
    best_k, best_c = max(valid, key=lambda kc: abs(kc[1]))

    # --- cost model ---------------------------------------------------------
    # Round-trip cost you must beat to trade the laggard on a leader signal.
    lag_spread = float(joined["spread_bps_lag"].median())
    roundtrip_cost_bps = lag_spread + args.taker_bps + 2 * args.slippage_bps
    # Crude gross-edge proxy: a 1-sigma leader move predicts best_c * sigma_lag
    # of laggard move. Compare that predicted capture to the cost.
    sigma_lag_bps = float(lag_ret.std() * 1e4)
    predicted_capture_bps = abs(best_c) * sigma_lag_bps

    print("\n=== LEAD-LAG REPORT ===")
    print(f"leader={args.leader}  laggard={args.laggard}  grid={args.grid_ms}ms  points={len(joined)}")
    print("\ncross-correlation (k = lag in grid steps; k>0 means laggard follows leader):")
    for k, c in xc:
        if np.isnan(c):
            continue
        bar = "#" * int(abs(c) * 50)
        flag = "  <-- peak" if k == best_k else ""
        print(f"  k={k:+3d} ({k*args.grid_ms:+5d}ms)  corr={c:+.3f} {bar}{flag}")

    print(f"\npeak: k={best_k} ({best_k*args.grid_ms:+d}ms), corr={best_c:+.3f}")
    if best_k <= 0:
        print("  ⚠ peak is at k<=0: your 'leader' does NOT lead. No tradable signal this direction.")

    print("\n=== COST REALITY CHECK (laggard side) ===")
    print(f"  median laggard spread : {lag_spread:6.2f} bps")
    print(f"  taker fee (round trip): {args.taker_bps:6.2f} bps")
    print(f"  slippage (both sides) : {2*args.slippage_bps:6.2f} bps")
    print(f"  -> round-trip cost    : {roundtrip_cost_bps:6.2f} bps")
    print(f"  predicted capture     : {predicted_capture_bps:6.2f} bps  (|corr| x laggard 1-sigma)")
    verdict = (
        "PLAUSIBLE EDGE — investigate further with execution simulation"
        if (best_k > 0 and predicted_capture_bps > roundtrip_cost_bps)
        else "NO EDGE after costs — signal does not beat round-trip cost"
    )
    print(f"\n  VERDICT: {verdict}")
    print("\n  (Reminder: this is an upper-bound proxy. Real fills face latency,")
    print("   queue position, and adverse selection that this does NOT model.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
