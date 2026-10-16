//! Depth-agnostic order book.
//!
//! Today we only feed it L1 (top-of-book) updates, so each side holds a single
//! level. The price-keyed maps and `levels`-parameterized accessors exist so
//! that when we add an L2 diff stream later, the feature layer keeps working
//! unchanged — it already asks the book for "imbalance over N levels".

use std::collections::BTreeMap;

use crate::events::BookTicker;

/// Convert a float price to an integer key so it can live in a `BTreeMap`.
/// 1e8 ("satoshi") resolution is plenty for crypto and keeps BTC prices well
/// inside u64 range.
const PRICE_SCALE: f64 = 1e8;

fn px_key(px: f64) -> u64 {
    (px * PRICE_SCALE).round() as u64
}

#[derive(Debug, Default, Clone)]
pub struct OrderBook {
    /// price_key -> quantity
    bids: BTreeMap<u64, f64>,
    asks: BTreeMap<u64, f64>,
}

impl OrderBook {
    pub fn new() -> Self {
        Self::default()
    }

    /// Apply an L1 update. In L1 mode the book holds exactly one level per side,
    /// so we replace rather than merge. (An L2 path would apply diffs instead.)
    pub fn apply_l1(&mut self, bt: &BookTicker) {
        self.bids.clear();
        self.asks.clear();
        if bt.bid_qty > 0.0 {
            self.bids.insert(px_key(bt.bid_px), bt.bid_qty);
        }
        if bt.ask_qty > 0.0 {
            self.asks.insert(px_key(bt.ask_px), bt.ask_qty);
        }
    }

    /// Best bid as (price, qty), highest bid price.
    pub fn best_bid(&self) -> Option<(f64, f64)> {
        self.bids
            .iter()
            .next_back()
            .map(|(k, q)| (*k as f64 / PRICE_SCALE, *q))
    }

    /// Best ask as (price, qty), lowest ask price.
    pub fn best_ask(&self) -> Option<(f64, f64)> {
        self.asks
            .iter()
            .next()
            .map(|(k, q)| (*k as f64 / PRICE_SCALE, *q))
    }

    pub fn mid(&self) -> Option<f64> {
        match (self.best_bid(), self.best_ask()) {
            (Some((bp, _)), Some((ap, _))) => Some((bp + ap) / 2.0),
            _ => None,
        }
    }

    /// Microprice: mid weighted toward the side with *less* size, which is the
    /// classic short-horizon fair-value estimate.
    pub fn microprice(&self) -> Option<f64> {
        match (self.best_bid(), self.best_ask()) {
            (Some((bp, bq)), Some((ap, aq))) if bq + aq > 0.0 => {
                Some((bp * aq + ap * bq) / (bq + aq))
            }
            _ => None,
        }
    }

    pub fn spread_bps(&self) -> Option<f64> {
        match (self.best_bid(), self.best_ask()) {
            (Some((bp, _)), Some((ap, _))) if bp > 0.0 => {
                let mid = (bp + ap) / 2.0;
                Some((ap - bp) / mid * 10_000.0)
            }
            _ => None,
        }
    }

    /// Order-book imbalance over the top `levels` per side, in [-1, 1].
    /// Positive => more bid size (buy pressure). With L1 data, `levels` is
    /// effectively clamped to 1.
    pub fn imbalance(&self, levels: usize) -> Option<f64> {
        let bid: f64 = self.bids.values().rev().take(levels).sum();
        let ask: f64 = self.asks.values().take(levels).sum();
        let total = bid + ask;
        if total > 0.0 {
            Some((bid - ask) / total)
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bt(bid_px: f64, bid_qty: f64, ask_px: f64, ask_qty: f64) -> BookTicker {
        BookTicker {
            ts: 0,
            recv_ts: 0,
            symbol: "BTCUSDT".into(),
            bid_px,
            bid_qty,
            ask_px,
            ask_qty,
        }
    }

    #[test]
    fn mid_and_spread() {
        let mut b = OrderBook::new();
        b.apply_l1(&bt(100.0, 5.0, 100.2, 5.0));
        assert!((b.mid().unwrap() - 100.1).abs() < 1e-9);
        // spread = 0.2 over mid 100.1 ~ 19.98 bps
        assert!((b.spread_bps().unwrap() - 19.98).abs() < 0.01);
    }

    #[test]
    fn imbalance_sign() {
        let mut b = OrderBook::new();
        b.apply_l1(&bt(100.0, 9.0, 100.2, 1.0));
        // heavy bid -> positive, (9-1)/10 = 0.8
        assert!((b.imbalance(1).unwrap() - 0.8).abs() < 1e-9);
    }

    #[test]
    fn microprice_leans_to_thin_side() {
        let mut b = OrderBook::new();
        // small ask size -> fair value pulled toward the ask
        b.apply_l1(&bt(100.0, 9.0, 100.2, 1.0));
        let mp = b.microprice().unwrap();
        assert!(mp > b.mid().unwrap());
    }
}
