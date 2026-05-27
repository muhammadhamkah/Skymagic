//! A roster of bar-based strategies for the head-to-head tournament. Each runs
//! on the same candles with the same conservative cost model as the Donchian
//! backtest (signal on bar i's close, fill at bar i+1's open, taker fees +
//! slippage both legs), so results are directly comparable.
//!
//! Strategy parameters are fixed at standard textbook values on purpose — the
//! tournament compares *families*, and tuning each one would just invite the
//! overfitting we're trying to avoid.

use std::collections::HashMap;

use crate::bars::{BarConfig, Trade};
use crate::candles::Candle;

fn mean(c: &[Candle], end: usize, n: usize) -> f64 {
    c[end - n..end].iter().map(|x| x.close).sum::<f64>() / n as f64
}

fn stddev(c: &[Candle], end: usize, n: usize) -> f64 {
    let m = mean(c, end, n);
    let var = c[end - n..end].iter().map(|x| (x.close - m).powi(2)).sum::<f64>() / n as f64;
    var.sqrt()
}

fn net(side: i8, entry_px: f64, exit_px: f64, qty: f64, fee: f64) -> f64 {
    let gross = side as f64 * (exit_px - entry_px) * qty;
    let fees = fee * (entry_px * qty + exit_px * qty);
    gross - fees
}

/// A "flip" strategy: it holds long/short to match a per-bar signal sign and
/// flips when the sign changes. Used by MA-crossover and time-series momentum.
fn run_flip(
    symbol: &str,
    c: &[Candle],
    cfg: &BarConfig,
    warmup: usize,
    signal: impl Fn(usize) -> i8,
    out: &mut Vec<Trade>,
) {
    if c.len() < warmup + 2 {
        return;
    }
    let slip = cfg.slippage_bps / 10_000.0;
    let fee = cfg.fee_bps / 10_000.0;
    let mut side: i8 = 0;
    let mut entry_px = 0.0;
    let mut qty = 0.0;
    let mut entry_time = 0i64;

    for i in warmup..c.len() - 1 {
        let mut desired = signal(i);
        if !cfg.allow_short && desired < 0 {
            desired = 0;
        }
        if desired == side {
            continue;
        }
        let exec = c[i + 1].open;
        if side != 0 {
            let exit_px = if side == 1 { exec * (1.0 - slip) } else { exec * (1.0 + slip) };
            out.push(Trade { symbol: symbol.to_string(), entry_time, net_pnl: net(side, entry_px, exit_px, qty, fee) });
            side = 0;
        }
        if desired != 0 {
            side = desired;
            entry_px = if side == 1 { exec * (1.0 + slip) } else { exec * (1.0 - slip) };
            if entry_px <= 0.0 {
                side = 0;
                continue;
            }
            qty = cfg.notional / entry_px;
            entry_time = c[i + 1].open_time;
        }
    }
}

/// Fast/slow SMA crossover (10/50). Long when fast>slow, short when fast<slow.
pub fn ma_crossover(symbol: &str, c: &[Candle], cfg: &BarConfig, out: &mut Vec<Trade>) {
    let (fast, slow) = (10usize, 50usize);
    run_flip(symbol, c, cfg, slow, |i| {
        if mean(c, i + 1, fast) > mean(c, i + 1, slow) { 1 } else { -1 }
    }, out);
}

/// Time-series momentum: long if the trailing 24-bar return is positive, else short.
pub fn ts_momentum(symbol: &str, c: &[Candle], cfg: &BarConfig, out: &mut Vec<Trade>) {
    let lookback = 24usize;
    run_flip(symbol, c, cfg, lookback, |i| {
        if c[i].close > c[i - lookback].close { 1 } else { -1 }
    }, out);
}

/// Mean reversion: enter when the close is >2 std from its 24-bar mean (long if
/// below, short if above), exit when it reverts back through the mean.
pub fn mean_reversion(symbol: &str, c: &[Candle], cfg: &BarConfig, out: &mut Vec<Trade>) {
    let (lookback, k) = (24usize, 2.0f64);
    if c.len() < lookback + 2 {
        return;
    }
    let slip = cfg.slippage_bps / 10_000.0;
    let fee = cfg.fee_bps / 10_000.0;
    let mut side: i8 = 0;
    let mut entry_px = 0.0;
    let mut qty = 0.0;
    let mut entry_time = 0i64;

    for i in lookback..c.len() - 1 {
        let sd = stddev(c, i + 1, lookback);
        if sd <= 0.0 {
            continue;
        }
        let z = (c[i].close - mean(c, i + 1, lookback)) / sd;
        let exec = c[i + 1].open;
        if side == 0 {
            let want = if z < -k { 1 } else if z > k && cfg.allow_short { -1 } else { 0 };
            if want != 0 {
                side = want;
                entry_px = if side == 1 { exec * (1.0 + slip) } else { exec * (1.0 - slip) };
                if entry_px <= 0.0 {
                    side = 0;
                    continue;
                }
                qty = cfg.notional / entry_px;
                entry_time = c[i + 1].open_time;
            }
        } else if (side == 1 && z >= 0.0) || (side == -1 && z <= 0.0) {
            let exit_px = if side == 1 { exec * (1.0 - slip) } else { exec * (1.0 + slip) };
            out.push(Trade { symbol: symbol.to_string(), entry_time, net_pnl: net(side, entry_px, exit_px, qty, fee) });
            side = 0;
        }
    }
}

