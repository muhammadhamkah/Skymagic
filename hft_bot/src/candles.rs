//! Candlestick (OHLCV) data for higher-timeframe strategies.
//!
//! Stored one symbol per file under a directory (e.g. `data/klines/BTCUSDT.ndjson`),
//! one candle per line, ascending by open time. The symbol lives in the filename,
//! not each row, to keep files compact.

use std::fs::{self, File};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Candle {
    /// Bar open time, epoch millis.
    pub open_time: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

pub fn write_candles(path: impl AsRef<Path>, candles: &[Candle]) -> Result<()> {
    if let Some(parent) = path.as_ref().parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)?;
        }
    }
    let mut w = BufWriter::new(File::create(path.as_ref())?);
    for c in candles {
        serde_json::to_writer(&mut w, c)?;
        w.write_all(b"\n")?;
    }
    w.flush()?;
    Ok(())
}

pub fn read_candles(path: impl AsRef<Path>) -> Result<Vec<Candle>> {
    let f = File::open(path.as_ref())
        .with_context(|| format!("opening {}", path.as_ref().display()))?;
    let mut out = Vec::new();
    for line in BufReader::new(f).lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        out.push(serde_json::from_str(&line).context("parsing candle line")?);
    }
    out.sort_by_key(|c: &Candle| c.open_time);
    Ok(out)
}

/// (symbol, path) for every `*.ndjson` in a klines directory.
pub fn list_symbol_files(dir: impl AsRef<Path>) -> Result<Vec<(String, PathBuf)>> {
    let mut out = Vec::new();
    for entry in fs::read_dir(dir.as_ref())
        .with_context(|| format!("reading dir {}", dir.as_ref().display()))?
    {
        let path = entry?.path();
        if path.extension().and_then(|e| e.to_str()) == Some("ndjson") {
            if let Some(stem) = path.file_stem().and_then(|s| s.to_str()) {
                out.push((stem.to_string(), path));
            }
        }
    }
    out.sort();
    Ok(out)
}
