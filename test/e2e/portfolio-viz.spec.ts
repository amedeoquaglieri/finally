import { expect, test } from "@playwright/test";
import { flattenPosition, openApp, tradeViaApi } from "./helpers";

test.describe("portfolio visualizations", () => {
  test("heatmap shows a P&L-colored cell for a held position", async ({ page, request }) => {
    const ticker = "GOOGL";
    await flattenPosition(request, ticker);
    await tradeViaApi(request, ticker, "buy", 3);
    await openApp(page);

    const heatmap = page.getByTestId("portfolio-heatmap");
    await expect(heatmap).toBeVisible();
    const cell = heatmap.getByTestId(`heatmap-cell-${ticker}`);
    await expect(cell).toBeVisible();
    await expect(cell).toHaveAttribute("data-pnl", /^(positive|negative|flat)$/);

    // Read the P&L sign and the rendered colour in one go (prices keep ticking, so the sign can flip).
    // Any non-zero P&L gets at least a minimal tint; "flat" cells are neutral slate, so no colour check.
    const { pnl, rgb } = await cell.evaluate((el) => {
      const parse = (c: string) => {
        const m = c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?/);
        if (!m || (m[4] !== undefined && Number(m[4]) === 0)) return null;
        return [Number(m[1]), Number(m[2]), Number(m[3])];
      };
      // The colour may be on the cell itself or on a descendant (e.g. an SVG <rect>).
      const candidates = [el, ...Array.from(el.querySelectorAll("*"))];
      for (const node of candidates) {
        const style = getComputedStyle(node);
        const color = parse(style.backgroundColor) ?? (node instanceof SVGElement ? parse(style.fill) : null);
        if (color) return { pnl: el.getAttribute("data-pnl"), rgb: color };
      }
      return { pnl: el.getAttribute("data-pnl"), rgb: null };
    });

    expect(rgb, "heatmap cell should have a fill or background colour").not.toBeNull();
    const [r, g] = rgb!;
    if (pnl === "positive") expect(g, `green-ish for profit, got rgb(${rgb})`).toBeGreaterThan(r);
    if (pnl === "negative") expect(r, `red-ish for loss, got rgb(${rgb})`).toBeGreaterThan(g);
  });

  test("P&L chart has data points", async ({ page, request }) => {
    // A trade records a snapshot, so there are at least two (startup + trade).
    await tradeViaApi(request, "V", "buy", 1);
    const history = (await (await request.get("/api/portfolio/history")).json()) as {
      snapshots: { total_value: number; recorded_at: string }[];
    };
    expect(history.snapshots.length).toBeGreaterThanOrEqual(2);

    await openApp(page);
    const chart = page.getByTestId("pnl-chart");
    await expect(chart).toBeVisible();
    await expect(chart.locator("canvas, svg").first()).toBeVisible();
    await expect
      .poll(async () => Number(await chart.getAttribute("data-points")))
      .toBeGreaterThanOrEqual(2);
  });
});
