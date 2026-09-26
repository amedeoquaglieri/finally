# FinAlly Backend

FastAPI backend for the FinAlly AI Trading Workstation.

## Structure

- `app/` - Application code
  - `market/` - Market data subsystem
    - `models.py` - PriceUpdate dataclass
    - `cache.py` - Thread-safe price cache
    - `interface.py` - MarketDataSource abstract interface and `normalize_ticker()`
    - `simulator.py` - GBM-based market simulator
    - `massive_client.py` - Massive/Polygon.io API client
    - `factory.py` - Data source factory
    - `stream.py` - SSE streaming endpoint
    - `seed_prices.py` - Default ticker prices and parameters

- `tests/` - Unit and integration tests
  - `market/` - Market data tests
  - `helpers.py` - Shared test helpers

## Setup

Python 3.12 is pinned in `.python-version` (matching the Docker image); `uv` downloads it if needed.
Dev tools (pytest, ruff, rich for the demo) are a `dev` dependency group, which `uv sync` and
`uv run` install by default. Use `uv sync --no-dev` for a production install without them.

```bash
uv sync
```

## Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=app --cov-report=html

# Run specific test file
uv run pytest tests/market/test_simulator.py

# Run with verbose output
uv run pytest -v
```

## Environment Variables

- `MASSIVE_API_KEY` - Optional. If set, use real market data from Massive API. If not set, use the built-in simulator.

## Development

```bash
# Run linter
uv run ruff check .

# Format code
uv run ruff format .

# Live terminal demo of the simulator
uv run market_data_demo.py
```
