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
    /// Hard stop-loss as a percent move against entry (0 = disabled).
    pub stop_loss_pct: f64,
    /// Fixed take-profit as a percent move in favor of entry (0 = disabled).
    pub take_profit_pct: f64,
    /// Trailing stop: exit if price retraces this percent from the best level
    /// reached since entry (0 = disabled). Locks in profit while letting the
    /// trade run — the right tool for trend-following.
    pub trail_pct: f64,
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
            stop_loss_pct: 0.0,
            take_profit_pct: 0.0,
            trail_pct: 0.0,
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
    let sl = cfg.stop_loss_pct / 100.0; // 0 = off
    let tp = cfg.take_profit_pct / 100.0;
    let trail = cfg.trail_pct / 100.0;

    let mut side: i8 = 0; // 0 flat, +1 long, -1 short
    let mut entry_px = 0.0;
    let mut qty = 0.0;
    let mut entry_time = 0i64;
    let mut peak = 0.0; // best favorable price since entry (high for long, low for short)

    // Channel entries/exits decide on bar i's close and fill at bar i+1's open.
    // Stops (hard, trailing) and TP are resting orders, filling intrabar at the
    // level. The trailing level uses the peak established through the *prior*
    // bar, so we never assume a favorable intrabar peak-then-retrace sequence.
    for i in lookback..candles.len() - 1 {
        let prior_high = window_high(candles, i - n, i); // prior N bars, excludes i
        let prior_low = window_low(candles, i - m, i);
        let bar = &candles[i];
        let exec_open = candles[i + 1].open;

        if side == 0 {
            let go_long = bar.close > prior_high;
            let go_short = cfg.allow_short && bar.close < prior_low;
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
            peak = entry_px;
            continue;
        }

        let mut exit_px: Option<f64> = None;
        if side == 1 {
            // Combine hard stop and trailing stop: as price falls you hit the
            // *higher* level first, so the effective stop is their max.
            let mut stop: Option<f64> = (sl > 0.0).then(|| entry_px * (1.0 - sl));
            if trail > 0.0 {
                let t = peak * (1.0 - trail);
                stop = Some(stop.map_or(t, |s| s.max(t)));
            }
            if let Some(l) = stop {
                if bar.low <= l {
                    exit_px = Some(l * (1.0 - slip));
                }
            }
            if exit_px.is_none() && tp > 0.0 && bar.high >= entry_px * (1.0 + tp) {
                exit_px = Some(entry_px * (1.0 + tp) * (1.0 - slip));
            }
            if exit_px.is_none() && bar.close < prior_low {
                exit_px = Some(exec_open * (1.0 - slip)); // channel exit at next open
            }
            if exit_px.is_none() {
                peak = peak.max(bar.high); // ratchet the trailing reference
            }
        } else {
            let mut stop: Option<f64> = (sl > 0.0).then(|| entry_px * (1.0 + sl));
            if trail > 0.0 {
                let t = peak * (1.0 + trail);
                stop = Some(stop.map_or(t, |s| s.min(t)));
            }
            if let Some(l) = stop {
                if bar.high >= l {
                    exit_px = Some(l * (1.0 + slip));
                }
            }
            if exit_px.is_none() && tp > 0.0 && bar.low <= entry_px * (1.0 - tp) {
                exit_px = Some(entry_px * (1.0 - tp) * (1.0 + slip));
            }
            if exit_px.is_none() && bar.close > prior_high {
                exit_px = Some(exec_open * (1.0 + slip));
            }
            if exit_px.is_none() {
                peak = peak.min(bar.low);
            }
        }

        if let Some(px) = exit_px {
            let gross = side as f64 * (px - entry_px) * qty;
            let fees = fee * (entry_px * qty + px * qty);
            out.push(Trade {
                symbol: symbol.to_string(),
                entry_time,
                net_pnl: gross - fees,
            });
            side = 0;
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
    fn stop_loss_caps_the_loss() {
        // Long breakout, then a crash. A tight stop should exit near the stop
        // level instead of riding all the way down to the channel exit.
        let mut candles = Vec::new();
        for i in 0..30 {
            candles.push(candle(i, 100.0, 100.5, 99.5, 100.0));
        }
        candles.push(candle(30, 101.0, 102.0, 100.5, 102.0)); // breakout -> long signal
        candles.push(candle(31, 102.0, 102.0, 90.0, 91.0)); // crash intrabar
        for i in 32..50 {
            candles.push(candle(i, 91.0, 91.5, 90.5, 91.0));
        }
        let no_stop = BarConfig { entry_lookback: 20, exit_lookback: 10, ..Default::default() };
        let with_stop = BarConfig { stop_loss_pct: 2.0, ..no_stop.clone() };

        let mut t0 = Vec::new();
        run_symbol("T", &candles, &no_stop, &mut t0);
        let mut t1 = Vec::new();
        run_symbol("T", &candles, &with_stop, &mut t1);
        let loss0: f64 = t0.iter().map(|t| t.net_pnl).sum();
        let loss1: f64 = t1.iter().map(|t| t.net_pnl).sum();
        assert!(!t1.is_empty(), "stop should produce a trade");
        assert!(loss1 > loss0, "stop-loss should cap the loss: {loss1} vs {loss0}");
    }

    #[test]
    fn trailing_stop_locks_in_more_than_channel_exit() {
        // Ramp up, then a sharp crash. A trailing stop exits near the peak;
        // the (lagging) channel exit gives most of it back.
        let mut candles = Vec::new();
        for i in 0..30 {
            candles.push(candle(i, 100.0, 100.5, 99.5, 100.0));
        }
        candles.push(candle(30, 101.0, 102.0, 100.5, 102.0)); // breakout
        for i in 31..51 {
            let p = 100.0 + (i - 30) as f64 * 3.0; // ramp to ~160
            candles.push(candle(i, p, p + 1.0, p - 1.0, p));
        }
        candles.push(candle(51, 160.0, 160.0, 110.0, 111.0)); // sharp crash
        for i in 52..62 {
            candles.push(candle(i, 111.0, 111.5, 110.5, 111.0));
        }
        let channel = BarConfig { entry_lookback: 20, exit_lookback: 10, ..Default::default() };
        let trailing = BarConfig { trail_pct: 5.0, ..channel.clone() };

        let mut tc = Vec::new();
        run_symbol("T", &candles, &channel, &mut tc);
        let mut tt = Vec::new();
        run_symbol("T", &candles, &trailing, &mut tt);
        let net_c: f64 = tc.iter().map(|t| t.net_pnl).sum();
        let net_t: f64 = tt.iter().map(|t| t.net_pnl).sum();
        assert!(net_t > 0.0, "trailing should lock a profit, got {net_t}");
        assert!(net_t > net_c, "trailing should beat channel on a sharp reversal: {net_t} vs {net_c}");
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
