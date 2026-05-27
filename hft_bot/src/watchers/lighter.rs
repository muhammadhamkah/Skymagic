//! Lighter (perp DEX) watcher.
//!
//! Endpoint: wss://mainnet.zklighter.elliot.ai/stream
//! Subscribe with slash: {"type":"subscribe","channel":"order_book/{id}"} and
//! "trade/{id}". The server echoes channels with a colon ("order_book:1").
//!
//! The order book is snapshot + deltas (bids/asks of {price,size} strings, with
//! size "0" meaning remove the level), so unlike Binance's bookTicker we keep a
//! local book per market and emit an L1 `BookTicker` whenever the touch changes.
//! Trades carry `is_maker_ask`; the taker is a buyer iff the maker was the ask,
//! so `is_buyer_maker = !is_maker_ask`.

use std::collections::{BTreeMap, HashMap};
use std::time::Duration;

use anyhow::{Context, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::Value;
use tokio::sync::mpsc::Sender;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;
use tracing::{info, warn};

use crate::events::{BookTicker, MarketEvent, Trade, Ts};

const WS: &str = "wss://mainnet.zklighter.elliot.ai/stream";
const PRICE_SCALE: f64 = 1e8;

fn px_key(p: f64) -> u64 {
    (p * PRICE_SCALE).round() as u64
}

fn now_millis() -> Ts {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as Ts)
        .unwrap_or(0)
}

fn parse_ts(v: &Value) -> Option<Ts> {
    v.as_i64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))
}

#[derive(Default)]
struct Book {
    bids: BTreeMap<u64, f64>,
    asks: BTreeMap<u64, f64>,
}

impl Book {
    fn apply(&mut self, ob: &Value, reset: bool) {
        if reset {
            self.bids.clear();
            self.asks.clear();
        }
        for (field, side) in [("asks", true), ("bids", false)] {
            let Some(levels) = ob.get(field).and_then(|v| v.as_array()) else { continue };
            for lvl in levels {
                let px = lvl.get("price").and_then(|v| v.as_str()).and_then(|s| s.parse::<f64>().ok());
                let sz = lvl.get("size").and_then(|v| v.as_str()).and_then(|s| s.parse::<f64>().ok());
                let (Some(px), Some(sz)) = (px, sz) else { continue };
                let map = if side { &mut self.asks } else { &mut self.bids };
                if sz <= 0.0 {
                    map.remove(&px_key(px));
                } else {
                    map.insert(px_key(px), sz);
                }
            }
        }
    }

    fn best_bid(&self) -> Option<(f64, f64)> {
        self.bids.iter().next_back().map(|(k, v)| (*k as f64 / PRICE_SCALE, *v))
    }
    fn best_ask(&self) -> Option<(f64, f64)> {
        self.asks.iter().next().map(|(k, v)| (*k as f64 / PRICE_SCALE, *v))
    }
}

/// Run forever: connect, subscribe to each market's book + trades, maintain the
/// books, and forward normalized events. Reconnects with backoff.
pub async fn run(markets: Vec<u32>, tx: Sender<MarketEvent>) -> Result<()> {
    let mut backoff = Duration::from_secs(1);
    loop {
        match stream_once(&markets, &tx).await {
            Ok(()) => return Ok(()),
            Err(e) => {
                warn!(error = %e, backoff_secs = backoff.as_secs(), "lighter disconnected, retrying");
                tokio::time::sleep(backoff).await;
                backoff = (backoff * 2).min(Duration::from_secs(30));
            }
        }
    }
}

async fn stream_once(markets: &[u32], tx: &Sender<MarketEvent>) -> Result<()> {
    let (mut ws, _resp) = connect_async(WS).await.context("lighter ws connect")?;
    info!(markets = markets.len(), "connected to lighter; subscribing");
    for m in markets {
        for ch in [format!("order_book/{m}"), format!("trade/{m}")] {
            let msg = serde_json::json!({"type": "subscribe", "channel": ch}).to_string();
            ws.send(Message::Text(msg)).await.context("subscribe")?;
        }
    }

    let mut books: HashMap<u32, Book> = HashMap::new();
    while let Some(msg) = ws.next().await {
        match msg.context("ws read")? {
            Message::Text(t) => {
                if !handle(&t, &mut books, tx).await {
                    return Ok(()); // consumer gone
                }
            }
            Message::Ping(p) => {
                ws.send(Message::Pong(p)).await.context("pong")?;
            }
            Message::Close(frame) => {
                anyhow::bail!("server close: {frame:?}");
            }
            _ => {}
        }
    }
    anyhow::bail!("stream ended");
}

