import { expect, test } from "@playwright/test";
import {
  expectHeaderCash,
  flattenPosition,
  getPortfolio,
  getPosition,
  moneyOf,
  openApp,
  positionQuantityCell,
  quantityPattern,
  submitTrade,
  tradeViaApi,
  type Trade,
} from "./helpers";

test.describe("trading", () => {
  test("buy shares: cash decreases, position appears, portfolio updates", async ({ page, request }) => {
    const ticker = "AAPL";
    await flattenPosition(request, ticker);
    await openApp(page);
    await expect(page.getByTestId(`position-row-${ticker}`)).toHaveCount(0);
    const cashBefore = (await getPortfolio(request)).cash_balance;

    const response = await submitTrade(page, ticker, "buy", 2);
    expect(response.status(), await response.text()).toBe(200);
    const trade = ((await response.json()) as { trade: Trade }).trade;
    expect(trade).toMatchObject({ ticker, side: "buy", quantity: 2 });

    const expectedCash = cashBefore - 2 * trade.price;
    await expectHeaderCash(page, expectedCash);

    const row = page.getByTestId(`position-row-${ticker}`);
    await expect(page.getByTestId("positions-table")).toBeVisible();
    await expect(row).toBeVisible();
    await expect(positionQuantityCell(page, ticker)).toHaveText(quantityPattern(2));
    await expect(page.getByTestId("trade-error")).toBeHidden();

    const portfolio = await getPortfolio(request);
    expect(portfolio.cash_balance).toBeCloseTo(expectedCash, 2);
    const position = portfolio.positions.find((p) => p.ticker === ticker);
    expect(position?.quantity).toBe(2);
    expect(position?.avg_cost).toBeCloseTo(trade.price, 2);

    // Header total value = cash + holdings (prices keep moving, so allow a small band).
    await expect
      .poll(async () => Math.abs((await moneyOf(page.getByTestId("header-total-value"))) - portfolio.total_value))
      .toBeLessThan(portfolio.total_value * 0.01);
  });

  test("sell shares: cash increases, position shrinks then disappears", async ({ page, request }) => {
    const ticker = "MSFT";
    await flattenPosition(request, ticker);
    await tradeViaApi(request, ticker, "buy", 5);
    await openApp(page);

    const row = page.getByTestId(`position-row-${ticker}`);
    await expect(positionQuantityCell(page, ticker)).toHaveText(quantityPattern(5));

    // Partial sell
    let cashBefore = (await getPortfolio(request)).cash_balance;
    let response = await submitTrade(page, ticker, "sell", 2);
    expect(response.status(), await response.text()).toBe(200);
    let trade = ((await response.json()) as { trade: Trade }).trade;
    await expectHeaderCash(page, cashBefore + 2 * trade.price);
    await expect(positionQuantityCell(page, ticker)).toHaveText(quantityPattern(3));
    expect((await getPosition(request, ticker))?.quantity).toBe(3);

    // Sell the rest: the row goes away
    cashBefore = (await getPortfolio(request)).cash_balance;
    response = await submitTrade(page, ticker, "sell", 3);
    expect(response.status(), await response.text()).toBe(200);
    trade = ((await response.json()) as { trade: Trade }).trade;
    await expectHeaderCash(page, cashBefore + 3 * trade.price);
    await expect(row).toHaveCount(0);
    expect(await getPosition(request, ticker)).toBeUndefined();
  });

  test("buying with insufficient cash shows a trade error and changes nothing", async ({ page, request }) => {
    await openApp(page);
    const before = await getPortfolio(request);

    const response = await submitTrade(page, "AMZN", "buy", 1_000_000);
    expect(response.status()).toBe(400);

    const error = page.getByTestId("trade-error");
    await expect(error).toBeVisible();
    await expect(error).toContainText(/\S/);

    const after = await getPortfolio(request);
    expect(after.cash_balance).toBeCloseTo(before.cash_balance, 2);
    expect(after.positions.find((p) => p.ticker === "AMZN")?.quantity).toBe(
      before.positions.find((p) => p.ticker === "AMZN")?.quantity,
    );
    await expectHeaderCash(page, before.cash_balance);
  });

  test("selling shares you don't own shows a trade error", async ({ page, request }) => {
    const ticker = "META";
    await flattenPosition(request, ticker);
    await openApp(page);
    const cashBefore = (await getPortfolio(request)).cash_balance;

    const response = await submitTrade(page, ticker, "sell", 1);
    expect(response.status()).toBe(400);
    await expect(page.getByTestId("trade-error")).toBeVisible();
    await expect(page.getByTestId(`position-row-${ticker}`)).toHaveCount(0);
    expect((await getPortfolio(request)).cash_balance).toBeCloseTo(cashBefore, 2);
  });
});
