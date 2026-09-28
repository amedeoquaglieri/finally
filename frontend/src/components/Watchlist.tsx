"use client";

import { useState, type FormEvent } from "react";
import Panel from "./Panel";
import Sparkline from "./Sparkline";
import { usePriceFlash } from "@/hooks/usePriceFlash";
import { formatPercent, formatPrice, signOf } from "@/lib/format";
import type { PricePoint, PriceHistory } from "@/lib/priceHistory";
import type { PriceSnapshot, PriceUpdate, WatchlistEntry } from "@/lib/types";

const TONE = { up: "text-up", down: "text-down", flat: "text-muted" } as const;

interface WatchlistProps {
  entries: WatchlistEntry[];
  prices: PriceSnapshot;
  history: PriceHistory;
  selected: string | null;
  onSelect: (ticker: string) => void;
  onAdd: (ticker: string) => Promise<void>;
  onRemove: (ticker: string) => Promise<void>;
}

export default function Watchlist({ entries, prices, history, selected, onSelect, onAdd, onRemove }: WatchlistProps) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const ticker = draft.trim().toUpperCase();
    if (!ticker) return;
    setBusy(true);
    setError(null);
    try {
      await onAdd(ticker);
      setDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (ticker: string) => {
    setError(null);
    try {
      await onRemove(ticker);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <Panel title="Watchlist" meta={<span>{entries.length} tickers</span>} testId="watchlist" className="h-full" bodyClassName="flex flex-col">
      <div className="grid grid-cols-[1fr_72px_76px_60px_20px] items-center gap-x-2 border-b border-line px-3 py-1 text-[11px] text-faint">
        <span>Ticker</span>
        <span>Session</span>
        <span className="text-right">Last</span>
        <span className="text-right">Day</span>
        <span />
      </div>
      <ul className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        {entries.map((entry) => (
          <WatchlistRow
            key={entry.ticker}
            entry={entry}
            live={prices[entry.ticker]}
            points={history[entry.ticker] ?? EMPTY}
            selected={entry.ticker === selected}
            onSelect={onSelect}
            onRemove={remove}
          />
        ))}
        {entries.length === 0 ? (
          <li className="px-3 py-6 text-sm text-muted">Your watchlist is empty. Add a ticker below.</li>
        ) : null}
      </ul>
      <form onSubmit={submit} className="shrink-0 border-t border-line p-2">
        <div className="flex gap-2">
          <label htmlFor="watchlist-add" className="sr-only">
            Ticker to add
          </label>
          <input
            id="watchlist-add"
            data-testid="watchlist-add-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value.toUpperCase())}
            placeholder="Add ticker, e.g. PYPL"
            maxLength={10}
            autoComplete="off"
            spellCheck={false}
            className="min-w-0 flex-1 border border-line bg-bg px-2 py-1.5 text-sm uppercase text-ink placeholder:normal-case placeholder:text-faint focus:border-blue focus:outline-none"
          />
          <button
            type="submit"
            data-testid="watchlist-add-button"
            disabled={busy || !draft.trim()}
            className="border border-blue/60 px-3 py-1.5 text-sm font-medium text-blue hover:bg-blue/10 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Add
          </button>
        </div>
        {error ? (
          <p role="alert" className="mt-1.5 text-xs text-down">
            {error}
          </p>
        ) : null}
      </form>
    </Panel>
  );
}

const EMPTY: PricePoint[] = [];

interface RowProps {
  entry: WatchlistEntry;
  live: PriceUpdate | undefined;
  points: PricePoint[];
  selected: boolean;
  onSelect: (ticker: string) => void;
  onRemove: (ticker: string) => void;
}

function WatchlistRow({ entry, live, points, selected, onSelect, onRemove }: RowProps) {
  const price = live?.price ?? entry.price;
  const dayPct = live?.day_change_percent ?? entry.day_change_percent;
  const flash = usePriceFlash(price);
  const t = entry.ticker;

  return (
    <li
      data-testid={`watchlist-row-${t}`}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={() => onSelect(t)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(t);
        }
      }}
      className={`group grid cursor-pointer grid-cols-[1fr_72px_76px_60px_20px] items-center gap-x-2 border-b border-line/60 border-l-2 py-1.5 pl-2.5 pr-3 hover:bg-raised ${
        selected ? "border-l-accent bg-raised" : "border-l-transparent"
      }`}
    >
      <span className={`text-sm font-semibold ${selected ? "text-accent" : "text-ink"}`}>{t}</span>
      <Sparkline points={points} />
      <span
        data-testid={`watchlist-price-${t}`}
        className={`price-cell num rounded-sm px-1 text-right text-sm text-ink ${flash}`}
      >
        {formatPrice(price)}
      </span>
      <span className={`num text-right text-xs ${TONE[signOf(dayPct)]}`}>{formatPercent(dayPct)}</span>
      <button
        type="button"
        data-testid={`watchlist-remove-${t}`}
        aria-label={`Remove ${t} from watchlist`}
        title={`Remove ${t}`}
        onClick={(e) => {
          e.stopPropagation();
          onRemove(t);
        }}
        className="flex h-5 w-5 items-center justify-center text-faint opacity-50 hover:text-down hover:opacity-100 focus-visible:opacity-100 group-hover:opacity-100"
      >
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
          <path d="M1 1l8 8M9 1l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
    </li>
  );
}
