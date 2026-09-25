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

An immutable, frozen dataclass — one instance represents "this ticker was at this price at this time, having previously been at that price, against this daily reference."

```python
@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds
    previous_close: float | None = None                   # daily-change reference

    @property
    def change(self) -> float:                 # tick-to-tick
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:         # tick-to-tick
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:                # "up" / "down" / "flat", drives flash animations
        ...

    @property
    def day_change(self) -> float | None:      # price - previous_close
        ...

    @property
    def day_change_percent(self) -> float | None:   # the watchlist's "daily change %"
        ...

    def to_dict(self) -> dict:
        return {
            "ticker", "price", "previous_price", "timestamp",
            "change", "change_percent", "direction",
            "previous_close", "day_change", "day_change_percent",
        }
```

Design notes:
- `frozen=True, slots=True` — cheap to construct on every tick (10 tickers × 2/sec), no accidental mutation shared across threads/readers.
- **Two kinds of change.** `change`/`change_percent`/`direction` compare with the previous tick (for flash animations). `day_change`/`day_change_percent` compare with `previous_close`: the previous session's close in Massive mode, or the price when the ticker was first added this session in simulator mode. `PriceCache` always sets `previous_close`.
- All change fields are derived, not stored — one source of truth, no risk of them drifting out of sync.
- `to_dict()` is the only serialization boundary; it's what gets JSON-encoded for SSE.

## 5. The Unified Interface: `MarketDataSource`

```python
def normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


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
await source.start(["AAPL", "GOOGL", ...])   # call exactly once; returns without network I/O
await source.add_ticker("tsla")               # dynamic watchlist changes; normalized to "TSLA"
await source.remove_ticker("GOOGL")
await source.stop()                            # safe to call multiple times
```

Both implementations:
- normalize every ticker with `normalize_ticker()` in `start`, `add_ticker` and `remove_ticker`, so `"aapl"` and `"AAPL"` are the same ticker whichever source is active;
- keep tickers added before `start()` and merge them into the start list.

Both concrete sources push into the cache on their own schedule; nothing ever pulls a price synchronously from the data source itself. This keeps `add_ticker`/`remove_ticker` cheap (they mutate internal state, the next tick/poll picks it up) and keeps request handlers non-blocking (`cache.get(ticker)` is an in-memory dict lookup under a lock, never a network call).

## 6. `PriceCache` — the Single Point of Truth

```python
class PriceCache:
    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version: int = 0

    def update(self, ticker: str, price: float, timestamp: float | None = None,
               previous_close: float | None = None) -> PriceUpdate:
        with self._lock:
            ts = time.time() if timestamp is None else timestamp
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price   # first tick: flat
            if previous_close is None:                        # keep the existing reference,
                previous_close = prev.previous_close if prev else price   # or the first price
            update = PriceUpdate(ticker=ticker, price=round(price, 2),
                                 previous_price=round(previous_price, 2), timestamp=ts,
                                 previous_close=round(previous_close, 2))
            self._prices[ticker] = update
            self._version += 1
            return update

    def remove(self, ticker: str) -> None:
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1            # removals reach SSE clients too

    def get(self, ticker: str) -> PriceUpdate | None: ...
    def get_all(self) -> dict[str, PriceUpdate]: ...        # shallow copy
    def get_price(self, ticker: str) -> float | None: ...

    @property
    def version(self) -> int: ...                            # monotonic counter, read under the lock
```

Key design decisions:
- **`threading.Lock`, not `asyncio.Lock`.** The Massive poller does its network I/O on a worker thread (`asyncio.to_thread`); a regular `Lock` is safe from both the simulator's asyncio task and any thread. A test hammers the cache from 8 threads and checks no update is lost.
- **`version` counter** — bumped on every `update()` call and on every `remove()` of a present ticker. The SSE loop polls this counter every 500ms instead of diffing the whole price dict; if nothing changed, no JSON is serialized or sent. This is the cheap building block that makes "don't send when nothing changed" trivial without a pub/sub system.
- **First update per ticker is `direction="flat"`** — `previous_price` defaults to the new price itself, so a freshly-added ticker doesn't flash red/green from an arbitrary baseline.
- **`previous_close` is sticky** — once set, later updates that omit it keep it, so the simulator loop and Massive polls without `prevDay` don't reset the daily change.
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

