# Market Data Backend — Code Review

**Date:** 2026-09-24
**Scope:** `backend/app/market/` (8 modules), `backend/tests/` (6 test modules plus `conftest.py`), `backend/pyproject.toml`, `backend/README.md`, `backend/CLAUDE.md`, `backend/market_data_demo.py`, and the market data docs in `planning/` and `planning/archive/`.
**Previous review:** `planning/archive/MARKET_DATA_REVIEW.md` (2026-02-10). Section 7 shows which of its items were fixed.

> **Update 2026-09-25:** **All findings are resolved**, including a second critical Massive bug (C2) that turned up during the fix work. The backend now has 120 tests at 99% coverage. See section 10 for the resolution log; sections 1–9 are kept as the original review.

---

## 1. Executive Summary

The subsystem is well designed. The strategy-pattern interface, the versioned thread-safe cache, the correlated GBM simulator and the SSE endpoint are clean and easy to follow. All 73 tests pass, lint is clean, and the simulator path works end to end: I ran it under uvicorn and received live SSE events.

However, the review found one **critical defect that the test suite cannot catch**: in Massive (real data) mode, every snapshot is rejected, so the price cache stays empty. The tests pass only because they build snapshots with `MagicMock`, which accepts any attribute name. The review also found a group of medium-severity integration issues. These will affect the watchlist, portfolio and frontend work that comes next.

| Area | Verdict |
|---|---|
| Simulator path (default) | ✅ Ready for integration |
| Massive path (`MASSIVE_API_KEY` set) | ❌ **Broken.** No prices are ever written to the cache |
| SSE endpoint | ⚠️ Works, but ticker removals never reach connected clients, and the router factory is unsafe to call twice |
| Tests | ⚠️ All pass, but mocks hide the critical bug; `stream.py` has 33% coverage and some tests assert nothing |
| Docs | ⚠️ Some drift: README install command is wrong, stale coverage numbers, wrong timestamp units and event rate |

**Fix before starting downstream integration:** issues C1, M1, M2 and M3 (section 4).

---

## 2. Test, Coverage and Lint Results

Environment: `uv sync --extra dev`, CPython 3.14.4 (see L6: no Python version is pinned), pytest 9.0.2, pytest-asyncio 1.3.0, massive 2.2.0.

```
uv run --extra dev pytest -v --cov=app --cov-report=term-missing
======================= 73 passed, 74 warnings in 7.92s ========================
```

- **73/73 pass.** I ran the suite 5 more times to check for flakiness: 73/73 each time, about 1.3 s per run on Linux.
- **74 warnings.** 73 are `DeprecationWarning: 'asyncio.DefaultEventLoopPolicy' is deprecated`, one per test, from the unneeded `event_loop_policy` fixture in `tests/conftest.py`. The 74th is a sandbox permission warning about `.pytest_cache` and is not a code issue.

| Module | Stmts | Miss | Cover | Uncovered |
|---|---|---|---|---|
| `models.py` | 26 | 0 | 100% | |
| `cache.py` | 39 | 0 | 100% | |
| `interface.py` | 13 | 0 | 100% | |
| `seed_prices.py` | 8 | 0 | 100% | |
| `factory.py` | 15 | 0 | 100% | |
| `simulator.py` | 139 | 3 | 98% | L149 (duplicate guard in `_add_ticker_internal`), L268-269 (exception handler in `_run_loop`) |
| `massive_client.py` | 67 | 4 | 94% | L85-87 (`_poll_loop`), L125 (`_fetch_snapshots`) |
| `stream.py` | 36 | 24 | **33%** | The whole route handler and generator body |
| **Total** | 349 | 31 | **91%** | |

**Lint:**
- `ruff check app/ tests/ market_data_demo.py`: **All checks passed.**
- `ruff format --check`: **3 files would be reformatted**: `tests/market/test_models.py`, `test_simulator.py` and `test_simulator_source.py`.

**Manual verification beyond the test suite:**

