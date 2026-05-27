//! Order-book-imbalance taker backtest.
//!
//! Answers the money question: trading OBI as a taker (crossing the spread on
//! both entry and exit) and paying fees, what is the **net P&L per trade**, and
//! what fraction of trades clear a target (e.g. 0.02 USDT)?
//!
//! Fills are intentionally pessimistic-but-honest for a taker:
//!   - go long  -> buy at the ask, later sell at the bid
//!   - go short -> sell at the bid, later buy at the ask
//! so the spread is paid twice, on top of taker fees. That spread cost is what
//! usually kills small HFT edges, so we model it explicitly rather than pricing
//! fills at the mid.

use crate::features::FeatureSnapshot;

#[derive(Debug, Clone)]
pub struct BacktestConfig {
    /// |book_imbalance| must reach this to open a position.
    pub entry_threshold: f64,
    /// Minimum holding time before exiting (ms).
    pub hold_ms: i64,
    /// Position size in USDT (quote) notional.
    pub notional_usdt: f64,
    /// Taker fee per side as a fraction (e.g. 0.0005 = 5 bps).
    pub taker_fee_rate: f64,
    /// Net-USDT-per-trade goal we're checking against.
    pub target_net_usdt: f64,
    /// Allow short entries on negative imbalance.
    pub allow_short: bool,
    /// Delay (ms) between observing a signal and the order reaching the engine.
    /// We fill at the touch *as of signal_time + latency*, so any adverse move
    /// during the delay is paid — this is the main realism lever.
    pub latency_ms: i64,
    /// Extra ticks paid through the touch per side, a stand-in for the depth
    /// we can't see with L1-only data (a buy fills `slippage_ticks` above the
    /// ask, a sell `slippage_ticks` below the bid).
    pub slippage_ticks: f64,
    /// Exchange price increment. Fills snap to this grid (buys up, sells down).
    pub tick_size: f64,
}

impl Default for BacktestConfig {
    fn default() -> Self {
        Self {
            entry_threshold: 0.3,
            hold_ms: 500,
            notional_usdt: 1000.0,
            taker_fee_rate: 0.0005,
            target_net_usdt: 0.02,
            allow_short: true,
            latency_ms: 100,
            slippage_ticks: 1.0,
            tick_size: 0.1,
        }
    }
}

#[derive(Debug, Clone)]
pub struct CompletedTrade {
    pub entry_ts: i64,
    pub exit_ts: i64,
    pub gross_pnl: f64,
    pub fees: f64,
    pub net_pnl: f64,
}

#[derive(Debug, Clone)]
pub struct BacktestReport {
    pub symbol: String,
    pub n_trades: usize,
    pub wins: usize,
    pub win_rate: f64,
    pub gross_pnl: f64,
    pub total_fees: f64,
    pub net_pnl: f64,
    pub net_per_trade: f64,
    pub median_net: f64,
    /// Fraction of trades whose net cleared `target_net_usdt`.
    pub hit_target_rate: f64,
    pub avg_hold_ms: f64,
    /// Fixed round-trip cost (USDT) at this notional: 2 taker fees. The spread
    /// is on top of this and varies per trade.
    pub round_trip_fee_usdt: f64,
}

/// Round to the tick grid; `up` rounds toward +inf (buys pay up), else toward
/// -inf (sells receive down). Both directions are pessimistic for us.
fn round_tick(px: f64, tick: f64, up: bool) -> f64 {
    if tick <= 0.0 {
        return px;
    }
    let n = px / tick;
    (if up { n.ceil() } else { n.floor() }) * tick
}

/// Index of the prevailing book at `arrival_ts`: the last snapshot at or before
/// it, searching forward from `from`. Models the order landing `latency` after
/// the signal and filling against whatever the touch is by then.
fn fill_at(snaps: &[FeatureSnapshot], from: usize, arrival_ts: i64) -> usize {
    let mut k = from;
    while k + 1 < snaps.len() && snaps[k + 1].ts <= arrival_ts {
        k += 1;
    }
    k
}

