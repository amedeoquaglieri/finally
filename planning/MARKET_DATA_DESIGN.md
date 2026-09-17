# Market Data Backend — Design Document

This document is the detailed design for FinAlly's market data subsystem: how live prices are produced (simulated or real), cached, and streamed to the frontend. It reflects the implementation in `backend/app/market/`. For a short overview see `planning/MARKET_DATA_SUMMARY.md`.

## 1. Goals & Constraints

- One unified interface for price data, regardless of source (simulator vs. real API), so the rest of the backend (SSE stream, portfolio valuation, trade execution) never branches on which source is active.
- No external dependency required by default — the app must run with zero API keys via a built-in simulator.
- Real data is a drop-in swap, selected purely by the presence of `MASSIVE_API_KEY`.
- Single-writer, multi-reader in-memory cache — no database round-trip needed for a price read.
- Cheap to poll for change: the SSE endpoint must not re-serialize and re-send data when nothing changed.

## 2. Architecture

```
                     ┌─────────────────────────┐
                     │   MarketDataSource (ABC)│
                     └───────────┬─────────────┘
                 ┌───────────────┴────────────────┐
                 │                                 │
     ┌───────────▼───────────┐        ┌────────────▼────────────┐
     │  SimulatorDataSource   │        │   MassiveDataSource      │
     │  (GBM, 500ms ticks)    │        │   (Polygon.io REST poll) │
     └───────────┬───────────┘        └────────────┬────────────┘
                 │                                  │
                 └───────────────┬──────────────────┘
                                 │ writes
                         ┌───────▼────────┐
                         │   PriceCache    │  (thread-safe, versioned)
                         └───────┬────────┘
                                 │ reads
             ┌───────────────────┼───────────────────┐
             │                   │                   │
   ┌─────────▼────────┐ ┌────────▼─────────┐ ┌───────▼────────┐
   │ SSE /api/stream/  │ │ Portfolio         │ │ Trade execution │
   │ prices            │ │ valuation         │ │ (fill price)    │
   └───────────────────┘ └───────────────────┘ └────────────────┘
```

`create_market_data_source(cache)` is the only place that decides which concrete implementation to build; everything downstream depends only on the `MarketDataSource` ABC and the `PriceCache`.

## 3. Module Layout

```
backend/app/market/
├── __init__.py          # Public API re-exports
├── models.py             # PriceUpdate
├── interface.py          # MarketDataSource ABC
├── cache.py              # PriceCache
├── seed_prices.py        # Seed prices, GBM params, correlation groups
├── simulator.py          # GBMSimulator + SimulatorDataSource
├── massive_client.py     # MassiveDataSource (Polygon.io / Massive)
├── factory.py            # create_market_data_source()
└── stream.py             # create_stream_router() (SSE)
```

Public surface (`app/market/__init__.py`):

```python
from app.market import (
    PriceUpdate,
    PriceCache,
    MarketDataSource,
    create_market_data_source,
    create_stream_router,
)
```

## 4. The Data Model: `PriceUpdate`

An immutable, frozen dataclass — one instance represents "this ticker was at this price at this time, having previously been at that price."

```python
@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds

    @property
    def change(self) -> float:
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        if self.price > self.previous_price:
            return "up"
        elif self.price < self.previous_price:
            return "down"
        return "flat"

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
        }
```

Design notes:
- `frozen=True, slots=True` — cheap to construct on every tick (10 tickers × 2/sec), no accidental mutation shared across threads/readers.
- `change`/`change_percent`/`direction` are derived, not stored — one source of truth (`price`, `previous_price`), no risk of them drifting out of sync.
- `to_dict()` is the only serialization boundary; it's what gets JSON-encoded for SSE.

## 5. The Unified Interface: `MarketDataSource`

```python
class MarketDataSource(ABC):
    @abstractmethod
    async def start(self, tickers: list[str]) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None: ...

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None: ...

    @abstractmethod
    def get_tickers(self) -> list[str]: ...
```

Lifecycle contract, enforced by convention (not by the type system) and documented on the ABC:

```python
source = create_market_data_source(cache)
await source.start(["AAPL", "GOOGL", ...])   # call exactly once
await source.add_ticker("TSLA")               # dynamic watchlist changes
await source.remove_ticker("GOOGL")
await source.stop()                            # safe to call multiple times
```

Both concrete sources push into the cache on their own schedule; nothing ever pulls a price synchronously from the data source itself. This keeps `add_ticker`/`remove_ticker` cheap (they mutate internal state, the next tick/poll picks it up) and keeps request handlers non-blocking (`cache.get(ticker)` is an in-memory dict lookup under a lock, never a network call).

## 6. `PriceCache` — the Single Point of Truth

