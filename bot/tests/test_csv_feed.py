"""Tests for the CSV importer — the path that lets real data into the harness.

Covers the three auto-detected layouts (Binance raw klines, headered OHLCV,
combined leader/laggard) and confirms a real CSV round-trips into a frame the
existing engine and lag detector accept unchanged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.feeds import make_lead_lag_series, export_csv, load_combined_csv
from bot.feeds.csv_feed import read_price_series, load_pair_csv
from bot.signals.lead_lag import detect_lag


def _write_binance_kline_csv(path, closes, start_ms=1_700_000_000_000, step_ms=1000):
    """Emit a headerless Binance-style kline CSV (12 cols, close at index 4)."""
    rows = []
    for i, c in enumerate(closes):
        ot = start_ms + i * step_ms
        ct = ot + step_ms - 1
        rows.append([ot, c, c, c, c, 1.0, ct, c, 10, 0.5, 0.5, 0])
    pd.DataFrame(rows).to_csv(path, header=False, index=False)


def test_combined_csv_roundtrip(tmp_path):
    df = make_lead_lag_series(n=2000, lag_bars=5, seed=1)
    p = tmp_path / "pair.csv"
    export_csv(df, p)
    loaded = load_combined_csv(p)
    assert list(loaded.columns) == ["leader", "laggard"]
    assert np.allclose(loaded["leader"].values, df["leader"].values)
    # The lag survives the round trip.
    assert detect_lag(loaded)[0] == 5


def test_binance_raw_kline_detected(tmp_path):
    closes = list(np.linspace(100, 110, 50))
    p = tmp_path / "BTCUSDT-1s.csv"
    _write_binance_kline_csv(p, closes)
    s = read_price_series(p)
    assert len(s) == 50
    assert isinstance(s.index, pd.DatetimeIndex)
    assert abs(s.iloc[0] - 100.0) < 1e-9 and abs(s.iloc[-1] - 110.0) < 1e-9


def test_load_pair_csv_aligns_two_files(tmp_path):
    df = make_lead_lag_series(n=500, lag_bars=4, seed=2)
    lead_p, lag_p = tmp_path / "lead.csv", tmp_path / "lag.csv"
    _write_binance_kline_csv(lead_p, df["leader"].tolist())
    _write_binance_kline_csv(lag_p, df["laggard"].tolist())
    pair = load_pair_csv(lead_p, lag_p)
    assert list(pair.columns) == ["leader", "laggard"]
    assert len(pair) == 500


def test_headered_ohlcv_csv(tmp_path):
    idx = pd.date_range("2024-01-01", periods=30, freq="1min")
    df = pd.DataFrame({"close_time": idx, "close": np.arange(30) + 100.0})
    p = tmp_path / "ohlcv.csv"
    df.to_csv(p, index=False)
    s = read_price_series(p)
    assert len(s) == 30 and abs(s.iloc[0] - 100.0) < 1e-9
