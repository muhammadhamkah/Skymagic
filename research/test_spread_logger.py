"""Offline tests for the premium math and storage. Run: python -m pytest research/ -q"""

import sqlite3

from research import spread_logger as sl


def fake_get(url: str) -> dict:
    if "btcidr" in url:
        return {"ticker": {"buy": "1650000000", "sell": "1652000000"}}
    if "usdtidr" in url:
        return {"ticker": {"buy": "16500", "sell": "16520"}}
    if "BTCUSDT" in url:
        return {"bidPrice": "100000.0", "askPrice": "100010.0"}
    if "er-api" in url:
        return {"rates": {"IDR": 16000.0}}
    raise AssertionError(f"unexpected url {url}")


def test_compute_premia_positive_premium():
    btc_p, usdt_p = sl.compute_premia(
        indodax_btc=(1_650_000_000, 1_652_000_000),
        indodax_usdt=(16_500, 16_520),
        binance_btc=(100_000, 100_010),
        usd_idr=16_000,
    )
    # 1.651e9 / (100005 * 16000) - 1 = 0.031869...
    assert abs(btc_p - 0.031869) < 1e-4
    # 16510 / 16000 - 1 = 0.031875
    assert abs(usdt_p - 0.031875) < 1e-6


def test_compute_premia_zero_when_aligned():
    btc_p, usdt_p = sl.compute_premia(
        indodax_btc=(1_600_000_000, 1_600_000_000),
        indodax_usdt=(16_000, 16_000),
        binance_btc=(100_000, 100_000),
        usd_idr=16_000,
    )
    assert abs(btc_p) < 1e-12
    assert abs(usdt_p) < 1e-12


def test_sample_uses_injected_fetcher():
    row = sl.sample(get=fake_get)
    assert row["usd_idr"] == 16000.0
    assert row["binance_btc_ask"] == 100010.0
    assert abs(row["btc_premium"] - 0.031869) < 1e-4
    assert row["ts"].endswith("+00:00")


def test_insert_and_summary_roundtrip():
    conn = sqlite3.connect(":memory:")
    conn.executescript(sl.SCHEMA)
    base = sl.sample(get=fake_get)
    for i, prem in enumerate([0.02, -0.01, 0.005, -0.02]):
        row = dict(base, ts=f"2026-09-0{i + 1}T00:00:00+00:00", btc_premium=prem, usdt_premium=prem / 2)
        sl.insert(conn, row)
    s = sl.summary(conn)
    assert s["n"] == 4
    assert s["btc"]["sign_flips"] == 3
    assert s["btc"]["share_abs_over_1.5pct"] == 0.5
    assert s["btc"]["max_pct"] == 2.0
    assert s["btc"]["min_pct"] == -2.0


def test_summary_empty():
    conn = sqlite3.connect(":memory:")
    conn.executescript(sl.SCHEMA)
    assert sl.summary(conn) == {"n": 0}
