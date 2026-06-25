"""Configuration objects for the lead-lag backtest.

Everything that affects whether the edge is real — fees, latency, slippage,
holding horizon — is an explicit, tunable parameter here. The whole point of
this project is that those parameters, not the signal, decide profitability.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CostModel:
    """Realistic trading-cost assumptions for a 100 USDT retail account.

    Defaults reflect Binance spot at the *worst* (VIP-0) fee tier, which is
    where a small account actually lives. Lead-lag reaction trades must hit the
    book to be fast, so they pay the taker fee on both legs.
    """

    taker_fee: float = 0.0010  # 0.10% — Binance VIP-0 taker
    maker_fee: float = 0.0010  # 0.10% — Binance VIP-0 maker
    bnb_discount: float = 0.0  # set 0.25 to model paying fees in BNB (-25%)
    # Half-spread you cross when taking liquidity, in fraction of price. The
    # laggard is usually the *less* liquid leg, so this is not negligible.
    half_spread: float = 0.0002  # 2 bps
    # Extra slippage beyond the half-spread (impact/queue), fraction of price.
    slippage: float = 0.0001  # 1 bp
    # "taker" crosses the book (fast, pays spread); "maker" posts passively
    # (slow, may *earn* the spread but risks not filling / adverse selection).
    mode: str = "taker"
    # For maker mode: fraction of the half-spread you actually capture per leg
    # after adverse selection. Conservative default — you rarely earn it all.
    spread_capture: float = 0.0

    def effective_taker(self) -> float:
        return self.taker_fee * (1.0 - self.bnb_discount)

    def effective_maker(self) -> float:
        return self.maker_fee * (1.0 - self.bnb_discount)

    def round_trip_cost(self) -> float:
        """Total cost of an in-and-out trade, fraction of notional.

        This is the number a lead-lag move must *exceed* to make money.

        * taker: two fees + crossing the spread twice + slippage twice.
        * maker: two maker fees, *minus* the half-spread you capture on each
          leg (a credit), plus residual slippage. Models the upside of posting
          passively while staying honest about adverse selection via
          ``spread_capture`` < 1.
        """
        if self.mode == "maker":
            return (
                2 * self.effective_maker()
                - 2 * self.half_spread * self.spread_capture
                + 2 * self.slippage
            )
        return 2 * self.effective_taker() + 2 * self.half_spread + 2 * self.slippage


@dataclass
class ExecutionModel:
    """How an order actually gets filled, including the killer: latency."""

    # Wall-clock delay between observing the signal and the fill landing, in
    # the same time unit as the bar interval (seconds). Retail-from-home is
    # ~0.05-0.2s+ once you add API round trips; colocated HFT is sub-ms.
    latency_seconds: float = 0.1
    bar_seconds: float = 1.0  # interval of one bar/observation, in seconds
    taker: bool = True  # take liquidity (fast) vs. post-only maker (slow)

    @property
    def latency_bars(self) -> int:
        """Latency expressed in whole bars, rounded up — you cannot act faster
        than the next observation you can actually see and fill against."""
        import math

        if self.bar_seconds <= 0:
            raise ValueError("bar_seconds must be > 0")
        return max(0, math.ceil(self.latency_seconds / self.bar_seconds))


@dataclass
class SignalConfig:
    """Lead-lag signal parameters."""

    leader: str = "BTCUSDT"
    laggard: str = "ETHUSDT"
    # Lookback window (in bars) over which the leader's return is measured.
    lookback_bars: int = 3
    # How long we hold the laggard position after entering, in bars.
    holding_bars: int = 5
    # Minimum leader move (fraction) required to trigger a trade. Acts as a
    # noise filter; raising it trades less but only on stronger signals.
    entry_threshold: float = 0.0
    # If True, auto-detect the lag from the cross-correlation and set the
    # holding/lookback around it instead of using the fixed values above.
    auto_lag: bool = False


@dataclass
class BacktestConfig:
    signal: SignalConfig = field(default_factory=SignalConfig)
    cost: CostModel = field(default_factory=CostModel)
    execution: ExecutionModel = field(default_factory=ExecutionModel)
    # Capital is intentionally small — this is the whole premise.
    capital_usdt: float = 100.0
    # Fraction of capital deployed per trade (1.0 = all-in each signal).
    position_fraction: float = 1.0
