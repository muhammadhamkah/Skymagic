use std::path::{Path, PathBuf};

use anyhow::Result;
use tokio::sync::broadcast::error::RecvError;
use tracing::{info, warn};
use tracing_subscriber::EnvFilter;

mod bus;
mod config;
mod events;
mod storage;
mod watchers;

#[tokio::main]
async fn main() -> Result<()> {
    init_tracing();

    let config_path = PathBuf::from(
        std::env::var("ARB_BOT_CONFIG").unwrap_or_else(|_| "config/config.toml".to_string()),
    );
    let cfg = config::load(&config_path)?;
    info!(config = %config_path.display(), "config loaded");

    let bus_tx = bus::new_bus(cfg.general.event_bus_capacity);

    let conn = storage::open_db(Path::new(&cfg.storage.sqlite_path))?;
    storage::spawn_logger(bus_tx.subscribe(), conn);

    spawn_console_subscriber(bus_tx.subscribe());

    if cfg.binance.enabled {
        watchers::binance::spawn(cfg.binance.symbols.clone(), bus_tx.clone());
    } else {
        warn!("binance watcher disabled in config");
    }

    info!("running; press Ctrl-C to stop");
    tokio::signal::ctrl_c().await?;
    info!("shutdown requested");
    Ok(())
}

fn init_tracing() {
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,arb_bot=debug"));
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_target(false)
        .init();
}

fn spawn_console_subscriber(mut rx: bus::EventReceiver) {
    tokio::spawn(async move {
        loop {
            match rx.recv().await {
                Ok(event) => {
                    info!(
                        exchange = event.exchange.as_str(),
                        symbol = %event.symbol,
                        bid = event.bid,
                        ask = event.ask,
                        mid = event.mid(),
                        "price"
                    );
                }
                Err(RecvError::Lagged(n)) => {
                    warn!(skipped = n, "console subscriber lagged");
                }
                Err(RecvError::Closed) => return,
            }
        }
    });
}