A ticker added dynamically (via watchlist / chat) that isn't in `TICKER_PARAMS` gets `DEFAULT_PARAMS` and a random seed price in `[50, 300]`, and is correlated at `CROSS_GROUP_CORR` with everything else. A ticker that was removed and is re-added **resumes its last price** (no jump back to the seed or to a new random price), so the valuation of a held position stays continuous.

### 7.3 Correlated moves via Cholesky decomposition

Independent GBM per ticker looks noisy and unrealistic — real tech stocks move together on macro news. To get correlated random shocks cheaply:

1. Build an `n × n` correlation matrix from the pairwise rule (`_pairwise_correlation`).
2. Compute its Cholesky factor `L` (`numpy.linalg.cholesky`), once, whenever the ticker set changes.
3. Each tick, draw `n` independent standard normals `Z_ind` and transform: `Z_corr = L @ Z_ind`. The result is `n` standard normals with the target correlation structure, one per ticker, reused directly as GBM's random term.

```python
def step(self) -> dict[str, float]:
    n = len(self._tickers)
    z_independent = self._rng.standard_normal(n)       # one seedable numpy Generator for all randomness
    z_correlated = self._cholesky @ z_independent if self._cholesky is not None else z_independent

    result: dict[str, float] = {}
    for i, ticker in enumerate(self._tickers):
        mu, sigma = self._params[ticker]["mu"], self._params[ticker]["sigma"]
        drift = (mu - 0.5 * sigma**2) * self._dt
        diffusion = sigma * math.sqrt(self._dt) * float(z_correlated[i])
        self._prices[ticker] *= math.exp(drift + diffusion)

        if self._rng.random() < self._event_prob:           # ~0.1% per tick per ticker
            shock = self._rng.uniform(0.02, 0.05) * (1 if self._rng.random() < 0.5 else -1)
            self._prices[ticker] *= 1 + shock

        result[ticker] = round(self._prices[ticker], 2)
    return result
```

`_rebuild_cholesky()` runs whenever a ticker is added or removed (O(n²) matrix build + decomposition, fine for `n < 50`), never on the hot per-tick path. Tests confirm the decomposition for all 10 default tickers, and that 20,000 seeded steps reproduce the configured per-tick volatility (±5%) and correlations (AAPL–GOOGL ≈ 0.6, AAPL–TSLA ≈ 0.3).

Pass `seed=` to `GBMSimulator` or `SimulatorDataSource` for reproducible price paths (tests, demos).

### 7.4 Time step sizing

`dt` is expressed as a fraction of a trading year so that `mu`/`sigma` can stay in familiar annualized units:

```python
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600          # 5,896,800 (252 days × 6.5h)
DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR           # ~8.48e-8, for 500ms ticks
```

At this `dt`, a single tick's expected move is about 0.006% (around 1¢ on AAPL), so roughly 30% of ticks round to an unchanged price; the 1-σ move over 10 minutes is only about 0.2%. Random 2-5% "event" shocks supply most of the visible drama: at the default 0.1% probability and 2 ticks/sec, each ticker gets an event about every ~500 seconds, i.e. about one event across all 10 tickers every ~50 seconds.

### 7.5 `SimulatorDataSource` — wiring into the async runtime

```python
class SimulatorDataSource(MarketDataSource):
    def __init__(self, price_cache: PriceCache, update_interval: float = 0.5,
                 event_probability: float = 0.001, seed: int | None = None) -> None:
        ...
        self._pending: list[str] = []          # tickers added before start()

    async def start(self, tickers: list[str]) -> None:
        tickers = list(dict.fromkeys(normalize_ticker(t) for t in [*tickers, *self._pending]))
        self._sim = GBMSimulator(tickers=tickers, event_probability=..., seed=...)
        for ticker in tickers:                  # seed cache immediately, with the session-open reference
            self._cache.update(ticker, self._sim.get_price(ticker),
                               previous_close=self._sim.get_open_price(ticker))
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        ...                                     # no-op (and no cache write) if already tracked

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker=ticker, price=price)
            except Exception:
                logger.exception("Simulator step failed")   # never let one bad tick kill the loop
            await asyncio.sleep(self._interval)
```

