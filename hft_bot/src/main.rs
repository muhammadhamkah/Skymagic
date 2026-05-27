//! hft_bot — a Binance single-venue HFT research harness.
//!
//! Subcommands:
//!   collect  live Binance USDT-M futures L1 + trades -> NDJSON  (run locally;
//!            Binance is geo-blocked from some clouds)
//!   synth    generate synthetic market data -> NDJSON (no network needed)
//!   replay   read NDJSON -> feature engine -> edge diagnostics

mod analysis;
mod backtest;
mod bars;
mod book;
mod candles;
mod events;
mod features;
mod rest;
mod storage;
mod strategies;
mod synth;
mod watchers;

use std::collections::HashMap;
use std::io::Write;

use anyhow::{bail, Context, Result};
use tokio::sync::mpsc;
use tracing::info;

use crate::analysis::edge_report;
use crate::backtest::BacktestConfig;
use crate::bars::{BarConfig, Trade};
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
        Some("universe") => run_universe(&flags),
        Some("fetch-klines") => run_fetch_klines(&flags),
        Some("synth-klines") => run_synth_klines(&flags),
        Some("backtest-bars") => run_backtest_bars(&flags),
        Some("tournament") => run_tournament(&flags),
        Some("lighter-probe") => run_lighter_probe(&flags),
        Some("lighter-markets") => rest::lighter_markets_dump(),
        Some("lighter-universe") => run_lighter_universe(&flags),
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
         Higher-timeframe (Donchian) workflow:\n\
         \thft_bot universe [--n 150] [--out data/universe.txt]\n\
         \thft_bot fetch-klines [--symbols-file data/universe.txt | --top 150] [--interval 1h]\n\
         \t                     [--months 24] [--out-dir data/klines]\n\
         \thft_bot synth-klines [--out-dir data/klines] [--symbols 12] [--bars 4000] [--trendiness 0.5]\n\
         \thft_bot backtest-bars --dir data/klines [--entry 20] [--exit 10] [--fee-bps 5]\n\
         \t                      [--notional 1000] [--train-frac 0.7] [--target 0.02] [--no-short]\n\
         \t                      [--stop-loss-pct 0] [--trail-pct 0] [--take-profit-pct 0]\n\
         \thft_bot tournament --dir data/klines [--strategies ma,tsmom,xsec,meanrev,donchian]\n\
         \t                   [--fee-bps 5] [--notional 1000] [--train-frac 0.7] [--walk 8] [--no-short]\n\
         \n\
         Lighter (perp DEX, zero-fee):\n\
         \thft_bot lighter-probe [--market 1] [--secs 15]   (dumps raw WS frames)\n\
         \thft_bot lighter-markets                          (dumps REST market list)\n\
         \thft_bot lighter-universe [--out data/lighter_markets.txt]   (all markets -> file)\n\
         \thft_bot collect --venue lighter --markets-file data/lighter_markets.txt [--out data/lighter.ndjson]\n\
         \thft_bot collect --venue lighter --markets all   (subscribe to every market)\n\
         \n\
         Note: `collect`, `universe`, `fetch-klines`, `lighter-probe` need direct network access; run locally."
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
    let venue = flag(flags, "venue").unwrap_or("binance").to_string();
    // For Lighter: (market_id, symbol). Source order: --markets-file, then
    // `--markets all` (fetch every market), then an explicit id list.
    let lighter_markets: Vec<(u32, String)> = if venue == "lighter" {
        if let Some(path) = flag(flags, "markets-file") {
            std::fs::read_to_string(path)
                .with_context(|| format!("reading {path}"))?
                .lines()
                .filter_map(|l| {
                    let mut it = l.split_whitespace();
                    let id: u32 = it.next()?.parse().ok()?;
                    let sym = it.next().map(str::to_string).unwrap_or_else(|| id.to_string());
                    Some((id, sym))
                })
                .collect()
        } else if flag(flags, "markets") == Some("all") {
            rest::lighter_all_markets()?
        } else {
            flag(flags, "markets")
                .unwrap_or("1")
                .split(',')
                .filter_map(|s| s.trim().parse::<u32>().ok().map(|id| (id, id.to_string())))
                .collect()
        }
    } else {
        Vec::new()
    };

    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;

    rt.block_on(async move {
        let recorder = Recorder::create(&out)?;
        let (tx, mut rx) = mpsc::channel::<MarketEvent>(200_000);

        let watcher = if venue == "lighter" {
            info!(markets = lighter_markets.len(), "collecting from lighter");
            tokio::spawn(watchers::lighter::run(lighter_markets, tx))
        } else {
            tokio::spawn(watchers::binance::run(symbols.clone(), tx))
        };

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

    let min_trades: usize = flag_parse(flags, "min-trades", 10);
    let mut rows: Vec<(String, backtest::BacktestReport, f64)> = Vec::new();
    for (sym, snaps) in &by_symbol {
        if let Some(r) = backtest::run(snaps, &cfg) {
            if r.n_trades == 0 {
                continue;
            }
            let mean_spread = snaps.iter().map(|s| s.spread_bps).sum::<f64>() / snaps.len() as f64;
            rows.push((sym.clone(), r, mean_spread));
        }
    }
    rows.sort_by(|a, b| b.1.net_per_trade.partial_cmp(&a.1.net_per_trade).unwrap());

    println!(
        "{:<14} {:>8} {:>8} {:>14} {:>12} {:>13}",
        "symbol", "trades", "win%", "net/trade", "spread_bps", "total net"
    );
    println!("{}", "-".repeat(74));
    let (mut agg_trades, mut agg_net, mut pos, mut shown) = (0usize, 0.0, 0usize, 0usize);
    for (sym, r, spr) in &rows {
        agg_trades += r.n_trades;
        agg_net += r.net_pnl;
        if r.net_pnl > 0.0 {
            pos += 1;
        }
        if r.n_trades < min_trades {
            continue;
        }
        shown += 1;
        println!(
            "{:<14} {:>8} {:>7.1}% {:>+14.5} {:>12.2} {:>+13.2}",
            sym, r.n_trades, r.win_rate * 100.0, r.net_per_trade, spr, r.net_pnl
        );
    }
    println!("{}", "-".repeat(74));
    let agg_per_trade = if agg_trades > 0 { agg_net / agg_trades as f64 } else { 0.0 };
    println!(
        "TOTAL: {} symbols traded, {} net-positive  |  {} trades  |  net {:+.2} USDT  |  net/trade {:+.5}",
        rows.len(), pos, agg_trades, agg_net, agg_per_trade
    );
    println!(
        "(showing {shown} symbols with >= {min_trades} trades, sorted by net/trade; target {:+.5} USDT)\n\
         read: net/trade beats target on a symbol only when its OBI edge clears its spread above.",
        cfg.target_net_usdt
    );
    Ok(())
}

// ---- strategy tournament ----------------------------------------------------

fn run_tournament(flags: &HashMap<String, String>) -> Result<()> {
    let dir = flag(flags, "dir").unwrap_or("data/klines");
    let cfg = BarConfig {
        entry_lookback: flag_parse(flags, "entry", 20),
        exit_lookback: flag_parse(flags, "exit", 10),
        fee_bps: flag_parse(flags, "fee-bps", 5.0),
        slippage_bps: flag_parse(flags, "slippage-bps", 1.0),
        notional: flag_parse(flags, "notional", 1000.0),
        allow_short: !flags.contains_key("no-short"),
        train_frac: flag_parse(flags, "train-frac", 0.7),
        target_net_usdt: flag_parse(flags, "target", 0.02),
        stop_loss_pct: 0.0,
        take_profit_pct: 0.0,
        trail_pct: 0.0,
    };
    let want: Option<Vec<String>> = flag(flags, "strategies")
        .map(|s| s.split(',').map(|x| x.trim().to_string()).collect());
    let included = |name: &str| want.as_ref().map_or(true, |w| w.iter().any(|x| x == name));

    let files = candles::list_symbol_files(dir)?;
    if files.is_empty() {
        bail!("no *.ndjson symbol files in {dir}");
    }
    let mut all: Vec<(String, Vec<candles::Candle>)> = Vec::new();
    let (mut tmin, mut tmax) = (i64::MAX, i64::MIN);
    for (sym, path) in &files {
        let c = candles::read_candles(path)?;
        for k in &c {
            tmin = tmin.min(k.open_time);
            tmax = tmax.max(k.open_time);
        }
        all.push((sym.clone(), c));
    }
    let cutoff = tmin + ((tmax - tmin) as f64 * cfg.train_frac) as i64;

    let mut entries: Vec<(&str, Vec<Trade>)> = Vec::new();
    if included("donchian") {
        let mut v = Vec::new();
        for (sym, c) in &all {
            bars::run_symbol(sym, c, &cfg, &mut v);
        }
        entries.push(("donchian (baseline)", v));
    }
    if included("ma") {
        let mut v = Vec::new();
        for (sym, c) in &all {
            strategies::ma_crossover(sym, c, &cfg, &mut v);
        }
        entries.push(("ma_crossover", v));
    }
    if included("tsmom") {
        let mut v = Vec::new();
        for (sym, c) in &all {
            strategies::ts_momentum(sym, c, &cfg, &mut v);
        }
        entries.push(("ts_momentum", v));
    }
    if included("meanrev") {
        let mut v = Vec::new();
        for (sym, c) in &all {
            strategies::mean_reversion(sym, c, &cfg, &mut v);
        }
        entries.push(("mean_reversion", v));
    }
    if included("xsec") {
        let mut v = Vec::new();
        strategies::cross_sectional(&all, &cfg, &mut v);
        entries.push(("xsec_momentum", v));
    }

    println!(
        "\nstrategy tournament on {} symbols  (conservative costs, same data + split)\n  \
         fee={:.1}bps/side  slippage={:.1}bps  notional={} USDT  short={}  train_frac={}\n",
        all.len(), cfg.fee_bps, cfg.slippage_bps, cfg.notional, cfg.allow_short, cfg.train_frac
    );

    struct Row<'a> {
        name: &'a str,
        is_total: f64,
        os: bars::Stats,
        dd: f64,
        pos: usize,
        breadth: usize,
    }
    let mut rows: Vec<Row> = Vec::new();
    for (name, trades) in &entries {
        let train: Vec<&Trade> = trades.iter().filter(|t| t.entry_time < cutoff).collect();
        let test: Vec<&Trade> = trades.iter().filter(|t| t.entry_time >= cutoff).collect();
        let is = bars::summarize(&train, cfg.target_net_usdt);
        let os = bars::summarize(&test, cfg.target_net_usdt);
        let dd = bars::max_drawdown(&test);
        let mut by: std::collections::HashMap<&str, f64> = std::collections::HashMap::new();
        for t in &test {
            *by.entry(t.symbol.as_str()).or_insert(0.0) += t.net_pnl;
        }
        let pos = by.values().filter(|v| **v > 0.0).count();
        rows.push(Row { name, is_total: is.total_net, os, dd, pos, breadth: by.len() });
    }
    rows.sort_by(|a, b| b.os.total_net.partial_cmp(&a.os.total_net).unwrap());

    println!(
        "{:<20} {:>8} {:>13} {:>13} {:>7} {:>12} {:>10} {:>13}",
        "strategy", "OOStrds", "OOS net/tr", "OOS total", "win%", "OOS maxDD", "breadth", "IS total"
    );
    println!("(ranked by out-of-sample total net)\n{}", "-".repeat(100));
    for r in &rows {
        println!(
            "{:<20} {:>8} {:>+13.5} {:>+13.2} {:>6.1}% {:>12.2} {:>4}/{:<5} {:>+13.2}",
            r.name, r.os.n, r.os.net_per_trade, r.os.total_net, r.os.win_rate * 100.0,
            r.dd, r.pos, r.breadth, r.is_total
        );
    }
    // Walk-forward: split the whole timeline into equal segments and show each
    // strategy's net per segment. A real edge is positive across most segments;
    // a one-split fluke shows up as profit in only one or two.
    let walk: usize = flag_parse(flags, "walk", 8);
    if walk >= 2 && tmax > tmin {
        let width = ((tmax - tmin) as f64 / walk as f64).max(1.0);
        println!(
            "\nwalk-forward: net per segment ({walk} equal periods, ~{} days each) — consistency check",
            (width / 86_400_000.0) as i64
        );
        print!("{:<20}", "strategy");
        for s in 1..=walk {
            print!(" {:>8}", format!("s{s}"));
        }
        println!(" {:>7}", "#pos");
        println!("{}", "-".repeat(20 + walk * 9 + 8));
        for (name, trades) in &entries {
            let mut seg = vec![0.0f64; walk];
            for t in trades {
                let idx = (((t.entry_time - tmin) as f64 / width) as usize).min(walk - 1);
                seg[idx] += t.net_pnl;
            }
            let pos = seg.iter().filter(|v| **v > 0.0).count();
            print!("{:<20}", name);
            for v in &seg {
                print!(" {:>+8.0}", v);
            }
            println!(" {:>5}/{}", pos, walk);
        }
    }

    println!(
        "\nread: trust a winner only if its OOS total is positive, its IS total agrees (not\n\
         in-sample-only), breadth is broad, AND it's positive across most walk-forward\n\
         segments. Survivorship bias (today's symbols on past data) flatters all of these."
    );
    Ok(())
}

