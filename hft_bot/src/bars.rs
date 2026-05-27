//! Higher-timeframe Donchian breakout backtest, long + short, multi-symbol.
//!
//! Rules (classic Donchian channel breakout):
//!   - Go LONG when the close breaks above the highest high of the prior N bars.
//!   - Exit the long when the close breaks below the lowest low of the prior M bars.
//!   - Mirror for shorts.
//! Signals are computed on bar `i`'s close and executed at bar `i+1`'s open, so
//! there is no look-ahead. Fills pay slippage + taker fees on both legs.
//!
//! Costs are no longer the binding constraint at this timeframe; the real risk
//! is overfitting across many symbols, so results are split into an in-sample
//! (train) and out-of-sample (test) period by time.

use crate::candles::Candle;

#[derive(Debug, Clone)]
pub struct BarConfig {
    pub entry_lookback: usize, // N
    pub exit_lookback: usize,  // M
    pub fee_bps: f64,          // per side
    pub slippage_bps: f64,     // per side
    pub notional: f64,
    pub allow_short: bool,
    /// Fraction of the overall time span used as in-sample (rest is held out).
    pub train_frac: f64,
    pub target_net_usdt: f64,
}

impl Default for BarConfig {
    fn default() -> Self {
        Self {
            entry_lookback: 20,
            exit_lookback: 10,
            fee_bps: 5.0,
            slippage_bps: 1.0,
            notional: 1000.0,
            allow_short: true,
            train_frac: 0.7,
            target_net_usdt: 0.02,
        }
    }
}

#[derive(Debug, Clone)]
pub struct Trade {
    pub symbol: String,
    pub entry_time: i64,
    pub net_pnl: f64,
}

/// Highest high / lowest low over `candles[from..to]` (half-open).
fn window_high(candles: &[Candle], from: usize, to: usize) -> f64 {
    candles[from..to].iter().fold(f64::MIN, |m, c| m.max(c.high))
}
fn window_low(candles: &[Candle], from: usize, to: usize) -> f64 {
    candles[from..to].iter().fold(f64::MAX, |m, c| m.min(c.low))
}

