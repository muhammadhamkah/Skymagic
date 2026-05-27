//! hft_bot — a Binance single-venue HFT research harness.
//!
//! Subcommands:
//!   collect  live Binance USDT-M futures L1 + trades -> NDJSON  (run locally;
//!            Binance is geo-blocked from some clouds)
//!   synth    generate synthetic market data -> NDJSON (no network needed)
//!   replay   read NDJSON -> feature engine -> edge diagnostics

mod analysis;
mod backtest;
mod book;
mod events;
mod features;
mod storage;
mod synth;
mod watchers;

use std::collections::HashMap;
use std::io::Write;

use anyhow::{bail, Context, Result};
use tokio::sync::mpsc;
use tracing::info;

use crate::analysis::edge_report;
use crate::backtest::BacktestConfig;
use crate::events::MarketEvent;
use crate::features::{FeatureEngine, FeatureSnapshot};
use crate::storage::{EventReader, Recorder};
use crate::synth::{SynthConfig, SynthGen};

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let args: Vec<String> = std::env::args().collect();
    let cmd = args.get(1).map(String::as_str);
    let flags = parse_flags(&args[2.min(args.len())..]);

    match cmd {
        Some("collect") => run_collect(&flags),
        Some("synth") => run_synth(&flags),
        Some("replay") => run_replay(&flags),
        Some("backtest") => run_backtest(&flags),
        _ => {
            print_usage();
            Ok(())
        }
    }
}

fn print_usage() {
    eprintln!(
        "hft_bot — Binance HFT research harness\n\
         \n\
         USAGE:\n\
         \thft_bot collect [--symbols btcusdt,ethusdt] [--out data/events.ndjson] [--print-every 200]\n\
         \thft_bot synth   [--out data/synth.ndjson] [--n 50000] [--signal 0.4] [--seed 42] [--symbol BTCUSDT]\n\
         \thft_bot replay  --in <file.ndjson> [--window-ms 1000] [--levels 1] [--horizon 20]\n\
         \thft_bot backtest --in <file.ndjson> [--threshold 0.3] [--hold-ms 500] [--notional 1000]\n\
         \t                 [--fee-bps 5] [--target 0.02] [--latency-ms 100] [--slippage-ticks 1]\n\
         \t                 [--tick 0.1] [--window-ms 1000] [--no-short]\n\
         \n\
         Note: `collect` needs direct Binance access; run it locally, not from a geo-blocked cloud."
    );
}

fn parse_flags(args: &[String]) -> HashMap<String, String> {
    let mut map = HashMap::new();
    let mut i = 0;
    while i < args.len() {
        if let Some(key) = args[i].strip_prefix("--") {
            let val = args.get(i + 1).cloned().unwrap_or_default();
            map.insert(key.to_string(), val);
            i += 2;
        } else {
            i += 1;
        }
    }
    map
}

fn flag<'a>(flags: &'a HashMap<String, String>, key: &str) -> Option<&'a str> {
    flags.get(key).map(String::as_str)
}

