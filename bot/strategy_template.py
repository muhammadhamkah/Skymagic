"""TEMPLATE — drop your private strategy here and run the honest evaluator.

Fill in `my_signal`. It must be CAUSAL: the value at bar t may use only data up
to and including t (never df['price'].shift(-1), never .iloc[t+1], no full-series
fit that leaks the future). The evaluator will catch you if it isn't.

Then run:  python -m bot.strategy_template

Nothing here is sent anywhere — your edge stays on your machine.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import CostModel
from .evaluate import evaluate_strategy, report
from .feeds import make_lead_lag_series  # swap for your own data loader / CSV


def my_signal(df: pd.DataFrame) -> pd.Series:
    """Return a Series in {-1, 0, +1} aligned to df, using only PAST data.

    >>> Replace the body below with your strategy. <<<

    Example placeholder (a trivial momentum rule on the price column) — delete
    and put your real logic here:
    """
    ret = np.log(df["price"]).diff(3)        # trailing 3-bar return (causal)
    sig = pd.Series(0, index=df.index, dtype=int)
    sig[ret > 0] = 1
    sig[ret < 0] = -1
    return sig


def load_data() -> pd.DataFrame:
    """Replace with your real data. Must have a 'price' column.

    For real prices, use the CSV importer:
        from .feeds import load_pair_csv
        df = load_pair_csv('leader.csv', 'laggard.csv'); df['price'] = df['laggard']
    """
    df = make_lead_lag_series(n=20_000, lag_bars=5)
    df["price"] = df["laggard"]
    return df


def main() -> int:
    df = load_data()
    # Set costs to YOUR venue/fee tier. Defaults = Binance VIP-0 taker (~26 bps).
    cost = CostModel(taker_fee=0.0010, half_spread=0.0002, slippage=0.0001)
    result = evaluate_strategy(df, my_signal, price_col="price", hold=5, cost=cost)
    report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