/// Run Donchian on one symbol's candles (ascending). Appends completed trades.
pub fn run_symbol(symbol: &str, candles: &[Candle], cfg: &BarConfig, out: &mut Vec<Trade>) {
    let n = cfg.entry_lookback;
    let m = cfg.exit_lookback;
    let lookback = n.max(m);
    if candles.len() < lookback + 2 {
        return;
    }
    let slip = cfg.slippage_bps / 10_000.0;
    let fee = cfg.fee_bps / 10_000.0;

    let mut side: i8 = 0; // 0 flat, +1 long, -1 short
    let mut entry_px = 0.0;
    let mut qty = 0.0;
    let mut entry_time = 0i64;

    // Decide on bar i, execute at bar i+1's open.
    for i in lookback..candles.len() - 1 {
        let prior_high = window_high(candles, i - n, i); // prior N bars, excludes i
        let prior_low = window_low(candles, i - m, i);
        let close = candles[i].close;
        let exec_open = candles[i + 1].open;

        if side == 0 {
            let go_long = close > prior_high;
            let go_short = cfg.allow_short && close < prior_low;
            if go_long {
                side = 1;
                entry_px = exec_open * (1.0 + slip); // buy pays up
            } else if go_short {
                side = -1;
                entry_px = exec_open * (1.0 - slip); // sell receives down
            } else {
                continue;
            }
            if entry_px <= 0.0 {
                side = 0;
                continue;
            }
            qty = cfg.notional / entry_px;
            entry_time = candles[i + 1].open_time;
        } else {
            // Exit on a channel break against the position.
            let exit_long = side == 1 && close < prior_low;
            let exit_short = side == -1 && close > prior_high;
            if exit_long || exit_short {
                let exit_px = if side == 1 {
                    exec_open * (1.0 - slip)
                } else {
                    exec_open * (1.0 + slip)
                };
                let gross = side as f64 * (exit_px - entry_px) * qty;
                let fees = fee * (entry_px * qty + exit_px * qty);
                out.push(Trade {
                    symbol: symbol.to_string(),
                    entry_time,
                    net_pnl: gross - fees,
                });
                side = 0;
            }
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct Stats {
    pub n: usize,
    pub wins: usize,
    pub total_net: f64,
    pub net_per_trade: f64,
    pub median_net: f64,
    pub win_rate: f64,
    pub hit_target_rate: f64,
}

pub fn summarize(trades: &[&Trade], target: f64) -> Stats {
    if trades.is_empty() {
        return Stats::default();
    }
    let n = trades.len();
    let wins = trades.iter().filter(|t| t.net_pnl > 0.0).count();
    let total: f64 = trades.iter().map(|t| t.net_pnl).sum();
    let hit = trades.iter().filter(|t| t.net_pnl >= target).count();
    let mut nets: Vec<f64> = trades.iter().map(|t| t.net_pnl).collect();
    nets.sort_by(|a, b| a.partial_cmp(b).unwrap());
    Stats {
        n,
        wins,
        total_net: total,
        net_per_trade: total / n as f64,
        median_net: nets[n / 2],
        win_rate: wins as f64 / n as f64,
        hit_target_rate: hit as f64 / n as f64,
    }
}

/// Time cutoff splitting the overall span into train (before) / test (after).
pub fn split_cutoff(trades: &[Trade], train_frac: f64) -> i64 {
    let min = trades.iter().map(|t| t.entry_time).min().unwrap_or(0);
    let max = trades.iter().map(|t| t.entry_time).max().unwrap_or(0);
    min + ((max - min) as f64 * train_frac) as i64
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candle(t: i64, o: f64, h: f64, l: f64, c: f64) -> Candle {
        Candle { open_time: t, open: o, high: h, low: l, close: c, volume: 1.0 }
    }

    #[test]
    fn donchian_catches_an_uptrend() {
        // Flat base, a clean breakout uptrend, then a pullback that triggers the
        // exit. Donchian should go long, ride the trend, and book a net gain.
        let mut candles = Vec::new();
        for i in 0..30 {
            candles.push(candle(i, 100.0, 100.5, 99.5, 100.0)); // flat base
        }
        for i in 30..60 {
            let p = 100.0 + (i - 29) as f64 * 2.0; // strong uptrend up to ~162
            candles.push(candle(i, p, p + 0.5, p - 0.5, p));
        }
        for i in 60..80 {
            let p = 162.0 - (i - 59) as f64 * 3.0; // pullback to trigger the exit
            candles.push(candle(i, p, p + 0.5, p - 0.5, p));
        }
        let cfg = BarConfig { entry_lookback: 20, exit_lookback: 10, ..Default::default() };
        let mut trades = Vec::new();
        run_symbol("TEST", &candles, &cfg, &mut trades);
        assert!(!trades.is_empty(), "should have entered the breakout");
        let total: f64 = trades.iter().map(|t| t.net_pnl).sum();
        assert!(total > 0.0, "uptrend should be net profitable, got {total}");
    }

    #[test]
    fn flat_market_only_pays_costs() {
        // Pure flat noise -> breakouts whipsaw, net should be <= 0 after fees.
        let mut candles = Vec::new();
        for i in 0..200 {
            let c = if i % 2 == 0 { 100.1 } else { 99.9 };
            candles.push(candle(i, 100.0, 100.2, 99.8, c));
        }
        let cfg = BarConfig::default();
        let mut trades = Vec::new();
        run_symbol("TEST", &candles, &cfg, &mut trades);
        let total: f64 = trades.iter().map(|t| t.net_pnl).sum();
        assert!(total <= 0.0, "flat chop should not be profitable, got {total}");
    }
}
