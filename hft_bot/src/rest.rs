//! Binance USDⓈ-M futures REST helpers: discover the most-liquid symbols and
//! download historical klines. Blocking (ureq) — these are one-off batch jobs,
//! not the hot path. Run locally; Binance REST is geo-blocked from some clouds.

use std::thread::sleep;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use serde::Deserialize;

use crate::candles::Candle;

const FAPI: &str = "https://fapi.binance.com";
const LIGHTER: &str = "https://mainnet.zklighter.elliot.ai";

/// Dump Lighter's market-list REST responses raw, so we can learn the schema
/// (market id, symbol, volume) to build the universe picker.
pub fn lighter_markets_dump() -> Result<()> {
    for path in ["/api/v1/orderBookDetails", "/api/v1/orderBooks"] {
        let url = format!("{LIGHTER}{path}");
        match reqwest::blocking::get(&url) {
            Ok(r) => {
                let status = r.status();
                let body = r.text().unwrap_or_default();
                let preview: String = body.chars().take(4000).collect();
                println!("== {url}  ({status}) ==\n{preview}\n");
            }
            Err(e) => println!("== {url}  ERROR: {e}\n"),
        }
    }
    Ok(())
}

#[derive(Deserialize)]
struct Ticker24h {
    symbol: String,
    #[serde(rename = "quoteVolume")]
    quote_volume: String,
}

/// Top `n` USDT-margined perpetuals by 24h quote volume.
pub fn top_symbols_by_volume(n: usize) -> Result<Vec<String>> {
    let url = format!("{FAPI}/fapi/v1/ticker/24hr");
    let tickers: Vec<Ticker24h> = reqwest::blocking::get(&url)
        .context("fetching 24h tickers")?
        .error_for_status()
        .context("24h tickers http status")?
        .json()
        .context("parsing 24h tickers")?;

    let mut rows: Vec<(String, f64)> = tickers
        .into_iter()
        .filter(|t| t.symbol.ends_with("USDT"))
        .map(|t| (t.symbol, t.quote_volume.parse::<f64>().unwrap_or(0.0)))
        .collect();
    rows.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    rows.truncate(n);
    Ok(rows.into_iter().map(|(s, _)| s).collect())
}

pub fn interval_ms(interval: &str) -> Result<i64> {
    let (num, unit) = interval.split_at(interval.len() - 1);
    let num: i64 = num.parse().context("interval number")?;
    let unit_ms = match unit {
        "m" => 60_000,
        "h" => 3_600_000,
        "d" => 86_400_000,
        _ => bail!("unsupported interval {interval}"),
    };
    Ok(num * unit_ms)
}

/// Download klines for one symbol from `start_ms` to now, paging forward.
pub fn fetch_klines(symbol: &str, interval: &str, start_ms: i64) -> Result<Vec<Candle>> {
    let step = interval_ms(interval)?;
    let mut cursor = start_ms;
    let now = chrono::Utc::now().timestamp_millis();
    let mut out: Vec<Candle> = Vec::new();

    loop {
        let url = format!(
            "{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&startTime={cursor}&limit=1500"
        );
        let rows: Vec<Vec<serde_json::Value>> = reqwest::blocking::get(&url)
            .with_context(|| format!("fetching klines for {symbol}"))?
            .error_for_status()
            .with_context(|| format!("klines http status for {symbol}"))?
            .json()
            .with_context(|| format!("parsing klines for {symbol}"))?;

        if rows.is_empty() {
            break;
        }

        let mut last_open = cursor;
        for k in &rows {
            // [openTime, open, high, low, close, volume, closeTime, ...]
            let open_time = k[0].as_i64().unwrap_or(0);
            let parse = |i: usize| k.get(i).and_then(|v| v.as_str()).and_then(|s| s.parse().ok()).unwrap_or(0.0);
            out.push(Candle {
                open_time,
                open: parse(1),
                high: parse(2),
                low: parse(3),
                close: parse(4),
                volume: parse(5),
            });
            last_open = open_time;
        }

        // Advance past the last bar we got. Stop when we reach ~now or stall.
        let next = last_open + step;
        if next <= cursor || next >= now {
            break;
        }
        cursor = next;
        sleep(Duration::from_millis(300)); // be gentle on rate limits
    }

    out.sort_by_key(|c| c.open_time);
    out.dedup_by_key(|c| c.open_time);
    Ok(out)
}
