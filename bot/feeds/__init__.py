"""Market-data feeds: synthetic (offline, known ground truth) and Binance."""

from .synthetic import make_lead_lag_series
from .binance import fetch_klines, load_pair

__all__ = ["make_lead_lag_series", "fetch_klines", "load_pair"]
