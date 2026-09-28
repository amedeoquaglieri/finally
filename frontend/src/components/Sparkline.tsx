import type { PricePoint } from "@/lib/priceHistory";
import { colors } from "@/lib/colors";

interface SparklineProps {
  points: PricePoint[];
  width?: number;
  height?: number;
  maxPoints?: number;
}

export default function Sparkline({ points, width = 72, height = 22, maxPoints = 120 }: SparklineProps) {
  const data = points.length > maxPoints ? points.slice(points.length - maxPoints) : points;
  if (data.length < 2) {
    return <svg width={width} height={height} aria-hidden="true" className="block" />;
  }
  const values = data.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pad = 2;
  const step = (width - pad * 2) / (data.length - 1);
  const path = data
    .map((p, i) => {
      const x = pad + i * step;
      const y = pad + (1 - (p.value - min) / span) * (height - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const trend = values[values.length - 1] - values[0];
  const stroke = trend > 0 ? colors.up : trend < 0 ? colors.down : colors.muted;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true" className="block">
      <polyline points={path} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
