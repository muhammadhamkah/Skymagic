//! Edge diagnostics.
//!
//! The whole point of the harness: given a stream of feature snapshots, does any
//! signal predict the *next* price move? We measure the correlation between a
//! signal at time `t` and the forward mid return over a horizon, and compare it
//! to the round-trip taker cost. If the signal can't clear fees, the strategy
//! is dead on arrival — that's the cheap lesson we're paying for up front.

use crate::features::FeatureSnapshot;

#[derive(Debug, Clone)]
pub struct EdgeReport {
    pub symbol: String,
    pub n: usize,
    /// Pearson corr of book imbalance(t) with forward return(t -> t+h).
    pub corr_book_imbalance: f64,
    /// Pearson corr of trade-flow imbalance(t) with forward return.
    pub corr_trade_flow: f64,
    pub mean_spread_bps: f64,
    /// Std dev of the per-step forward return, in bps. Rough sense of how much
    /// move there is to capture at this horizon.
    pub fwd_return_std_bps: f64,
}

fn pearson(x: &[f64], y: &[f64]) -> f64 {
    let n = x.len();
    if n == 0 {
        return 0.0;
    }
    let nf = n as f64;
    let mx = x.iter().sum::<f64>() / nf;
    let my = y.iter().sum::<f64>() / nf;
    let mut cov = 0.0;
    let mut vx = 0.0;
    let mut vy = 0.0;
    for i in 0..n {
        let dx = x[i] - mx;
        let dy = y[i] - my;
        cov += dx * dy;
        vx += dx * dx;
        vy += dy * dy;
    }
    if vx <= 0.0 || vy <= 0.0 {
        return 0.0;
    }
    cov / (vx.sqrt() * vy.sqrt())
}

/// `snaps` must be a single symbol's snapshots in time order.
pub fn edge_report(snaps: &[FeatureSnapshot], horizon: usize) -> Option<EdgeReport> {
    if snaps.len() <= horizon || horizon == 0 {
        return None;
    }
    let usable = snaps.len() - horizon;
    let mut book = Vec::with_capacity(usable);
    let mut flow = Vec::with_capacity(usable);
    let mut fwd = Vec::with_capacity(usable);

    for i in 0..usable {
        let now = snaps[i].mid;
        let later = snaps[i + horizon].mid;
        if now <= 0.0 || later <= 0.0 {
            continue;
        }
        book.push(snaps[i].book_imbalance);
        flow.push(snaps[i].trade_flow_imbalance);
        fwd.push((later / now).ln() * 10_000.0); // bps
    }
    if fwd.is_empty() {
        return None;
    }

    let mean_spread_bps = snaps.iter().map(|s| s.spread_bps).sum::<f64>() / snaps.len() as f64;
    let mfwd = fwd.iter().sum::<f64>() / fwd.len() as f64;
    let var = fwd.iter().map(|r| (r - mfwd).powi(2)).sum::<f64>() / fwd.len() as f64;

    Some(EdgeReport {
        symbol: snaps[0].symbol.clone(),
        n: fwd.len(),
        corr_book_imbalance: pearson(&book, &fwd),
        corr_trade_flow: pearson(&flow, &fwd),
        mean_spread_bps,
        fwd_return_std_bps: var.sqrt(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snap(symbol: &str, ts: i64, mid: f64, book_imb: f64) -> FeatureSnapshot {
        FeatureSnapshot {
            ts,
            symbol: symbol.into(),
            mid,
            microprice: mid,
            spread_bps: 1.0,
            book_imbalance: book_imb,
            trade_flow: 0.0,
            trade_flow_imbalance: 0.0,
        }
    }

    #[test]
    fn recovers_planted_signal() {
        // Construct a series where imbalance perfectly predicts the next move.
        let mut snaps = Vec::new();
        let mut mid = 100.0;
        for i in 0..500 {
            let imb = if i % 2 == 0 { 0.5 } else { -0.5 };
            snaps.push(snap("X", i, mid, imb));
            mid += imb; // next mid moves in the direction of this imbalance
        }
        let r = edge_report(&snaps, 1).unwrap();
        assert!(
            r.corr_book_imbalance > 0.9,
            "expected strong positive corr, got {}",
            r.corr_book_imbalance
        );
    }
}
