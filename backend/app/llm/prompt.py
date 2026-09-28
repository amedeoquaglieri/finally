"""System prompt, portfolio context and conversation history for the LLM call."""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are FinAlly, an AI trading assistant built into a simulated trading workstation.
The user trades a virtual portfolio with fake money: market orders only, instant fill at the \
current price, no fees. Fractional shares are allowed.

Your job:
- Analyze the portfolio's composition, risk concentration and P&L using the live data provided.
- Suggest trades with brief reasoning.
- Execute trades when the user asks for them or agrees to a suggestion, by listing them in \
"trades". Listed trades execute immediately, so only include trades the user wants now.
- Manage the watchlist proactively via "watchlist_changes" (add tickers the user is \
discussing, remove ones they no longer care about when asked).
- Be concise and data-driven: use the actual numbers, keep answers short.

Rules:
- Buys need enough cash (quantity x current price); sells need enough shares held. Check the \
portfolio context before proposing a trade and don't list trades that would fail.
- Use plain uppercase ticker symbols (e.g. AAPL). Quantities are share counts, not dollars; \
if the user gives a dollar amount, convert it using the current price.
- Your "message" should describe what you are doing; the results of each trade and \
watchlist change are shown to the user separately.
- Always respond with valid JSON matching the schema: {"message": string, \
"trades": [{"ticker", "side": "buy"|"sell", "quantity"}], \
"watchlist_changes": [{"ticker", "action": "add"|"remove"}]}. Use empty lists when there \
are no actions."""


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def build_context(portfolio: dict, watchlist: list[dict]) -> str:
    """Render the portfolio (GET /api/portfolio shape) and watchlist entries as prompt text."""
    lines = [
        "Current portfolio state (live):",
        f"- Cash: {_money(portfolio['cash_balance'])}",
        f"- Positions value: {_money(portfolio['positions_value'])}",
        f"- Total value: {_money(portfolio['total_value'])}",
        f"- Unrealized P&L: {_money(portfolio['unrealized_pnl'])}",
    ]

    positions = portfolio.get("positions") or []
    if positions:
        lines.append("Positions:")
        for p in positions:
            lines.append(
                f"- {p['ticker']}: {p['quantity']:g} shares, avg cost {_money(p['avg_cost'])}, "
                f"price {_money(p['current_price'])}, value {_money(p['market_value'])}, "
                f"P&L {_money(p['unrealized_pnl'])} ({_pct(p['unrealized_pnl_percent'])}), "
                f"weight {p['weight']:.1f}%"
            )
    else:
        lines.append("Positions: none")

    if watchlist:
        lines.append("Watchlist:")
        for w in watchlist:
            lines.append(
                f"- {w['ticker']}: price {_money(w.get('price'))}, "
                f"day change {_pct(w.get('day_change_percent'))}"
            )
    else:
        lines.append("Watchlist: empty")

    return "\n".join(lines)


def _describe_actions(actions: dict | None) -> str:
    """One-line summary of what actually happened for an assistant turn."""
    if not actions:
        return ""
    parts = []
    for t in actions.get("trades") or []:
        text = f"{t.get('side')} {t.get('quantity')} {t.get('ticker')}"
        if t.get("status") == "executed":
            parts.append(f"{text} executed at {_money(t.get('price'))}")
        else:
            parts.append(f"{text} FAILED: {t.get('error')}")
    for w in actions.get("watchlist_changes") or []:
        text = f"{w.get('action')} {w.get('ticker')} (watchlist)"
        if w.get("status") == "executed":
            parts.append(f"{text} applied")
        else:
            parts.append(f"{text} FAILED: {w.get('error')}")
    return "; ".join(parts)


def history_messages(history: list[dict]) -> list[dict]:
    """Convert stored chat rows (oldest first) into LLM messages.

    Assistant turns are annotated with the outcome of their actions so the model
    knows which trades really executed or failed.
    """
    messages = []
    for row in history:
        content = row["content"]
        if row["role"] == "assistant":
            outcome = _describe_actions(row.get("actions"))
            if outcome:
                content = f"{content}\n[Action results: {outcome}]"
        messages.append({"role": row["role"], "content": content})
    return messages


def build_messages(portfolio: dict, watchlist: list[dict], history: list[dict]) -> list[dict]:
    """Full message list: system prompt, live context, then the conversation (ending with the user)."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": build_context(portfolio, watchlist)},
        *history_messages(history),
    ]
