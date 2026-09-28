import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Terminal from "./Terminal";
import { MockEventSource, priceUpdate } from "@/test/mockEventSource";
import type { ChatMessage, Portfolio, WatchlistEntry } from "@/lib/types";

const emptyPortfolio: Portfolio = { cash_balance: 10000, positions_value: 0, total_value: 10000, unrealized_pnl: 0, positions: [] };

const boughtPortfolio: Portfolio = {
  cash_balance: 9000,
  positions_value: 1000,
  total_value: 10000,
  unrealized_pnl: 0,
  positions: [
    { ticker: "AAPL", quantity: 5, avg_cost: 200, current_price: 200, market_value: 1000, unrealized_pnl: 0, unrealized_pnl_percent: 0, weight: 10 },
  ],
};

const watch = (ticker: string): WatchlistEntry => ({
  ticker,
  price: null,
  previous_price: null,
  change: null,
  change_percent: null,
  day_change_percent: null,
  direction: null,
  timestamp: null,
});

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

type Handler = (init?: RequestInit) => Promise<Response>;

function mockApi(overrides: Record<string, Handler> = {}) {
  const routes: Record<string, Handler> = {
    "GET /api/portfolio": () => json(emptyPortfolio),
    "GET /api/portfolio/history": () => json({ snapshots: [{ total_value: 10000, recorded_at: "2026-01-01T00:00:00Z" }] }),
    "GET /api/watchlist": () => json({ tickers: [watch("AAPL"), watch("MSFT")] }),
    "GET /api/chat/history": () => json({ messages: [] }),
    ...overrides,
  };
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const handler = routes[key];
    if (!handler) return json({ detail: `unmocked ${key}` }, 500);
    return handler(init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Terminal", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", MockEventSource);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.stubGlobal("EventSource", MockEventSource);
  });

  it("loads the watchlist, cash and streams prices into the header", async () => {
    mockApi();
    render(<Terminal />);
    expect(await screen.findByTestId("watchlist-row-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("watchlist-row-MSFT")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("header-cash")).toHaveTextContent("$10,000.00"));
    expect(screen.getByTestId("header-total-value")).toHaveTextContent("$10,000.00");
    expect(screen.getByTestId("main-chart")).toHaveAttribute("data-ticker", "AAPL");

    const es = MockEventSource.latest();
    expect(es.url).toBe("/api/stream/prices");
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "reconnecting");
    es.open();
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "connected");
    es.emit({ AAPL: priceUpdate("AAPL", 201.5, 1), MSFT: priceUpdate("MSFT", 410, 1) });
    expect(screen.getByTestId("watchlist-price-AAPL")).toHaveTextContent("201.50");
  });

  it("reports reconnecting and disconnected states", async () => {
    mockApi();
    render(<Terminal />);
    const es = MockEventSource.latest();
    es.open();
    es.fail(MockEventSource.CONNECTING);
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "reconnecting");
    es.fail(MockEventSource.CLOSED);
    expect(screen.getByTestId("connection-status")).toHaveAttribute("data-status", "disconnected");
  });

  it("executes a trade and revalues the header with live prices", async () => {
    const user = userEvent.setup();
    const fetchMock = mockApi({
      "POST /api/portfolio/trade": () =>
        json({
          trade: { id: "t1", ticker: "AAPL", side: "buy", quantity: 5, price: 200, executed_at: "2026-01-01T00:00:01Z" },
          portfolio: boughtPortfolio,
        }),
    });
    render(<Terminal />);
    await screen.findByTestId("watchlist-row-AAPL");
    await user.clear(screen.getByTestId("trade-ticker"));
    await user.type(screen.getByTestId("trade-ticker"), "aapl");
    await user.type(screen.getByTestId("trade-quantity"), "5");
    await user.click(screen.getByTestId("trade-buy"));

    const call = fetchMock.mock.calls.find(([url, init]) => url === "/api/portfolio/trade" && init?.method === "POST");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ ticker: "AAPL", quantity: 5, side: "buy" });
    expect(await screen.findByTestId("position-row-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("header-cash")).toHaveTextContent("$9,000.00");
    expect(screen.queryByTestId("trade-error")).not.toBeInTheDocument();

    MockEventSource.latest().emit({ AAPL: priceUpdate("AAPL", 210, 2) });
    expect(screen.getByTestId("header-total-value")).toHaveTextContent("$10,050.00");
    expect(within(screen.getByTestId("positions-table")).getByTestId("position-row-AAPL")).toHaveTextContent("+$50.00");
  });

  it("shows the API error detail when a trade fails", async () => {
    const user = userEvent.setup();
    mockApi({ "POST /api/portfolio/trade": () => json({ detail: "Insufficient cash to buy 1000 AAPL" }, 400) });
    render(<Terminal />);
    await screen.findByTestId("watchlist-row-AAPL");
    await user.type(screen.getByTestId("trade-quantity"), "1000");
    await user.click(screen.getByTestId("trade-buy"));
    expect(await screen.findByTestId("trade-error")).toHaveTextContent("Insufficient cash to buy 1000 AAPL");
  });

  it("removes and adds watchlist tickers through the API", async () => {
    const user = userEvent.setup();
    const fetchMock = mockApi({
      "DELETE /api/watchlist/MSFT": () => Promise.resolve(new Response(null, { status: 204 })),
      "POST /api/watchlist": () => json(watch("PYPL"), 201),
    });
    render(<Terminal />);
    await user.click(await screen.findByTestId("watchlist-remove-MSFT"));
    await waitFor(() => expect(screen.queryByTestId("watchlist-row-MSFT")).not.toBeInTheDocument());
    await user.type(screen.getByTestId("watchlist-add-input"), "pypl");
    await user.click(screen.getByTestId("watchlist-add-button"));
    expect(await screen.findByTestId("watchlist-row-PYPL")).toBeInTheDocument();
    expect(screen.getByTestId("main-chart")).toHaveAttribute("data-ticker", "PYPL");
    expect(fetchMock.mock.calls.some(([url, init]) => url === "/api/watchlist/MSFT" && init?.method === "DELETE")).toBe(true);
  });

  it("sends a chat message, shows loading, then the reply and refreshes the portfolio", async () => {
    const user = userEvent.setup();
    let resolveChat: (r: Response) => void = () => {};
    const reply: ChatMessage = {
      id: "a1",
      role: "assistant",
      content: "Buying 5 AAPL for you.",
      created_at: "2026-01-01T00:00:02Z",
      actions: { trades: [{ ticker: "AAPL", side: "buy", quantity: 5, status: "executed", price: 200, error: null }], watchlist_changes: [] },
    };
    let portfolioCalls = 0;
    mockApi({
      "POST /api/chat": () => new Promise<Response>((r) => (resolveChat = r)),
      "GET /api/portfolio": () => json(portfolioCalls++ === 0 ? emptyPortfolio : boughtPortfolio),
    });
    render(<Terminal />);
    await screen.findByTestId("watchlist-row-AAPL");
    await user.type(screen.getByTestId("chat-input"), "buy 5 AAPL");
    await user.click(screen.getByTestId("chat-send"));

    expect(screen.getByTestId("chat-loading")).toBeInTheDocument();
    expect(screen.getAllByTestId("chat-message")[0]).toHaveAttribute("data-role", "user");

    resolveChat(new Response(JSON.stringify(reply), { status: 200 }));
    await waitFor(() => expect(screen.queryByTestId("chat-loading")).not.toBeInTheDocument());
    const assistant = screen.getAllByTestId("chat-message").find((m) => m.getAttribute("data-role") === "assistant");
    expect(assistant).toHaveTextContent("Buying 5 AAPL for you.");
    expect(screen.getByTestId("chat-action")).toHaveTextContent("Bought 5 AAPL at $200.00");
    expect(await screen.findByTestId("position-row-AAPL")).toBeInTheDocument();
  });
});
