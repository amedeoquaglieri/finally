import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ChatPanel from "./ChatPanel";
import type { ChatMessage } from "@/lib/types";

const messages: ChatMessage[] = [
  { id: "1", role: "user", content: "buy 5 AAPL", created_at: "", actions: null },
  {
    id: "2",
    role: "assistant",
    content: "Buying 5 AAPL for you.",
    created_at: "",
    actions: {
      trades: [{ ticker: "AAPL", side: "buy", quantity: 5, status: "executed", price: 190, error: null }],
      watchlist_changes: [{ ticker: "PYPL", action: "add", status: "failed", error: "PYPL is already in your watchlist" }],
    },
  },
];

function setup(props: Partial<Parameters<typeof ChatPanel>[0]> = {}) {
  const all = { messages, loading: false, onSend: vi.fn(), open: true, onToggle: vi.fn(), ...props };
  render(<ChatPanel {...all} />);
  return all;
}

describe("ChatPanel", () => {
  it("renders messages with roles and inline action confirmations", () => {
    setup();
    const rendered = screen.getAllByTestId("chat-message");
    expect(rendered.map((m) => m.getAttribute("data-role"))).toEqual(["user", "assistant"]);
    const actions = screen.getAllByTestId("chat-action");
    expect(actions).toHaveLength(2);
    expect(actions[0]).toHaveTextContent("Bought 5 AAPL at $190.00");
    expect(actions[0]).toHaveAttribute("data-status", "executed");
    expect(actions[1]).toHaveTextContent("Add PYPL failed: PYPL is already in your watchlist");
    expect(actions[1]).toHaveAttribute("data-status", "failed");
  });

  it("shows the loading indicator and disables sending while waiting", () => {
    setup({ loading: true });
    expect(screen.getByTestId("chat-loading")).toBeInTheDocument();
    expect(screen.getByTestId("chat-send")).toBeDisabled();
  });

  it("hides the loading indicator when idle", () => {
    setup();
    expect(screen.queryByTestId("chat-loading")).not.toBeInTheDocument();
  });

  it("sends the trimmed message and clears the input", async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.type(screen.getByTestId("chat-input"), "  how am I doing?  {Enter}");
    expect(props.onSend).toHaveBeenCalledWith("how am I doing?");
    expect(screen.getByTestId("chat-input")).toHaveValue("");
  });

  it("collapses to a rail", () => {
    setup({ open: false });
    expect(screen.getByTestId("chat-panel")).toHaveAttribute("data-open", "false");
    expect(screen.queryByTestId("chat-input")).not.toBeInTheDocument();
  });
});