```python
class PriceCache:
    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version: int = 0

    def update(self, ticker: str, price: float, timestamp: float | None = None) -> PriceUpdate:
        with self._lock:
            ts = timestamp or time.time()
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price   # first tick: flat

            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                timestamp=ts,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None: ...
    def get_all(self) -> dict[str, PriceUpdate]: ...        # shallow copy
    def get_price(self, ticker: str) -> float | None: ...
    def remove(self, ticker: str) -> None: ...

    @property
    def version(self) -> int: ...                            # monotonic counter
```

Key design decisions:
- **`threading.Lock`, not `asyncio.Lock`.** The Massive poller does its network I/O on a worker thread (`asyncio.to_thread`) and writes from that thread; a plain asyncio lock wouldn't protect against cross-thread access. A regular `Lock` is safe from both the simulator's asyncio task and the poller's thread.
- **`version` counter** — bumped on every single `update()` call. The SSE loop polls this counter every 500ms instead of diffing the whole price dict; if nothing changed, `current_version == last_version` and no JSON is serialized or sent. This is the cheap building block that makes "don't send when nothing changed" trivial without a pub/sub system.
- **First update per ticker is `direction="flat"`** — `previous_price` defaults to the new price itself, so a freshly-added ticker doesn't flash red/green from an arbitrary baseline.
- **`get_all()` returns a copy** — callers (SSE generator, portfolio valuation) can iterate freely without holding the lock or racing a concurrent writer.

## 7. Simulator: Correlated GBM

### 7.1 Why GBM

Geometric Brownian Motion is the standard model for "prices that drift up over time but jitter randomly, and never go negative": `S(t+dt) = S(t) · exp((μ − σ²/2)·dt + σ·√dt·Z)`. It's cheap to compute per tick and parameterized by two intuitive knobs per ticker: `mu` (drift) and `sigma` (volatility).

### 7.2 Seed data (`seed_prices.py`)

```python
SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00,
    "TSLA": 250.00, "NVDA": 800.00, "META": 500.00, "JPM": 195.00,
    "V": 280.00, "NFLX": 600.00,
}

TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},   # high volatility
    "NVDA": {"sigma": 0.40, "mu": 0.08},   # high volatility, strong drift
    "JPM":  {"sigma": 0.18, "mu": 0.04},   # low volatility (bank)
    # ... one entry per default ticker
}
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}   # for tickers added later

CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech": {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}
INTRA_TECH_CORR = 0.6
INTRA_FINANCE_CORR = 0.5
CROSS_GROUP_CORR = 0.3
TSLA_CORR = 0.3   # TSLA moves somewhat independently even though it's "tech"
```

A ticker added dynamically (via watchlist / chat) that isn't in `TICKER_PARAMS` gets `DEFAULT_PARAMS` and a random seed price in `[50, 300]`, and is correlated at `CROSS_GROUP_CORR` with everything else.

### 7.3 Correlated moves via Cholesky decomposition

Independent GBM per ticker looks noisy and unrealistic — real tech stocks move together on macro news. To get correlated random shocks cheaply:

1. Build an `n × n` correlation matrix from the pairwise rule (`_pairwise_correlation`).
2. Compute its Cholesky factor `L` (`numpy.linalg.cholesky`), once, whenever the ticker set changes.
3. Each tick, draw `n` independent standard normals `Z_ind` and transform: `Z_corr = L @ Z_ind`. The result is `n` standard normals with the target correlation structure, one per ticker, reused directly as GBM's random term.

```python
def step(self) -> dict[str, float]:
    n = len(self._tickers)
    z_independent = np.random.standard_normal(n)
    z_correlated = self._cholesky @ z_independent if self._cholesky is not None else z_independent

    result: dict[str, float] = {}
    for i, ticker in enumerate(self._tickers):
        mu, sigma = self._params[ticker]["mu"], self._params[ticker]["sigma"]
        drift = (mu - 0.5 * sigma**2) * self._dt
        diffusion = sigma * math.sqrt(self._dt) * z_correlated[i]
        self._prices[ticker] *= math.exp(drift + diffusion)

        if random.random() < self._event_prob:          # ~0.1% per tick per ticker
            shock = random.uniform(0.02, 0.05) * random.choice([-1, 1])
            self._prices[ticker] *= 1 + shock

        result[ticker] = round(self._prices[ticker], 2)
    return result
```

`_rebuild_cholesky()` runs whenever a ticker is added or removed (O(n²) matrix build + decomposition, fine for `n < 50`), never on the hot per-tick path.

### 7.4 Time step sizing

`dt` is expressed as a fraction of a trading year so that `mu`/`sigma` can stay in familiar annualized units:

```python
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600          # 5,896,800 (252 days × 6.5h)
DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR           # ~8.48e-8, for 500ms ticks
```

