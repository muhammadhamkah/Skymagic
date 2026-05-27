//! Lighter (perp DEX) connectivity.
//!
//! Endpoint: wss://mainnet.zklighter.elliot.ai/stream
//! Subscribe: {"type":"subscribe","channel":"order_book/{market_id}"} (and trade/{id}).
//!
//! The order book arrives as a snapshot then deltas (bids/asks of {price,size}),
//! so unlike Binance's bookTicker we must maintain the book to extract L1. The
//! `probe` fn below just dumps raw frames so we can confirm the exact message
//! shapes before building the full collector.

use std::time::Duration;

use anyhow::{Context, Result};
use futures_util::{SinkExt, StreamExt};
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;
use tracing::info;

const WS: &str = "wss://mainnet.zklighter.elliot.ai/stream";

/// Connect, subscribe to one market's order book + trades, and print the raw
/// frames for `secs` seconds (or `max_frames`). Used to learn the real schema.
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
                Some(Ok(Message::Ping(p))) => {
                    ws.send(Message::Pong(p)).await.ok();
                }
                Some(Ok(_)) => {}
                Some(Err(e)) => {
                    println!("ws error: {e}");
                    break;
                }
                None => break,
            }
        }
    }
    println!("\nprobe done: {count} frames");
    Ok(())
}
