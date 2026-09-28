"use client";

import { useEffect, useMemo, useRef } from "react";
import { BaselineSeries, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import Panel from "./Panel";
import { baseChartOptions } from "@/lib/chartTheme";
import { colors } from "@/lib/colors";
import { formatSignedCurrency, signOf } from "@/lib/format";
import { snapshotsToPoints } from "@/lib/snapshots";
import type { Snapshot } from "@/lib/types";

const TONE = { up: "text-up", down: "text-down", flat: "text-muted" } as const;

interface PnlChartProps {
  snapshots: Snapshot[];
  liveValue: number | null;
  /** Unix seconds of the latest price tick, used to place the live point. */
  liveTime: number | null;
}

export default function PnlChart({ snapshots, liveValue, liveTime }: PnlChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Baseline"> | null>(null);

  // Round the live value to cents so the chart only redraws on visible changes.
  const liveCents = liveValue == null ? null : Math.round(liveValue * 100) / 100;
  const liveSecond = liveTime == null ? null : Math.floor(liveTime);
  const points = useMemo(
    () => snapshotsToPoints(snapshots, liveSecond == null ? null : liveCents, liveSecond ?? 0),
    [snapshots, liveCents, liveSecond],
  );
  const base = points.length ? points[0].value : null;
  const change = base != null && points.length ? points[points.length - 1].value - base : null;

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      ...baseChartOptions(),
      timeScale: { borderColor: colors.line, timeVisible: true, secondsVisible: false },
    });
    seriesRef.current = chart.addSeries(BaselineSeries, {
      lineWidth: 2,
      topLineColor: colors.up,
      topFillColor1: "rgba(52, 185, 138, 0.22)",
      topFillColor2: "rgba(52, 185, 138, 0.02)",
      bottomLineColor: colors.down,
      bottomFillColor1: "rgba(238, 93, 108, 0.02)",
      bottomFillColor2: "rgba(238, 93, 108, 0.22)",
      priceLineVisible: false,
    });
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    if (base != null) series.applyOptions({ baseValue: { type: "price", price: base } });
    series.setData(points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
  }, [points, base]);

  const pointCount = points.length;
  useEffect(() => {
    chartRef.current?.timeScale().fitContent();
  }, [pointCount]);

  return (
    <Panel
      title="Portfolio value"
      testId="pnl-chart"
      attrs={{ "data-points": String(snapshots.length) }}
      className="h-full"
      bodyClassName="relative"
      meta={
        change != null ? (
          <span className={`num ${TONE[signOf(change)]}`}>{formatSignedCurrency(change)} since start</span>
        ) : null
      }
    >
      <div className="absolute inset-0" ref={containerRef} />
      {points.length < 2 ? (
        <p className="pointer-events-none absolute inset-0 flex items-center justify-center px-4 text-center text-sm text-muted">
          Portfolio value is recorded every 30 seconds and after each trade.
        </p>
      ) : null}
    </Panel>
  );
}