At this `dt`, a single tick's expected move is sub-cent, which accumulates into realistic-looking intraday drift over minutes — occasionally punctuated by a random 2-5% "event" shock for visual drama (approximately one event per ticker per ~50 seconds at the default 0.1% probability and 2 ticks/sec).

### 7.5 `SimulatorDataSource` — wiring into the async runtime

```python
class SimulatorDataSource(MarketDataSource):
    def __init__(self, price_cache: PriceCache, update_interval: float = 0.5,
                 event_probability: float = 0.001) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(tickers=tickers, event_probability=self._event_prob)
        for ticker in tickers:                       # seed cache immediately
            self._cache.update(ticker=ticker, price=self._sim.get_price(ticker))
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker=ticker, price=price)
            except Exception:
                logger.exception("Simulator step failed")   # never let one bad tick kill the loop
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
```

Two details worth calling out:
- **Cache is seeded synchronously in `start()`**, before the loop task is even created — so a client connecting to the SSE stream immediately after startup sees prices on the very first payload instead of waiting up to 500ms for the first tick.
- **The loop swallows and logs exceptions per-iteration** rather than letting the background task die silently; a bug in one ticker's math shouldn't take down price streaming for all of them.

## 8. Real Data: `MassiveDataSource` (Polygon.io)

### 8.1 Why REST polling, not WebSockets

The Massive/Polygon WebSocket API requires a paid tier for real-time full-market data; REST snapshot polling works on every tier (including free) and is simple to reason about: one HTTP call returns a snapshot for every requested ticker.

```python
class MassiveDataSource(MarketDataSource):
    def __init__(self, api_key: str, price_cache: PriceCache, poll_interval: float = 15.0) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval          # 15s fits the free tier's 5 req/min
        self._tickers: list[str] = []
        self._client: RESTClient | None = None

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(tickers)
        await self._poll_once()                  # immediate first poll, cache populated before loop starts
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        if not self._tickers or not self._client:
            return
        try:
            snapshots = await asyncio.to_thread(self._fetch_snapshots)   # SDK is sync; don't block the loop
            for snap in snapshots:
                try:
                    self._cache.update(
                        ticker=snap.ticker,
                        price=snap.last_trade.price,
                        timestamp=snap.last_trade.timestamp / 1000.0,     # ms → s
                    )
                except (AttributeError, TypeError) as e:
                    logger.warning("Skipping snapshot for %s: %s", getattr(snap, "ticker", "???"), e)
        except Exception as e:
            logger.error("Massive poll failed: %s", e)
            # 401 (bad key), 429 (rate limit), network errors: log and retry next interval, never crash the poller

    def _fetch_snapshots(self) -> list:
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=self._tickers,
        )
```

### 8.2 Rate limits drive the poll interval

| Tier | Requests/min | Recommended poll interval |
|---|---|---|
| Free | 5 | 15s (default) |
| Paid | higher | 2–5s |

`poll_interval` is a constructor parameter specifically so this can be tuned per deployment without touching the polling logic.

### 8.3 Failure isolation

- A malformed individual snapshot (missing `last_trade`, unexpected type) is skipped with a warning — it doesn't abort the whole poll cycle, so one bad ticker doesn't blank out the other nine.
- A whole-poll failure (auth error, network blip, rate limit) is logged and the loop simply waits for the next interval and retries — no crash, no need for the caller to handle retries.
- The synchronous Polygon/Massive SDK call runs via `asyncio.to_thread` so a slow HTTP response never blocks the event loop (and therefore never blocks SSE delivery to other clients or other request handling).

### 8.4 Interface parity with the simulator

`add_ticker`/`remove_ticker` just mutate the in-memory `self._tickers` list — the next poll cycle (up to `poll_interval` seconds later) naturally picks up the change, mirroring the simulator's "next tick includes it" semantics. `remove_ticker` additionally calls `cache.remove(ticker)` immediately so a removed ticker disappears from the SSE stream right away rather than lingering with a stale last price for up to 15s.

## 9. Source Selection: `factory.py`

```python
def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        logger.info("Market data source: Massive API (real data)")
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)
    else:
        logger.info("Market data source: GBM Simulator")
        return SimulatorDataSource(price_cache=price_cache)
```

This is the *only* place in the codebase that inspects `MASSIVE_API_KEY` or imports both concrete classes. Application startup code (FastAPI lifespan) never needs to know which one it got:

```python
# app startup (FastAPI lifespan), illustrative
cache = PriceCache()
source = create_market_data_source(cache)
await source.start(await get_watchlist_tickers())

app.state.price_cache = cache
app.state.market_source = source

# shutdown
await source.stop()
```

## 10. SSE Streaming: `stream.py`

