use std::time::Duration;

use anyhow::{Context, Result};
use chrono::Utc;
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use tokio::time::sleep;
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tracing::{debug, error, info, warn};

use crate::bus::EventSender;
use crate::events::{Exchange, PriceEvent};

#[derive(Debug, Deserialize)]
struct CombinedFrame {
    #[allow(dead_code)]
    stream: String,
    data: BookTicker,
}

#[derive(Debug, Deserialize)]
struct BookTicker {
    s: String,
    b: String,
    a: String,
}

fn to_binance_symbol(normalized: &str) -> String {
    normalized.replace('/', "").to_lowercase()
}

fn from_binance_symbol(raw: &str) -> String {
    let upper = raw.to_uppercase();
    for quote in ["USDT", "USDC", "BUSD", "FDUSD"] {
        if let Some(base) = upper.strip_suffix(quote) {
            if !base.is_empty() {
                return format!("{}/{}", base, quote);
            }
        }
    }
    upper
}

fn build_url(symbols: &[String]) -> String {
    let streams: Vec<String> = symbols
        .iter()
        .map(|s| format!("{}@bookTicker", to_binance_symbol(s)))
        .collect();
    format!(
        "wss://stream.binance.com:9443/stream?streams={}",
        streams.join("/")
    )
}

pub fn spawn(symbols: Vec<String>, bus: EventSender) {
    tokio::spawn(async move {
        if symbols.is_empty() {
            warn!(exchange = "binance", "no symbols configured; watcher idle");
            return;
        }
        let url = build_url(&symbols);
        info!(
            exchange = "binance",
            symbol_count = symbols.len(),
            "starting watcher"
        );

        let mut backoff = Duration::from_secs(1);
        let max_backoff = Duration::from_secs(30);

        loop {
            match run_once(&url, &bus).await {
                Ok(()) => warn!(exchange = "binance", "websocket closed cleanly; reconnecting"),
                Err(e) => error!(exchange = "binance", error = %e, "websocket error"),
            }
            info!(
                exchange = "binance",
                backoff_secs = backoff.as_secs(),
                "reconnecting after backoff"
            );
            sleep(backoff).await;
            backoff = (backoff * 2).min(max_backoff);
        }
    });
}

async fn run_once(url: &str, bus: &EventSender) -> Result<()> {
    let (ws, _resp) = connect_async(url)
        .await
        .context("connecting to Binance WebSocket")?;
    info!(exchange = "binance", "connected");

    let (mut sink, mut stream) = ws.split();

    while let Some(msg) = stream.next().await {
        let msg = msg.context("reading websocket message")?;
        match msg {
            Message::Text(text) => handle_text(&text, bus),
            Message::Ping(payload) => {
                if let Err(e) = sink.send(Message::Pong(payload)).await {
                    warn!(error = %e, "failed to send pong");
                }
            }
            Message::Close(frame) => {
                info!(?frame, "server closed connection");
                return Ok(());
            }
            _ => {}
        }
    }

    Ok(())
}

fn handle_text(text: &str, bus: &EventSender) {
    let frame: CombinedFrame = match serde_json::from_str(text) {
        Ok(f) => f,
        Err(e) => {
            debug!(error = %e, raw = text, "could not parse frame");
            return;
        }
    };

    let bid: f64 = match frame.data.b.parse() {
        Ok(v) => v,
        Err(_) => return,
    };
    let ask: f64 = match frame.data.a.parse() {
        Ok(v) => v,
        Err(_) => return,
    };

    let event = PriceEvent {
        received_at: Utc::now(),
        exchange: Exchange::Binance,
        symbol: from_binance_symbol(&frame.data.s),
        bid,
        ask,
    };

    let _ = bus.send(event);
}
