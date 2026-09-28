import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Heatmap from "./Heatmap";
import type { Position } from "@/lib/types";

const position = (ticker: string, pnl: number, value: number): Position => ({
  ticker,
  quantity: 1,
  avg_cost: value - pnl,
  current_price: value,
  market_value: value,
  unrealized_pnl: pnl,
  unrealized_pnl_percent: (pnl / (value - pnl)) * 100,
  weight: 10,
});

function rgb(el: HTMLElement): number[] {
  return (el.style.backgroundColor.match(/\d+/g) ?? []).map(Number);
}

describe("Heatmap", () => {
  it("renders a cell per position with its P&L sign and matching color", () => {
    render(
      <Heatmap
        positions={[position("AAPL", 0.01, 1000), position("TSLA", -0.02, 500), position("MSFT", 0, 300)]}
        onSelect={vi.fn()}
      />,
    );
    const up = screen.getByTestId("heatmap-cell-AAPL");
    const down = screen.getByTestId("heatmap-cell-TSLA");
    const flat = screen.getByTestId("heatmap-cell-MSFT");
    expect(up).toHaveAttribute("data-pnl", "positive");
    expect(down).toHaveAttribute("data-pnl", "negative");
    expect(flat).toHaveAttribute("data-pnl", "flat");
    const [ur, ug] = rgb(up);
    const [dr, dg] = rgb(down);
    expect(ug).toBeGreaterThan(ur);
    expect(dr).toBeGreaterThan(dg);
  });

  it("shows an empty state without positions", () => {
    render(<Heatmap positions={[]} onSelect={vi.fn()} />);
    expect(screen.getByTestId("portfolio-heatmap")).toHaveTextContent("No positions yet");
  });
});
