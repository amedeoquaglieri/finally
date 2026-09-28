"use client";

import { useEffect, useState, type FormEvent } from "react";
import { formatCurrency, formatQuantity } from "@/lib/format";
import type { Side, Trade } from "@/lib/types";

interface TradeBarProps {
  /** Ticker selected elsewhere; pre-fills the ticker field when it changes. */
  selectedTicker: string | null;
  onTrade: (ticker: string, quantity: number, side: Side) => Promise<Trade>;
}

export default function TradeBar({ selectedTicker, onTrade }: TradeBarProps) {
  const [ticker, setTicker] = useState(selectedTicker ?? "");
  const [quantity, setQuantity] = useState("");
  const [pending, setPending] = useState<Side | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);

  const [lastSelected, setLastSelected] = useState(selectedTicker);
  if (selectedTicker !== lastSelected) {
    setLastSelected(selectedTicker);
    if (selectedTicker) setTicker(selectedTicker);
  }

  useEffect(() => {
    if (!confirmation) return;
    const t = setTimeout(() => setConfirmation(null), 4000);
    return () => clearTimeout(t);
  }, [confirmation]);

  const submit = async (side: Side, e?: FormEvent) => {
    e?.preventDefault();
    const symbol = ticker.trim().toUpperCase();
    const qty = Number(quantity);
    setConfirmation(null);
    if (!symbol) {
      setError("Enter a ticker to trade.");
      return;
    }
    if (!quantity.trim() || !Number.isFinite(qty) || qty <= 0) {
      setError("Enter a quantity greater than zero.");
      return;
    }
    setError(null);
    setPending(side);
    try {
      const trade = await onTrade(symbol, qty, side);
      setConfirmation(
        `${trade.side === "buy" ? "Bought" : "Sold"} ${formatQuantity(trade.quantity)} ${trade.ticker} at ${formatCurrency(trade.price)}`,
      );
      setQuantity("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
    }
  };

  const inputClass =
    "border border-line bg-bg px-2 py-1.5 text-sm text-ink placeholder:text-faint focus:border-blue focus:outline-none";

  return (
    <form
      onSubmit={(e) => submit("buy", e)}
      aria-label="Market order"
      className="flex shrink-0 flex-wrap items-center gap-2 border border-line bg-panel px-3 py-2"
    >
      <span className="mr-1 text-[13px] font-medium text-muted">Market order</span>
      <label className="sr-only" htmlFor="trade-ticker">
        Ticker
      </label>
      <input
        id="trade-ticker"
        data-testid="trade-ticker"
        value={ticker}
        onChange={(e) => setTicker(e.target.value.toUpperCase())}
        placeholder="Ticker"
        maxLength={10}
        autoComplete="off"
        spellCheck={false}
        className={`${inputClass} w-24 font-semibold uppercase placeholder:font-normal placeholder:normal-case`}
      />
      <label className="sr-only" htmlFor="trade-quantity">
        Quantity
      </label>
      <input
        id="trade-quantity"
        data-testid="trade-quantity"
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
        placeholder="Shares"
        inputMode="decimal"
        type="number"
        min="0"
        step="any"
        className={`${inputClass} num w-24`}
      />
      <button
        type="button"
        data-testid="trade-buy"
        disabled={pending !== null}
        onClick={() => submit("buy")}
        className="bg-purple px-4 py-1.5 text-sm font-semibold text-white hover:bg-purple-hi disabled:opacity-50"
      >
        {pending === "buy" ? "Buying…" : "Buy"}
      </button>
      <button
        type="button"
        data-testid="trade-sell"
        disabled={pending !== null}
        onClick={() => submit("sell")}
        className="border border-purple bg-transparent px-4 py-1.5 text-sm font-semibold text-ink hover:bg-purple/25 disabled:opacity-50"
      >
        {pending === "sell" ? "Selling…" : "Sell"}
      </button>
      <div className="min-w-0 flex-1 text-sm" aria-live="polite">
        {error ? (
          <p data-testid="trade-error" role="alert" className="truncate text-down" title={error}>
            {error}
          </p>
        ) : confirmation ? (
          <p data-testid="trade-confirmation" className="truncate text-up">
            {confirmation}
          </p>
        ) : null}
      </div>
    </form>
  );
}
