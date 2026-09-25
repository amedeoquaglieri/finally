# Market Data Backend — Summary

**Status:** Complete, tested and reviewed. All issues from the 2026-09-24 review (`planning/MARKET_DATA_REVIEW.md`) are resolved as of 2026-09-25. Both the simulator and the Massive (real data) paths are verified end to end.

## What Was Built

A complete market data subsystem in `backend/app/market/` (8 modules) providing live price simulation and real market data via a unified interface.

### Architecture

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  GBM simulator (default, no API key needed)
└── MassiveDataSource    →  Polygon.io REST poller (when MASSIVE_API_KEY set)
        │
        ▼
   PriceCache (thread-safe, in-memory, versioned)
        │
        ├──→ SSE stream endpoint (/api/stream/prices)
        ├──→ Portfolio valuation
        └──→ Trade execution
```

### Modules

| File | Purpose |
|------|---------|
| `models.py` | `PriceUpdate` — immutable frozen dataclass (ticker, price, previous_price, timestamp, previous_close; derived change, change_percent, direction, day_change, day_change_percent) |
| `interface.py` | `MarketDataSource` ABC (`start/stop/add_ticker/remove_ticker/get_tickers`) and `normalize_ticker()` |
| `cache.py` | `PriceCache` — thread-safe price store; version counter bumped on every update and removal |
| `seed_prices.py` | Realistic seed prices, per-ticker GBM params (drift/volatility), correlation groups |
| `simulator.py` | `GBMSimulator` (Geometric Brownian Motion with Cholesky-correlated moves, seedable) + `SimulatorDataSource` |
| `massive_client.py` | `MassiveDataSource` — non-blocking REST polling client for Polygon.io via the `massive` package |
| `factory.py` | `create_market_data_source()` — selects simulator or Massive based on `MASSIVE_API_KEY` env var |
| `stream.py` | `create_stream_router()` — FastAPI SSE endpoint factory (full snapshots, version-based change detection, keepalive) |

### Key Design Decisions

- **Strategy pattern** — both data sources implement the same ABC; downstream code is source-agnostic
- **PriceCache as single point of truth** — producers write, consumers read; no direct coupling
- **Full-snapshot SSE events** — each event carries every tracked ticker; clients replace state, so removals are visible (`data: {}` when empty)
- **Two kinds of change** — tick-to-tick (`change`, `direction`) for flash animations; daily (`day_change_percent`) against `previous_close` for the watchlist
- **GBM with correlated moves** — Cholesky decomposition of sector-based correlation matrix; tech stocks correlate at 0.6, finance at 0.5, cross-sector at 0.3
- **Random shock events** — ~0.1% chance per tick per ticker of a 2-5% move for visual drama (about one event every ~50s across 10 tickers)
- **Normalized tickers** — `"aapl"` and `"AAPL"` are the same ticker in both sources
- **SSE over WebSockets** — simpler, one-way push, universal browser support

## Test Suite

**120 tests, all passing** on Python 3.12 (pinned) and 3.14; 10 consecutive runs without a failure. 7 test modules in `backend/tests/market/`.

| Module | Tests | Covers |
|--------|-------|--------|
| test_models.py | 14 | `PriceUpdate` derived fields, daily change, serialization |
| test_cache.py | 20 | cache semantics, version on removal, `previous_close`, 8-thread concurrent writers |
| test_simulator.py | 25 | GBM statistics (volatility and correlations), shocks, seeding, all 10 default tickers, re-add |
| test_simulator_source.py | 14 | lifecycle, normalization, exception resilience, pre-start adds, daily-change reference |
| test_factory.py | 7 | env var selection |
| test_massive.py | 28 | parsing with real SDK models, non-blocking start, client config, and the real SDK over HTTP against a local fake API |
| test_stream.py | 12 | SSE events, removals, empty snapshot, keepalive, cancellation, router factory |

Coverage: 99% (every module 100% except `simulator.py` at 99%). `ruff check` and `ruff format --check` are clean.

## Review History

- **2026-02-10** (`planning/archive/MARKET_DATA_REVIEW.md`): 7 issues fixed — hatch build config, lazy imports, SSE return type, `GBMSimulator.get_tickers()`, correlation constants, unused imports, Massive test mocks.
- **2026-09-24/25** (`planning/MARKET_DATA_REVIEW.md`): found that Massive mode could never produce a price (wrong SDK attribute and time unit, and a wrong request URL from passing the SDK's enum), plus SSE removal, router, normalization, daily-change, startup and test-quality issues. All fixed; see the review's resolution log.

## Demo

A Rich terminal demo is available at `backend/market_data_demo.py`:

```bash
cd backend
uv run --extra dev market_data_demo.py
```

Displays a live-updating dashboard with all 10 tickers, sparklines, color-coded direction arrows, and an event log for notable price moves. Runs 60 seconds or until Ctrl+C.

## Usage for Downstream Code

```python
from app.market import PriceCache, create_market_data_source, create_stream_router

# Startup (FastAPI lifespan)
cache = PriceCache()
source = create_market_data_source(cache)  # Reads MASSIVE_API_KEY
await source.start(["AAPL", "GOOGL", "MSFT", ...])  # Returns immediately
app.include_router(create_stream_router(cache))     # GET /api/stream/prices

# Read prices
update = cache.get("AAPL")          # PriceUpdate or None
price = cache.get_price("AAPL")     # float or None (None → 400 on trade)
all_prices = cache.get_all()        # dict[str, PriceUpdate]
update.day_change_percent           # watchlist "daily change %"

# Dynamic watchlist (tickers are normalized; keep tickers with open positions tracked)
await source.add_ticker("tsla")
await source.remove_ticker("GOOGL")

# Shutdown
await source.stop()
```
