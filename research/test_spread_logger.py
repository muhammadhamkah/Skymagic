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


def test_binance_falls_back_to_main_api_when_vision_host_fails():
    calls = []

    def flaky_get(url: str) -> dict:
        calls.append(url)
        if "binance.vision" in url:
            raise RuntimeError("451")
        return {"bidPrice": "1", "askPrice": "2"}

    assert sl.fetch_binance("BTCUSDT", flaky_get) == (1.0, 2.0)
    assert "binance.vision" in calls[0] and "api.binance.com" in calls[1]


def _four_rows():
    base = sl.sample(get=fake_get)
    for i, prem in enumerate([0.02, -0.01, 0.005, -0.02]):
        yield dict(base, ts=f"2026-09-0{i + 1}T00:00:00+00:00", btc_premium=prem, usdt_premium=prem / 2)


def _check_summary(s):
    assert s["n"] == 4
    assert s["btc"]["sign_flips"] == 3
    assert s["btc"]["share_abs_over_1.5pct"] == 0.5
    assert s["btc"]["max_pct"] == 2.0
    assert s["btc"]["min_pct"] == -2.0


def test_sqlite_insert_and_summary_roundtrip():
    conn = sqlite3.connect(":memory:")
    conn.executescript(sl.SCHEMA)
    for row in _four_rows():
        sl.insert(conn, row)
    _check_summary(sl.summary(conn))


def test_csv_append_and_summary_roundtrip(tmp_path):
    path = tmp_path / "nested" / "idr_premium.csv"
    for row in _four_rows():
        sl.append_csv(str(path), row)
    lines = path.read_text().splitlines()
    assert lines[0].split(",") == sl.CSV_COLUMNS  # header written exactly once
    assert len(lines) == 5
    _check_summary(sl.summary_from_rows(sl.load_csv_premia(str(path))))


def test_summary_empty(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.executescript(sl.SCHEMA)
    assert sl.summary(conn) == {"n": 0}
    assert sl.summary_from_rows(sl.load_csv_premia(str(tmp_path / "missing.csv"))) == {"n": 0}
