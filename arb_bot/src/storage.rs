use std::path::Path;

use anyhow::{Context, Result};
use rusqlite::{params, Connection};
use tokio::sync::broadcast::error::RecvError;
use tokio::sync::mpsc;
use tracing::{error, info, warn};

use crate::bus::EventReceiver;
use crate::events::PriceEvent;

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS price_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms     INTEGER NOT NULL,
    exchange  TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    bid       REAL NOT NULL,
    ask       REAL NOT NULL,
    mid       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_events_ts ON price_events(ts_ms);
CREATE INDEX IF NOT EXISTS idx_price_events_symbol ON price_events(symbol);
";

pub fn open_db(path: &Path) -> Result<Connection> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("creating sqlite parent dir {}", parent.display()))?;
        }
    }
    let conn = Connection::open(path)
        .with_context(|| format!("opening sqlite db at {}", path.display()))?;
    conn.execute_batch(SCHEMA).context("applying schema")?;
    Ok(conn)
}

pub fn spawn_logger(mut rx: EventReceiver, conn: Connection) {
    // rusqlite::Connection is !Sync, so hold it on a single blocking thread
    // and forward events from the broadcast bus via an mpsc channel.
    let (tx, mut crx) = mpsc::channel::<PriceEvent>(1024);

    tokio::spawn(async move {
        loop {
            match rx.recv().await {
                Ok(event) => {
                    if tx.send(event).await.is_err() {
                        warn!("sqlite writer dropped; storage forwarder stopping");
                        return;
                    }
                }
                Err(RecvError::Lagged(n)) => {
                    warn!(skipped = n, "storage logger lagged");
                }
                Err(RecvError::Closed) => {
                    info!("bus closed; storage forwarder stopping");
                    return;
                }
            }
        }
    });

    tokio::task::spawn_blocking(move || {
        info!("sqlite writer started");
        while let Some(event) = crx.blocking_recv() {
            let ts_ms = event.received_at.timestamp_millis();
            if let Err(e) = conn.execute(
                "INSERT INTO price_events (ts_ms, exchange, symbol, bid, ask, mid)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    ts_ms,
                    event.exchange.as_str(),
                    event.symbol,
                    event.bid,
                    event.ask,
                    event.mid()
                ],
            ) {
                error!(error = %e, "sqlite insert failed");
            }
        }
        info!("sqlite writer stopped");
    });
}
