"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { formatCurrency, formatQuantity } from "@/lib/format";
import type { ChatActions, ChatMessage } from "@/lib/types";

interface ChatPanelProps {
  messages: ChatMessage[];
  loading: boolean;
  onSend: (message: string) => void;
  open: boolean;
  onToggle: () => void;
}

export default function ChatPanel({ messages, loading, onSend, open, onToggle }: ChatPanelProps) {
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading, open]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || loading) return;
    onSend(text);
    setDraft("");
  };

  if (!open) {
    return (
      <aside data-testid="chat-panel" data-open="false" className="flex shrink-0 flex-col border border-line bg-panel">
        <button
          type="button"
          onClick={onToggle}
          aria-label="Open FinAlly chat"
          aria-expanded={false}
          className="flex h-full w-10 flex-col items-center gap-3 py-3 text-muted hover:bg-raised hover:text-ink"
        >
          <ChevronIcon direction="left" />
          <span className="text-sm font-medium text-accent [writing-mode:vertical-rl]">Ask FinAlly</span>
        </button>
      </aside>
    );
  }

  return (
    <aside
      data-testid="chat-panel"
      data-open="true"
      className="flex min-h-0 w-full shrink-0 flex-col border border-line bg-panel lg:w-[360px]"
    >
      <header className="flex h-8 shrink-0 items-center justify-between border-b border-line px-3">
        <h2 className="text-[13px] font-medium text-accent">FinAlly assistant</h2>
        <button
          type="button"
          onClick={onToggle}
          aria-label="Collapse chat"
          aria-expanded={true}
          className="flex h-6 w-6 items-center justify-center text-muted hover:text-ink"
        >
          <ChevronIcon direction="right" />
        </button>
      </header>

      <div ref={listRef} className="scroll-thin min-h-0 flex-1 space-y-3 overflow-y-auto p-3" aria-live="polite">
        {messages.length === 0 && !loading ? <EmptyChat onPick={onSend} /> : null}
        {messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}
        {loading ? (
          <div data-testid="chat-loading" role="status" className="flex items-center gap-2 text-sm text-muted">
            <span className="flex gap-1" aria-hidden="true">
              <span className="typing-dot h-1.5 w-1.5 rounded-full bg-accent" />
              <span className="typing-dot h-1.5 w-1.5 rounded-full bg-accent" />
              <span className="typing-dot h-1.5 w-1.5 rounded-full bg-accent" />
            </span>
            FinAlly is thinking…
          </div>
        ) : null}
      </div>

      <form onSubmit={submit} className="flex shrink-0 gap-2 border-t border-line p-2">
        <label htmlFor="chat-input" className="sr-only">
          Message FinAlly
        </label>
        <input
          id="chat-input"
          data-testid="chat-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about your portfolio or place a trade"
          autoComplete="off"
          className="min-w-0 flex-1 border border-line bg-bg px-2 py-1.5 text-sm text-ink placeholder:text-faint focus:border-blue focus:outline-none"
        />
        <button
          type="submit"
          data-testid="chat-send"
          disabled={loading || !draft.trim()}
          className="bg-purple px-3 py-1.5 text-sm font-semibold text-white hover:bg-purple-hi disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </aside>
  );
}

const SUGGESTIONS = ["How is my portfolio doing?", "Buy 5 AAPL", "Add PYPL to my watchlist"];

function EmptyChat({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="space-y-2 text-sm text-muted">
      <p>Ask for analysis, or tell FinAlly what to trade. Orders it places fill instantly.</p>
      <div className="flex flex-wrap gap-1.5">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="border border-line px-2 py-1 text-xs text-ink hover:border-accent/60"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div data-testid="chat-message" data-role={message.role} className={`flex flex-col ${isUser ? "items-end" : "items-start"}`}>
      <div
        className={`max-w-[92%] whitespace-pre-wrap px-2.5 py-1.5 text-sm leading-snug ${
          isUser ? "bg-blue/15 text-ink" : "border-l-2 border-accent/70 bg-raised text-ink"
        }`}
      >
        {message.content}
      </div>
      {!isUser && message.actions ? <ActionList actions={message.actions} /> : null}
    </div>
  );
}

export function ActionList({ actions }: { actions: ChatActions }) {
  const items: { key: string; ok: boolean; text: string }[] = [];
  (actions.trades ?? []).forEach((t, i) => {
    const verb = t.side === "buy" ? "Bought" : "Sold";
    const ok = t.status === "executed";
    items.push({
      key: `t${i}`,
      ok,
      text: ok
        ? `${verb} ${formatQuantity(t.quantity)} ${t.ticker}${t.price != null ? ` at ${formatCurrency(t.price)}` : ""}`
        : `${t.side === "buy" ? "Buy" : "Sell"} ${formatQuantity(t.quantity)} ${t.ticker} failed: ${t.error ?? "unknown error"}`,
    });
  });
  (actions.watchlist_changes ?? []).forEach((w, i) => {
    const ok = w.status === "executed";
    items.push({
      key: `w${i}`,
      ok,
      text: ok
        ? w.action === "add"
          ? `Added ${w.ticker} to watchlist`
          : `Removed ${w.ticker} from watchlist`
        : `${w.action === "add" ? "Add" : "Remove"} ${w.ticker} failed: ${w.error ?? "unknown error"}`,
    });
  });
  if (!items.length) return null;
  return (
    <ul className="mt-1 max-w-[92%] space-y-1">
      {items.map((item) => (
        <li
          key={item.key}
          data-testid="chat-action"
          data-status={item.ok ? "executed" : "failed"}
          className={`flex items-start gap-1.5 border px-2 py-1 text-xs ${
            item.ok ? "border-up/40 text-up" : "border-down/40 text-down"
          }`}
        >
          <span aria-hidden="true">{item.ok ? "✓" : "✕"}</span>
          <span>{item.text}</span>
        </li>
      ))}
    </ul>
  );
}

function ChevronIcon({ direction }: { direction: "left" | "right" }) {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path
        d={direction === "left" ? "M7.5 2.5L4 6l3.5 3.5" : "M4.5 2.5L8 6l-3.5 3.5"}
        stroke="currentColor"
        strokeWidth="1.5"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