/// Cross-sectional momentum across the whole universe: every `rebalance` bars,
/// rank symbols by trailing `lookback`-bar return, long the top decile / short
/// the bottom decile, hold one rebalance period. Each position is one trade.
pub fn cross_sectional(all: &[(String, Vec<Candle>)], cfg: &BarConfig, out: &mut Vec<Trade>) {
    let (lookback, rebalance, q) = (24usize, 6usize, 0.10f64);
    let slip = cfg.slippage_bps / 10_000.0;
    let fee = cfg.fee_bps / 10_000.0;

    // Per-symbol close-by-time, and a uniform master timeline (union of times).
    let mut maps: HashMap<&str, HashMap<i64, f64>> = HashMap::new();
    let mut times: Vec<i64> = Vec::new();
    for (sym, candles) in all {
        let m: HashMap<i64, f64> = candles.iter().map(|c| (c.open_time, c.close)).collect();
        for c in candles {
            times.push(c.open_time);
        }
        maps.insert(sym.as_str(), m);
    }
    times.sort_unstable();
    times.dedup();
    if times.len() < lookback + rebalance + 1 {
        return;
    }

    let mut idx = lookback;
    while idx + rebalance < times.len() {
        let (t, t_past, t_fwd) = (times[idx], times[idx - lookback], times[idx + rebalance]);
        let mut ranked: Vec<(&str, f64)> = Vec::new();
        for (sym, m) in &maps {
            if let (Some(now), Some(past)) = (m.get(&t), m.get(&t_past)) {
                if *past > 0.0 {
                    ranked.push((sym, now / past - 1.0));
                }
            }
        }
        if ranked.len() >= 10 {
            ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
            let k = ((ranked.len() as f64 * q).ceil() as usize).max(1);
            for (rank, (sym, _)) in ranked.iter().enumerate() {
                let side: i8 = if rank < k {
                    1
                } else if rank >= ranked.len() - k && cfg.allow_short {
                    -1
                } else {
                    continue;
                };
                let m = &maps[sym];
                if let (Some(&px0), Some(&px1)) = (m.get(&t), m.get(&t_fwd)) {
                    let entry_px = if side == 1 { px0 * (1.0 + slip) } else { px0 * (1.0 - slip) };
                    let exit_px = if side == 1 { px1 * (1.0 - slip) } else { px1 * (1.0 + slip) };
                    if entry_px <= 0.0 {
                        continue;
                    }
                    let qty = cfg.notional / entry_px;
                    out.push(Trade { symbol: sym.to_string(), entry_time: t, net_pnl: net(side, entry_px, exit_px, qty, fee) });
                }
            }
        }
        idx += rebalance;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candle(t: i64, c: f64) -> Candle {
        Candle { open_time: t, open: c, high: c, low: c, close: c, volume: 1.0 }
    }

    #[test]
    fn ts_momentum_profits_in_uptrend_then_reverses() {
        // Steady uptrend (momentum long), then a downtrend (flip short and also
        // profit). Net should be positive.
        let mut c = Vec::new();
        for i in 0..60 {
            c.push(candle(i, 100.0 + i as f64));
        }
        for i in 60..120 {
            c.push(candle(i, 160.0 - (i - 59) as f64));
        }
        let cfg = BarConfig { fee_bps: 5.0, slippage_bps: 1.0, ..Default::default() };
        let mut out = Vec::new();
        ts_momentum("T", &c, &cfg, &mut out);
        let total: f64 = out.iter().map(|t| t.net_pnl).sum();
        assert!(!out.is_empty());
        assert!(total > 0.0, "momentum should profit on a trend+reversal, got {total}");
    }

    #[test]
    fn mean_reversion_profits_on_a_dip_and_recovery() {
        // Flat at 100, a dip below 2-sigma that *persists* one bar (so the
        // next-bar-open entry fills at the low), then recovery to the mean.
        let mut c = Vec::new();
        for i in 0..40 {
            c.push(candle(i, 100.0));
        }
        c.push(candle(40, 90.0)); // dip -> z << -2 -> signal long
        c.push(candle(41, 90.0)); // still low -> entry fills here at ~90
        for i in 42..70 {
            c.push(candle(i, 100.0)); // recover to mean -> exit at ~100
        }
        let cfg = BarConfig { fee_bps: 5.0, slippage_bps: 1.0, ..Default::default() };
        let mut out = Vec::new();
        mean_reversion("T", &c, &cfg, &mut out);
        let total: f64 = out.iter().map(|t| t.net_pnl).sum();
        assert!(!out.is_empty(), "should have entered the dip");
        assert!(total > 0.0, "mean reversion should profit on dip+recovery, got {total}");
    }
}
