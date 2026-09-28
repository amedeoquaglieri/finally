"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ChatPanel from "./ChatPanel";
import Header from "./Header";
import Heatmap from "./Heatmap";
import PnlChart from "./PnlChart";
import PositionsTable from "./PositionsTable";
import PriceChart from "./PriceChart";
import TradeBar from "./TradeBar";
import Watchlist from "./Watchlist";
import { usePriceStream } from "@/hooks/usePriceStream";
import { api } from "@/lib/api";
import { computeLivePortfolio } from "@/lib/portfolio";
import type { ChatMessage, Portfolio, Side, Snapshot, WatchlistEntry } from "@/lib/types";

const HISTORY_POLL_MS = 30_000;
const NO_POINTS: never[] = [];

export default function Terminal() {
  const { prices, history, status } = usePriceStream();
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([]);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatOpen, setChatOpen] = useState(true);

  const refreshPortfolio = useCallback(() => api.getPortfolio().then(setPortfolio).catch(() => {}), []);
  const refreshHistory = useCallback(() => api.getHistory().then(setSnapshots).catch(() => {}), []);
  const refreshWatchlist = useCallback(() => api.getWatchlist().then(setWatchlist).catch(() => {}), []);

  useEffect(() => {
    refreshPortfolio();
    refreshHistory();
    refreshWatchlist();
    api
      .getChatHistory()
      .then(setMessages)
      .catch(() => {});
    const timer = setInterval(() => {
      refreshHistory();
      refreshPortfolio();
    }, HISTORY_POLL_MS);
    return () => clearInterval(timer);
  }, [refreshPortfolio, refreshHistory, refreshWatchlist]);

  // Keep a valid selection: default to the first watchlist ticker.
  const activeTicker = selected ?? watchlist[0]?.ticker ?? null;

  const lastTick = useMemo(() => {
    const times = Object.values(prices).map((p) => p.timestamp);
    return times.length ? Math.max(...times) : null;
  }, [prices]);

  const live = useMemo(() => (portfolio ? computeLivePortfolio(portfolio, prices) : null), [portfolio, prices]);

  const handleTrade = useCallback(
    async (ticker: string, quantity: number, side: Side) => {
      const res = await api.trade(ticker, quantity, side);
      setPortfolio(res.portfolio);
      refreshHistory();
      return res.trade;
    },
    [refreshHistory],
  );

  const handleAdd = useCallback(async (ticker: string) => {
    const entry = await api.addWatchlist(ticker);
    setWatchlist((prev) => (prev.some((e) => e.ticker === entry.ticker) ? prev : [...prev, entry]));
    setSelected(entry.ticker);
  }, []);

  const handleRemove = useCallback(async (ticker: string) => {
    await api.removeWatchlist(ticker);
    setWatchlist((prev) => prev.filter((e) => e.ticker !== ticker));
    setSelected((cur) => (cur === ticker ? null : cur));
  }, []);

  const handleSend = useCallback(
    async (text: string) => {
      const pending: ChatMessage = {
        id: `local-${Date.now()}`,
        role: "user",
        content: text,
        created_at: new Date().toISOString(),
        actions: null,
      };
      setMessages((prev) => [...prev, pending]);
      setChatLoading(true);
      try {
        const reply = await api.sendChat(text);
        setMessages((prev) => [...prev, reply]);
        if (reply.actions?.trades?.length) {
          refreshPortfolio();
          refreshHistory();
        }
        if (reply.actions?.watchlist_changes?.length) refreshWatchlist();
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          {
            id: `local-error-${Date.now()}`,
            role: "assistant",
            content: `Message not delivered: ${err instanceof Error ? err.message : String(err)}`,
            created_at: new Date().toISOString(),
            actions: null,
          },
        ]);
      } finally {
        setChatLoading(false);
      }
    },
    [refreshPortfolio, refreshHistory, refreshWatchlist],
  );

  const selectedEntry = watchlist.find((e) => e.ticker === activeTicker);
  const selectedLive = activeTicker ? prices[activeTicker] : undefined;

  return (
    <div className="flex min-h-screen flex-col lg:h-screen">
      <Header
        totalValue={live?.totalValue ?? null}
        cash={live?.cash ?? null}
        unrealizedPnl={live?.unrealizedPnl ?? null}
        status={status}
      />
      <main className="flex min-h-0 flex-1 flex-col gap-1 p-1 lg:flex-row">
        <div className="h-[420px] shrink-0 lg:h-auto lg:w-[340px]">
          <Watchlist
            entries={watchlist}
            prices={prices}
            history={history}
            selected={activeTicker}
            onSelect={setSelected}
            onAdd={handleAdd}
            onRemove={handleRemove}
          />
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="h-[320px] lg:h-auto lg:min-h-[220px] lg:flex-[1.25]">
            <PriceChart
              ticker={activeTicker}
              points={(activeTicker && history[activeTicker]) || NO_POINTS}
              price={selectedLive?.price ?? selectedEntry?.price ?? null}
              dayChangePercent={selectedLive?.day_change_percent ?? selectedEntry?.day_change_percent ?? null}
            />
          </div>
          <TradeBar selectedTicker={activeTicker} onTrade={handleTrade} />
          <div className="grid gap-1 md:grid-cols-2 lg:min-h-[190px] lg:flex-1">
            <div className="h-[240px] lg:h-auto">
              <Heatmap positions={live?.positions ?? []} onSelect={setSelected} />
            </div>
            <div className="h-[240px] lg:h-auto">
              <PnlChart snapshots={snapshots} liveValue={live?.totalValue ?? null} liveTime={lastTick} />
            </div>
          </div>
          <div className="h-[220px] lg:h-auto lg:min-h-[130px] lg:flex-[0.75]">
            <PositionsTable positions={live?.positions ?? []} selected={activeTicker} onSelect={setSelected} />
          </div>
        </div>

        <div className="flex min-h-[420px] lg:min-h-0">
          <ChatPanel
            messages={messages}
            loading={chatLoading}
            onSend={handleSend}
            open={chatOpen}
            onToggle={() => setChatOpen((o) => !o)}
          />
        </div>
      </main>
    </div>
  );
}