```python
def create_stream_router(price_cache: PriceCache) -> APIRouter:
    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",   # disable proxy buffering if deployed behind nginx
            },
        )
    return router


async def _generate_events(price_cache: PriceCache, request: Request, interval: float = 0.5):
    yield "retry: 1000\n\n"          # tell EventSource to reconnect after 1s if dropped

    last_version = -1
    try:
        while True:
            if await request.is_disconnected():
                break

            current_version = price_cache.version
            if current_version != last_version:        # skip the write entirely if nothing changed
                last_version = current_version
                prices = price_cache.get_all()
                if prices:
                    payload = json.dumps({t: u.to_dict() for t, u in prices.items()})
                    yield f"data: {payload}\n\n"

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass   # client disconnected mid-await; generator teardown
```

Design notes:
- **Batches all tickers into one SSE event** rather than one event per ticker — one JSON payload per tick is simpler for the frontend to consume (`JSON.parse` once, update N rows) and avoids N separate `EventSource` message dispatches per tick.
- **`request.is_disconnected()`** is polled each loop iteration so a client that closes the tab stops consuming a background task — otherwise idle generators would accumulate across reconnects.
- **`retry: 1000`** is part of the SSE protocol itself; `EventSource`'s automatic reconnect (no client code needed) will wait 1s and retry, which combined with the connection-status dot in the header (green/yellow/red) gives the resilience behavior called for in the plan.
- **Version check, not content diff** — cheap `int != int` comparison per loop iteration instead of comparing dict contents; the cache's `version` counter (bumped in `PriceCache.update`) is the only signal needed.

Wire-format example of one event:

```
data: {"AAPL": {"ticker": "AAPL", "price": 190.53, "previous_price": 190.41, "timestamp": 1737000000.123, "change": 0.12, "change_percent": 0.063, "direction": "up"}, "GOOGL": {...}}

```

## 11. Consumption Patterns Elsewhere in the Backend

```python
# Portfolio valuation
price = price_cache.get_price(position.ticker)
market_value = position.quantity * price if price is not None else None

# Trade execution (market order fill price)
update = price_cache.get(ticker)
if update is None:
    raise HTTPException(400, f"No price available for {ticker}")
fill_price = update.price
```

Both call sites are synchronous, lock-protected dict reads — no network I/O, no `await`, regardless of whether the simulator or Massive is the active source underneath.

## 12. Testing Approach

`backend/tests/market/` (73 tests, 84% coverage overall):

| Module | Focus |
|---|---|
| `test_models.py` | `PriceUpdate` derived properties (`change`, `change_percent`, `direction`), `to_dict()` shape |
| `test_cache.py` | Thread-safety under concurrent `update()`, version increments, `get`/`get_all`/`remove` semantics, first-update-is-flat |
| `test_simulator.py` | GBM math correctness, Cholesky correlation matrix construction, event-shock probability, add/remove ticker rebuilds correlation |
| `test_simulator_source.py` | `SimulatorDataSource` lifecycle (`start`/`stop`/`add_ticker`/`remove_ticker`) against a real `PriceCache`, integration-style |
| `test_factory.py` | Env var branching (`MASSIVE_API_KEY` set vs. unset/empty) selects the correct class |
| `test_massive.py` | REST client mocked; snapshot parsing, timestamp conversion, malformed-snapshot skip, poll-failure isolation |

Representative unit test shapes:

```python
def test_price_cache_first_update_is_flat():
    cache = PriceCache()
    update = cache.update("AAPL", 190.00)
    assert update.direction == "flat"
    assert update.previous_price == update.price

def test_price_cache_version_increments():
    cache = PriceCache()
    assert cache.version == 0
    cache.update("AAPL", 190.00)
    assert cache.version == 1

def test_factory_selects_massive_when_key_present(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
    source = create_market_data_source(PriceCache())
    assert isinstance(source, MassiveDataSource)

def test_factory_selects_simulator_when_key_absent(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    source = create_market_data_source(PriceCache())
    assert isinstance(source, SimulatorDataSource)
```

## 13. Extension Points

- **New data source**: implement `MarketDataSource`, add a branch in `factory.py`. Nothing else changes — SSE, portfolio, and trade code are unaware of the addition.
- **Different correlation model**: swap `_pairwise_correlation` / `CORRELATION_GROUPS` in `seed_prices.py`; `GBMSimulator` only depends on the resulting correlation matrix, not on how it's derived.
- **Sub-second SSE cadence**: `update_interval` (simulator) and the SSE loop's `interval` parameter are independent knobs; tightening either doesn't require touching `PriceCache`.
- **Multi-user**: `PriceCache` and both sources are already free of per-user state — a future multi-tenant version would key watchlists per user but could still share one global price cache, since prices aren't user-specific.
