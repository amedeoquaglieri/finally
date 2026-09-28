import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Watchlist from "./Watchlist";
import { priceUpdate } from "@/test/mockEventSource";
import type { WatchlistEntry } from "@/lib/types";

const entry = (ticker: string, price: number | null = null): WatchlistEntry => ({
  ticker,
  price,
  previous_price: price,
  change: null,
  change_percent: null,
  day_change_percent: null,
  direction: null,
  timestamp: null,
});

function setup(overrides: Partial<Parameters<typeof Watchlist>[0]> = {}) {
  const props = {
    entries: [entry("AAPL", 190), entry("MSFT")],
    prices: {},
    history: {},
    selected: "AAPL",
    onSelect: vi.fn(),
    onAdd: vi.fn().mockResolvedValue(undefined),
    onRemove: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  const utils = render(<Watchlist {...props} />);
  return { ...utils, props };
}

describe("Watchlist", () => {
  it("renders a row per ticker with prices and day change", () => {
    setup({ prices: { MSFT: priceUpdate("MSFT", 420, 1, 400) } });
    expect(screen.getByTestId("watchlist-row-AAPL")).toBeInTheDocument();
    expect(screen.getByTestId("watchlist-price-AAPL")).toHaveTextContent("190.00");
    expect(screen.getByTestId("watchlist-price-MSFT")).toHaveTextContent("420.00");
    expect(screen.getByTestId("watchlist-row-MSFT")).toHaveTextContent("+5.00%");
  });

  it("flashes green on an uptick and red on a downtick, then clears", () => {
    vi.useFakeTimers();
    try {
      const { rerender, props } = setup({ prices: { AAPL: priceUpdate("AAPL", 190, 1) } });
      const cell = () => screen.getByTestId("watchlist-price-AAPL");
      expect(cell().className).not.toMatch(/flash-/);

      rerender(<Watchlist {...props} prices={{ AAPL: priceUpdate("AAPL", 191, 2) }} />);
      expect(cell()).toHaveClass("flash-up");
      act(() => vi.advanceTimersByTime(600));
      expect(cell().className).not.toMatch(/flash-/);

      rerender(<Watchlist {...props} prices={{ AAPL: priceUpdate("AAPL", 189, 3) }} />);
      expect(cell()).toHaveClass("flash-down");
    } finally {
      vi.useRealTimers();
    }
  });

  it("adds a ticker (uppercased) and clears the input", async () => {
    const user = userEvent.setup();
    const { props } = setup();
    await user.type(screen.getByTestId("watchlist-add-input"), "pypl");
    await user.click(screen.getByTestId("watchlist-add-button"));
    expect(props.onAdd).toHaveBeenCalledWith("PYPL");
    expect(screen.getByTestId("watchlist-add-input")).toHaveValue("");
  });

  it("shows the API error when adding fails", async () => {
    const user = userEvent.setup();
    setup({ onAdd: vi.fn().mockRejectedValue(new Error("AAPL is already in your watchlist")) });
    await user.type(screen.getByTestId("watchlist-add-input"), "AAPL{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("AAPL is already in your watchlist");
  });

  it("removes a ticker without selecting the row", async () => {
    const user = userEvent.setup();
    const { props } = setup();
    await user.click(screen.getByTestId("watchlist-remove-MSFT"));
    expect(props.onRemove).toHaveBeenCalledWith("MSFT");
    expect(props.onSelect).not.toHaveBeenCalled();
  });

  it("selects a ticker on click", async () => {
    const user = userEvent.setup();
    const { props } = setup();
    await user.click(screen.getByTestId("watchlist-row-MSFT"));
    expect(props.onSelect).toHaveBeenCalledWith("MSFT");
  });
});