| Check | Result |
|---|---|
| SSE endpoint under real uvicorn (simulator) | ✅ `retry: 1000` first, then a `data:` event about every 500 ms. Headers are correct (`text/event-stream`, `no-cache`, `X-Accel-Buffering: no`) |
| `MassiveDataSource._poll_once` fed a **real** `massive.rest.models.TickerSnapshot` | ❌ Snapshot skipped: `'LastTrade' object has no attribute 'timestamp'`. Cache stays empty |
| Removing tickers while an SSE client is connected | ❌ The client never receives the removal (see M1) |
| Calling `create_stream_router()` twice | ❌ Duplicate `/api/stream/prices` route, bound to the first cache (see M2) |
| Cholesky decomposition with 10 default tickers plus 60 unknown ones | ✅ Succeeds; minimum eigenvalue of the correlation matrix is 0.4 |
| `market_data_demo.py` | ✅ Starts and runs |
| README's `uv sync --dev` (dry run) | ❌ Would **uninstall** pytest, pytest-asyncio, pytest-cov and ruff (see L5) |

---

## 3. Architecture Assessment

The design matches `planning/PLAN.md` §6 and `planning/MARKET_DATA_DESIGN.md`:

```
MarketDataSource (ABC) ─┬─ SimulatorDataSource (GBM, 500 ms)
                        └─ MassiveDataSource   (REST poll, 15 s)
                                  │ writes
                            PriceCache (Lock + version counter)
                                  │ reads
                SSE /api/stream/prices · portfolio valuation · trade fills
```

**Strengths**
- **Clear separation of concerns.** Each module has one job, and the only place the source is chosen is `factory.py`.
- **Push model.** Sources write to the cache and consumers read it, so request handlers never wait on network I/O.
- **Immutable `PriceUpdate`** (`frozen=True, slots=True`). `change`, `change_percent` and `direction` are derived from the stored prices, so they can't drift out of sync.
- **Correct GBM math.** `S·exp((μ − σ²/2)dt + σ√dt·Z)` with `dt = 0.5 / (252·6.5·3600)`. Prices are always positive, which the tests confirm over 10,000 steps.
- **Cholesky-correlated shocks.** The sector correlation structure (tech 0.6, finance 0.5, TSLA and cross-sector 0.3) is positive definite, and the factor is rebuilt only when tickers change, never on every tick.
- **Resilient background loops.** Simulator step failures and Massive poll failures are logged and the loop keeps going. `stop()` is idempotent and awaits the cancelled task.
- **`threading.Lock`** is the right choice, because the Massive SDK runs in `asyncio.to_thread`.
- **Cache seeded at `start()`/`add_ticker()`**, so the first SSE payload already has prices.
- **Cheap SSE change detection** by comparing the version counter instead of the dict contents.

---

## 4. Findings

Severity levels: **Critical** means a feature does not work. **Medium** means incorrect behaviour that downstream work will hit. **Low** means robustness, code quality or maintainability.

### C1 — Critical: Massive mode never writes a price (wrong SDK attribute and wrong time unit)

`massive_client.py:101-107`
```python
price = snap.last_trade.price
timestamp = snap.last_trade.timestamp / 1000.0   # ← attribute does not exist
```
In the installed SDK (massive 2.2.0), `massive.rest.models.trades.LastTrade` has **no `timestamp` field**. The trade time is `sip_timestamp`, mapped from the API's `lastTrade.t`. The resulting `AttributeError` is caught by the per-snapshot `except (AttributeError, TypeError)`, so **every ticker is skipped with a warning on every poll**. The cache stays empty, SSE sends nothing, and trades fail with "no price". Reproduced with `TickerSnapshot.from_dict(...)`:

```
WARNING app.market.massive_client: Skipping snapshot for AAPL: 'LastTrade' object has no attribute 'timestamp'
cache after poll with REAL SDK object: None
```

**The unit is also wrong.** The snapshot endpoint's `lastTrade.t` is a **nanosecond** SIP timestamp, not milliseconds; only the minute bar `min.t` is in milliseconds. Fixing just the attribute name would still give timestamps around 1.7×10¹⁵ s. `planning/MARKET_DATA_DESIGN.md` §8.1 and `planning/archive/MASSIVE_API.md` repeat the "milliseconds" assumption. Confirm the unit once against a live response.

