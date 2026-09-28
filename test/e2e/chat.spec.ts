import { expect, test } from "@playwright/test";
import { ensureNotInWatchlist, getPosition, openApp, sendChat } from "./helpers";

// These rely on the deterministic LLM mock (LLM_MOCK=true), see planning/TEAM_CONTRACTS.md §6.

test.describe("AI chat (mocked)", () => {
  test("plain message gets the mock reply", async ({ page }) => {
    await openApp(page);
    // Unique text so earlier runs' history (same DB) can't match.
    const question = `How is my portfolio doing? (${Date.now()})`;
    await sendChat(page, question, "This is a mock response from FinAlly");
    await expect(
      page.locator('[data-testid="chat-message"][data-role="user"]').filter({ hasText: question }),
    ).toBeVisible();
  });

  test("buy via chat executes the trade and shows it inline", async ({ page, request }) => {
    const ticker = "NVDA";
    const before = (await getPosition(request, ticker))?.quantity ?? 0;
    await openApp(page);

    await sendChat(page, `buy 1 ${ticker}`, `Buying 1 ${ticker} for you.`);
    await expect(page.getByTestId("chat-action").filter({ hasText: ticker }).last()).toBeVisible();
    await expect(page.getByTestId(`position-row-${ticker}`)).toBeVisible();
    expect((await getPosition(request, ticker))?.quantity).toBeCloseTo(before + 1, 6);
  });

  test("add a ticker to the watchlist via chat", async ({ page, request }) => {
    const ticker = "PYPL";
    await ensureNotInWatchlist(request, ticker);
    await openApp(page);
    await expect(page.getByTestId(`watchlist-row-${ticker}`)).toHaveCount(0);

    await sendChat(page, `add ${ticker}`, `Adding ${ticker} to your watchlist.`);
    await expect(page.getByTestId("chat-action").filter({ hasText: ticker }).last()).toBeVisible();
    await expect(page.getByTestId(`watchlist-row-${ticker}`)).toBeVisible();
  });

  test("conversation history persists across reloads", async ({ page }) => {
    await openApp(page);
    const marker = `history check ${Date.now()}`;
    await sendChat(page, marker, "This is a mock response from FinAlly");
    await openApp(page);
    await expect(
      page.locator('[data-testid="chat-message"][data-role="user"]').filter({ hasText: marker }),
    ).toBeVisible();
  });
});
