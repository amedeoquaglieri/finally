import type { ConnectionStatus } from "@/hooks/usePriceStream";
import { formatCurrency, formatSignedCurrency, signOf } from "@/lib/format";

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connected: "Live",
  reconnecting: "Reconnecting",
  disconnected: "Disconnected",
};

const STATUS_DOT: Record<ConnectionStatus, string> = {
  connected: "bg-up shadow-[0_0_0_3px_rgb(52_185_138/0.2)]",
  reconnecting: "bg-accent shadow-[0_0_0_3px_rgb(236_173_10/0.2)]",
  disconnected: "bg-down shadow-[0_0_0_3px_rgb(238_93_108/0.2)]",
};

const TONE = { up: "text-up", down: "text-down", flat: "text-muted" } as const;

interface HeaderProps {
  totalValue: number | null;
  cash: number | null;
  unrealizedPnl: number | null;
  status: ConnectionStatus;
}

export default function Header({ totalValue, cash, unrealizedPnl, status }: HeaderProps) {
  return (
    <header className="flex shrink-0 flex-wrap items-center gap-x-8 gap-y-2 border-b border-line bg-panel px-4 py-2">
      <div className="flex items-baseline gap-2">
        <span className="text-xl font-semibold tracking-tight text-ink">
          Fin<span className="text-accent">Ally</span>
        </span>
        <span className="hidden text-xs text-faint sm:inline">AI trading workstation</span>
      </div>

      <dl className="flex flex-wrap items-baseline gap-x-8 gap-y-1">
        <div className="flex items-baseline gap-2">
          <dt className="text-xs text-muted">Portfolio value</dt>
          <dd data-testid="header-total-value" className="text-2xl font-semibold text-ink">
            {formatCurrency(totalValue)}
          </dd>
        </div>
        <div className="flex items-baseline gap-2">
          <dt className="text-xs text-muted">Cash</dt>
          <dd data-testid="header-cash" className="text-base font-medium text-ink">
            {formatCurrency(cash)}
          </dd>
        </div>
        <div className="flex items-baseline gap-2">
          <dt className="text-xs text-muted">Unrealized P&amp;L</dt>
          <dd className={`text-base font-medium ${TONE[signOf(unrealizedPnl)]}`}>
            {formatSignedCurrency(unrealizedPnl)}
          </dd>
        </div>
      </dl>

      <div className="ml-auto flex items-center gap-2 text-xs text-muted" role="status" aria-live="polite">
        <span
          data-testid="connection-status"
          data-status={status}
          aria-label={`Price stream ${STATUS_LABEL[status].toLowerCase()}`}
          className={`inline-block h-2 w-2 rounded-full ${STATUS_DOT[status]}`}
        />
        <span>{STATUS_LABEL[status]}</span>
      </div>
    </header>
  );
}