**Why the tests miss it:** `_make_snapshot()` in `tests/market/test_massive.py:11-18` builds snapshots from `MagicMock`, which creates any attribute you ask for. The tests therefore check the code against itself, not against the SDK.

**Suggested fix:**
```python
trade = snap.last_trade
price = trade.price if trade else None
if price is None:                      # e.g. pre-market after the midnight reset
    price = snap.day.close if snap.day and snap.day.close else None
if price is None:
    logger.warning("No price in snapshot for %s", snap.ticker); continue
ts_ns = (trade.sip_timestamp if trade else None) or snap.updated
timestamp = ts_ns / 1e9 if ts_ns else None
```
Also, build test fixtures with `TickerSnapshot.from_dict({...})` (or `MagicMock(spec=TickerSnapshot)`) so attribute typos fail the tests.

### C2 — Critical (found 2026-09-25 while fixing C1): Massive requests go to the wrong URL

`massive_client.py` called `get_snapshot_all(market_type=SnapshotMarketType.STOCKS, ...)`, as the SDK's type hints and the planning docs suggest. In `massive` 2.2.0, `SnapshotMarketType` is a plain `Enum` (not a `str` enum), and the SDK:
- builds the URL with `f"/v2/snapshot/locale/{locale}/markets/{market_type}/tickers"`, which formats the member as `SnapshotMarketType.STOCKS`;
- picks the locale with `market_type == "stocks"`, which is `False` for the member, so the locale becomes `global`.

The request therefore went to `/v2/snapshot/locale/global/markets/SnapshotMarketType.STOCKS/tickers`, so **even with C1 fixed, every poll would fail** against the real API. This only showed up once the real SDK was driven over HTTP against a local fake API: every mock-based test had passed.

**Fix:** pass `SnapshotMarketType.STOCKS.value` (the string `"stocks"`). The test suite now includes an HTTP-level test that fails on the wrong path.

### M1 — Medium: ticker removals are never sent to connected SSE clients

- `cache.py:59-62`: `remove()` does not bump `_version`.
- `stream.py:80`: `if prices:` skips the send when the cache is empty.

Result: after a removal, clients keep showing the removed ticker until some other ticker's price changes. In Massive mode that is up to 15 s. If the watchlist becomes empty, the stale rows stay **forever**. Verified live: an SSE client connected, then all 3 tickers were removed. No further event arrived, and the client's last event still listed `['AAPL', 'GOOGL', 'TSLA']`.

This hits PLAN.md's "Add and remove a ticker from the watchlist" E2E scenario if the frontend renders rows from the stream.

**Fix:** bump `_version` in `remove()` when a key was actually removed, and send `data: {}` when the cache is empty. Also document in the SSE contract that **each event is a full snapshot**, so the frontend replaces its state rather than merging into it.

### M2 — Medium: the module-level `router` makes `create_stream_router()` unsafe to call twice

`stream.py:17` creates `router` at import time, and every call to `create_stream_router()` adds another `/prices` route to that same object. Verified: after two calls the router has `['/api/stream/prices', '/api/stream/prices']`, and a new app built from the second call serves the route bound to the **first** cache, because the first matching route wins.

This will affect the upcoming API route tests, which will typically build a fresh app or cache per test: they would silently stream a previous test's cache. The previous review raised this as item 3.6, and it was not fixed.

**Fix:** move `router = APIRouter(prefix="/api/stream", tags=["streaming"])` inside `create_stream_router()`.

### M3 — Medium: the two implementations normalize tickers differently

- `MassiveDataSource.add_ticker/remove_ticker` apply `.upper().strip()`, but `start()` does not (`massive_client.py:43`).
- `SimulatorDataSource` never normalizes. Verified: `add_ticker("aapl")` creates a **second** ticker `aapl` next to `AAPL`, with a random seed price ($207.77 in the probe) and default parameters.

This breaks the "downstream code is source-agnostic" goal. An LLM that emits `"aapl"` would get correct behaviour in Massive mode and a phantom ticker in simulator mode.

