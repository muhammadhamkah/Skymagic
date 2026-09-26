"""Load real market data from local CSV files.

The live Binance REST feed needs network egress that locked-down environments
(including Claude Code on the web) block. This importer is the escape hatch:
export klines anywhere — Binance's free public dumps at
https://data.binance.vision, a TradingView export, your own logger — drop the
files in, and run the identical backtest/hunt/stress harness on real prices.

Three input shapes are auto-detected:

  1. **Binance raw kline CSV** (no header, 11-12 numeric columns) — exactly what
     data.binance.vision ships. Close price = col 4, timestamp = col 6.
  2. **Headered OHLCV CSV** with a ``close`` column and a time-like column
     (``close_time`` / ``open_time`` / ``timestamp`` / ``time`` / ``date``).
  3. **Two-column CSV** of ``time, price``.

Use :func:`load_pair_csv` for two files (one per symbol) or
:func:`load_combined_csv` for a single file that already has ``leader`` and
``laggard`` columns.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# Binance kline column order (data.binance.vision and the REST API share it).
_BINANCE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_base", "taker_quote", "ignore",
]
_TIME_CANDIDATES = ("close_time", "open_time", "timestamp", "time", "date", "datetime")


def _to_datetime(s: pd.Series) -> pd.Series:
    """Parse a timestamp column whether it's epoch s/ms/us or a date string."""
    if pd.api.types.is_numeric_dtype(s):
        v = float(s.iloc[0])
        unit = "us" if v > 1e14 else "ms" if v > 1e11 else "s"
        return pd.to_datetime(s, unit=unit)
    return pd.to_datetime(s)


def read_price_series(path: str | Path, price_col: str = "close") -> pd.Series:
    """Read one symbol's close-price series from a CSV, indexed by time.

    Auto-detects the three supported layouts. Returns a float Series named
    ``close`` indexed by a DatetimeIndex, sorted and de-duplicated.
    """
    path = Path(path)
    # Peek at the first line to decide whether there's a header.
    first = path.read_text().splitlines()[0]
    first_field = first.split(",")[0].strip().strip('"')
    has_header = not _looks_numeric(first_field)

    if not has_header:
        df = pd.read_csv(path, header=None)
        ncols = df.shape[1]
        if ncols >= 7:  # Binance raw kline
            df = df.iloc[:, : len(_BINANCE_COLS)] if ncols >= len(_BINANCE_COLS) else df
            df.columns = _BINANCE_COLS[: df.shape[1]]
            idx = _to_datetime(df["close_time"])
            out = pd.Series(df["close"].astype(float).values, index=idx, name="close")
        elif ncols == 2:  # time, price
            idx = _to_datetime(df.iloc[:, 0])
            out = pd.Series(df.iloc[:, 1].astype(float).values, index=idx, name="close")
        else:
            raise ValueError(f"{path}: headerless CSV with {ncols} columns is unsupported")
    else:
        df = pd.read_csv(path)
        cols_lower = {c.lower(): c for c in df.columns}
        pcol = cols_lower.get(price_col.lower())
        if pcol is None and df.shape[1] == 2:
            idx = _to_datetime(df.iloc[:, 0])
            out = pd.Series(df.iloc[:, 1].astype(float).values, index=idx, name="close")
        else:
            if pcol is None:
                raise ValueError(
                    f"{path}: no '{price_col}' column found (have {list(df.columns)})")
            tcol = next((cols_lower[c] for c in _TIME_CANDIDATES if c in cols_lower), None)
            if tcol is None:
                raise ValueError(
                    f"{path}: no time column found (looked for {_TIME_CANDIDATES})")
            idx = _to_datetime(df[tcol])
            out = pd.Series(df[pcol].astype(float).values, index=idx, name="close")

    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def _looks_numeric(field: str) -> bool:
    try:
        float(field)
        return True
    except ValueError:
        return False


def load_pair_csv(
    leader_path: str | Path,
    laggard_path: str | Path,
    price_col: str = "close",
) -> pd.DataFrame:
    """Load leader & laggard from two CSVs, time-aligned by inner join.

    Returns a DataFrame with ``leader`` and ``laggard`` price columns on the
    timestamps both symbols share.
    """
    leader = read_price_series(leader_path, price_col)
    laggard = read_price_series(laggard_path, price_col)
    out = pd.DataFrame({"leader": leader, "laggard": laggard}).dropna()
    if out.empty:
        raise ValueError(
            "No overlapping timestamps between leader and laggard — are they the "
            "same interval and time range?")
    return out


def load_combined_csv(path: str | Path) -> pd.DataFrame:
    """Load a single CSV that already has ``leader`` and ``laggard`` columns."""
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    if "leader" not in cols or "laggard" not in cols:
        raise ValueError(
            f"{path}: combined CSV needs 'leader' and 'laggard' columns "
            f"(have {list(df.columns)})")
    tcol = next((cols[c] for c in _TIME_CANDIDATES if c in cols), None)
    out = df[[cols["leader"], cols["laggard"]]].astype(float)
    out.columns = ["leader", "laggard"]
    if tcol is not None:
        out.index = _to_datetime(df[tcol])
    return out.dropna()


def export_csv(df: pd.DataFrame, path: str | Path) -> None:
    """Write a leader/laggard frame to a combined CSV (round-trips with
    :func:`load_combined_csv`). Handy for producing a sample/template file."""
    out = df.copy()
    out.index.name = out.index.name or "timestamp"
    out.to_csv(path)
