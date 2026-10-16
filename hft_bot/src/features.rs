//! Feature engine: turns a stream of `MarketEvent`s into per-symbol feature
//! snapshots. This is the surface we use to answer "is there any short-horizon
//! edge after fees?" before committing to a strategy.

use std::collections::{HashMap, VecDeque};

use serde::Serialize;

use crate::book::OrderBook;
use crate::events::{MarketEvent, Ts};

/// A point-in-time view of the tradeable features for one symbol.
#[derive(Debug, Clone, Serialize)]
pub struct FeatureSnapshot {
    pub ts: Ts,
    pub symbol: String,
    pub mid: f64,
    pub microprice: f64,
    /// Best bid / ask at this instant. Needed to simulate taker fills (a buy
    /// crosses to the ask, a sell crosses to the bid).
    pub bid_px: f64,
    pub ask_px: f64,
    pub spread_bps: f64,
    /// Top-of-book size imbalance in [-1, 1].
    pub book_imbalance: f64,
    /// Net signed traded volume over the rolling window (buys positive).
    pub trade_flow: f64,
    /// Trade-flow normalized to [-1, 1] by gross volume in the window.
    pub trade_flow_imbalance: f64,
}

/// Per-symbol rolling state.
struct SymbolState {
    book: OrderBook,
    /// (ts, signed_qty) within the rolling window.
    trades: VecDeque<(Ts, f64)>,
    signed_sum: f64,
    gross_sum: f64,
}

impl SymbolState {
    fn new() -> Self {
        Self {
            book: OrderBook::new(),
            trades: VecDeque::new(),
            signed_sum: 0.0,
            gross_sum: 0.0,
        }
    }

    fn evict_before(&mut self, cutoff: Ts) {
        while let Some(&(ts, q)) = self.trades.front() {
            if ts < cutoff {
                self.trades.pop_front();
                self.signed_sum -= q;
                self.gross_sum -= q.abs();
            } else {
                break;
            }
        }
    }
}

pub struct FeatureEngine {
    window_ms: i64,
    imbalance_levels: usize,
    symbols: HashMap<String, SymbolState>,
}

impl FeatureEngine {
    pub fn new(window_ms: i64, imbalance_levels: usize) -> Self {
        Self {
            window_ms,
            imbalance_levels,
            symbols: HashMap::new(),
        }
    }

    /// Feed one event. Returns a snapshot when the book has a valid two-sided
    /// quote for the event's symbol (so callers can record/inspect it).
    pub fn on_event(&mut self, ev: &MarketEvent) -> Option<FeatureSnapshot> {
        let now = ev.ts();
        let state = self
            .symbols
            .entry(ev.symbol().to_string())
            .or_insert_with(SymbolState::new);

        match ev {
            MarketEvent::BookTicker(bt) => state.book.apply_l1(bt),
            MarketEvent::Trade(t) => {
                let q = t.signed_qty();
                state.trades.push_back((t.ts, q));
                state.signed_sum += q;
                state.gross_sum += q.abs();
            }
        }

        state.evict_before(now - self.window_ms);

        let mid = state.book.mid()?;
        let microprice = state.book.microprice()?;
        let spread_bps = state.book.spread_bps()?;
        let book_imbalance = state.book.imbalance(self.imbalance_levels)?;
        let (bid_px, _) = state.book.best_bid()?;
        let (ask_px, _) = state.book.best_ask()?;
        let trade_flow_imbalance = if state.gross_sum > 0.0 {
            state.signed_sum / state.gross_sum
        } else {
            0.0
        };

        Some(FeatureSnapshot {
            ts: now,
            symbol: ev.symbol().to_string(),
            mid,
            microprice,
            bid_px,
            ask_px,
            spread_bps,
            book_imbalance,
            trade_flow: state.signed_sum,
            trade_flow_imbalance,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::{BookTicker, Trade};

    fn book_ev(ts: Ts, bq: f64, aq: f64) -> MarketEvent {
        MarketEvent::BookTicker(BookTicker {
            ts,
            recv_ts: ts,
            symbol: "BTCUSDT".into(),
            bid_px: 100.0,
            bid_qty: bq,
            ask_px: 100.2,
            ask_qty: aq,
        })
    }

    fn trade_ev(ts: Ts, qty: f64, buyer_maker: bool) -> MarketEvent {
        MarketEvent::Trade(Trade {
            ts,
            recv_ts: ts,
            symbol: "BTCUSDT".into(),
            px: 100.1,
            qty,
            is_buyer_maker: buyer_maker,
        })
    }

    #[test]
    fn no_snapshot_until_two_sided() {
        let mut e = FeatureEngine::new(1000, 1);
        // a lone trade with no book yet -> no snapshot
        assert!(e.on_event(&trade_ev(0, 1.0, false)).is_none());
        // once we have a book, we get one
        assert!(e.on_event(&book_ev(1, 5.0, 5.0)).is_some());
    }

    #[test]
    fn trade_flow_window_evicts() {
        let mut e = FeatureEngine::new(1000, 1);
        e.on_event(&book_ev(0, 5.0, 5.0));
        e.on_event(&trade_ev(0, 3.0, false)); // +3 buy
        let s = e.on_event(&book_ev(10, 5.0, 5.0)).unwrap();
        assert!((s.trade_flow - 3.0).abs() < 1e-9);
        // advance past the window: the old trade should be evicted
        let s2 = e.on_event(&book_ev(2000, 5.0, 5.0)).unwrap();
        assert!(s2.trade_flow.abs() < 1e-9);
    }
}
