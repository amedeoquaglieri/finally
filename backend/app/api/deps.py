"""Shared route dependencies."""

from __future__ import annotations

from fastapi import Request

from app.context import AppContext


def get_ctx(request: Request) -> AppContext:
    """The AppContext created by the lifespan."""
    return request.app.state.ctx
