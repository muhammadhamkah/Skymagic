//! Normalized market events. Everything downstream (storage, replay, features)
//! speaks this vocabulary, not raw exchange JSON.

use serde::{Deserialize, Serialize};

/// Epoch milliseconds.
pub type Ts = i64;

/// A single normalized market event from one venue/symbol.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum MarketEvent {
    BookTicker(BookTicker),
    Trade(Trade),
}

/// Top-of-book update (L1). On Binance this is the `bookTicker` stream.
///
/// We start L1-only on purpose: it is free, tick-by-tick, and captures
/// top-of-book imbalance. The feature layer treats this as a depth-1 book,
/// so swapping in full L2 later is a data-source change, not a rewrite.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BookTicker {
    /// Exchange event time (millis).
    pub ts: Ts,
    /// Local receive time (millis). `recv_ts - ts` is a rough latency proxy.
    pub recv_ts: Ts,
    pub symbol: String,
    pub bid_px: f64,
    pub bid_qty: f64,
    pub ask_px: f64,
    pub ask_qty: f64,
}

/// A trade print. On Binance this is the `aggTrade` stream.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Trade {
    pub ts: Ts,
    pub recv_ts: Ts,
    pub symbol: String,
    pub px: f64,
    pub qty: f64,
    /// True when the buyer is the maker, i.e. the aggressor was a *seller*
    /// (a downtick-initiating trade). Mirrors Binance's `m` flag.
    pub is_buyer_maker: bool,
}

impl Trade {
    /// Signed size: positive for buyer-aggressor (uptick), negative for seller.
    pub fn signed_qty(&self) -> f64 {
        if self.is_buyer_maker {
            -self.qty
        } else {
            self.qty
        }
    }
}

impl MarketEvent {
    pub fn ts(&self) -> Ts {
        match self {
            MarketEvent::BookTicker(b) => b.ts,
            MarketEvent::Trade(t) => t.ts,
        }
    }

    pub fn symbol(&self) -> &str {
        match self {
            MarketEvent::BookTicker(b) => &b.symbol,
            MarketEvent::Trade(t) => &t.symbol,
        }
    }
}
