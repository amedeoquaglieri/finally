"""Watchlist routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.context import AppContext
from app.services import watchlist as watchlist_service

from .deps import get_ctx

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class AddTickerRequest(BaseModel):
    ticker: str


@router.get("")
def get_watchlist(ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"tickers": watchlist_service.get_watchlist(ctx)}


@router.post("", status_code=201)
async def add_ticker(body: AddTickerRequest, ctx: AppContext = Depends(get_ctx)) -> dict:
    try:
        return await watchlist_service.add_to_watchlist(ctx, body.ticker)
    except watchlist_service.WatchlistError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None


@router.delete("/{ticker}", status_code=204)
async def remove_ticker(ticker: str, ctx: AppContext = Depends(get_ctx)) -> Response:
    try:
        await watchlist_service.remove_from_watchlist(ctx, ticker)
    except watchlist_service.WatchlistError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    return Response(status_code=204)