// ---- lighter (perp DEX) -----------------------------------------------------

fn run_lighter_universe(flags: &HashMap<String, String>) -> Result<()> {
    let out = flag(flags, "out").unwrap_or("data/lighter_markets.txt");
    let markets = rest::lighter_all_markets()?;
    if let Some(parent) = std::path::Path::new(out).parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let body: String = markets
        .iter()
        .map(|(id, s)| format!("{id} {s}"))
        .collect::<Vec<_>>()
        .join("\n");
    std::fs::write(out, body)?;
    println!("wrote {} lighter markets to {out}", markets.len());
    for (id, s) in markets.iter().take(25) {
        println!("  {id:>4}  {s}");
    }
    if markets.len() > 25 {
        println!("  ... +{} more", markets.len() - 25);
    }
    Ok(())
}

fn run_lighter_probe(flags: &HashMap<String, String>) -> Result<()> {
    let market: u32 = flag_parse(flags, "market", 1);
    let secs: u64 = flag_parse(flags, "secs", 15);
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(watchers::lighter::probe(market, secs))
}

// ---- higher-timeframe: universe / klines / bars backtest --------------------

fn run_universe(flags: &HashMap<String, String>) -> Result<()> {
    let n: usize = flag_parse(flags, "n", 150);
    let out = flag(flags, "out").unwrap_or("data/universe.txt");
    println!("fetching top {n} USDⓈ-M perps by 24h volume...");
    let symbols = rest::top_symbols_by_volume(n)?;
    if let Some(parent) = std::path::Path::new(out).parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    std::fs::write(out, symbols.join("\n"))?;
    println!("wrote {} symbols to {out}", symbols.len());
    for (i, s) in symbols.iter().take(20).enumerate() {
        println!("  {:>3}. {s}", i + 1);
    }
    if symbols.len() > 20 {
        println!("  ... +{} more", symbols.len() - 20);
    }
    Ok(())
}

