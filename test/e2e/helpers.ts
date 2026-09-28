import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import * as net from "node:net";

export const DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"];

export interface Position {
  ticker: string;
  quantity: number;
  avg_cost: number;
  current_price: number | null;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_percent: number;
  weight: number;
}

export interface Portfolio {
  cash_balance: number;
  positions_value: number;
  total_value: number;
  unrealized_pnl: number;
  positions: Position[];
}

export interface Trade {
  id: string;
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  executed_at: string;
}

/** Parse a displayed money value such as "$10,000.00" or "-$1,234.5" into a number. */
export function parseMoney(text: string | null): number {
  const cleaned = (text ?? "").replace(/[^0-9.\-]/g, "");
  const value = Number.parseFloat(cleaned);
  if (Number.isNaN(value)) throw new Error(`Could not parse a money value from ${JSON.stringify(text)}`);
  return value;
}

export async function moneyOf(locator: Locator): Promise<number> {
  return parseMoney(await locator.textContent());
}

// ---------------------------------------------------------------------------
// Direct API helpers, used to set up and inspect state around UI actions.
// ---------------------------------------------------------------------------

export async function getPortfolio(request: APIRequestContext): Promise<Portfolio> {
  const res = await request.get("/api/portfolio");
  expect(res.ok(), `GET /api/portfolio -> ${res.status()}`).toBeTruthy();
  return res.json();
}

export async function getPosition(request: APIRequestContext, ticker: string): Promise<Position | undefined> {
  const portfolio = await getPortfolio(request);
  return portfolio.positions.find((p) => p.ticker === ticker);
}

export async function getWatchlistTickers(request: APIRequestContext): Promise<string[]> {
  const res = await request.get("/api/watchlist");
  expect(res.ok(), `GET /api/watchlist -> ${res.status()}`).toBeTruthy();
  const body = (await res.json()) as { tickers: { ticker: string }[] };
  return body.tickers.map((t) => t.ticker);
}

export async function ensureNotInWatchlist(request: APIRequestContext, ticker: string): Promise<void> {
  const res = await request.delete(`/api/watchlist/${ticker}`);
  expect([204, 404], `DELETE /api/watchlist/${ticker} -> ${res.status()}`).toContain(res.status());
}

export async function ensureInWatchlist(request: APIRequestContext, ticker: string): Promise<void> {
  const res = await request.post("/api/watchlist", { data: { ticker } });
  expect([201, 409], `POST /api/watchlist ${ticker} -> ${res.status()}`).toContain(res.status());
}

/** Execute a trade via the API, retrying briefly while the ticker has no price yet. */
export async function tradeViaApi(
  request: APIRequestContext,
  ticker: string,
  side: "buy" | "sell",
  quantity: number,
): Promise<Trade> {
  let lastError = "";
  for (let attempt = 0; attempt < 20; attempt++) {
    const res = await request.post("/api/portfolio/trade", { data: { ticker, side, quantity } });
    if (res.ok()) return ((await res.json()) as { trade: Trade }).trade;
    lastError = `${res.status()} ${await res.text()}`;
    if (!/no price/i.test(lastError)) break;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`Trade ${side} ${quantity} ${ticker} failed: ${lastError}`);
}

/** Sell any open position in `ticker` so a test starts from a flat position. */
export async function flattenPosition(request: APIRequestContext, ticker: string): Promise<void> {
  const position = await getPosition(request, ticker);
  if (position && position.quantity > 0) await tradeViaApi(request, ticker, "sell", position.quantity);
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

/** Open the app and wait until the price stream is connected and the header is populated. */
export async function openApp(page: Page, url = "/"): Promise<void> {
  await page.goto(url);
  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-status", "connected", {
    timeout: 20_000,
  });
  await expect(page.getByTestId("header-cash")).toContainText(/\d/);
}

/** Fill the trade bar and submit; resolves with the backend's response to the trade request. */
export async function submitTrade(page: Page, ticker: string, side: "buy" | "sell", quantity: number | string) {
  await page.getByTestId("trade-ticker").fill(ticker);
  await page.getByTestId("trade-quantity").fill(String(quantity));
  const responsePromise = page.waitForResponse(
    (r) => r.url().includes("/api/portfolio/trade") && r.request().method() === "POST",
  );
  await page.getByTestId(side === "buy" ? "trade-buy" : "trade-sell").click();
  return responsePromise;
}

/** Wait until the header cash shows `expected` (to the cent, allowing for display rounding). */
export async function expectHeaderCash(page: Page, expected: number): Promise<void> {
  await expect
    .poll(() => moneyOf(page.getByTestId("header-cash")), { message: `header cash ≈ ${expected}` })
    .toBeCloseTo(expected, 1);
}

/** Send a chat message and wait for the assistant reply containing `replyText`. */
export async function sendChat(page: Page, message: string, replyText: string | RegExp): Promise<Locator> {
  await page.getByTestId("chat-input").fill(message);
  await page.getByTestId("chat-send").click();
  const reply = page
    .locator('[data-testid="chat-message"][data-role="assistant"]')
    .filter({ hasText: replyText })
    .last();
  await expect(reply).toBeVisible({ timeout: 20_000 });
  return reply;
}

/** The quantity cell of a positions-table row (column order per PLAN.md §10: ticker, quantity, ...). */
export function positionQuantityCell(page: Page, ticker: string): Locator {
  return page.getByTestId(`position-row-${ticker}`).locator("td").nth(1);
}

/** A regex matching exactly the quantity `n`, formatted with or without trailing decimals. */
export function quantityPattern(n: number): RegExp {
  return new RegExp(`^\\s*${n}(\\.0+)?\\s*$`);
}

// ---------------------------------------------------------------------------
// Network fault injection
// ---------------------------------------------------------------------------

export interface FlakyProxy {
  /** Base URL (http://127.0.0.1:<port>) that forwards to the app. */
  url: string;
  /** Destroy every open connection and refuse new ones until `restore()`. */
  cut(): void;
  restore(): void;
  close(): Promise<void>;
}

/**
 * Start a TCP proxy in front of the app. Unlike `context.setOffline()`, which does not interrupt an
 * EventSource that is already streaming in Chromium, `cut()` drops the live SSE socket for real.
 */
export async function startFlakyProxy(target: string): Promise<FlakyProxy> {
  const { hostname, port } = new URL(target);
  const targetPort = Number(port || 80);
  const sockets = new Set<net.Socket>();
  let down = false;

  const track = (socket: net.Socket) => {
    sockets.add(socket);
    socket.on("close", () => sockets.delete(socket));
    socket.on("error", () => socket.destroy());
  };

  const server = net.createServer((client) => {
    track(client);
    if (down) return client.destroy();
    const upstream = net.connect(targetPort, hostname);
    track(upstream);
    client.pipe(upstream);
    upstream.pipe(client);
    client.on("close", () => upstream.destroy());
    upstream.on("close", () => client.destroy());
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port: listenPort } = server.address() as net.AddressInfo;

  return {
    url: `http://127.0.0.1:${listenPort}`,
    cut() {
      down = true;
      for (const socket of sockets) socket.destroy();
    },
    restore() {
      down = false;
    },
    close() {
      for (const socket of sockets) socket.destroy();
      return new Promise((resolve) => server.close(() => resolve()));
    },
  };
}
