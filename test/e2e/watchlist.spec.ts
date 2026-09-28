import { expect, test } from "@playwright/test";
import { ensureNotInWatchlist, getWatchlistTickers, openApp } from "./helpers";

// A ticker outside the default list and not used by any other spec.
const TICKER = "IBM";

test.describe("watchlist", () => {
  test("add and remove a ticker", async ({ page, request }) => {
    await ensureNotInWatchlist(request, TICKER);
    await openApp(page);
    await expect(page.getByTestId(`watchlist-row-${TICKER}`)).toHaveCount(0);

    await page.getByTestId("watchlist-add-input").fill(TICKER);
    await page.getByTestId("watchlist-add-button").click();

    const row = page.getByTestId(`watchlist-row-${TICKER}`);
    await expect(row).toBeVisible();
    // The new ticker starts streaming a price.
    await expect(page.getByTestId(`watchlist-price-${TICKER}`)).toContainText(/\d/, { timeout: 15_000 });
    expect(await getWatchlistTickers(request)).toContain(TICKER);

    // Survives a reload (persisted server-side).
    await openApp(page);
    await expect(row).toBeVisible();

    await page.getByTestId(`watchlist-remove-${TICKER}`).click();
    await expect(row).toHaveCount(0);
    expect(await getWatchlistTickers(request)).not.toContain(TICKER);

    await openApp(page);
    await expect(row).toHaveCount(0);
  });

  test("clicking a watchlist ticker selects it in the main chart", async ({ page }) => {
    await openApp(page);
    const chart = page.getByTestId("main-chart");

    for (const ticker of ["TSLA", "JPM"]) {
      await page.getByTestId(`watchlist-row-${ticker}`).getByText(ticker, { exact: true }).first().click();
      await expect(chart).toHaveAttribute("data-ticker", ticker);
    }
  });
});