fn run_fetch_klines(flags: &HashMap<String, String>) -> Result<()> {
    let interval = flag(flags, "interval").unwrap_or("1h").to_string();
    let months: i64 = flag_parse(flags, "months", 24);
    let out_dir = flag(flags, "out-dir").unwrap_or("data/klines").to_string();

    let symbols: Vec<String> = match flag(flags, "symbols-file") {
        Some(path) => std::fs::read_to_string(path)
            .with_context(|| format!("reading {path}"))?
            .lines()
            .map(|l| l.trim().to_string())
            .filter(|l| !l.is_empty())
            .collect(),
        None => {
            let top: usize = flag_parse(flags, "top", 150);
            println!("no --symbols-file; fetching top {top} by volume first...");
            rest::top_symbols_by_volume(top)?
        }
    };

    std::fs::create_dir_all(&out_dir)?;
    let start_ms = chrono::Utc::now().timestamp_millis() - months * 30 * 86_400_000;
    println!(
        "downloading {interval} klines for {} symbols, ~{months} months each -> {out_dir}/",
        symbols.len()
    );

    let mut ok = 0;
    for (i, sym) in symbols.iter().enumerate() {
        match rest::fetch_klines(sym, &interval, start_ms) {
            Ok(candles) if !candles.is_empty() => {
                let path = format!("{out_dir}/{sym}.ndjson");
                candles::write_candles(&path, &candles)?;
                ok += 1;
                println!("  [{:>3}/{}] {sym}: {} bars", i + 1, symbols.len(), candles.len());
            }
            Ok(_) => println!("  [{:>3}/{}] {sym}: no data, skipped", i + 1, symbols.len()),
            Err(e) => println!("  [{:>3}/{}] {sym}: error {e}", i + 1, symbols.len()),
        }
    }
    println!("done. {ok}/{} symbols saved to {out_dir}/", symbols.len());
    Ok(())
}

