"""Conditional lead-lag event study: 'only trade when the move clears the spread'.

This tests the refinement: instead of trading the *average* relationship, only
enter the laggard when the leader has just made a move large enough that the
expected follow-through should beat cost. Crucially, it pays the laggard's
ACTUAL touch price AT EVENT TIME -- so if the spread widens exactly when the
leader jumps (it does, in real markets), this captures that cost honestly.

Entry/exit are modeled as TAKER fills at the far touch:
  long : buy at ask(entry), sell at bid(exit)
  short: sell at bid(entry), buy at ask(exit)
This means the bid-ask spread at entry AND exit is paid automatically from the
real quotes -- no constant-spread assumption.

Usage:
    python event_study.py --db ticks.db --leader BTCUSDT --laggard SOLUSDT \
        --grid-ms 250 --signal-steps 1 --threshold-bps 8 \
        --latency-steps 1 --hold-steps 2 --taker-bps 0 --slippage-bps 1
"""

from __future__ import annotations

import argparse
import sqlite3

import numpy as np
import pandas as pd


def load_quotes(conn: sqlite3.Connection, symbol: str, grid_ms: int) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT ts_local_ms, bid, ask FROM ticks WHERE symbol=? ORDER BY ts_local_ms",
        conn, params=(symbol,),
    )
    if df.empty:
        raise SystemExit(f"no rows for {symbol}")
    df["ts"] = pd.to_datetime(df["ts_local_ms"], unit="ms")
    df = df.set_index("ts")
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    g = df[["bid", "ask", "mid"]].resample(f"{grid_ms}ms").last().ffill()
    return g