**Fix:** normalize in one place. Either add a shared `normalize_ticker()` used by both sources in `start`, `add_ticker` and `remove_ticker`, or state in the interface docstring that callers must pass uppercase tickers and enforce it at the API layer. Doing both is safest.

### M4 — Medium: no "daily change" reference for the watchlist

PLAN.md §10 asks for **"daily change %"** in the watchlist. `PriceUpdate.change` and `change_percent` are **tick-to-tick**: `previous_price` is the previous cached value. Nothing in the payload gives a session reference, and Massive's `todays_change_percent` / `prev_day.close` are discarded.

**Fix before the frontend is built:** add an optional reference price, such as `session_open` or `prev_close`, to `PriceUpdate` and `to_dict()`. The simulator would use the seed price (or the first-seen price); Massive would use `prev_day.close`. The alternative is to document that the frontend computes the change from the first price it sees.

### M5 — Medium: `MassiveDataSource.start()` blocks app startup on a network call

`massive_client.py:46` awaits the first poll inside `start()`, which runs in the FastAPI lifespan. The SDK defaults are a 10 s connect timeout, a 10 s read timeout, and `retries=3` with urllib3 retrying on **429, 5xx and 413** and honouring `Retry-After`. A slow or rate-limited API can therefore delay container startup and the health check by tens of seconds. On the free tier (5 requests per minute), automatic 429 retries also use up the remaining quota.

**Fix:** start the task right away and let the loop poll first, then sleep: `while True: await self._poll_once(); await asyncio.sleep(...)`. Also construct `RESTClient(api_key=..., retries=0 or 1, read_timeout=5)`, since the poll loop already retries.

### L1 — Low: removing and re-adding a ticker resets the simulator price

`simulator.py:146-152`: `GBMSimulator.remove_ticker` discards the price. Re-adding resets it to `SEED_PRICES`, or to a **new random price in [50, 300]** for unknown tickers. Verified: AAPL drifted to 250, was removed and re-added, and came back at 190.00.

If the user holds a position in that ticker, valuation and P&L jump. Two ways to address it:
- Downstream: follow `planning/archive/MARKET_DATA_DESIGN.md` §11 and **keep tracking tickers that have open positions** even when they leave the watchlist. This also matters because trade execution needs a cached price.
- In the simulator: remember the last price of removed tickers.

### L2 — Low: `add_ticker()` before `start()` behaves differently in each source

`SimulatorDataSource.add_ticker` silently does nothing when `_sim is None` (`simulator.py:243`). `MassiveDataSource.add_ticker` appends, but `start()` then overwrites `_tickers` (`massive_client.py:43`), so the ticker is lost there too. Either raise an error or buffer the ticker, the same way in both sources.

### L3 — Low: SSE generator swallows `CancelledError` and has no heartbeat

- `stream.py:86-87` catches `asyncio.CancelledError` and does not re-raise it. Swallowing cancellation is an asyncio anti-pattern; log the message and `raise`.
- When the version doesn't change (Massive between polls, or an empty cache) the stream is silent. Sending a `: keepalive\n\n` comment every ~15 s protects against proxy idle timeouts on App Runner or Render. It also lets the frontend's connection dot tell "connected" apart from "stalled".

### L4 — Low: minor correctness and robustness items

- `massive_client.py:125-127`: `_fetch_snapshots` passes the live `self._tickers` list into a worker thread, while `add_ticker` mutates it with `.append()` on the event loop. Pass `list(self._tickers)`.
- `simulator.py:242-249`: `add_ticker` for an existing ticker still writes a cache update. The cache's previous price becomes the current price, so a `flat` event with change 0 goes out and cancels any in-progress flash. Return early when the ticker already exists.
- `cache.py:30`: `timestamp or time.time()` treats `0.0` as "missing". Use `if timestamp is None`.
- `cache.py:64-67`: `version` is read without the lock. This is carried over from the previous review and is fine under the GIL, but taking the lock costs little.
- Simulator randomness uses the global `random` and `np.random` state, so tests can't seed it. A `seed`/`rng` constructor argument (`np.random.default_rng`) would allow deterministic tests of GBM statistics and shock events.

### L5 — Low: tooling, dependencies and docs

