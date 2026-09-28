"use client";

import { useEffect, useRef, useState } from "react";
import Panel from "./Panel";
import { HEATMAP_SATURATION_PCT, pnlColor } from "@/lib/colors";
import { formatCurrency, formatPercent, formatSignedCurrency } from "@/lib/format";
import { squarify } from "@/lib/treemap";
import type { Position } from "@/lib/types";

const GAP = 2;

interface HeatmapProps {
  positions: Position[];
  onSelect: (ticker: string) => void;
}

export default function Heatmap({ positions, onSelect }: HeatmapProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 400, h: 220 });

  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setSize({ w: width, h: height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const byTicker = new Map(positions.map((p) => [p.ticker, p]));
  const rects = squarify(
    positions.map((p) => ({ id: p.ticker, value: p.market_value })),
    size.w,
    size.h,
  );

  return (
    <Panel
      title="Positions by weight"
      testId="portfolio-heatmap"
      className="h-full"
      bodyClassName="relative"
      meta={<ScaleLegend />}
    >
      <div ref={ref} className="absolute inset-1">
        {rects.map((r) => {
          const p = byTicker.get(r.id)!;
          // Sign at cent precision, so float noise on an unmoved price reads as flat.
          const cents = Math.round(p.unrealized_pnl * 100);
          const pnl = cents > 0 ? "positive" : cents < 0 ? "negative" : "flat";
          const showLabel = r.w >= 44 && r.h >= 30;
          const showDetail = r.w >= 70 && r.h >= 48;
          return (
            <button
              type="button"
              key={r.id}
              data-testid={`heatmap-cell-${r.id}`}
              data-pnl={pnl}
              onClick={() => onSelect(r.id)}
              title={`${r.id}: ${formatCurrency(p.market_value)} (${p.weight.toFixed(1)}% of portfolio), P&L ${formatSignedCurrency(p.unrealized_pnl)} (${formatPercent(p.unrealized_pnl_percent)})`}
              aria-label={`${r.id}, ${p.weight.toFixed(1)} percent of portfolio, P&L ${formatPercent(p.unrealized_pnl_percent)}`}
              className="absolute flex flex-col items-start justify-between overflow-hidden rounded-[3px] p-1.5 text-left transition-[filter] hover:brightness-125"
              style={{
                left: `${(r.x / size.w) * 100}%`,
                top: `${(r.y / size.h) * 100}%`,
                width: `calc(${(r.w / size.w) * 100}% - ${GAP}px)`,
                height: `calc(${(r.h / size.h) * 100}% - ${GAP}px)`,
                backgroundColor: pnlColor(pnl === "flat" ? 0 : p.unrealized_pnl_percent),
              }}
            >
              {showLabel ? (
                <>
                  <span className="text-sm font-semibold leading-none text-white">{r.id}</span>
                  <span className="num text-xs leading-none text-white/85">
                    {formatPercent(p.unrealized_pnl_percent)}
                    {showDetail ? <span className="ml-1 text-white/60">{p.weight.toFixed(0)}%</span> : null}
                  </span>
                </>
              ) : null}
            </button>
          );
        })}
      </div>
      {positions.length === 0 ? (
        <p className="pointer-events-none absolute inset-0 flex items-center justify-center px-4 text-center text-sm text-muted">
          No positions yet. Buy from the trade bar or ask FinAlly.
        </p>
      ) : null}
    </Panel>
  );
}

function ScaleLegend() {
  return (
    <span className="flex items-center gap-1.5" aria-label={`Color scale from −${HEATMAP_SATURATION_PCT}% to +${HEATMAP_SATURATION_PCT}% P&L`}>
      <span>−{HEATMAP_SATURATION_PCT}%</span>
      <span
        className="inline-block h-2 w-16 rounded-sm"
        style={{
          background: `linear-gradient(to right, ${pnlColor(-HEATMAP_SATURATION_PCT)}, ${pnlColor(0)}, ${pnlColor(HEATMAP_SATURATION_PCT)})`,
        }}
      />
      <span>+{HEATMAP_SATURATION_PCT}%</span>
    </span>
  );
}
