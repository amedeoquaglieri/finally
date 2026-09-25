# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools (not `--dev`)
```

Python 3.12 is pinned in `.python-version`, matching the Docker image.

## Market Data API

The market data subsystem lives in `app/market/`. Use these imports:

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source, normalize_ticker
```

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price`, `timestamp`, `previous_close`, plus properties `change`, `change_percent`, `direction` ("up"/"down"/"flat") — all tick-to-tick — and `day_change`, `day_change_percent` (against `previous_close`), and `to_dict()` for JSON serialization. Use `day_change_percent` for the watchlist's "daily change %"; use `direction` for price flashes.

- **`PriceCache`** — Thread-safe in-memory store. Key methods:
  - `update(ticker, price, timestamp=None, previous_close=None) -> PriceUpdate` — `previous_close` persists across updates once set; it defaults to the first price seen
  - `get(ticker) -> PriceUpdate | None`
  - `get_price(ticker) -> float | None`
  - `get_all() -> dict[str, PriceUpdate]`
  - `remove(ticker)`
  - `version` property — monotonic counter, increments on every update and removal (for SSE change detection)

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource` and `MassiveDataSource`. Lifecycle: `start(tickers)` -> `add_ticker()` / `remove_ticker()` -> `stop()`. Both sources normalize tickers with `normalize_ticker()` (strip + uppercase), and keep tickers added before `start()`. `start()` never blocks on the network.

- **`create_market_data_source(cache)`** — Factory. Returns `MassiveDataSource` if `MASSIVE_API_KEY` is set, otherwise `SimulatorDataSource`.

### Price reference for daily change

- **Massive:** the previous session's close (`prevDay.c` in the snapshot).
- **Simulator:** the price when the ticker was first added this session (its seed price for default tickers). Removing and re-adding a ticker resumes its last price and keeps this reference.

### Integration notes for the watchlist / portfolio routes

- Keep tracking tickers that have an open position even after they leave the watchlist (only call `source.remove_ticker()` when no position remains), or portfolio valuation and sells lose their price.
- In Massive mode a newly added ticker has no price until the next poll (up to `poll_interval`, default 15s); trade execution should return a clear 400 on a cache miss.

### SSE Streaming

```python
from app.market import create_stream_router

router = create_stream_router(price_cache)  # Returns a new FastAPI APIRouter per call
# Endpoint: GET /api/stream/prices (text/event-stream)
```

Each event is a full snapshot `{ticker: PriceUpdate.to_dict(), ...}` of all tracked tickers — replace client state with it, don't merge. `data: {}` means no tickers are tracked. Idle streams get a `: keepalive` comment every 15s (ignored by `EventSource`).

### Seed Data

Default tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices and per-ticker volatility/drift params are in `app/market/seed_prices.py`. Pass `seed=` to `GBMSimulator` / `SimulatorDataSource` for reproducible prices in tests.

## Running Tests

```bash
uv run --extra dev pytest -v              # All tests
uv run --extra dev pytest --cov=app       # With coverage
uv run --extra dev ruff check app/ tests/ # Lint
```

Massive tests use real SDK model objects (`TickerSnapshot.from_dict`) and one test drives the real SDK over HTTP against a local fake API — don't replace these with `MagicMock` snapshots, which hide attribute and URL bugs. Use `tests.helpers.wait_until` instead of fixed sleeps in async tests.

## Demo

```bash
uv run --extra dev market_data_demo.py   # Live terminal dashboard with simulated prices
```
