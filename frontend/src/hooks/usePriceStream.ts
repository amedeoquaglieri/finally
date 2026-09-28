"use client";

import { useEffect, useState } from "react";
import { appendSnapshot, type PriceHistory } from "@/lib/priceHistory";
import type { PriceSnapshot } from "@/lib/types";

export type ConnectionStatus = "connected" | "reconnecting" | "disconnected";

const RETRY_MS = 3000;

/**
 * Subscribes to the SSE price stream. Each event is a full snapshot that
 * replaces the previous one; per-ticker history accumulates since page load.
 */
export function usePriceStream(url = "/api/stream/prices", maxPoints = 3000) {
  const [prices, setPrices] = useState<PriceSnapshot>({});
  const [history, setHistory] = useState<PriceHistory>({});
  const [status, setStatus] = useState<ConnectionStatus>("reconnecting");

  useEffect(() => {
    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;

    const connect = () => {
      const es = new EventSource(url);
      source = es;
      es.onopen = () => setStatus("connected");
      es.onmessage = (event: MessageEvent<string>) => {
        let snapshot: PriceSnapshot;
        try {
          snapshot = JSON.parse(event.data);
        } catch {
          return;
        }
        setStatus("connected");
        setPrices(snapshot);
        setHistory((prev) => appendSnapshot(prev, snapshot, maxPoints));
      };
      es.onerror = () => {
        if (es.readyState === EventSource.CLOSED) {
          // The browser gave up; retry ourselves.
          setStatus("disconnected");
          es.close();
          retryTimer = setTimeout(() => {
            if (disposed) return;
            setStatus("reconnecting");
            connect();
          }, RETRY_MS);
        } else {
          // EventSource is retrying on its own.
          setStatus("reconnecting");
        }
      };
    };

    connect();
    return () => {
      disposed = true;
      clearTimeout(retryTimer);
      source?.close();
    };
  }, [url, maxPoints]);

  return { prices, history, status };
}
