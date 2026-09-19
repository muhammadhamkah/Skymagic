"""Trading signals."""

from .lead_lag import detect_lag, leader_returns, lead_lag_signal

__all__ = ["detect_lag", "leader_returns", "lead_lag_signal"]
