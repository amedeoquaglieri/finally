# FinAlly E2E tests

Playwright (TypeScript) end-to-end tests that drive the real app in Chromium. They rely on the
deterministic LLM mock (`LLM_MOCK=true`, see `planning/TEAM_CONTRACTS.md` §6) and the `data-testid`
contract (§7).

## Run with Docker (recommended)

From the project root:

```bash
docker compose -f test/docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright
docker compose -f test/docker-compose.test.yml down -v
```

This builds the app from the root `Dockerfile` with `LLM_MOCK=true`, the simulator and no volume (a fresh
database every run), waits for `/api/health`, then runs the suite in the
`mcr.microsoft.com/playwright` container against `http://finally-app:8000`. (That is a network alias of the
`app` service: Chromium HSTS-preloads the `.app` TLD and would force `http://app:8000` to https.) The exit code is the suite's.
The HTML report is written to `test/playwright-report/` and failure traces/screenshots to
`test/test-results/` (open a trace with `npx playwright show-trace <trace.zip>`).

Keep the image tag in `docker-compose.test.yml` in sync with the `@playwright/test` version in
`package.json`.

## Run locally against a running app

Start the app however you like (e.g. `scripts/start_mac.sh` with `LLM_MOCK=true` in `.env`, or the backend
with `uv run uvicorn app.main:app` serving `frontend/out` via `STATIC_DIR`), then:

```bash
cd test
npm install
npx playwright install --with-deps chromium
BASE_URL=http://localhost:8000 npx playwright test      # BASE_URL defaults to http://localhost:8000
npx playwright show-report
```

Use a fresh database for the "starting balance is $10,000" assertion. If the DB already has trades, the
fresh-start test checks that the header matches the API balance and skips the exact $10,000 check.

## Layout

| File | Covers |
|---|---|
| `e2e/00-fresh-start.spec.ts` | Default 10 tickers with prices, $10,000 cash, connection status, health endpoint |
| `e2e/watchlist.spec.ts` | Add/remove a ticker (persists across reload); clicking a ticker sets `main-chart` `data-ticker` |
| `e2e/trading.spec.ts` | Buy, partial and full sell (cash deltas use the filled price), insufficient cash, selling unowned shares |
| `e2e/portfolio-viz.spec.ts` | Heatmap cell with P&L colouring; P&L chart has data points |
| `e2e/chat.spec.ts` | Mock reply, "buy 1 NVDA" (inline action + position), "add PYPL" (watchlist), history after reload |
| `e2e/streaming.spec.ts` | Prices update live; SSE drops (browser offline) and reconnects |
| `e2e/helpers.ts` | API setup helpers, money parsing, trade/chat UI helpers |

Tests share one backend, so they run serially (`workers: 1`). Each test sets up its own preconditions through
the API (e.g. closes an existing position, removes a ticker from the watchlist first) and asserts
deltas rather than absolute values, so they pass in any order and on re-runs.
