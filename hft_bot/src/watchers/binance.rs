//! Binance USDT-M futures watcher.
//!
//! Subscribes to the combined `bookTicker` (L1) and `aggTrade` streams for the
//! configured symbols, normalizes frames into `MarketEvent`s, and forwards them
//! on an mpsc channel. Reconnects with exponential backoff and answers pings.
//!
//! Note: futures (`fstream.binance.com`) is chosen over spot because its free
//! historical dumps and `bookTicker` give us tick-by-tick L1 to backtest on.

use std::time::Duration;

use anyhow::{Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use tokio::sync::mpsc::Sender;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;
use tracing::{debug, info, warn};

use crate::events::{BookTicker, MarketEvent, Trade, Ts};

const WS_BASE: &str = "wss://fstream.binance.com/stream?streams=";
const BACKOFF_START: Duration = Duration::from_secs(1);
const BACKOFF_MAX: Duration = Duration::from_secs(30);

fn now_millis() -> Ts {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as Ts)
        .unwrap_or(0)
}

fn build_url(symbols: &[String]) -> String {
    let streams: Vec<String> = symbols
        .iter()
        .flat_map(|s| {
            let s = s.to_lowercase();
            [format!("{s}@bookTicker"), format!("{s}@aggTrade")]
        })
        .collect();
    format!("{WS_BASE}{}", streams.join("/"))
}

/// Run forever: connect, stream, reconnect on failure. Only returns `Err` if
/// the downstream channel is closed (i.e. nobody is listening anymore).
pub async fn run(symbols: Vec<String>, tx: Sender<MarketEvent>) -> Result<()> {
    let url = build_url(&symbols);
    info!(%url, "binance futures watcher starting");
    let mut backoff = BACKOFF_START;

    loop {
        match stream_once(&url, &tx).await {
            Ok(()) => {
                // Channel closed; nothing left to do.
                return Ok(());
            }
            Err(e) => {
                warn!(error = %e, backoff_secs = backoff.as_secs(), "watcher disconnected, retrying");
                tokio::time::sleep(backoff).await;
                backoff = (backoff * 2).min(BACKOFF_MAX);
            }
        }
    }
}

/// One connection's lifetime. Returns `Ok(())` only when the consumer channel
/// is closed; any network/parse trouble surfaces as `Err` to trigger backoff.
async fn stream_once(url: &str, tx: &Sender<MarketEvent>) -> Result<()> {
    let (mut ws, _resp) = connect_async(url).await.context("ws connect")?;
    info!("connected to binance");
    let mut backoff_reset_done = false;

    while let Some(msg) = ws.next().await {
        let msg = msg.context("ws read")?;
        match msg {
            Message::Text(txt) => {
                if !backoff_reset_done {
                    debug!("first frame received");
                    backoff_reset_done = true;
                }
                if let Some(ev) = parse_frame(&txt) {
                    if tx.send(ev).await.is_err() {
                        return Ok(()); // consumer gone
                    }
                }
            }
            Message::Ping(payload) => {
                ws.send(Message::Pong(payload)).await.context("pong")?;
            }
            Message::Close(frame) => {
                warn!(?frame, "server closed connection");
                anyhow::bail!("server close");
            }
            _ => {}
        }
    }
    anyhow::bail!("stream ended");
}

/// Parse one combined-stream frame into a `MarketEvent`, or `None` if it's a
/// control/unknown frame.
fn parse_frame(txt: &str) -> Option<MarketEvent> {
    let combined: Combined = serde_json::from_str(txt).ok()?;
    let recv_ts = now_millis();

    if combined.stream.ends_with("@bookTicker") {
        let r: RawBookTicker = serde_json::from_value(combined.data).ok()?;
        let ts = if r.event_time > 0 { r.event_time } else { r.txn_time };
        Some(MarketEvent::BookTicker(BookTicker {
            ts: if ts > 0 { ts } else { recv_ts },
            recv_ts,
            symbol: r.symbol,
            bid_px: r.bid_px.parse().ok()?,
            bid_qty: r.bid_qty.parse().ok()?,
            ask_px: r.ask_px.parse().ok()?,
            ask_qty: r.ask_qty.parse().ok()?,
        }))
    } else if combined.stream.ends_with("@aggTrade") {
        let r: RawAggTrade = serde_json::from_value(combined.data).ok()?;
        Some(MarketEvent::Trade(Trade {
            ts: if r.trade_time > 0 { r.trade_time } else { recv_ts },
            recv_ts,
            symbol: r.symbol,
            px: r.px.parse().ok()?,
            qty: r.qty.parse().ok()?,
            is_buyer_maker: r.is_buyer_maker,
        }))
    } else {
        None
    }
}

#[derive(Deserialize)]
struct Combined {
    stream: String,
    data: serde_json::Value,
}

#[derive(Deserialize)]
struct RawBookTicker {
    #[serde(rename = "s")]
    symbol: String,
    #[serde(rename = "b")]
    bid_px: String,
    #[serde(rename = "B")]
    bid_qty: String,
    #[serde(rename = "a")]
    ask_px: String,
    #[serde(rename = "A")]
    ask_qty: String,
    #[serde(rename = "E", default)]
    event_time: Ts,
    #[serde(rename = "T", default)]
    txn_time: Ts,
}

#[derive(Deserialize)]
struct RawAggTrade {
    #[serde(rename = "s")]
    symbol: String,
    #[serde(rename = "p")]
    px: String,
    #[serde(rename = "q")]
    qty: String,
    #[serde(rename = "T", default)]
    trade_time: Ts,
    #[serde(rename = "m")]
    is_buyer_maker: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_book_ticker_frame() {
        let frame = r#"{"stream":"btcusdt@bookTicker","data":{"e":"bookTicker","u":1,"E":1700000000000,"T":1700000000000,"s":"BTCUSDT","b":"60000.0","B":"1.5","a":"60001.0","A":"2.0"}}"#;
        let ev = parse_frame(frame).expect("should parse");
        match ev {
            MarketEvent::BookTicker(b) => {
                assert_eq!(b.symbol, "BTCUSDT");
                assert_eq!(b.bid_px, 60000.0);
                assert_eq!(b.ask_qty, 2.0);
                assert_eq!(b.ts, 1700000000000);
            }
            _ => panic!("wrong variant"),
        }
    }

    #[test]
    fn parses_agg_trade_frame() {
        let frame = r#"{"stream":"btcusdt@aggTrade","data":{"e":"aggTrade","E":1700000000001,"a":1,"s":"BTCUSDT","p":"60000.5","q":"0.25","f":1,"l":2,"T":1700000000000,"m":true}}"#;
        let ev = parse_frame(frame).expect("should parse");
        match ev {
            MarketEvent::Trade(t) => {
                assert_eq!(t.px, 60000.5);
                assert_eq!(t.qty, 0.25);
                assert!(t.is_buyer_maker);
                assert_eq!(t.signed_qty(), -0.25);
            }
            _ => panic!("wrong variant"),
        }
    }

    #[test]
    fn url_has_both_streams() {
        let url = build_url(&["BTCUSDT".into()]);
        assert!(url.contains("btcusdt@bookTicker"));
        assert!(url.contains("btcusdt@aggTrade"));
    }
}
