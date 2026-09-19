"""Lead-lag trading bot — backtest-first.

The package is deliberately built so the *backtest harness* comes before any
live trading. The harness injects execution latency and realistic taker fees
and is strictly lookahead-safe, so it can tell us whether a lead-lag edge
actually survives the costs that destroy most retail "scalping" strategies —
before a single dollar is at risk.
"""

__all__ = ["config"]
