import { expect, test } from "@playwright/test";
import { openApp, startFlakyProxy } from "./helpers";

test.describe("live prices", () => {
  test("prices stream and update in the watchlist", async ({ page }) => {
    await openApp(page);
    const price = page.getByTestId("watchlist-price-AAPL");
    await expect(price).toContainText(/\d/);
    const initial = await price.textContent();
    await expect.poll(() => price.textContent(), { timeout: 15_000 }).not.toBe(initial);
  });

  test("SSE reconnects after the connection drops", async ({ page, baseURL }) => {
    const proxy = await startFlakyProxy(baseURL!);
    try {
      await openApp(page, `${proxy.url}/`);
      const status = page.getByTestId("connection-status");
      const price = page.getByTestId("watchlist-price-AAPL");

      proxy.cut();
      await expect(status).toHaveAttribute("data-status", /^(reconnecting|disconnected)$/, { timeout: 15_000 });
      // Stays down while the network is down.
      await page.waitForTimeout(2_000);
      await expect(status).not.toHaveAttribute("data-status", "connected");

      proxy.restore();
      await expect(status).toHaveAttribute("data-status", "connected", { timeout: 30_000 });

      // Prices flow again after reconnecting.
      const afterReconnect = await price.textContent();
      await expect.poll(() => price.textContent(), { timeout: 15_000 }).not.toBe(afterReconnect);
    } finally {
      await proxy.close();
    }
  });
});
