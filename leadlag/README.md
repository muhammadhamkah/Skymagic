# Lead-Lag Research Harness (MEXC)

A small, honest toolkit to test the thesis: *"if MEXC is truly 0-fee, a
lead-lag strategy is highly profitable."* It collects timestamped best-bid/ask
data, measures whether one symbol genuinely leads another, and checks whether
any signal survives **realistic round-trip cost** — not just fees.

## TL;DR finding

Fees are rarely the binding constraint. In a synthetic test with an
*extremely* strong planted lead-lag (correlation **0.857** at +500ms) the trade
**still loses money at 0% fees**, because the laggard's **bid-ask spread**
alone (≈4 bps) plus slippage exceeds what the signal can capture. "0 fees"
does not rescue a strategy that has to cross the spread on a thin book.

What still has to be true for a real edge, beyond zero fees:
1. The predicted move must beat **spread + slippage** (not just fees).
2. Your **latency** must be fast enough to act before the lag closes
   (sub-second; REST polling usually isn't fast enough — you'd need websockets
   and ideally colocation).
3. The book must be **deep enough** for your size without moving the price.
4. You must survive **adverse selection** — faster players arbitrage durable
   edges flat, so a clean backtest often models fills you wouldn't get.

## Files

| file | purpose |
|---|---|
| `mexc.py` | thin public-API client (no key needed): book ticker |
| `collect.py` | poll REST bookTicker at a fixed cadence → SQLite of timestamped ticks |
| `ws_collect.py` | **websocket** collector for ms-resolution ticks (protobuf) |
| `pbdecode.py` | dependency-free protobuf wire decoder used by `ws_collect.py` |
| `analyze.py` | cross-correlation lead-lag + conservative cost reality check |
| `event_study.py` | conditional "trade only when it clears the spread" + breakeven-latency sweep |
| `latency_probe.py` | measure your REAL latency to MEXC (RTT distribution + feed staleness) |

## Install

```bash
pip install -r requirements.txt
```

## Use

**1. Collect** (run locally — needs network access to `api.mexc.com`):

```bash
python collect.py --symbols BTCUSDT ETHUSDT SOLUSDT \
    --interval-ms 250 --duration-min 30 --db ticks.db
```

**1b. Collect at ms resolution (websocket)** — needed to evaluate low-latency:

```bash
# FIRST confirm the protobuf field numbers against live data:
python ws_collect.py --symbols BTCUSDT --raw 3
# then collect for real (adjust --field-* only if --raw showed different numbers):
python ws_collect.py --symbols BTCUSDT ETHUSDT SOLUSDT --duration-min 30 --db ticks_ws.db
```

> **Resolution caveat:** MEXC's *public* websocket aggregates updates — the
> book-ticker stream's floor is **100ms**, the deals stream's is **10ms**. So
> even by websocket you can resolve lead-lag to ~10–100ms, **not single-digit
> ms**. Sub-10ms structure is only visible on raw/colocated feeds retail does
> not receive. Interpret any "breakeven latency" with that floor in mind.

**2. Analyze** a leader → laggard pair:

```bash
python analyze.py --db ticks.db --leader BTCUSDT --laggard SOLUSDT \
    --grid-ms 250 --max-lag 20 --taker-bps 5
```

Set `--taker-bps 0` to model a truly 0-fee pair. If the verdict is still
"NO EDGE," fees were never the problem.

## How the analysis works

- Builds uniform-grid mid-price series for both symbols (shared local clock).
- Computes cross-correlation of log returns at lags `k = -max_lag..+max_lag`.
  `k > 0` means the laggard moves *after* the leader (the tradable direction).
- Compares a generous **predicted capture** (`|corr| × laggard 1-sigma`)
  against **round-trip cost** = laggard spread + taker fee + 2×slippage.

The cost model is an **upper bound on the good case** — it ignores latency,
queue position, and adverse selection, all of which only make reality worse.
If the signal can't beat costs even here, it definitely can't in production.

## Honest limitations

- REST polling resolution is bounded by network round-trip (~50–200ms). True
  sub-100ms lead-lag needs a websocket feed — a sensible next step.
- Local timestamps make this valid for *relative* lead-lag between symbols on
  the **same** exchange. Cross-exchange comparison needs clock-sync care.
- This measures *whether an edge exists*, not a full execution simulator.
