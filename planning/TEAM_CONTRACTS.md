# FinAlly — Team Contracts

Written by the team lead. This is the shared contract between the six agents building the
rest of FinAlly. `planning/PLAN.md` is the spec; this file pins down the interfaces between
team members so work can proceed in parallel. **If you need to change a contract, message the
affected teammates and the lead (`main`) first, then update this file.**

## 1. Team and file ownership

Everyone works in the same git working tree on branch `agent-teams`. Only edit files you own.
If you need a change in someone else's files, message the owner.

| Agent name | Role | Owns |
|---|---|---|
| `database` | Database Engineer | `backend/app/db/**`, `backend/tests/db/**`, `db/.gitkeep` |
| `backend` | Backend API Engineer | `backend/app/main.py`, `backend/app/context.py`, `backend/app/api/**`, `backend/app/services/**`, `backend/tests/api/**`, `backend/tests/services/**`, `backend/pyproject.toml` + `uv.lock` |
| `llm` | LLM Engineer | `backend/app/llm/**`, `backend/tests/llm/**` |
| `frontend` | Frontend Engineer | `frontend/**` |
| `devops` | DevOps Engineer | `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `scripts/**`, `.env.example`, root `.gitignore` |
| `tester` | Integration Tester | `test/**` |

- `backend/app/market/**` is complete — do not modify it. Read `backend/CLAUDE.md` for its API.
- **Do not `git commit`, `git checkout`, `git stash` or `git reset`.** The lead commits. Concurrent git
  operations in one working tree corrupt each other.
- Dependencies: `litellm`, `pydantic`, `python-dotenv` and dev `httpx` are already in
  `backend/pyproject.toml`. Need another Python dep? Ask `backend` to `uv add` it.
- Unit tests: every engineer writes tests for their own code. Backend: `cd backend && uv run --extra dev pytest`.
  Keep `uv run --extra dev ruff check app/ tests/` clean.

## 2. Runtime configuration

| Env var | Default | Used by |
|---|---|---|
| `DB_PATH` | `<backend_dir>/../db/finally.db` (i.e. project-root `db/`) | database |
| `STATIC_DIR` | `<backend_dir>/static` | backend (serves the Next.js export if the dir exists) |
| `OPENROUTER_API_KEY` | — | llm |
| `MASSIVE_API_KEY` | empty → simulator | market (existing) |
| `LLM_MOCK` | `false` | llm |

- The backend loads `<backend_dir>/../.env` with `python-dotenv` at startup if present (no override of
  existing env vars). In Docker, env comes from `--env-file`.
- Docker layout: backend contents at `/app` (so `/app/app/main.py`, `/app/pyproject.toml`), frontend
  export copied to `/app/static`, `DB_PATH=/app/db/finally.db`, volume `finally-data:/app/db`.
- Server command: `uvicorn app.main:app --host 0.0.0.0 --port 8000` from the backend dir.

## 3. Database layer — `backend/app/db/` (owner: `database`)

Plain `sqlite3` (stdlib), synchronous. Schema exactly as PLAN.md §7 (every table has `user_id`
default `"default"`; UUID text ids; ISO-8601 UTC timestamps). Schema SQL lives in
`backend/app/db/schema.sql`. Enable WAL mode. Each function opens/closes its own connection (or
uses a short-lived one) so it's safe to call from FastAPI's threadpool.

Public API, importable as `from app.db import ...`:

```python
DEFAULT_USER = "default"

class InsufficientCashError(Exception): ...
class InsufficientSharesError(Exception): ...

def init_db(db_path: str | Path | None = None) -> None
    # Idempotent. Creates tables if missing; seeds user profile (cash 10000.0) and the 10 default
    # watchlist tickers only when the users_profile row doesn't exist. db_path=None → DB_PATH env / default.
    # Called by backend on app startup. Tests call it with a tmp path.

def get_cash_balance(user_id=DEFAULT_USER) -> float

def get_watchlist(user_id=DEFAULT_USER) -> list[str]            # tickers, ordered by added_at
def add_watchlist_ticker(ticker: str, user_id=DEFAULT_USER) -> bool     # False if already present
def remove_watchlist_ticker(ticker: str, user_id=DEFAULT_USER) -> bool  # False if not present

def get_positions(user_id=DEFAULT_USER) -> list[dict]
    # [{"ticker", "quantity", "avg_cost", "updated_at"}], only quantity > 0
def get_position(ticker: str, user_id=DEFAULT_USER) -> dict | None

def record_trade(ticker: str, side: str, quantity: float, price: float, user_id=DEFAULT_USER) -> dict
    # ONE atomic transaction: validates (buy: cash >= qty*price, else InsufficientCashError;
    # sell: held qty >= qty, else InsufficientSharesError), updates cash, upserts position
    # (weighted avg cost on buy; avg cost unchanged on sell; delete row when qty reaches ~0, use a
    # 1e-9 epsilon), appends to trades. Returns the trade row:
    # {"id", "ticker", "side", "quantity", "price", "executed_at"}
def get_trades(limit: int = 100, user_id=DEFAULT_USER) -> list[dict]   # newest first

def record_snapshot(total_value: float, user_id=DEFAULT_USER) -> dict  # {"total_value", "recorded_at"}
def get_snapshots(limit: int = 1000, user_id=DEFAULT_USER) -> list[dict]  # oldest first

def add_chat_message(role: str, content: str, actions: dict | None = None, user_id=DEFAULT_USER) -> dict
    # {"id", "role", "content", "actions" (decoded dict or None), "created_at"}
def get_chat_messages(limit: int = 20, user_id=DEFAULT_USER) -> list[dict]  # the most recent `limit`, oldest first
```

Tickers are stored normalized (uppercase) — callers normalize with `app.market.normalize_ticker`,
but the DB layer should also uppercase defensively.

## 4. Services & app wiring (owner: `backend`)

`backend/app/context.py`:

```python
@dataclass
class AppContext:
    price_cache: PriceCache
    market_source: MarketDataSource
```

Created in the FastAPI lifespan, stored on `app.state.ctx`. Lifespan also: load `.env`, `init_db()`,
start the market source with watchlist tickers ∪ position tickers, record an initial portfolio
snapshot, and start a background task that records a snapshot every 30 s.

`backend/app/services/portfolio.py` and `backend/app/services/watchlist.py` — the single code path for
trades and watchlist changes. The REST routes **and** the LLM chat flow both call these:

```python
class TradeError(Exception): ...        # message is user-facing
class WatchlistError(Exception): ...    # message is user-facing; has .status_code (400/404/409)

def execute_trade(ctx, ticker: str, side: str, quantity: float) -> dict
    # normalizes ticker; validates side in {"buy","sell"} and quantity > 0; price from
    # ctx.price_cache.get_price() (None → TradeError "No price available for X yet");
    # calls db.record_trade (maps Insufficient*Error → TradeError); ensures the ticker is tracked by
    # the market source; records a portfolio snapshot. Returns the trade dict.
def get_portfolio(ctx) -> dict            # shape = GET /api/portfolio response
async def add_to_watchlist(ctx, ticker: str) -> dict     # returns a watchlist entry (shape below)
async def remove_from_watchlist(ctx, ticker: str) -> None
    # only calls market_source.remove_ticker() if no open position in that ticker
def get_watchlist(ctx) -> list[dict]
```

Valid ticker (after normalization): `^[A-Z][A-Z0-9.\-]{0,9}$`, else 400.

## 5. REST API (implemented by `backend`, consumed by `frontend` and `tester`)

All JSON. Errors use FastAPI's `{"detail": "<user-facing message>"}`.

| Method & path | Request | Success response |
|---|---|---|
| `GET /api/health` | — | `{"status": "ok"}` |
| `GET /api/portfolio` | — | `Portfolio` |
| `POST /api/portfolio/trade` | `{"ticker": "AAPL", "quantity": 5, "side": "buy"}` | `200 {"trade": Trade, "portfolio": Portfolio}`; `400` on validation failure |
| `GET /api/portfolio/history` | — | `{"snapshots": [{"total_value": 10000.0, "recorded_at": "..."}]}` oldest first |
| `GET /api/watchlist` | — | `{"tickers": [WatchlistEntry]}` |
| `POST /api/watchlist` | `{"ticker": "PYPL"}` | `201 WatchlistEntry`; `400` invalid, `409` already present |
| `DELETE /api/watchlist/{ticker}` | — | `204`; `404` if not in watchlist |
| `POST /api/chat` | `{"message": "..."}` | `200 ChatMessage` (the assistant reply) |
| `GET /api/chat/history` | — | `{"messages": [ChatMessage]}` oldest first (user and assistant) |
| `GET /api/stream/prices` | — | SSE (existing, see below) |

```jsonc
// Portfolio
{
  "cash_balance": 8100.0,
  "positions_value": 1950.0,
  "total_value": 10050.0,          // cash + positions_value
  "unrealized_pnl": 50.0,          // sum over positions
  "positions": [{
    "ticker": "AAPL", "quantity": 10, "avg_cost": 190.0,
    "current_price": 195.0,        // null if no price yet → valued at avg_cost
    "market_value": 1950.0,
    "unrealized_pnl": 50.0,
    "unrealized_pnl_percent": 2.63, // vs avg cost, in percent
    "weight": 19.4                  // % of total_value
  }]
}
// Trade
{"id": "uuid", "ticker": "AAPL", "side": "buy", "quantity": 10, "price": 190.0, "executed_at": "ISO"}
// WatchlistEntry — price fields null until the first price arrives
{"ticker": "AAPL", "price": 190.1, "previous_price": 190.0, "change": 0.1, "change_percent": 0.05,
 "day_change_percent": 0.3, "direction": "up", "timestamp": 1727000000.0}
// ChatMessage
{"id": "uuid", "role": "assistant", "content": "Bought 5 AAPL.", "created_at": "ISO",
 "actions": {                       // null for user messages
   "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 5, "status": "executed",
               "price": 190.0, "error": null}],
   "watchlist_changes": [{"ticker": "PYPL", "action": "add", "status": "failed",
                          "error": "PYPL is already in your watchlist"}]
 }}
```

ChatMessage notes: `status` is `"executed"` or `"failed"` for both trades and watchlist changes.
Assistant messages always have `actions` as an object (possibly with empty arrays), never null; user
messages have `actions: null`. When an action fails, the assistant `content` is not rewritten; the
error is only in `actions[].error`, so the UI must show failed actions inline.

### SSE — `GET /api/stream/prices` (existing)

Each event is a full snapshot: `data: {"AAPL": PriceUpdate, ...}` where PriceUpdate is
`{ticker, price, previous_price, timestamp, change, change_percent, direction, previous_close,
day_change, day_change_percent}`. Replace client state with each event (don't merge) — removed
tickers disappear. `data: {}` = nothing tracked. Tracked tickers can include held positions no
longer on the watchlist, so the frontend should render the watchlist from `/api/watchlist` and use the SSE
snapshot only for prices.

### Static files

If `STATIC_DIR` exists, the backend mounts it at `/` (`StaticFiles(directory=..., html=True)`) **after**
all `/api` routes, so `/api/*` always wins.

## 6. LLM chat — `backend/app/llm/` (owner: `llm`)

```python
async def handle_chat(ctx: AppContext, user_message: str) -> dict   # returns the assistant ChatMessage
```

Flow per PLAN.md §9: store the user message → build context (portfolio via
`services.portfolio.get_portfolio`, watchlist with prices, last ~20 messages) → call LLM (the
`cerebras` project skill: LiteLLM → `openrouter/openai/gpt-oss-120b`, Cerebras provider,
structured output with a Pydantic model) → execute trades via `services.portfolio.execute_trade` and
watchlist changes via `services.watchlist.add_to_watchlist` / `remove_from_watchlist`, collecting
per-action status/error → store the assistant message with `actions` → return it. The LiteLLM call is
blocking; run it with `asyncio.to_thread`. LLM/network failure → still return a friendly assistant message
("Sorry, I couldn't reach the AI service…") with empty actions, not a 500.

The `backend` agent writes the thin route `POST /api/chat` → `handle_chat` and `GET /api/chat/history`.

### Mock mode (`LLM_MOCK=true`) — deterministic, relied on by E2E tests

Case-insensitive rules on the user message, first match wins:

| Message pattern | Mock structured output |
|---|---|
| `buy <qty> <TICKER>` (e.g. "buy 5 AAPL") | message `"Buying <qty> <TICKER> for you."`, trades `[{ticker, "buy", qty}]` |
| `sell <qty> <TICKER>` | message `"Selling <qty> <TICKER> for you."`, trades `[{ticker, "sell", qty}]` |
| `add <TICKER>` or `watch <TICKER>` | message `"Adding <TICKER> to your watchlist."`, watchlist_changes `[{ticker, "add"}]` |
| `remove <TICKER>` or `unwatch <TICKER>` | message `"Removing <TICKER> from your watchlist."`, watchlist_changes `[{ticker, "remove"}]` |
| anything else | message `"This is a mock response from FinAlly. Your portfolio is ready."`, no actions |

Mock output goes through the **same** execution path as real output (so validation errors still appear in `actions`).

## 7. Frontend (owner: `frontend`)

Next.js + TypeScript + Tailwind, `output: 'export'`, build output `frontend/out/` (copied to
`/app/static` by the Dockerfile). `npm run build` must produce it; `npm test` runs unit tests.
Same-origin `/api/*` calls. For local dev against a backend on :8000, use Next `rewrites` only
when `NODE_ENV === 'development'` (rewrites are not allowed with export in production builds).

### `data-testid` contract (used by `tester`'s Playwright suite)

| testid | Element |
|---|---|
| `header-total-value`, `header-cash` | header numbers (text contains the formatted $ value) |
| `connection-status` | status dot, with attribute `data-status="connected" \| "reconnecting" \| "disconnected"` |
| `watchlist` | watchlist panel |
| `watchlist-row-<TICKER>` | one row per watched ticker |
| `watchlist-price-<TICKER>` | price cell (gets the flash class) |
| `watchlist-remove-<TICKER>` | remove button in the row |
| `watchlist-add-input`, `watchlist-add-button` | add-ticker form |
| `main-chart` | selected-ticker chart container; attribute `data-ticker="<TICKER>"` |
| `trade-ticker`, `trade-quantity`, `trade-buy`, `trade-sell` | trade bar |
| `trade-error` | error message area (visible when a trade fails) |
| `positions-table`, `position-row-<TICKER>` | positions table and rows |
| `portfolio-heatmap` | treemap container |
| `heatmap-cell-<TICKER>` | one treemap cell per position, attribute `data-pnl="positive" \| "negative" \| "flat"` |
| `pnl-chart` | P&L line chart container, attribute `data-points="<n>"` (number of snapshots plotted) |
| `chat-panel`, `chat-input`, `chat-send` | chat panel |
| `chat-message` | each message, attribute `data-role="user" \| "assistant"` |
| `chat-loading` | loading indicator while awaiting a reply |
| `chat-action` | each inline trade / watchlist confirmation |

## 8. DevOps (owner: `devops`)

Multi-stage `Dockerfile` per PLAN.md §11 (Node 20 slim → Python 3.12 slim + uv). Image name `finally`,
container name `finally`, port 8000, volume `finally-data:/app/db`, `--env-file .env` (scripts should
tolerate a missing `.env` by warning and running without it — mock/simulator still work). Add a
`HEALTHCHECK` hitting `/api/health`. `.env.example` per PLAN.md §5. `.dockerignore` must exclude
`node_modules`, `.next`, `out`, `.venv`, `__pycache__`, `.env`, `db/*.db`.

## 9. E2E (owner: `tester`)

`test/docker-compose.test.yml` runs the app image with `LLM_MOCK=true` (fresh DB per run — no named
volume) plus a Playwright container that runs the suite in `test/` against `http://finally-app:8000`
(a network alias on the `app` service — Chromium HSTS-preloads the `.app` TLD, so `http://app` is
forced to https). Scenarios
per PLAN.md §12. Failures are reported to the owning agent (use §1) with the failing test, the expected
vs actual behaviour and any logs; retest after they report a fix.
