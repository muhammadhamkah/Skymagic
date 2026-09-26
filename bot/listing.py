"""New-listing momentum: is there a tradable edge in a fresh listing?

Unlike HFT / lead-lag, this gap is NOT speed-gated — a listing pump-and-bleed
plays out over minutes to hours, so retail latency is irrelevant. The catches
are different and just as deadly:

  * **Brutal, time-varying spreads.** In the first minutes a new listing's
    half-spread can be 50-150 bps and only narrows as liquidity builds. Early
    entry — exactly when the move is biggest — is also when trading is most
    expensive.
  * **Fat-tailed, selection-driven outcomes.** A few listings 3x; many bleed
    out. The *mean* can look great while the *median* loses, and you cannot know
    in advance which is which. Mean >> median is the warning sign.
  * **Shortability.** The classic "list and bleed" decay is only harvestable if
    you can short — usually impossible on spot at listing (perps list later).

This module models all three: a synthetic listing-event generator with a
realistic pump→decay path and a decaying spread, and a cost-aware backtest that
sweeps entry timing, holding horizon, and side (long the pump vs. fade it).

Run:  python -m bot.listing
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

pd.set_option("display.width", 140)
pd.set_option("display.max_columns", 30)


@dataclass
class ListingModel:
    """Distributions governing a population of synthetic listing events."""

    minutes: int = 240            # length of each event window (1-min bars)
    pump_mean: float = 0.25       # mean log peak overshoot (+25%)
    pump_sd: float = 0.55         # spread of peak sizes (heavy tail; some flop)
    peak_min: int = 3             # earliest the pump peaks (min)
    peak_max: int = 30            # latest the pump peaks (min)
    terminal_mean: float = -0.18  # mean log drift over the window (list & bleed)
    terminal_sd: float = 0.25
    noise_sd: float = 0.012       # per-minute idiosyncratic noise
    noise_phi: float = 0.9        # noise autocorrelation
    spread0: float = 0.010        # half-spread at t=0 (100 bps!) ...
    spread_floor: float = 0.0005  # ... decaying to 5 bps ...
    spread_tau: float = 15.0      # ... with a 15-min decay scale


def make_event(model: ListingModel, rng: np.random.Generator) -> pd.DataFrame:
    """One listing event: a price path and a time-varying half-spread.

    The price is a pump (hump that peaks at ``tau``) plus a terminal drift plus
    autocorrelated noise — the canonical "spike then bleed" shape, randomized.
    """
    T = model.minutes
    t = np.arange(T)

    A = rng.normal(model.pump_mean, model.pump_sd)        # peak overshoot (log)
    tau = rng.integers(model.peak_min, model.peak_max + 1)
    B = rng.normal(model.terminal_mean, model.terminal_sd)  # terminal drift (log)

    # Hump that peaks at t=tau with height A: A*(t/tau)*exp(1 - t/tau).
    hump = A * (t / tau) * np.exp(1.0 - t / tau)
    drift = B * (t / T)

    noise = np.zeros(T)
    for i in range(1, T):
        noise[i] = model.noise_phi * noise[i - 1] + rng.normal(0, model.noise_sd)

    log_price = hump + drift + noise
    price = np.exp(log_price)  # P0 = 1.0

    half_spread = model.spread_floor + model.spread0 * np.exp(-t / model.spread_tau)
    return pd.DataFrame({"price": price, "half_spread": half_spread})


def make_dataset(n_events: int, model: ListingModel, seed: int = 13) -> list[pd.DataFrame]:
    rng = np.random.default_rng(seed)
    return [make_event(model, rng) for _ in range(n_events)]


def load_event_csv(path: str) -> pd.DataFrame:
    """Load one real listing from an OHLC CSV (as written by fetch_listings).

    ``price`` = close. The true spread isn't in klines, so we approximate the
    half-spread per bar as ``0.5 * (high - low) / close`` — a rough but real
    proxy: a new listing's intrabar range is wide exactly when its spread is
    wide. Floored at 5 bps. Documented as an approximation, not ground truth.
    """
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    close = df[cols["close"]].astype(float)
    high = df[cols.get("high", cols["close"])].astype(float)
    low = df[cols.get("low", cols["close"])].astype(float)
    half_spread = (0.5 * (high - low) / close).clip(lower=0.0005)
    return pd.DataFrame({"price": close.values, "half_spread": half_spread.values})


def load_dataset_csv(csv_dir: str) -> list[pd.DataFrame]:
    from pathlib import Path

    paths = sorted(Path(csv_dir).glob("*.csv"))
    if not paths:
        raise SystemExit(f"No CSVs found in {csv_dir}")
    return [load_event_csv(str(p)) for p in paths]


def backtest_side(
    events: list[pd.DataFrame],
    side: str,
    entry_delay: int,
    hold: int,
    fee: float = 0.0010,
    slippage: float = 0.0005,
) -> dict:
    """Trade every listing the same way; return distribution stats per event.

    For each event: enter ``entry_delay`` minutes after listing, hold ``hold``
    minutes, exit. Pay the (time-varying) half-spread on entry and exit, plus
    fees and slippage. ``side`` is "long" (ride the pump) or "short" (fade it).
    """
    rets = []
    for ev in events:
        T = len(ev)
        e = min(entry_delay, T - 2)
        x = min(e + hold, T - 1)
        p_e, p_x = ev["price"].iloc[e], ev["price"].iloc[x]
        # Return on capital. Long = p_x/p_e - 1 (unbounded above, correct).
        # Short P&L = (p_e - p_x)/p_e = 1 - p_x/p_e, BOUNDED at +100% — a short
        # can't gain more than the whole position (price floors at zero). The
        # naive p_e/p_x - 1 explodes on near-zero exits and fabricates absurd
        # returns on coins that collapsed; that is a formula artifact, not money.
        gross = (p_x / p_e - 1.0) if side == "long" else (1.0 - p_x / p_e)
        cost = ev["half_spread"].iloc[e] + ev["half_spread"].iloc[x] + 2 * fee + 2 * slippage
        rets.append(gross - cost)
    r = np.array(rets)
    return {
        "side": side,
        "entry_min": entry_delay,
        "hold_min": hold,
        "events": len(r),
        "mean_%": r.mean() * 100,
        "median_%": np.median(r) * 100,
        "win_%": (r > 0).mean() * 100,
        "p05_%": np.percentile(r, 5) * 100,
        "p95_%": np.percentile(r, 95) * 100,
        "entry_cost_bps": events[0]["half_spread"].iloc[min(entry_delay, len(events[0]) - 2)] * 1e4,
    }


def sweep(events: list[pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for side in ("long", "short"):
        for entry in (1, 5, 15, 30, 60):
            for hold in (15, 30, 60, 120):
                rows.append(backtest_side(events, side, entry, hold))
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="New-listing momentum backtest")
    ap.add_argument("--csv-dir", help="dir of real listing OHLC CSVs (from fetch_listings)")
    ap.add_argument("--n-events", type=int, default=400, help="synthetic events if no --csv-dir")
    args = ap.parse_args(argv)

    model = ListingModel()
    if args.csv_dir:
        events = load_dataset_csv(args.csv_dir)
        print(f"Loaded {len(events)} REAL listing events from {args.csv_dir}.")
        print("(half-spread approximated from each bar's high-low range.)\n")
    else:
        events = make_dataset(args.n_events, model)
        print(f"Simulated {len(events)} listing events "
              f"({model.minutes}-min windows, pump→bleed with decaying spread).\n")

    # What a buy-and-hold-from-listing investor experiences (the naive play).
    window = len(events[0])
    naive = backtest_side(events, "long", entry_delay=1, hold=window - 2)
    print(f"Naive 'buy at listing, hold {window//60}h':  "
          f"mean {naive['mean_%']:+.1f}%  median {naive['median_%']:+.1f}%  "
          f"win {naive['win_%']:.0f}%   <- mean>>median = fat-tailed, most bleed\n")

    res = sweep(events)
    res = res.sort_values("mean_%", ascending=False).reset_index(drop=True)

    print("=== Top 8 by MEAN net return per event ===")
    print(res.head(8).to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    print("\n=== Same rows, but watch MEDIAN (the typical trade) ===")
    best = res.head(8)
    fragile = best[best["median_%"] <= 0]
    print(f"  of the top 8 by mean, {len(fragile)} have a NEGATIVE median — "
          f"profit lives entirely in the tail.")

    print("\n=== Best by MEDIAN (a typical-trade edge, not a tail bet) ===")
    by_med = res.sort_values("median_%", ascending=False).head(5)
    print(by_med.to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    print(
        "\nReading it: 'long early' rides the pump but pays a huge entry spread "
        "(see\nentry_cost_bps) and its edge is mean-not-median — a few moonshots "
        "carry it.\n'Short/fade' harvests the bleed but only if you can actually "
        "short at listing\n(usually you can't on spot). A positive *median* with "
        "a survivable p05 is the\nonly thing that's a real, repeatable edge "
        "rather than a lottery ticket.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
