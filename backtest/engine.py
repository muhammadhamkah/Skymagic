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
    strategy_return: float    # total, net of fees + slippage
    buy_hold_return: float
    max_drawdown: float       # strategy max drawdown
    hold_max_drawdown: float  # buy & hold max drawdown, same bars
    exposure: float           # fraction of tradable bars spent in a position
    cost_legs: int            # number of fee/slippage-charged fills
    trades: list[Trade]

    def summary(self) -> str:
        edge = self.strategy_return - self.buy_hold_return
        verdict = "BEATS" if edge > 0 else "LOSES TO"
        # win rate standard error, so a small-N number isn't read as gospel
        n = self.n_trades
        se = (self.win_rate * (1 - self.win_rate) / n) ** 0.5 if n else 0.0
        return (
            f"[{self.label}] {self.strategy}\n"
            f"  bars              : {self.n_bars}\n"
            f"  trades            : {self.n_trades}  (win rate {self.win_rate:6.1%} "
            f"+/- {1.96 * se:4.1%})  <- win rate != profit\n"
            f"  time in market    : {self.exposure:6.1%}  (rest in cash; risk != hold)\n"
            f"  strategy return   : {self.strategy_return:+8.2%}  (net of fees+slippage)\n"
            f"  buy & hold return : {self.buy_hold_return:+8.2%}\n"
            f"  edge vs hold      : {edge:+8.2%}   -> {verdict} hold\n"
            f"  max drawdown      : {self.max_drawdown:8.2%}  (hold: {self.hold_max_drawdown:.2%})\n"
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


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = float("-inf")
    mdd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    return mdd


def run(
    candles: list[Candle],
    strategy: str = "revert_poc",
    window: int = 48,
    rows: int = 25,
    value_area: float = 0.68,
    fee: float = 0.001,        # 0.1% taker commission, per side
    slippage: float = 0.0005,  # spread+slippage, per side, charged adversarially
    label: str = "all",
) -> Result:
    """Backtest one strategy. Fills at the next bar's open after a signal.

    Costs are charged on BOTH sides of every trade: `fee` (commission) plus
    `slippage` (a stand-in for the bid-ask spread, market impact and not always
    hitting the printed open). Buys fill at open*(1+slippage), sells at
    open*(1-slippage). This is intentionally conservative — for a Convert-style
    venue where the spread IS the cost, set fee~0 and slippage to the quoted
    spread. Fills are still assumed full, immediate and price-impact-free beyond
    the flat slippage term.
    """
    pos = _signals(candles, strategy, window, rows, value_area)

    equity = 1.0
    cost_legs = 0
    trades: list[Trade] = []
    strat_curve: list[float] = []

    in_pos = False
    entry_price = 0.0
    entry_time = 0
    bars_in_pos = 0
    tradable_bars = 0

    # act on bar t's signal at bar t+1's open
    for t in range(window, len(candles) - 1):
        want = pos[t]
        raw = candles[t + 1].open
        tradable_bars += 1

        if want == 1 and not in_pos:
            in_pos = True
            entry_price = raw * (1 + slippage)   # buy crosses the spread up
            entry_time = candles[t + 1].open_time
            equity *= (1 - fee)
            cost_legs += 1
        elif want == 0 and in_pos:
            sell = raw * (1 - slippage)          # sell crosses the spread down
            gross = sell / entry_price
            equity *= gross * (1 - fee)
            cost_legs += 1
            trades.append(Trade(entry_time, candles[t + 1].open_time,
                                entry_price, sell, gross * (1 - fee) - 1))
            in_pos = False

        if in_pos:
            bars_in_pos += 1
        # equity including any open position, for drawdown
        mtm = equity * (candles[t + 1].close / entry_price) if in_pos else equity
        strat_curve.append(mtm)

    # close any position at the last close
    if in_pos:
        sell = candles[-1].close * (1 - slippage)
        gross = sell / entry_price
        equity *= gross * (1 - fee)
        cost_legs += 1
        trades.append(Trade(entry_time, candles[-1].open_time,
                            entry_price, sell, gross * (1 - fee) - 1))

    first_open = candles[window + 1].open if window + 1 < len(candles) else candles[0].open
    buy_hold = candles[-1].close / first_open - 1.0
    hold_curve = [c.close / first_open for c in candles[window + 1:]]
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
        max_drawdown=_max_drawdown(strat_curve),
        hold_max_drawdown=_max_drawdown(hold_curve),
        exposure=bars_in_pos / tradable_bars if tradable_bars else 0.0,
        cost_legs=cost_legs,
        trades=trades,
    )


def run_split(candles: list[Candle], strategy: str, split: float = 0.6,
              **kwargs) -> tuple[Result, Result]:
    """Run in-sample / out-of-sample. The OOS result is the one that matters."""
    cut = int(len(candles) * split)
    in_sample = run(candles[:cut], strategy=strategy, label="in-sample", **kwargs)
    out_sample = run(candles[cut:], strategy=strategy, label="out-of-sample", **kwargs)
    return in_sample, out_sample
