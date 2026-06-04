"""Backtest harness for volume-profile (POC/VAH/VAL) trading rules.

Lets you test whether a "buy low / sell high" rule built on a pivot/rolling
volume profile actually beats buy-and-hold *after fees* — without risking a
cent. Data comes from a local Binance-kline CSV, or a synthetic generator so
the engine runs even where the network blocks the Binance API.
"""
