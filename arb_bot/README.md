# arb_bot

Multi-source confirmation arbitrage bot. Watches leader exchanges via WebSocket,
detects lead-lag price moves, and (later) executes on a slower trading venue.

This is **Phase 1**: project scaffold, async event bus, Binance bookTicker
watcher, SQLite event log, console subscriber. No signal logic, no orders.

## Layout

```
arb_bot/
├── Cargo.toml
├── config/
│   ├── config.toml              # symbols, db path, bus capacity
│   └── secrets.toml.example     # template for exchange API keys (Phase 5+)
├── src/
│   ├── main.rs                  # wires up bus, storage, watcher, subscriber
│   ├── events.rs                # Exchange, PriceEvent types
│   ├── bus.rs                   # broadcast-channel-based event bus
│   ├── config.rs                # TOML config loader
│   ├── storage.rs               # SQLite writer task
│   └── watchers/
│       ├── mod.rs
│       └── binance.rs           # Binance bookTicker stream client
└── data/                        # SQLite db lives here (gitignored)
```

## Running

```bash
cd arb_bot
cargo run --release
```

You should see lines like:

```
INFO config loaded
INFO sqlite writer started
INFO binance starting watcher symbol_count=10
INFO binance connected
INFO price exchange="binance" symbol="SOL/USDT" bid=148.21 ask=148.22 mid=148.215
```

Stop with Ctrl-C.

## Config

Edit `config/config.toml` to change the symbol list. Symbols are normalized as
`BASE/QUOTE` and Binance currently expects USDT/USDC/BUSD/FDUSD quotes.

Override config path with `ARB_BOT_CONFIG=/path/to/config.toml cargo run`.

## Inspecting the event log

```bash
sqlite3 data/arb_bot.db 'SELECT exchange, symbol, bid, ask, ts_ms FROM price_events ORDER BY id DESC LIMIT 20;'
```

## Architecture notes

The bus is a `tokio::sync::broadcast` channel. Every watcher publishes
`PriceEvent`s; every subscriber (console printer, SQLite writer, future signal
detector) sees every event. If a subscriber falls behind, it receives a
`Lagged(n)` error and skips ahead — useful signal that something is too slow.

## What's next

- Phase 2: Coinbase, OKX, Bybit, Bitget watchers + slow venue (MEXC/Gate)
- Phase 3: signal detector with multi-source confirmation
- Phase 4: paper-trade execution + P&L tracking
- Phase 5: live execution with risk manager and kill switch
