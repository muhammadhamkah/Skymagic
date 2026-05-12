use std::path::Path;

use anyhow::{Context, Result};
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub general: General,
    pub storage: Storage,
    pub binance: ExchangeConfig,
}

#[derive(Debug, Clone, Deserialize)]
pub struct General {
    pub event_bus_capacity: usize,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Storage {
    pub sqlite_path: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ExchangeConfig {
    pub enabled: bool,
    pub symbols: Vec<String>,
}

pub fn load(path: &Path) -> Result<Config> {
    let text = std::fs::read_to_string(path)
        .with_context(|| format!("reading config file {}", path.display()))?;
    let cfg: Config = toml::from_str(&text)
        .with_context(|| format!("parsing config file {}", path.display()))?;
    Ok(cfg)
}
