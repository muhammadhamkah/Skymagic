use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Exchange {
    Binance,
    Coinbase,
    Okx,
    Bybit,
    Bitget,
    Mexc,
    Gate,
}

impl Exchange {
    pub fn as_str(self) -> &'static str {
        match self {
            Exchange::Binance => "binance",
            Exchange::Coinbase => "coinbase",
            Exchange::Okx => "okx",
            Exchange::Bybit => "bybit",
            Exchange::Bitget => "bitget",
            Exchange::Mexc => "mexc",
            Exchange::Gate => "gate",
        }
    }
}

#[derive(Debug, Clone)]
pub struct PriceEvent {
    pub received_at: DateTime<Utc>,
    pub exchange: Exchange,
    pub symbol: String,
    pub bid: f64,
    pub ask: f64,
}

impl PriceEvent {
    pub fn mid(&self) -> f64 {
        (self.bid + self.ask) / 2.0
    }
}