fn flag_parse<T: std::str::FromStr>(flags: &HashMap<String, String>, key: &str, default: T) -> T {
    flag(flags, key)
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

// ---- collect ---------------------------------------------------------------

fn run_collect(flags: &HashMap<String, String>) -> Result<()> {
    let symbols: Vec<String> = flag(flags, "symbols")
        .unwrap_or("btcusdt")
        .split(',')
        .filter(|s| !s.is_empty())
        .map(|s| s.trim().to_string())
        .collect();
    let out = flag(flags, "out").unwrap_or("data/events.ndjson").to_string();
    let print_every: u64 = flag_parse(flags, "print-every", 200);

    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;

    rt.block_on(async move {
        let recorder = Recorder::create(&out)?;
        let (tx, mut rx) = mpsc::channel::<MarketEvent>(100_000);

        let watcher = tokio::spawn(watchers::binance::run(symbols.clone(), tx));

        let mut engine = FeatureEngine::new(1000, 1);
        let mut n: u64 = 0;
        info!(?symbols, out = %out, "collecting; Ctrl-C to stop");

        loop {
            tokio::select! {
                maybe = rx.recv() => {
                    match maybe {
                        Some(ev) => {
                            if let Some(s) = engine.on_event(&ev) {
                                if n % print_every == 0 {
                                    info!(
                                        sym = %s.symbol, mid = %format!("{:.2}", s.mid),
                                        spread_bps = %format!("{:.2}", s.spread_bps),
                                        book_imb = %format!("{:+.3}", s.book_imbalance),
                                        flow = %format!("{:+.3}", s.trade_flow_imbalance),
                                        "tick"
                                    );
                                }
                            }
                            recorder.record(ev);
                            n += 1;
                        }
                        None => break,
                    }
                }
                _ = tokio::signal::ctrl_c() => {
                    info!("shutdown signal received");
                    break;
                }
            }
        }

        watcher.abort();
        let written = recorder.finish()?;
        info!(events = written, "stopped; flushed to {out}");
        Ok::<(), anyhow::Error>(())
    })
}

// ---- synth -----------------------------------------------------------------

fn run_synth(flags: &HashMap<String, String>) -> Result<()> {
    let out = flag(flags, "out").unwrap_or("data/synth.ndjson").to_string();
    let cfg = SynthConfig {
        symbol: flag(flags, "symbol").unwrap_or("BTCUSDT").to_string(),
        n_events: flag_parse(flags, "n", 50_000),
        signal_strength: flag_parse(flags, "signal", 0.4),
        seed: flag_parse(flags, "seed", 42),
        ..Default::default()
    };

    let recorder = Recorder::create(&out)?;
    let n = cfg.n_events;
    for ev in SynthGen::new(cfg) {
        recorder.record(ev);
    }
    let written = recorder.finish()?;
    println!("wrote {written} synthetic events (requested {n}) to {out}");
    Ok(())
}

// ---- replay ----------------------------------------------------------------

/// Replay an NDJSON file through the feature engine, grouping snapshots by
/// symbol. `on_snapshot` is called for each snapshot (e.g. to write CSV).
fn load_snapshots(
    input: &str,
    window_ms: i64,
    levels: usize,
    mut on_snapshot: impl FnMut(&FeatureSnapshot) -> Result<()>,
) -> Result<(HashMap<String, Vec<FeatureSnapshot>>, u64)> {
    let mut engine = FeatureEngine::new(window_ms, levels);
    let mut by_symbol: HashMap<String, Vec<FeatureSnapshot>> = HashMap::new();
    let mut total = 0u64;
    for item in EventReader::open(input)? {
        let ev = item?;
        total += 1;
        if let Some(s) = engine.on_event(&ev) {
            on_snapshot(&s)?;
            by_symbol.entry(s.symbol.clone()).or_default().push(s);
        }
    }
    if total == 0 {
        bail!("no events read from {input}");
    }
    Ok((by_symbol, total))
}

fn run_replay(flags: &HashMap<String, String>) -> Result<()> {
    let input = flag(flags, "in").context("replay needs --in <file.ndjson>")?;
    let window_ms: i64 = flag_parse(flags, "window-ms", 1000);
    let levels: usize = flag_parse(flags, "levels", 1);
    let horizon: usize = flag_parse(flags, "horizon", 20);
    let features_out = flag(flags, "features-out");

    let mut csv = match features_out {
        Some(path) => {
            let mut f = std::fs::File::create(path)
                .with_context(|| format!("creating {path}"))?;
            writeln!(f, "ts,symbol,mid,microprice,bid_px,ask_px,spread_bps,book_imbalance,trade_flow,trade_flow_imbalance")?;
            Some(f)
        }
        None => None,
    };

    let (by_symbol, total) = load_snapshots(input, window_ms, levels, |s| {
        if let Some(f) = csv.as_mut() {
            writeln!(
                f,
                "{},{},{},{},{},{},{},{},{},{}",
                s.ts, s.symbol, s.mid, s.microprice, s.bid_px, s.ask_px,
                s.spread_bps, s.book_imbalance, s.trade_flow, s.trade_flow_imbalance
            )?;
        }
        Ok(())
    })?;

    println!("\nreplayed {total} events; window={window_ms}ms levels={levels} horizon={horizon} snapshots\n");
    println!(
        "{:<10} {:>8} {:>14} {:>14} {:>12} {:>14}",
        "symbol", "n", "corr(book)", "corr(flow)", "spread_bps", "fwd_std_bps"
    );
    println!("{}", "-".repeat(76));

    let mut symbols: Vec<&String> = by_symbol.keys().collect();
    symbols.sort();
    for sym in symbols {
        let snaps = &by_symbol[sym];
        match edge_report(snaps, horizon) {
            Some(r) => println!(
                "{:<10} {:>8} {:>14.4} {:>14.4} {:>12.3} {:>14.3}",
                r.symbol, r.n, r.corr_book_imbalance, r.corr_trade_flow,
                r.mean_spread_bps, r.fwd_return_std_bps
            ),
            None => println!("{sym:<10} {:>8} (too few snapshots for horizon)", snaps.len()),
        }
    }

    println!(
        "\nread: a signal is only tradeable if its forward edge (corr x fwd_std_bps) \n\
         clears the round-trip taker cost (~{:.1} bps on Binance futures + the spread).\n\
         use `backtest` to turn this into net USDT/trade.",
        2.0 * 5.0
    );
    Ok(())
}

// ---- backtest --------------------------------------------------------------

fn run_backtest(flags: &HashMap<String, String>) -> Result<()> {
    let input = flag(flags, "in").context("backtest needs --in <file.ndjson>")?;
    let window_ms: i64 = flag_parse(flags, "window-ms", 1000);
    let levels: usize = flag_parse(flags, "levels", 1);
    let fee_bps: f64 = flag_parse(flags, "fee-bps", 5.0);
    let cfg = BacktestConfig {
        entry_threshold: flag_parse(flags, "threshold", 0.3),
        hold_ms: flag_parse(flags, "hold-ms", 500),
        notional_usdt: flag_parse(flags, "notional", 1000.0),
        taker_fee_rate: fee_bps / 10_000.0,
        target_net_usdt: flag_parse(flags, "target", 0.02),
        allow_short: !flags.contains_key("no-short"),
        latency_ms: flag_parse(flags, "latency-ms", 100),
        slippage_ticks: flag_parse(flags, "slippage-ticks", 1.0),
        tick_size: flag_parse(flags, "tick", 0.1),
    };

    let (by_symbol, total) = load_snapshots(input, window_ms, levels, |_| Ok(()))?;

    println!(
        "\nbacktest OBI taker on {total} events  (costs modeled pessimistically)\n  \
         threshold=|{:.2}|  hold={}ms  notional={} USDT  taker_fee={:.1}bps/side  \
         target_net={} USDT  short={}\n  \
         latency={}ms  slippage={} tick  tick_size={}\n",
        cfg.entry_threshold, cfg.hold_ms, cfg.notional_usdt, fee_bps,
        cfg.target_net_usdt, cfg.allow_short,
        cfg.latency_ms, cfg.slippage_ticks, cfg.tick_size
    );

    let mut symbols: Vec<&String> = by_symbol.keys().collect();
    symbols.sort();
    for sym in symbols {
        let snaps = &by_symbol[sym];
        match backtest::run(snaps, &cfg) {
            Some(r) if r.n_trades > 0 => {
                println!("=== {} ===", r.symbol);
                println!("  trades            : {}", r.n_trades);
                println!("  win rate          : {:.1}%  ({}/{})", r.win_rate * 100.0, r.wins, r.n_trades);
                println!("  gross P&L         : {:+.4} USDT", r.gross_pnl);
                println!("  fees paid         : {:.4} USDT", r.total_fees);
                println!("  net P&L           : {:+.4} USDT", r.net_pnl);
                println!("  net / trade       : {:+.5} USDT   (target {:+.5})", r.net_per_trade, cfg.target_net_usdt);
                println!("  median net / trade: {:+.5} USDT", r.median_net);
                println!("  hit target rate   : {:.1}%", r.hit_target_rate * 100.0);
                println!("  avg hold          : {:.0} ms", r.avg_hold_ms);
                println!("  round-trip fee    : {:.4} USDT/trade (spread is on top)", r.round_trip_fee_usdt);
                let verdict = if r.net_per_trade >= cfg.target_net_usdt {
                    "GOAL MET on this data"
                } else if r.net_per_trade > 0.0 {
                    "profitable but below target"
                } else {
                    "unprofitable after costs"
                };
                println!("  verdict           : {verdict}\n");
            }
            _ => println!("=== {sym} ===\n  no trades triggered (threshold too high or too few snapshots)\n"),
        }
    }
    Ok(())
}
