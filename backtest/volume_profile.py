"""Volume profile: POC / Value Area High / Value Area Low from candles.

Mirrors the calculation in the DGT "Pivot Anchored Volume Profile" Pine
indicator — each bar's volume is spread across that bar's high->low range and
binned into price levels — but with one deliberate change:

    We use a ROLLING window of the last N *closed* bars, never a pivot.

The Pine version anchors to pivot highs/lows, and a pivot is only confirmed
`pvtLength` bars after it occurs (ta.pivothigh(L, L) needs L bars to its
right). On a chart that lag is invisible; in a live bot it is lookahead bias.
A rolling window of closed bars contains only information available at the
decision bar, so a backtest built on it does not cheat with the future.
"""

from __future__ import annotations

from dataclasses import dataclass

from .data import Candle


@dataclass
class Profile:
    poc: float       # price of the highest-volume level
    vah: float       # value-area high
    val: float       # value-area low
    low: float       # window price floor
    high: float      # window price ceiling


def compute(
    window: list[Candle],
    rows: int = 25,
    value_area: float = 0.68,
) -> Profile | None:
    """Build a volume profile over `window` (all bars assumed already closed).

    Returns None if the window is degenerate (flat price or no volume).
    """
    if not window:
        return None

    price_low = min(c.low for c in window)
    price_high = max(c.high for c in window)
    step = (price_high - price_low) / rows
    if step <= 0:
        return None

    bins = [0.0] * rows
    for c in window:
        if c.volume <= 0:
            continue
        span = c.high - c.low
        # distribute the bar's volume across every level it overlapped,
        # proportional to overlap — uniform within the bar (same assumption
        # the Pine script makes). span==0 dumps it all in one level.
        for level in range(rows):
            lo = price_low + level * step
            hi = lo + step
            if c.high >= lo and c.low < hi:
                weight = 1.0 if span == 0 else step / span
                bins[level] += c.volume * weight

    total = sum(bins)
    if total <= 0:
        return None

    poc_level = bins.index(max(bins))
    target = total * value_area

    above = poc_level
    below = poc_level
    acc = bins[poc_level]
    while acc < target:
        if below == 0 and above == rows - 1:
            break
        vol_above = bins[above + 1] if above < rows - 1 else 0.0
        vol_below = bins[below - 1] if below > 0 else 0.0
        if vol_above == 0 and vol_below == 0:
            break
        # expand toward the heavier adjacent level (matches the Pine logic)
        if vol_above >= vol_below:
            acc += vol_above
            above += 1
        else:
            acc += vol_below
            below -= 1

    return Profile(
        poc=price_low + (poc_level + 0.5) * step,
        vah=price_low + (above + 1.0) * step,
        val=price_low + below * step,
        low=price_low,
        high=price_high,
    )
