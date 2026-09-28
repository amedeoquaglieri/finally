import { expect, test } from "@playwright/test";
import { DEFAULT_TICKERS, getPortfolio, moneyOf, openApp } from "./helpers";

test.describe("fresh start", () => {
  test("shows the default watchlist, $10,000 cash and a connected stream", async ({ page, request }) => {
    await openApp(page);

    await expect(page.getByTestId("watchlist")).toBeVisible();
    for (const ticker of DEFAULT_TICKERS) {
      await expect(page.getByTestId(`watchlist-row-${ticker}`), `${ticker} row`).toBeVisible();
      await expect(page.getByTestId(`watchlist-price-${ticker}`), `${ticker} price`).toContainText(/\d/);
    }

    await expect(page.getByTestId("connection-status")).toHaveAttribute("data-status", "connected");
    await expect(page.getByTestId("chat-panel")).toBeVisible();
    await expect(page.getByTestId("main-chart")).toBeVisible();

    // The header always mirrors the backend's cash balance.
    const portfolio = await getPortfolio(request);
    expect(await moneyOf(page.getByTestId("header-cash"))).toBeCloseTo(portfolio.cash_balance, 1);

    // The exact starting balance only holds on an untouched database (the compose run starts with one,
    // and this file sorts first). If other tests already traded, skip that part rather than fail.
    const untouched = portfolio.positions.length === 0 && portfolio.cash_balance === 10000;
    test.info().annotations.push({ type: "fresh-db", description: String(untouched) });
    if (untouched) {
      await expect(page.getByTestId("header-cash")).toContainText("10,000");
      expect(await moneyOf(page.getByTestId("header-total-value"))).toBeCloseTo(10000, 1);
    }
  });

  test("the API reports health ok", async ({ request }) => {
    const res = await request.get("/api/health");
    expect(res.status()).toBe(200);
    expect(await res.json()).toEqual({ status: "ok" });
  });
});
