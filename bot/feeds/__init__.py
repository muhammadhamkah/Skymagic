"""Market-data feeds: synthetic (offline), Binance (live), CSV (real, offline)."""

from .synthetic import make_lead_lag_series
from .binance import fetch_klines, load_pair
from .csv_feed import (
    load_pair_csv,
    load_combined_csv,
    read_price_series,
    export_csv,
)

__all__ = [
    "make_lead_lag_series",
    "fetch_klines",
    "load_pair",
    "load_pair_csv",
    "load_combined_csv",
    "read_price_series",
    "export_csv",
]
