"""Shared application context passed to services, routes and the LLM chat flow."""

from __future__ import annotations

from dataclasses import dataclass

from app.market import MarketDataSource, PriceCache


@dataclass
class AppContext:
    """Runtime dependencies created in the FastAPI lifespan and stored on app.state.ctx."""

    price_cache: PriceCache
    market_source: MarketDataSource