/// `snaps` must be one symbol's snapshots in time order.
pub fn run(snaps: &[FeatureSnapshot], cfg: &BacktestConfig) -> Option<BacktestReport> {
    if snaps.is_empty() {
        return None;
    }

    let mut trades: Vec<CompletedTrade> = Vec::new();
    let slip = cfg.slippage_ticks * cfg.tick_size;

    // Open position state.
    let mut side: i8 = 0;
    let mut entry_px = 0.0;
    let mut qty = 0.0;
    let mut entry_ts = 0i64;

    let mut i = 0;
    while i < snaps.len() {
        let s = &snaps[i];
        if side == 0 {
            let long = s.book_imbalance >= cfg.entry_threshold;
            let short = cfg.allow_short && s.book_imbalance <= -cfg.entry_threshold;
            if !long && !short {
                i += 1;
                continue;
            }
            // Signal now; order fills at the touch once it lands (latency later).
            let fi = fill_at(snaps, i, s.ts + cfg.latency_ms);
            let f = &snaps[fi];
            side = if long { 1 } else { -1 };
            entry_px = if long {
                round_tick(f.ask_px, cfg.tick_size, true) + slip
            } else {
                round_tick(f.bid_px, cfg.tick_size, false) - slip
            };
            if entry_px <= 0.0 {
                side = 0;
                i += 1;
                continue;
            }
            qty = cfg.notional_usdt / entry_px;
            entry_ts = f.ts;
            i = fi + 1;
        } else if s.ts - entry_ts >= cfg.hold_ms {
            let fi = fill_at(snaps, i, s.ts + cfg.latency_ms);
            let f = &snaps[fi];
            let exit_px = if side == 1 {
                round_tick(f.bid_px, cfg.tick_size, false) - slip
            } else {
                round_tick(f.ask_px, cfg.tick_size, true) + slip
            };
            let gross = side as f64 * (exit_px - entry_px) * qty;
            let fees = cfg.taker_fee_rate * (entry_px * qty + exit_px * qty);
            trades.push(CompletedTrade {
                entry_ts,
                exit_ts: f.ts,
                gross_pnl: gross,
                fees,
                net_pnl: gross - fees,
            });
            side = 0;
            i = fi + 1;
        } else {
            i += 1;
        }
    }

    if trades.is_empty() {
        return Some(BacktestReport {
            symbol: snaps[0].symbol.clone(),
            n_trades: 0,
            wins: 0,
            win_rate: 0.0,
            gross_pnl: 0.0,
            total_fees: 0.0,
            net_pnl: 0.0,
            net_per_trade: 0.0,
            median_net: 0.0,
            hit_target_rate: 0.0,
            avg_hold_ms: 0.0,
            round_trip_fee_usdt: 2.0 * cfg.taker_fee_rate * cfg.notional_usdt,
        });
    }

    let n = trades.len();
    let wins = trades.iter().filter(|t| t.net_pnl > 0.0).count();
    let gross_pnl: f64 = trades.iter().map(|t| t.gross_pnl).sum();
    let total_fees: f64 = trades.iter().map(|t| t.fees).sum();
    let net_pnl: f64 = trades.iter().map(|t| t.net_pnl).sum();
    let hit = trades
        .iter()
        .filter(|t| t.net_pnl >= cfg.target_net_usdt)
        .count();
    let avg_hold_ms =
        trades.iter().map(|t| (t.exit_ts - t.entry_ts) as f64).sum::<f64>() / n as f64;

    let mut nets: Vec<f64> = trades.iter().map(|t| t.net_pnl).collect();
    nets.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let median_net = nets[n / 2];

    Some(BacktestReport {
        symbol: snaps[0].symbol.clone(),
        n_trades: n,
        wins,
        win_rate: wins as f64 / n as f64,
        gross_pnl,
        total_fees,
        net_pnl,
        net_per_trade: net_pnl / n as f64,
        median_net,
        hit_target_rate: hit as f64 / n as f64,
        avg_hold_ms,
        round_trip_fee_usdt: 2.0 * cfg.taker_fee_rate * cfg.notional_usdt,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snap(ts: i64, imb: f64, bid: f64, ask: f64) -> FeatureSnapshot {
        FeatureSnapshot {
            ts,
            symbol: "BTCUSDT".into(),
            mid: (bid + ask) / 2.0,
            microprice: (bid + ask) / 2.0,
            bid_px: bid,
            ask_px: ask,
            spread_bps: 1.0,
            book_imbalance: imb,
            trade_flow: 0.0,
            trade_flow_imbalance: 0.0,
        }
    }

    /// Frictionless config: isolates the P&L arithmetic from latency/slippage.
    fn clean_cfg() -> BacktestConfig {
        BacktestConfig {
            entry_threshold: 0.3,
            hold_ms: 500,
            notional_usdt: 1000.0,
            taker_fee_rate: 0.0005,
            target_net_usdt: 0.02,
            allow_short: true,
            latency_ms: 0,
            slippage_ticks: 0.0,
            tick_size: 1e-9,
        }
    }

    #[test]
    fn pnl_math_is_exact() {
        // Long entry at ask=100.0, exit at bid=101.0 after the hold.
        // notional 1000 -> qty = 10. gross = (101-100)*10 = 10.
        // fees = 0.0005 * (100*10 + 101*10) = 0.0005 * 2010 = 1.005.
        // net = 10 - 1.005 = 8.995.
        let snaps = vec![
            snap(0, 0.9, 99.9, 100.0),     // strong buy imbalance -> long at ask 100
            snap(1000, 0.0, 101.0, 101.1), // hold elapsed -> exit at bid 101
        ];
        let r = run(&snaps, &clean_cfg()).unwrap();
        assert_eq!(r.n_trades, 1);
        assert!((r.gross_pnl - 10.0).abs() < 1e-6, "gross {}", r.gross_pnl);
        assert!((r.total_fees - 1.005).abs() < 1e-6, "fees {}", r.total_fees);
        assert!((r.net_pnl - 8.995).abs() < 1e-6, "net {}", r.net_pnl);
        assert_eq!(r.hit_target_rate, 1.0);
    }

    #[test]
    fn spread_and_fees_turn_flat_move_into_a_loss() {
        // Price doesn't move, but we cross the spread twice and pay fees ->
        // guaranteed loss. The HFT taker trap. Uses the (pessimistic) defaults.
        let snaps = vec![
            snap(0, 0.9, 99.95, 100.05),
            snap(1000, 0.0, 99.95, 100.05),
        ];
        let r = run(&snaps, &BacktestConfig::default()).unwrap();
        assert_eq!(r.n_trades, 1);
        assert!(r.net_pnl < 0.0, "expected a loss, got {}", r.net_pnl);
        assert_eq!(r.hit_target_rate, 0.0);
    }

    #[test]
    fn latency_only_worsens_pnl() {
        // Price ticks up between signal and fill: with latency we buy the higher
        // ask (adverse selection), so net must be <= the zero-latency case.
        let snaps = vec![
            snap(0, 0.9, 99.9, 100.0),    // signal here
            snap(40, 0.0, 100.9, 101.0),  // price moved up during the delay
            snap(1000, 0.0, 100.9, 101.0),
        ];
        let mut nolat = clean_cfg();
        nolat.latency_ms = 0;
        let mut lat = clean_cfg();
        lat.latency_ms = 50; // fill lands on the 40ms snapshot (worse ask)

        let r0 = run(&snaps, &nolat).unwrap();
        let r1 = run(&snaps, &lat).unwrap();
        assert_eq!(r0.n_trades, 1);
        assert_eq!(r1.n_trades, 1);
        assert!(
            r1.net_pnl < r0.net_pnl,
            "latency should reduce pnl: {} vs {}",
            r1.net_pnl,
            r0.net_pnl
        );
    }

    #[test]
    fn slippage_only_worsens_pnl() {
        let snaps = vec![
            snap(0, 0.9, 99.9, 100.0),
            snap(1000, 0.0, 101.0, 101.1),
        ];
        let mut no_slip = clean_cfg();
        no_slip.slippage_ticks = 0.0;
        let mut with_slip = clean_cfg();
        with_slip.tick_size = 0.1;
        with_slip.slippage_ticks = 1.0;

        let r0 = run(&snaps, &no_slip).unwrap();
        let r1 = run(&snaps, &with_slip).unwrap();
        assert!(
            r1.net_pnl < r0.net_pnl,
            "slippage should reduce pnl: {} vs {}",
            r1.net_pnl,
            r0.net_pnl
        );
    }
}
