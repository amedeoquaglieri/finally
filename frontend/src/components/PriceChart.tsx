"use client";

import { useEffect, useRef } from "react";
import { AreaSeries, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import Panel from "./Panel";
import { baseChartOptions } from "@/lib/chartTheme";
import { colors } from "@/lib/colors";
import { formatPercent, formatPrice, signOf } from "@/lib/format";
import { toSecondBuckets, type PricePoint } from "@/lib/priceHistory";

const TONE = { up: "text-up", down: "text-down", flat: "text-muted" } as const;

interface PriceChartProps {
  ticker: string | null;
  points: PricePoint[];
  price: number | null;
  dayChangePercent: number | null;
}

export default function PriceChart({ ticker, points, price, dayChangePercent }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);
  const loaded = useRef<{ ticker: string | null; lastTime: number }>({ ticker: null, lastTime: -Infinity });

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, baseChartOptions());
    seriesRef.current = chart.addSeries(AreaSeries, {
      lineColor: colors.blue,
      lineWidth: 2,
      topColor: "rgba(32, 157, 215, 0.28)",
      bottomColor: "rgba(32, 157, 215, 0.02)",
      priceLineColor: colors.blue,
      lastValueVisible: true,
    });
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      loaded.current = { ticker: null, lastTime: -Infinity };
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    const buckets = toSecondBuckets(points);
    const state = loaded.current;
    if (state.ticker !== ticker) {
      series.setData(buckets.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
      chartRef.current?.timeScale().fitContent();
      state.ticker = ticker;
    } else {
      // Same ticker: push only the buckets at or after the last one drawn.
      for (const p of buckets) {
        if (p.time >= state.lastTime) series.update({ time: p.time as UTCTimestamp, value: p.value });
      }
    }
    state.lastTime = buckets.length ? buckets[buckets.length - 1].time : -Infinity;
  }, [ticker, points]);

  return (
    <Panel
      title={ticker ?? "Price chart"}
      testId="main-chart"
      attrs={{ "data-ticker": ticker ?? "" }}
      className="h-full"
      bodyClassName="relative"
      meta={
        ticker ? (
          <>
            <span className="num text-sm font-medium text-ink">{formatPrice(price)}</span>
            <span className={`num ${TONE[signOf(dayChangePercent)]}`}>{formatPercent(dayChangePercent)} today</span>
          </>
        ) : null
      }
    >
      <div className="absolute inset-0" ref={containerRef} />
      {ticker && points.length < 2 ? (
        <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-muted">
          Collecting prices for {ticker}…
        </p>
      ) : null}
      {!ticker ? (
        <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-muted">
          Select a ticker in the watchlist to chart it.
        </p>
      ) : null}
    </Panel>
  );
}