- **`backend/README.md` install command is wrong.** `uv sync --dev` installs *dependency groups*, but dev tools are declared as an *optional extra* (`[project.optional-dependencies] dev`). A dry run shows `uv sync --dev` would **uninstall** pytest, pytest-asyncio, pytest-cov and ruff. Use `uv sync --extra dev`, as `backend/CLAUDE.md` does, or move the tools to `[dependency-groups]`.
- **`rich` is a runtime dependency** but only `market_data_demo.py` uses it. Moving it to the dev extra keeps the production image smaller.
- **Doc drift:**
  - `MARKET_DATA_SUMMARY.md` says "all issues resolved" and lists 84% overall and 56% `massive_client` coverage. Current figures are 91% and 94%, and previous items 3.4 and 3.6 remain open.
  - `MARKET_DATA_DESIGN.md` §7.4 says "approximately one event per ticker per ~50 seconds". The correct rate is one event per ticker per ~500 s (0.001 × 2 ticks/s), or about one event across all 10 tickers every ~50 s.
  - `MARKET_DATA_DESIGN.md` §8.1 and `archive/MASSIVE_API.md` show the `timestamp / 1000` ms conversion (see C1).

### L6 — Low: Python version not pinned

`requires-python = ">=3.12"` and there is no `.python-version`, so `uv` chose **CPython 3.14** for this run, while PLAN.md §11 specifies a Python 3.12 Docker image. The suite passes on 3.14, but local and container environments should match. Add `backend/.python-version` containing `3.12`.

---

## 5. Test Suite Quality

Positives: good coverage of `PriceUpdate`, `PriceCache` and factory branching (including a whitespace-only key); lifecycle idempotence; malformed-snapshot and whole-poll failure isolation; clear docstrings.

Gaps and weak tests:

| # | Issue | Location |
|---|---|---|
| T1 | **Mocks too permissive.** `MagicMock` snapshots accept any attribute, which is the root cause of C1 going unnoticed. Use real `TickerSnapshot.from_dict` fixtures | `test_massive.py:11-18` |
| T2 | **No tests for `stream.py`** (33%). The generator can be unit-tested directly with a fake request whose `is_disconnected()` returns `False` then `True`: assert the `retry:` preamble, one `data:` event per version change, no event when the version is unchanged, and (after the M1 fix) an event on removal | `stream.py` |
| T3 | `test_exception_resilience` never injects an error, so the handler at L268-269 is untested. Patch `_sim.step` with `side_effect=[RuntimeError, {...}]` and assert the loop continues | `test_simulator_source.py:96-111` |
| T4 | `test_custom_event_probability` asserts nothing. With `event_probability=1.0`, assert that each step moves the price by at least 2% | `test_simulator_source.py:127-138` |
| T5 | `test_prices_rounded_to_two_decimals` checks `str(float)`, which is fragile. Use `assert p == round(p, 2)` | `test_simulator.py:123-130` |
| T6 | **Timing-sensitive.** `test_custom_update_interval` needs at least 3 ticks at 10 ms intervals within a 50 ms sleep. That's fine on Linux (5/5 runs passed), but with Windows' default ~15.6 ms timer resolution it's borderline. Use a larger margin or poll until the version changes, with a timeout | `test_simulator_source.py:113-125` |
| T7 | Missing: the 10 default tickers together (Cholesky decomposition succeeds), statistical GBM sanity (mean/variance of log-returns), concurrent multi-thread writes to `PriceCache`, ticker normalization, and remove-then-re-add | — |
| T8 | `conftest.py`'s `event_loop_policy` fixture is unnecessary under `asyncio_mode = "auto"` and emits 73 deprecation warnings. Delete it | `tests/conftest.py:6-11` |
| T9 | `ruff format` not applied to 3 test files | see §2 |

---

## 6. Simulator Realism Note (design feedback, not a defect)

With `dt` sized to real trading time, per-tick moves are about 0.006% (about 1¢ on AAPL). Measured: **31% of AAPL ticks are "flat"** after rounding to 2 decimal places, and the expected 1-σ move over a 10-minute demo is only about **0.22%**. Random shocks (2-5%) supply most of the visible action. If the demo feels static, an optional `speed` multiplier on `dt` (for example ×10 to ×50) makes the sparklines and heatmap livelier without changing the model.

