import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { MockEventSource } from "./mockEventSource";

// Canvas charts don't render in jsdom; stub the library with no-op charts.
vi.mock("lightweight-charts", () => {
  const series = () => ({ setData: vi.fn(), update: vi.fn(), applyOptions: vi.fn() });
  return {
    createChart: vi.fn(() => ({
      addSeries: vi.fn(series),
      remove: vi.fn(),
      timeScale: () => ({ fitContent: vi.fn() }),
      applyOptions: vi.fn(),
    })),
    AreaSeries: "Area",
    BaselineSeries: "Baseline",
    LineSeries: "Line",
    ColorType: { Solid: "solid" },
    CrosshairMode: { Normal: 0, Magnet: 1 },
  };
});

vi.stubGlobal("EventSource", MockEventSource);

afterEach(() => {
  cleanup();
  MockEventSource.reset();
});
