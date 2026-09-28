"""Chat routes: thin wrappers around the LLM chat flow (app.llm)."""

from __future__ import annotations

import asyncio
import importlib
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import db
from app.context import AppContext

from .deps import get_ctx

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

MAX_MESSAGE_LENGTH = 4000
LLM_MODULE = "app.llm"


class ChatRequest(BaseModel):
    message: str


@router.post("")
async def chat(body: ChatRequest, ctx: AppContext = Depends(get_ctx)) -> dict:
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message must not be empty")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Message is too long (max {MAX_MESSAGE_LENGTH} characters)",
        )

    # Imported off the event loop so a slow first import can never freeze SSE and
    # other requests. Normally already preloaded by the lifespan (which also warms
    # litellm outside mock mode), in which case this just returns the cached module.
    llm = await asyncio.to_thread(importlib.import_module, LLM_MODULE)

    try:
        return await llm.handle_chat(ctx, message)
    except Exception:
        # handle_chat reports LLM failures as assistant messages; this is a last resort
        logger.exception("Chat handling failed")
        raise HTTPException(
            status_code=500, detail="Something went wrong handling your message"
        ) from None


@router.get("/history")
def history(limit: int = Query(100, ge=1, le=500)) -> dict:
    return {"messages": db.get_chat_messages(limit=limit)}