fn run_synth_klines(flags: &HashMap<String, String>) -> Result<()> {
    let out_dir = flag(flags, "out-dir").unwrap_or("data/klines").to_string();
    let n_symbols: usize = flag_parse(flags, "symbols", 12);
    let bars: usize = flag_parse(flags, "bars", 4000);
    let trendiness: f64 = flag_parse(flags, "trendiness", 0.5);
    std::fs::create_dir_all(&out_dir)?;

    let start = 1_700_000_000_000i64;
    let step = 3_600_000i64; // 1h
    for i in 0..n_symbols {
        let candles = synth::gen_candles(bars, start, step, 100.0, 1000 + i as u64, trendiness);
        let path = format!("{out_dir}/SYN{i:03}.ndjson");
        candles::write_candles(&path, &candles)?;
    }
    println!("wrote {n_symbols} synthetic symbols x {bars} bars (trendiness {trendiness}) to {out_dir}/");
    Ok(())
}

fn run_backtest_bars(flags: &HashMap<String, String>) -> Result<()> {
    let dir = flag(flags, "dir").unwrap_or("data/klines");
    let cfg = BarConfig {
        entry_lookback: flag_parse(flags, "entry", 20),
        exit_lookback: flag_parse(flags, "exit", 10),
        fee_bps: flag_parse(flags, "fee-bps", 5.0),
        slippage_bps: flag_parse(flags, "slippage-bps", 1.0),
        notional: flag_parse(flags, "notional", 1000.0),
        allow_short: !flags.contains_key("no-short"),
        train_frac: flag_parse(flags, "train-frac", 0.7),
        target_net_usdt: flag_parse(flags, "target", 0.02),
        stop_loss_pct: flag_parse(flags, "stop-loss-pct", 0.0),
        take_profit_pct: flag_parse(flags, "take-profit-pct", 0.0),
        trail_pct: flag_parse(flags, "trail-pct", 0.0),
    };

    let files = candles::list_symbol_files(dir)?;
    if files.is_empty() {
        bail!("no *.ndjson symbol files in {dir}");
    }

    let mut trades: Vec<Trade> = Vec::new();
    let mut symbols_traded = 0;
    for (sym, path) in &files {
        let candles = candles::read_candles(path)?;
        let before = trades.len();
        bars::run_symbol(sym, &candles, &cfg, &mut trades);
        if trades.len() > before {
            symbols_traded += 1;
        }
    }

    let mut riskbits: Vec<String> = Vec::new();
    if cfg.stop_loss_pct > 0.0 {
        riskbits.push(format!("stop={}%", cfg.stop_loss_pct));
    }
    if cfg.trail_pct > 0.0 {
        riskbits.push(format!("trail={}%", cfg.trail_pct));
    }
    if cfg.take_profit_pct > 0.0 {
        riskbits.push(format!("tp={}%", cfg.take_profit_pct));
    }
    let risk = if riskbits.is_empty() {
        "stops=off".to_string()
    } else {
        riskbits.join("  ")
    };
    println!(
        "\nDonchian {}/{} breakout on {} symbols ({} traded)\n  \
         fee={:.1}bps/side  slippage={:.1}bps  notional={} USDT  short={}  train_frac={}\n  {}\n",
        cfg.entry_lookback, cfg.exit_lookback, files.len(), symbols_traded,
        cfg.fee_bps, cfg.slippage_bps, cfg.notional, cfg.allow_short, cfg.train_frac, risk
    );

    if trades.is_empty() {
        println!("no trades triggered.");
        return Ok(());
    }

    let cutoff = bars::split_cutoff(&trades, cfg.train_frac);
    let train: Vec<&Trade> = trades.iter().filter(|t| t.entry_time < cutoff).collect();
    let test: Vec<&Trade> = trades.iter().filter(|t| t.entry_time >= cutoff).collect();
    let ts = bars::summarize(&train, cfg.target_net_usdt);
    let os = bars::summarize(&test, cfg.target_net_usdt);

    println!(
        "{:<14} {:>8} {:>16} {:>13} {:>13} {:>13} {:>9}",
        "period", "trades", "win rate", "net/trade", "median", "total net", "hit tgt"
    );
    println!("{}", "-".repeat(92));
    for (label, s) in [("in-sample", &ts), ("OUT-OF-SAMPLE", &os)] {
        println!(
            "{:<14} {:>8} {:>16} {:>+13.5} {:>+13.5} {:>+13.4} {:>8.1}%",
            label, s.n,
            format!("{:.1}% ({}/{})", s.win_rate * 100.0, s.wins, s.n),
            s.net_per_trade, s.median_net, s.total_net, s.hit_target_rate * 100.0
        );
    }

    // Breadth: how many symbols are net-positive out of sample (overfit check).
    use std::collections::HashMap as Map;
    let mut by_sym: Map<&str, f64> = Map::new();
    for t in &test {
        *by_sym.entry(t.symbol.as_str()).or_insert(0.0) += t.net_pnl;
    }
    let pos = by_sym.values().filter(|v| **v > 0.0).count();
    println!(
        "\nout-of-sample breadth: {pos}/{} symbols net-positive",
        by_sym.len()
    );

    // Monthly P&L + cumulative (equity curve) to expose regime dependence.
    use std::collections::BTreeMap;
    let mut sorted: Vec<&Trade> = trades.iter().collect();
    sorted.sort_by_key(|t| t.entry_time);
    let mut by_month: BTreeMap<String, (usize, f64, i64)> = BTreeMap::new();
    for t in &sorted {
        let key = chrono::DateTime::from_timestamp_millis(t.entry_time)
            .map(|d| d.format("%Y-%m").to_string())
            .unwrap_or_else(|| "????-??".into());
        let e = by_month.entry(key).or_insert((0, 0.0, t.entry_time));
        e.0 += 1;
        e.1 += t.net_pnl;
        e.2 = e.2.min(t.entry_time);
    }
    println!("\nmonthly P&L (cumulative column is the equity curve):");
    println!("{:<9} {:>8} {:>14} {:>16}", "month", "trades", "net", "cumulative");
    let mut cum = 0.0;
    for (mon, (cnt, net, min_ts)) in &by_month {
        cum += net;
        let tag = if *min_ts >= cutoff { "  <- out-of-sample" } else { "" };
        println!("{:<9} {:>8} {:>+14.2} {:>+16.2}{}", mon, cnt, net, cum, tag);
    }

    let verdict = if os.n < 30 {
        "INCONCLUSIVE — too few out-of-sample trades"
    } else if os.net_per_trade <= 0.0 {
        "NO EDGE out-of-sample"
    } else if ts.net_per_trade <= 0.0 {
        "REGIME-DEPENDENT — profitable out-of-sample but LOST in-sample; not a stable edge"
    } else if os.net_per_trade >= cfg.target_net_usdt {
        "edge positive in BOTH periods AND clears target"
    } else {
        "edge positive in both periods but below target"
    };
    println!("\nverdict: {verdict}");
    println!(
        "\nnote: this uses TODAY's liquid symbols on PAST data (survivorship bias) — \n\
         real-world results will be somewhat worse than shown."
    );
    Ok(())
}