/// Returns false if the downstream channel is closed.
async fn handle(txt: &str, books: &mut HashMap<u32, Book>, tx: &Sender<MarketEvent>) -> bool {
    let Ok(v) = serde_json::from_str::<Value>(txt) else { return true };
    let channel = v.get("channel").and_then(|c| c.as_str()).unwrap_or("");
    let recv = now_millis();

    if let Some(suffix) = channel.strip_prefix("order_book:") {
        let mid: u32 = match suffix.parse() {
            Ok(m) => m,
            Err(_) => return true,
        };
        let typ = v.get("type").and_then(|t| t.as_str()).unwrap_or("");
        let reset = typ.contains("subscribed"); // snapshot vs delta
        if let Some(ob) = v.get("order_book") {
            let book = books.entry(mid).or_default();
            book.apply(ob, reset);
            if let (Some((bp, bq)), Some((ap, aq))) = (book.best_bid(), book.best_ask()) {
                let ts = v.get("timestamp").and_then(parse_ts).unwrap_or(recv);
                let ev = MarketEvent::BookTicker(BookTicker {
                    ts,
                    recv_ts: recv,
                    symbol: mid.to_string(),
                    bid_px: bp,
                    bid_qty: bq,
                    ask_px: ap,
                    ask_qty: aq,
                });
                if tx.send(ev).await.is_err() {
                    return false;
                }
            }
        }
    } else if channel.starts_with("trade:") {
        if let Some(trades) = v.get("trades").and_then(|t| t.as_array()) {
            for tr in trades {
                let px = tr.get("price").and_then(|x| x.as_str()).and_then(|s| s.parse::<f64>().ok());
                let qty = tr.get("size").and_then(|x| x.as_str()).and_then(|s| s.parse::<f64>().ok());
                let (Some(px), Some(qty)) = (px, qty) else { continue };
                let mid = tr.get("market_id").and_then(|x| x.as_u64()).unwrap_or(0);
                let maker_ask = tr.get("is_maker_ask").and_then(|x| x.as_bool()).unwrap_or(false);
                let ts = tr.get("timestamp").and_then(parse_ts).unwrap_or(recv);
                let ev = MarketEvent::Trade(Trade {
                    ts,
                    recv_ts: recv,
                    symbol: mid.to_string(),
                    px,
                    qty,
                    is_buyer_maker: !maker_ask,
                });
                if tx.send(ev).await.is_err() {
                    return false;
                }
            }
        }
    }
    true
}

/// One-off: subscribe to one market and print raw frames, to learn the schema.
pub async fn probe(market: u32, secs: u64) -> Result<()> {
    info!(market, secs, "connecting to lighter: {WS}");
    let (mut ws, _resp) = connect_async(WS).await.context("lighter ws connect")?;
    println!("connected. subscribing to order_book/{market} and trade/{market}\n");
    for ch in [format!("order_book/{market}"), format!("trade/{market}")] {
        let msg = serde_json::json!({"type": "subscribe", "channel": ch}).to_string();
        ws.send(Message::Text(msg)).await.context("subscribe send")?;
    }

    let deadline = tokio::time::Instant::now() + Duration::from_secs(secs);
    let mut count = 0u32;
    loop {
        tokio::select! {
            _ = tokio::time::sleep_until(deadline) => break,
            frame = ws.next() => match frame {
                Some(Ok(Message::Text(t))) => {
                    count += 1;
                    let preview: String = t.chars().take(800).collect();
                    println!("[{count}] {preview}");
                    if count >= 40 {
                        break;
                    }
                }
                Some(Ok(Message::Ping(p))) => { ws.send(Message::Pong(p)).await.ok(); }
                Some(Ok(_)) => {}
                Some(Err(e)) => { println!("ws error: {e}"); break; }
                None => break,
            }
        }
    }
    println!("\nprobe done: {count} frames");
    Ok(())
}