def main() -> int:
    ap = argparse.ArgumentParser(description="Conditional lead-lag event study")
    ap.add_argument("--db", default="ticks.db")
    ap.add_argument("--leader", required=True)
    ap.add_argument("--laggard", required=True)
    ap.add_argument("--grid-ms", type=int, default=250)
    ap.add_argument("--signal-steps", type=int, default=1,
                    help="window (in grid steps) over which the leader move is measured")
    ap.add_argument("--threshold-bps", type=float, default=8.0,
                    help="only trade when |leader move| over the window >= this")
    ap.add_argument("--latency-steps", type=int, default=1,
                    help="grid steps between observing the signal and getting filled")
    ap.add_argument("--latency-sweep", type=int, default=0,
                    help="if >0, sweep latency from 0..N steps and report the breakeven "
                         "latency (max latency at which mean net P&L stays positive)")
    ap.add_argument("--hold-steps", type=int, default=2,
                    help="grid steps held before exit (~ the measured lag)")
    ap.add_argument("--taker-bps", type=float, default=0.0, help="taker fee per side (use 0 for 0-fee)")
    ap.add_argument("--slippage-bps", type=float, default=1.0, help="extra slippage per side")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    lead = load_quotes(conn, args.leader, args.grid_ms)
    lag = load_quotes(conn, args.laggard, args.grid_ms)
    conn.close()

    j = lead.join(lag, lsuffix="_L", rsuffix="_g", how="inner").dropna()
    if len(j) < 50:
        raise SystemExit(f"only {len(j)} aligned points; collect more data")

    mid_L = j["mid_L"].to_numpy()
    bid_g = j["bid_g"].to_numpy()
    ask_g = j["ask_g"].to_numpy()
    mid_g = j["mid_g"].to_numpy()
    n = len(j)

    sig = args.signal_steps
    hold = args.hold_steps
    per_side = args.taker_bps + args.slippage_bps

    def run(lat: int):
        """Simulate the strategy at a given latency (in grid steps). Returns events array."""
        ev = []
        last_exit = -1
        for t in range(sig, n - lat - hold):
            if t <= last_exit:  # no overlapping positions
                continue
            leader_move_bps = (np.log(mid_L[t]) - np.log(mid_L[t - sig])) * 1e4
            if abs(leader_move_bps) < args.threshold_bps:
                continue
            direction = 1 if leader_move_bps > 0 else -1
            entry = t + lat
            exit_ = entry + hold
            if direction == 1:
                entry_px, exit_px = ask_g[entry], bid_g[exit_]      # buy ask, sell bid
            else:
                entry_px, exit_px = bid_g[entry], ask_g[exit_]      # sell bid, buy ask
            gross_bps = direction * (exit_px - entry_px) / entry_px * 1e4
            net_bps = gross_bps - 2 * per_side                     # fee+slip both sides
            avail_bps = direction * (np.log(mid_g[exit_]) - np.log(mid_g[entry])) * 1e4
            spr = (ask_g[entry] - bid_g[entry]) / mid_g[entry] * 1e4
            ev.append((leader_move_bps, avail_bps, gross_bps, net_bps, spr))
            last_exit = exit_
        return np.array(ev) if ev else np.empty((0, 5))

    # --- latency sweep: find the breakeven latency ------------------------
    if args.latency_sweep > 0:
        print("\n=== BREAKEVEN-LATENCY SWEEP ===")
        print(f"leader={args.leader} laggard={args.laggard} grid={args.grid_ms}ms "
              f"threshold={args.threshold_bps}bps hold={hold*args.grid_ms}ms")
        print(f"\n  {'latency':>12} {'events':>7} {'net/trade':>11} {'hit%':>6}")
        breakeven_ms = None
        for L in range(0, args.latency_sweep + 1):
            a = run(L)
            if len(a) == 0:
                continue
            net = a[:, 3]
            mark = ""
            if net.mean() > 0:
                breakeven_ms = L * args.grid_ms
            else:
                mark = "  <-- turns negative here"
            print(f"  {L*args.grid_ms:>10}ms {len(a):>7} {net.mean():>+9.2f}bps "
                  f"{(net>0).mean()*100:>5.1f}{mark}")
        print(f"\n  BREAKEVEN LATENCY: "
              + (f"~{breakeven_ms}ms — you must act faster than this for positive net."
                 if breakeven_ms is not None else
                 "negative even at 0ms latency — no edge regardless of speed."))
        print(f"\n  NOTE: grid is {args.grid_ms}ms, so this sweep cannot resolve "
              "single-digit-ms\n  breakeven. For that you need ms-resolution data "
              "(websocket collector),\n  not REST polling.")
        return 0

    lat = args.latency_steps
    events_arr = run(lat)
    events = [tuple(r) for r in events_arr]

    print("\n=== CONDITIONAL EVENT STUDY ===")
    print(f"leader={args.leader} laggard={args.laggard} grid={args.grid_ms}ms")
    print(f"signal_window={sig*args.grid_ms}ms  threshold={args.threshold_bps}bps  "
          f"latency={lat*args.grid_ms}ms  hold={hold*args.grid_ms}ms  "
          f"cost/side={per_side}bps")
    if not events:
        print(f"\nNo events cleared the {args.threshold_bps}bps threshold. "
              "Lower --threshold-bps or collect more data.")
        return 0

    arr = np.array(events)
    leader_mv, avail, gross, net, spr = (arr[:, i] for i in range(5))
    n_ev = len(events)
    print(f"\nevents: {n_ev}")
    print(f"  laggard mid follow-through (avail) : mean {avail.mean():+6.2f} bps")
    print(f"  spread paid at entry (median)      :      {np.median(spr):6.2f} bps")
    print(f"  gross P&L (after spread, pre-fee)  : mean {gross.mean():+6.2f} bps")
    print(f"  NET P&L (after spread+fee+slip)    : mean {net.mean():+6.2f} bps")
    print(f"  hit rate (net > 0)                 :      {(net > 0).mean()*100:5.1f}%")
    print(f"  total net over sample              :      {net.sum():+6.1f} bps "
          f"({n_ev} trades)")
    verdict = ("PLAUSIBLE — positive net per event; worth a websocket re-test with real latency"
               if net.mean() > 0 else
               "NO EDGE — the spread paid at event time eats the follow-through")
    print(f"\n  VERDICT: {verdict}")
    print("\n  Note: even a positive result here is optimistic — it assumes you get")
    print("  filled at the touch every time. Real adverse selection means you miss")
    print("  the good fills and catch the bad ones. Confirm with live data + a")
    print("  websocket feed before risking capital.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