Details worth calling out:
- **Cache is seeded synchronously in `start()`/`add_ticker()`**, so a client connecting to the SSE stream immediately after startup sees prices on the very first payload.
- **Daily-change reference** is the price when the ticker was first added this session (`GBMSimulator.get_open_price`). It survives remove + re-add, so re-adding a ticker doesn't reset its daily change.
- **The loop swallows and logs exceptions per-iteration** rather than letting the background task die silently; a test injects failing steps and checks prices keep flowing.

## 8. Real Data: `MassiveDataSource` (Polygon.io)

### 8.1 Why REST polling, not WebSockets

The Massive/Polygon WebSocket API requires a paid tier for real-time full-market data; REST snapshot polling works on every tier (including free) and is simple to reason about: one HTTP call returns a snapshot for every requested ticker.

```python
CLIENT_RETRIES = 0      # the poll loop already retries every interval
CLIENT_TIMEOUT = 5.0


class MassiveDataSource(MarketDataSource):
    def __init__(self, api_key: str, price_cache: PriceCache, poll_interval: float = 15.0) -> None:
        ...                                     # 15s fits the free tier's 5 req/min

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key, connect_timeout=CLIENT_TIMEOUT,
                                  read_timeout=CLIENT_TIMEOUT, retries=CLIENT_RETRIES)
        self._tickers = list(dict.fromkeys(normalize_ticker(t) for t in [*tickers, *self._tickers]))
        # Poll in the background: a slow or failing API never delays app startup
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")

    async def _poll_loop(self) -> None:
        while True:
            await self._poll_once()             # first poll immediately
            await asyncio.sleep(self._interval)

    async def _poll_once(self) -> None:
        if not self._tickers or not self._client:
            return
        try:
            # SDK is sync: run in a thread; pass a copy since add_ticker() may mutate the list
            snapshots = await asyncio.to_thread(self._fetch_snapshots, list(self._tickers))
            for snap in snapshots:
                try:
                    price, timestamp = self._extract_price(snap)
                    if price is None:
                        continue                            # logged and skipped
                    self._cache.update(ticker=snap.ticker, price=price, timestamp=timestamp,
                                       previous_close=self._extract_previous_close(snap))
                except (AttributeError, TypeError) as e:
                    logger.warning("Skipping snapshot for %s: %s", getattr(snap, "ticker", "???"), e)
        except Exception as e:
            logger.error("Massive poll failed: %s", e)   # 401, 429, network: retry next interval

    def _fetch_snapshots(self, tickers: list[str]) -> list[TickerSnapshot]:
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS.value,   # the string "stocks" — see 8.4
            tickers=tickers,
        )
```

### 8.2 Rate limits drive the poll interval

| Tier | Requests/min | Recommended poll interval |
|---|---|---|
| Free | 5 | 15s (default) |
| Paid | higher | 2–5s |

`poll_interval` is a constructor parameter specifically so this can be tuned per deployment without touching the polling logic. SDK-level retries are disabled: the SDK would otherwise retry 429s up to 3 times (honouring `Retry-After`), which only delays a failing poll and spends more of the free tier's quota.

### 8.3 Failure isolation

- A malformed individual snapshot (missing price, unexpected type) is skipped with a warning — it doesn't abort the whole poll cycle, so one bad ticker doesn't blank out the other nine.
- A whole-poll failure (auth error, network blip, rate limit) is logged and the loop simply waits for the next interval and retries — no crash, no need for the caller to handle retries.
- `start()` does no network I/O, so a slow or unreachable API cannot delay FastAPI startup or the health check. Until the first poll lands, the cache is empty for Massive tickers; trade execution returns a 400 on a cache miss.
- The synchronous SDK call runs via `asyncio.to_thread` so a slow HTTP response never blocks the event loop (and therefore never blocks SSE delivery or other request handling).

