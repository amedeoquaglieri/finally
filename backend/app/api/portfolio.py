"""Portfolio routes: valuation, trades and value history."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.context import AppContext
from app.services import portfolio as portfolio_service

from .deps import get_ctx

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class TradeRequest(BaseModel):
    # Loosely typed so bad values reach the service and get its user-facing message
    ticker: str
    quantity: Any
    side: str


@router.get("")
def get_portfolio(ctx: AppContext = Depends(get_ctx)) -> dict:
    return portfolio_service.get_portfolio(ctx)


@router.post("/trade")
async def trade(body: TradeRequest, ctx: AppContext = Depends(get_ctx)) -> dict:
    # Async so the service can schedule market source updates on the running loop
    try:
        executed = portfolio_service.execute_trade(ctx, body.ticker, body.side, body.quantity)
    except portfolio_service.TradeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"trade": executed, "portfolio": portfolio_service.get_portfolio(ctx)}


@router.get("/history")
def history() -> dict:
    snapshots = db.get_snapshots()
    return {
        "snapshots": [
            {"total_value": s["total_value"], "recorded_at": s["recorded_at"]} for s in snapshots
        ]
    }