---

## 7. Status of Previous Review Items (2026-02-10)

| Item | Status |
|---|---|
| 3.1 hatchling `packages = ["app"]` missing | ✅ Fixed (`pyproject.toml:27-28`) |
| 3.2 Massive tests fail without `massive` package | ✅ Fixed (top-level imports; `massive` is a core dependency) |
| 3.3 `_generate_events` return type | ✅ Fixed (`AsyncGenerator[str, None]`) |
| 3.4 `version` read without lock | ⏸ Open (acceptable, see L4) |
| 3.5 `get_tickers` reaches into private state | ✅ Fixed (`GBMSimulator.get_tickers()`) |
| 3.6 module-level router | ❌ **Open, and more serious than first rated** (M2) |
| 3.7 unused test imports | ✅ Fixed |
| 4.2 add SSE test | ❌ Open (T2) |
| 4.2 thread-safety test / 10-ticker Cholesky test | ❌ Open (T7) |
| 4.3 `DEFAULT_CORR` vs `CROSS_GROUP_CORR` | ✅ Fixed |

---

## 8. Recommended Action Plan

**Must fix before downstream integration** (small, contained changes):
1. **C1:** read `last_trade.sip_timestamp` (nanoseconds, so ÷1e9), add fallbacks, and rewrite the Massive tests with real SDK model objects.
2. **M1:** bump the version on `remove()` and send an empty snapshot; document that events are full snapshots.
3. **M2:** create the `APIRouter` inside `create_stream_router()`.
4. **M3:** normalize tickers consistently in both sources.

**Should fix during the API/frontend phase:**

5. **M4:** add a daily-change reference price to `PriceUpdate`.
6. **M5:** make Massive startup non-blocking and reduce SDK retries.
7. **T2/T3/T4:** add SSE generator tests and make the resilience and event-probability tests meaningful.
8. **L1:** in the watchlist route, keep tracking tickers that have open positions.
9. **L5/L6:** fix the README install command, pin Python 3.12, and move `rich` to dev.

**Nice to have:** L2, L3, L4, T5-T9, the §6 speed multiplier, and correcting the stale numbers in `MARKET_DATA_SUMMARY.md` and `MARKET_DATA_DESIGN.md`.

---

## 9. Conclusion

The market data backend is a solid foundation. The architecture is clean, the simulator is mathematically sound, and the default (no-API-key) path works end to end, so it is ready for the portfolio, watchlist and SSE frontend work. **The real-data path is not.** A wrong attribute name, hidden by permissive mocks, means Massive mode would show no prices at all. That fix and the three integration fixes (M1-M3) are each only a few lines and should be done before the next agent builds on this layer.

---

## 10. Resolution Log

**2026-09-25: all findings resolved.** Work is on branch `market-data-backend-fixes`.

Final state: **120 tests pass** on Python 3.12 (now pinned) and 3.14, 10 of 10 repeat runs, with no warnings. Coverage is **99%**: every module is at 100% except `simulator.py` at 99%. `ruff check` and `ruff format --check` are clean.

End-to-end checks beyond the test suite:
- **Simulator:** SSE under uvicorn, including the new daily-change fields and a ticker removal reaching a connected client.
- **Massive:** real SDK over HTTP against a local fake API. `start()` returned in 2 ms while the API took 300 ms; the request went to `/v2/snapshot/locale/us/markets/stocks/tickers` with normalized tickers and the Bearer key; prices, previous close, daily change and nanosecond timestamps were parsed correctly.
- **Demo:** `market_data_demo.py` runs.

### Critical

| Item | Fix | Tests |
|---|---|---|
| **C1** | `_extract_price()` reads `last_trade.sip_timestamp` (falling back to `snap.updated`), converting nanoseconds to seconds (÷1e9). Price falls back to `day.close` when there is no last trade; a missing or zero price is skipped with a warning | Snapshots are built with the real `TickerSnapshot.from_dict()`. Tests cover a full API-shaped payload, the `day.close` fallback, a zeroed day bar, a missing timestamp and a non-numeric price. 8 of these tests fail against the old code |
| **C2** | Pass `SnapshotMarketType.STOCKS.value` to the SDK | `TestMassiveOverHTTP` drives the real SDK against a local HTTP server that only answers the correct path; it fails with the enum member |