### 8.4 Reading the SDK correctly (verified against `massive` 2.2.0)

- **Price:** `snap.last_trade.price`, falling back to `snap.day.close` when there is no trade yet (snapshots are cleared at midnight ET and repopulate from ~4am; a zero close counts as no price).
- **Timestamp:** `snap.last_trade.sip_timestamp`, falling back to `snap.updated` — both Unix **nanoseconds**, so divide by `1e9`. `LastTrade` has **no** `timestamp` attribute.
- **Daily-change reference:** `snap.prev_day.close` (API field `prevDay.c`).
- **Market type:** pass `SnapshotMarketType.STOCKS.value` (the string `"stocks"`), **not** the enum member. `SnapshotMarketType` is a plain `Enum`, and `get_snapshot_all` formats the URL from it and compares it to `"stocks"` to pick the locale; the member produces `/v2/snapshot/locale/global/markets/SnapshotMarketType.STOCKS/tickers`.

Tests build snapshots with `TickerSnapshot.from_dict(...)` rather than `MagicMock`, and one test drives the real SDK over HTTP against a local fake API that only answers `/v2/snapshot/locale/us/markets/stocks/tickers` — so attribute, unit and URL mistakes all fail loudly.

### 8.5 Interface parity with the simulator

`add_ticker`/`remove_ticker` normalize the ticker and mutate the in-memory `self._tickers` list — the next poll cycle (up to `poll_interval` seconds later) naturally picks up the change, mirroring the simulator's "next tick includes it" semantics. `remove_ticker` additionally calls `cache.remove(ticker)` immediately so a removed ticker disappears from the SSE stream right away rather than lingering with a stale last price for up to 15s.

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
await source.start(await get_watchlist_tickers())   # plus tickers with open positions
app.include_router(create_stream_router(cache))

app.state.price_cache = cache
app.state.market_source = source

# shutdown
await source.stop()
```

## 10. SSE Streaming: `stream.py`

```python
HEARTBEAT_INTERVAL = 15.0


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["streaming"])   # new router per call

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


async def _generate_events(price_cache, request, interval=0.5, heartbeat_interval=HEARTBEAT_INTERVAL):
    yield "retry: 1000\n\n"          # tell EventSource to reconnect after 1s if dropped

    last_version = -1
    last_sent = time.monotonic()
    try:
        while True:
            if await request.is_disconnected():
                break

            current_version = price_cache.version
            if current_version != last_version:        # skip the write entirely if nothing changed
                last_version = current_version
                prices = price_cache.get_all()          # sent even when empty: `data: {}`
                payload = json.dumps({t: u.to_dict() for t, u in prices.items()})
                yield f"data: {payload}\n\n"
                last_sent = time.monotonic()
            elif time.monotonic() - last_sent >= heartbeat_interval:
                yield ": keepalive\n\n"                 # SSE comment; EventSource ignores it
                last_sent = time.monotonic()

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled")
        raise                                           # never swallow cancellation
```

Design notes:
- **Batches all tickers into one SSE event** rather than one event per ticker — one JSON payload per tick is simpler for the frontend to consume (`JSON.parse` once, update N rows) and avoids N separate `EventSource` message dispatches per tick.
- **Every event is a full snapshot.** Clients replace their state with it rather than merging: a ticker missing from an event has been removed, and `data: {}` means nothing is tracked.
- **A new `APIRouter` per `create_stream_router()` call**, so separate apps (e.g. one per test) never share or shadow each other's routes.
- **Keepalive comments** every 15s on idle streams (between Massive polls, or with an empty watchlist) stop proxies with idle timeouts from closing the connection.
- **`request.is_disconnected()`** is polled each loop iteration so a client that closes the tab stops consuming a background task — otherwise idle generators would accumulate across reconnects.
- **`retry: 1000`** is part of the SSE protocol itself; `EventSource`'s automatic reconnect (no client code needed) will wait 1s and retry, which combined with the connection-status dot in the header (green/yellow/red) gives the resilience behavior called for in the plan.
- **Version check, not content diff** — cheap `int != int` comparison per loop iteration instead of comparing dict contents; the cache's `version` counter (bumped in `PriceCache.update` and `PriceCache.remove`) is the only signal needed.

Wire-format example of one event:

```
data: {"AAPL": {"ticker": "AAPL", "price": 190.53, "previous_price": 190.41, "timestamp": 1737000000.123, "change": 0.12, "change_percent": 0.063, "direction": "up", "previous_close": 190.0, "day_change": 0.53, "day_change_percent": 0.2789}, "GOOGL": {...}}

