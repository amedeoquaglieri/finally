import Panel from "./Panel";
import { formatCurrency, formatPercent, formatPrice, formatQuantity, formatSignedCurrency, signOf } from "@/lib/format";
import type { Position } from "@/lib/types";

const TONE = { up: "text-up", down: "text-down", flat: "text-muted" } as const;

interface PositionsTableProps {
  positions: Position[];
  selected: string | null;
  onSelect: (ticker: string) => void;
}

export default function PositionsTable({ positions, selected, onSelect }: PositionsTableProps) {
  return (
    <Panel title="Positions" meta={<span>{positions.length} open</span>} className="h-full" bodyClassName="scroll-thin overflow-auto">
      <table data-testid="positions-table" className="w-full min-w-[560px] border-collapse text-sm">
        <thead className="sticky top-0 bg-panel text-[11px] text-faint">
          <tr className="border-b border-line">
            <th className="px-3 py-1 text-left font-normal">Ticker</th>
            <th className="px-2 py-1 text-right font-normal">Qty</th>
            <th className="px-2 py-1 text-right font-normal">Avg cost</th>
            <th className="px-2 py-1 text-right font-normal">Price</th>
            <th className="px-2 py-1 text-right font-normal">Market value</th>
            <th className="px-2 py-1 text-right font-normal">Unrealized P&amp;L</th>
            <th className="px-2 py-1 text-right font-normal">Change</th>
            <th className="px-3 py-1 text-right font-normal">Weight</th>
          </tr>
        </thead>
        <tbody className="num">
          {positions.map((p) => {
            const tone = TONE[signOf(p.unrealized_pnl)];
            return (
              <tr
                key={p.ticker}
                data-testid={`position-row-${p.ticker}`}
                onClick={() => onSelect(p.ticker)}
                className={`cursor-pointer border-b border-line/60 hover:bg-raised ${p.ticker === selected ? "bg-raised" : ""}`}
              >
                <td className="px-3 py-1.5 text-left font-semibold">{p.ticker}</td>
                <td className="px-2 py-1.5 text-right">{formatQuantity(p.quantity)}</td>
                <td className="px-2 py-1.5 text-right text-muted">{formatPrice(p.avg_cost)}</td>
                <td className="px-2 py-1.5 text-right">{formatPrice(p.current_price)}</td>
                <td className="px-2 py-1.5 text-right">{formatCurrency(p.market_value)}</td>
                <td className={`px-2 py-1.5 text-right ${tone}`}>{formatSignedCurrency(p.unrealized_pnl)}</td>
                <td className={`px-2 py-1.5 text-right ${tone}`}>{formatPercent(p.unrealized_pnl_percent)}</td>
                <td className="px-3 py-1.5 text-right text-muted">{p.weight.toFixed(1)}%</td>
              </tr>
            );
          })}
          {positions.length === 0 ? (
            <tr>
              <td colSpan={8} className="px-3 py-5 text-center font-sans text-sm text-muted">
                No open positions. Buy from the trade bar or ask FinAlly.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </Panel>
  );
}