### Medium

| Item | Fix | Tests |
|---|---|---|
| **M1** | `PriceCache.remove()` bumps `version` when a ticker was present; SSE sends a snapshot even when empty (`data: {}`). Documented that events are full snapshots to be replaced, not merged | Version bump on removal (and none for an unknown ticker); SSE removal event and empty snapshot. Verified live |
| **M2** | `APIRouter` is created inside `create_stream_router()` | Two calls give independent routers, and the app's route is bound to the right cache |
| **M3** | `normalize_ticker()` (strip + uppercase) in `interface.py`, exported from `app.market`, used by both sources in `start` (with de-duplication), `add_ticker` and `remove_ticker` | Normalization tests for both sources |
| **M4** | `PriceUpdate.previous_close` plus `day_change` and `day_change_percent`, included in `to_dict()`/SSE. Massive uses `prevDay.c`. The simulator uses the price when the ticker was first added this session. `PriceCache.update(previous_close=...)` keeps the reference across updates that omit it | Model, cache, simulator and Massive tests, including the reference surviving remove + re-add |
| **M5** | `start()` only creates the task, and the loop polls first then sleeps, so startup does no network I/O. `RESTClient` gets 5 s timeouts and `retries=0` (the loop retries every interval) | `start()` returns in < 0.25 s against a 0.5 s API; client constructor arguments are asserted |

### Low

| Item | Fix |
|---|---|
| **L1** | `GBMSimulator` remembers removed tickers' last prices and resumes them on re-add (also for random-priced unknown tickers). Guidance to keep tracking held tickers was added to `backend/CLAUDE.md` and the design doc (§11) |
| **L2** | Both sources keep tickers added before `start()` and merge them into the start list. In the simulator, `remove_ticker` before `start()` also works |
| **L3** | The SSE generator re-raises `CancelledError`, and sends a `: keepalive` comment after 15 s idle (tested; a mutation check confirms the cancellation test fails if the `raise` is removed) |
| **L4** | Worker thread gets a copy of the ticker list; a duplicate `add_ticker` writes nothing to the cache; `timestamp=0.0` is honoured (`is None` check); `version` is read under the lock; the simulator takes a `seed` and uses one `numpy` Generator for all randomness |
| **L5** | README install/test commands fixed (`--extra dev`); `rich` moved to the dev extra (`uv.lock` updated); the summary and design docs were brought up to date, including the event rate (one per ticker per ~500 s, ~50 s across 10 tickers) and SDK usage |
| **L6** | `backend/.python-version` pins 3.12 to match the Docker image |

### Tests

| Item | Fix |
|---|---|
| **T1** | Real SDK models in all Massive tests, plus the HTTP-level test |
| **T2** | New `test_stream.py` (12 tests): `stream.py` coverage went from 33% to 100% |
| **T3** | `test_exception_resilience` injects two failing steps and checks prices keep flowing |
| **T4** | `test_custom_event_probability` asserts a 2–5% move per tick; the GBM tests check shock size over 200 steps |
| **T5** | Rounding checked with `p == round(p, 2)` over 100 steps |
| **T6** | Fixed sleeps replaced with `tests.helpers.wait_until()` polling |
| **T7** | Added: all 10 default tickers, GBM volatility and correlation statistics over 20,000 seeded steps, 8-thread concurrent cache writers, remove + re-add, seeding reproducibility |
| **T8** | Removed the deprecated `event_loop_policy` fixture (`conftest.py`); the 73 deprecation warnings are gone |
| **T9** | `ruff format` applied to all tests |

### Not changed

- **Simulator realism (§6):** left as designed. The design doc's §13 now describes the `dt` knob for a livelier demo.
- **Downstream integration:** keeping held tickers tracked, and returning a 400 on a price cache miss, belong to the watchlist/portfolio routes that aren't built yet. The contract is documented in `backend/CLAUDE.md`.