```

## 11. Consumption Patterns Elsewhere in the Backend

```python
# Portfolio valuation
price = price_cache.get_price(position.ticker)
market_value = position.quantity * price if price is not None else None

# Trade execution (market order fill price)
update = price_cache.get(normalize_ticker(ticker))
if update is None:
    raise HTTPException(400, f"No price available for {ticker}")
fill_price = update.price

# Watchlist removal: keep pricing tickers the user still holds
await db.delete_watchlist_entry(ticker)
if not await db.has_open_position(ticker):
    await source.remove_ticker(ticker)
```

All call sites are synchronous, lock-protected dict reads (plus the source call) — no network I/O, regardless of whether the simulator or Massive is the active source underneath.

## 12. Testing Approach

`backend/tests/market/` — 120 tests, 99% coverage, passing on Python 3.12 and 3.14:

| Module | Focus |
|---|---|
| `test_models.py` | `PriceUpdate` tick and daily change properties, `to_dict()` shape |
| `test_cache.py` | Version increments on update and removal, `previous_close` semantics, `get`/`get_all`/`remove`, first-update-is-flat, 8-thread concurrent writers |
| `test_simulator.py` | Seeded reproducibility, GBM volatility and correlation statistics, shocks, all 10 default tickers, re-add resumes price |
| `test_simulator_source.py` | `SimulatorDataSource` lifecycle against a real `PriceCache`, normalization, pre-start adds, injected step failures, daily-change reference |
| `test_factory.py` | Env var branching (`MASSIVE_API_KEY` set vs. unset/empty/whitespace) selects the correct class |
| `test_massive.py` | Snapshot parsing with real SDK models, fallbacks, non-blocking start, client config, and the real SDK over HTTP against a local fake API |
| `test_stream.py` | SSE generator (snapshots, removals, empty snapshot, keepalive, cancellation) and the router factory |

Async tests wait with `tests.helpers.wait_until(condition)` instead of fixed sleeps, so they're fast and tolerant of coarse OS timers.

Representative unit test shapes:

```python
def test_price_cache_first_update_is_flat():
    cache = PriceCache()
    update = cache.update("AAPL", 190.00)
    assert update.direction == "flat"
    assert update.previous_price == update.price

def test_remove_bumps_version():
    cache = PriceCache()
    cache.update("AAPL", 190.00)
    v = cache.version
    cache.remove("AAPL")
    assert cache.version == v + 1

def test_factory_selects_massive_when_key_present(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
    source = create_market_data_source(PriceCache())
    assert isinstance(source, MassiveDataSource)
```

## 13. Extension Points

- **New data source**: implement `MarketDataSource`, add a branch in `factory.py`. Nothing else changes — SSE, portfolio, and trade code are unaware of the addition.
- **Different correlation model**: swap `_pairwise_correlation` / `CORRELATION_GROUPS` in `seed_prices.py`; `GBMSimulator` only depends on the resulting correlation matrix, not on how it's derived.
- **Livelier demo**: pass a larger `dt` to `GBMSimulator` (e.g. ×10–×50) to speed up simulated time without changing the model.
- **Sub-second SSE cadence**: `update_interval` (simulator) and the SSE loop's `interval` parameter are independent knobs; tightening either doesn't require touching `PriceCache`.
- **Multi-user**: `PriceCache` and both sources are already free of per-user state — a future multi-tenant version would key watchlists per user but could still share one global price cache, since prices aren't user-specific.
