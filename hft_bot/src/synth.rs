//! Synthetic market-data generator.
//!
//! Binance is unreachable from some networks (e.g. cloud CI), so this lets us
//! exercise the full storage -> replay -> features -> analysis path end-to-end
//! without a live feed. It also injects a deliberate, tunable relationship
//! between book imbalance and the next price move, so the analysis step has a
//! known signal to recover (a sanity check that the diagnostics actually work).

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};

use crate::events::{BookTicker, MarketEvent, Trade, Ts};

pub struct SynthConfig {
    pub symbol: String,
    pub n_events: usize,
    pub start_ts: Ts,
    pub step_ms: i64,
    pub start_px: f64,
    pub seed: u64,
    /// How strongly book imbalance pushes the next mid move. 0.0 = pure noise.
    pub signal_strength: f64,
}

impl Default for SynthConfig {
    fn default() -> Self {
        Self {
            symbol: "BTCUSDT".into(),
            n_events: 50_000,
            start_ts: 1_700_000_000_000,
            step_ms: 5,
            start_px: 60_000.0,
            seed: 42,
            signal_strength: 0.4,
        }
    }
}

pub struct SynthGen {
    cfg: SynthConfig,
    rng: StdRng,
    i: usize,
    ts: Ts,
    mid: f64,
    last_imbalance: f64,
}

impl SynthGen {
    pub fn new(cfg: SynthConfig) -> Self {
        let rng = StdRng::seed_from_u64(cfg.seed);
        let ts = cfg.start_ts;
        let mid = cfg.start_px;
        Self {
            cfg,
            rng,
            i: 0,
            ts,
            mid,
            last_imbalance: 0.0,
        }
    }
}

impl Iterator for SynthGen {
    type Item = MarketEvent;

    fn next(&mut self) -> Option<MarketEvent> {
        if self.i >= self.cfg.n_events {
            return None;
        }
        self.i += 1;
        self.ts += self.cfg.step_ms;

        // Mid drifts by noise plus a push from the *previous* imbalance, so that
        // imbalance(t) predicts return(t->t+1) with strength `signal_strength`.
        let noise: f64 = self.rng.gen_range(-1.0..1.0);
        let drift = self.cfg.signal_strength * self.last_imbalance;
        let tick = 0.5;
        self.mid += (noise + drift) * tick;

        // Build a fresh imbalance for this step that will drive the *next* move.
        let bid_qty: f64 = self.rng.gen_range(0.1..10.0);
        let ask_qty: f64 = self.rng.gen_range(0.1..10.0);
        self.last_imbalance = (bid_qty - ask_qty) / (bid_qty + ask_qty);

        let spread = tick;
        let bid_px = (self.mid - spread / 2.0).max(1.0);
        let ask_px = self.mid + spread / 2.0;

        // Every few events, emit a trade biased by current imbalance instead of
        // a quote update.
        if self.i % 3 == 0 {
            let buy = self.rng.gen::<f64>() < 0.5 + 0.4 * self.last_imbalance;
            return Some(MarketEvent::Trade(Trade {
                ts: self.ts,
                recv_ts: self.ts,
                symbol: self.cfg.symbol.clone(),
                px: if buy { ask_px } else { bid_px },
                qty: self.rng.gen_range(0.01..1.0),
                is_buyer_maker: !buy,
            }));
        }

        Some(MarketEvent::BookTicker(BookTicker {
            ts: self.ts,
            recv_ts: self.ts,
            symbol: self.cfg.symbol.clone(),
            bid_px,
            bid_qty,
            ask_px,
            ask_qty,
        }))
    }
}
