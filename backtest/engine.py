"""Long-only spot backtest engine with realistic per-fill fees.

Two volume-profile theses, both decided on bar t using only a rolling profile
of bars [t-window .. t-1] (strictly past — no lookahead), executed at bar
t+1's open to avoid same-bar fill bias:

  revert_poc : buy when close prints below the Value Area Low (a "dip"),
               sell when it reverts up to the POC. Mean-reversion.
  breakout   : buy when close crosses above the Value Area High,
               sell when it falls back below the POC. Momentum.

Spot only: no shorting, no leverage. Fee is charged on both the buy and the
sell. P&L is always reported against buy-and-hold over the same bars, and
split into in-sample / out-of-sample so you can see whether an edge survives
on data it was not tuned on.
"""

from __future__ import annotations

from dataclasses import dataclass

from .data import Candle
from .volume_profile import compute


@dataclass
class Trade:
    entry_time: int
    exit_time: int
    entry: float
    exit: float
    ret: float  # net return after fees, as a fraction


@dataclass
class Result:
    label: str
    strategy: str
    n_bars: int
    n_trades: int
    win_rate: float
    strategy_return: float    # total, net of fees
    buy_hold_return: float
    max_drawdown: float
    fees_paid: float          # cumulative fee drag, fraction of equity
    trades: list[Trade]

    def summary(self) -> str:
        edge = self.strategy_return - self.buy_hold_return
        verdict = "BEATS" if edge > 0 else "LOSES TO"
        return (
            f"[{self.label}] {self.strategy}\n"
            f"  bars              : {self.n_bars}\n"
            f"  trades            : {self.n_trades}  (win rate {self.win_rate:6.1%})\n"
            f"  strategy return   : {self.strategy_return:+8.2%}  (net of fees)\n"
            f"  buy & hold return : {self.buy_hold_return:+8.2%}\n"
            f"  edge vs hold      : {edge:+8.2%}   -> {verdict} hold\n"
            f"  max drawdown      : {self.max_drawdown:8.2%}\n"
            f"  cumulative fees   : {self.fees_paid:8.2%} of equity\n"
        )


def _signals(candles: list[Candle], strategy: str, window: int, rows: int,
             value_area: float) -> list[int]:
    """Return desired position (0 or 1) per bar, decided from PAST bars only."""
    pos = [0] * len(candles)
    state = 0
    for t in range(window, len(candles)):
        prof = compute(candles[t - window:t], rows=rows, value_area=value_area)
        if prof is None:
            pos[t] = state
            continue
        close = candles[t].close
        if strategy == "revert_poc":
            if state == 0 and close < prof.val:
                state = 1
            elif state == 1 and close >= prof.poc:
                state = 0
        elif strategy == "breakout":
            if state == 0 and close > prof.vah:
                state = 1
            elif state == 1 and close < prof.poc:
                state = 0
        else:
            raise ValueError(f"unknown strategy: {strategy}")
        pos[t] = state
    return pos


def run(
    candles: list[Candle],
    strategy: str = "revert_poc",
    window: int = 48,
    rows: int = 25,
    value_area: float = 0.68,
    fee: float = 0.001,      # 0.1% taker, per side
    label: str = "all",
) -> Result:
    """Backtest one strategy. Fills at the next bar's open after a signal."""
    pos = _signals(candles, strategy, window, rows, value_area)

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    fees_paid = 0.0
    trades: list[Trade] = []

    in_pos = False
    entry_price = 0.0
    entry_time = 0

    # act on bar t's signal at bar t+1's open
    for t in range(window, len(candles) - 1):
        want = pos[t]
        fill = candles[t + 1].open

        if want == 1 and not in_pos:
            in_pos = True
            entry_price = fill
            entry_time = candles[t + 1].open_time
            equity *= (1 - fee)
            fees_paid += fee
        elif want == 0 and in_pos:
            gross = fill / entry_price
            equity *= gross * (1 - fee)
            fees_paid += fee
            net = gross * (1 - fee) ** 2 - 1
            trades.append(Trade(entry_time, candles[t + 1].open_time,
                                entry_price, fill, net))
            in_pos = False
        elif in_pos:
            # mark-to-market the open position for drawdown tracking
            pass

        # equity including any open position, for drawdown
        mtm = equity * (candles[t + 1].close / entry_price) if in_pos else equity
        peak = max(peak, mtm)
        max_dd = max(max_dd, (peak - mtm) / peak)

    # close any position at the last close
    if in_pos:
        last = candles[-1].close
        gross = last / entry_price
        equity *= gross * (1 - fee)
        fees_paid += fee
        trades.append(Trade(entry_time, candles[-1].open_time,
                            entry_price, last, gross * (1 - fee) ** 2 - 1))

    first_open = candles[window + 1].open if window + 1 < len(candles) else candles[0].open
    buy_hold = candles[-1].close / first_open - 1.0
    wins = sum(1 for tr in trades if tr.ret > 0)
    win_rate = wins / len(trades) if trades else 0.0

    return Result(
        label=label,
        strategy=strategy,
        n_bars=len(candles),
        n_trades=len(trades),
        win_rate=win_rate,
        strategy_return=equity - 1.0,
        buy_hold_return=buy_hold,
        max_drawdown=max_dd,
        fees_paid=fees_paid,
        trades=trades,
    )


def run_split(candles: list[Candle], strategy: str, split: float = 0.6,
              **kwargs) -> tuple[Result, Result]:
    """Run in-sample / out-of-sample. The OOS result is the one that matters."""
    cut = int(len(candles) * split)
    in_sample = run(candles[:cut], strategy=strategy, label="in-sample", **kwargs)
    out_sample = run(candles[cut:], strategy=strategy, label="out-of-sample", **kwargs)
    return in_sample, out_sample
