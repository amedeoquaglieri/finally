import { act } from "@testing-library/react";
import type { PriceSnapshot, PriceUpdate } from "@/lib/types";

export class MockEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static instances: MockEventSource[] = [];

  readyState = MockEventSource.CONNECTING;
  onopen: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;

  constructor(public url: string) {
    MockEventSource.instances.push(this);
  }

  close() {
    this.readyState = MockEventSource.CLOSED;
  }

  static latest(): MockEventSource {
    return MockEventSource.instances[MockEventSource.instances.length - 1];
  }

  static reset() {
    MockEventSource.instances = [];
  }

  open() {
    act(() => {
      this.readyState = MockEventSource.OPEN;
      this.onopen?.(new Event("open"));
    });
  }

  emit(snapshot: PriceSnapshot) {
    act(() => {
      this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(snapshot) }));
    });
  }

  fail(readyState: number) {
    act(() => {
      this.readyState = readyState;
      this.onerror?.(new Event("error"));
    });
  }
}

export function priceUpdate(ticker: string, price: number, timestamp: number, previousClose = price): PriceUpdate {
  const change = 0;
  return {
    ticker,
    price,
    previous_price: price,
    timestamp,
    change,
    change_percent: 0,
    direction: "flat",
    previous_close: previousClose,
    day_change: price - previousClose,
    day_change_percent: ((price - previousClose) / previousClose) * 100,
  };
}
