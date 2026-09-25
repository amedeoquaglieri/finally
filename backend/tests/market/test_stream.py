"""Tests for the SSE streaming endpoint."""

import asyncio
import json

import pytest
from fastapi import FastAPI

from app.market.cache import PriceCache
from app.market.stream import _generate_events, create_stream_router


class FakeRequest:
    """Minimal stand-in for a Starlette Request.

    Reports a disconnect after `polls` calls to is_disconnected(), and runs
    `on_poll(n)` before each check so tests can change the cache mid-stream.
    """

    client = None

    def __init__(self, polls: int, on_poll=None) -> None:
        self._polls = polls
        self._count = 0
        self._on_poll = on_poll

    async def is_disconnected(self) -> bool:
        if self._on_poll:
            self._on_poll(self._count)
        self._count += 1
        return self._count > self._polls


async def _collect(cache: PriceCache, request: FakeRequest) -> list[str]:
    return [event async for event in _generate_events(cache, request, interval=0)]


def _payloads(events: list[str]) -> list[dict]:
    return [json.loads(e.removeprefix("data: ")) for e in events if e.startswith("data: ")]


class TestGenerateEvents:
    """Unit tests for the SSE event generator."""

    async def test_starts_with_retry_directive(self):
        events = await _collect(PriceCache(), FakeRequest(polls=0))
        assert events == ["retry: 1000\n\n"]

    async def test_sends_snapshot_of_all_prices(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        cache.update("GOOGL", 175.0)

        events = await _collect(cache, FakeRequest(polls=1))

        assert events[1].endswith("\n\n")
        [payload] = _payloads(events)
        assert set(payload) == {"AAPL", "GOOGL"}
        assert payload["AAPL"]["price"] == 190.0

    async def test_no_event_when_version_unchanged(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        events = await _collect(cache, FakeRequest(polls=3))

        assert len(_payloads(events)) == 1

    async def test_sends_event_on_each_update(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        def on_poll(n: int) -> None:
            if n == 1:
                cache.update("AAPL", 191.0)

        events = await _collect(cache, FakeRequest(polls=3, on_poll=on_poll))

        prices = [p["AAPL"]["price"] for p in _payloads(events)]
        assert prices == [190.0, 191.0]

    async def test_removal_is_sent(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        cache.update("GOOGL", 175.0)

        def on_poll(n: int) -> None:
            if n == 1:
                cache.remove("GOOGL")

        events = await _collect(cache, FakeRequest(polls=2, on_poll=on_poll))

        assert [set(p) for p in _payloads(events)] == [{"AAPL", "GOOGL"}, {"AAPL"}]

    async def test_removing_last_ticker_sends_empty_snapshot(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        def on_poll(n: int) -> None:
            if n == 1:
                cache.remove("AAPL")

        events = await _collect(cache, FakeRequest(polls=2, on_poll=on_poll))

        payloads = _payloads(events)
        assert [set(p) for p in payloads] == [{"AAPL"}, set()]
        assert events[-1] == "data: {}\n\n"

    async def test_heartbeat_when_idle(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        request = FakeRequest(polls=3)

        events = [
            e async for e in _generate_events(cache, request, interval=0, heartbeat_interval=0)
        ]

        # One snapshot, then a keepalive comment on each idle poll
        assert len(_payloads(events)) == 1
        assert events.count(": keepalive\n\n") == 2

    async def test_no_heartbeat_while_data_flows(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)

        def on_poll(n: int) -> None:
            cache.update("AAPL", 190.0 + n)

        events = await _collect(cache, FakeRequest(polls=3, on_poll=on_poll))

        assert ": keepalive\n\n" not in events

    async def test_cancellation_propagates(self):
        """Cancelling the stream (client gone) must not be swallowed by the generator."""
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        gen = _generate_events(cache, FakeRequest(polls=100), interval=10)

        async def consume() -> None:
            async for _ in gen:
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)  # Generator is now sleeping between polls
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestCreateStreamRouter:
    """Tests for the router factory."""

    def test_registers_prices_route(self):
        router = create_stream_router(PriceCache())
        assert [r.path for r in router.routes] == ["/api/stream/prices"]

    def test_each_call_returns_independent_router(self):
        cache1, cache2 = PriceCache(), PriceCache()
        router1 = create_stream_router(cache1)
        router2 = create_stream_router(cache2)

        assert router1 is not router2
        assert len(router2.routes) == 1

        app = FastAPI()
        app.include_router(router2)
        [route] = [r for r in app.routes if getattr(r, "path", "") == "/api/stream/prices"]
        bound = [cell.cell_contents for cell in route.endpoint.__closure__]
        assert any(obj is cache2 for obj in bound)
        assert not any(obj is cache1 for obj in bound)

    async def test_response_headers(self):
        """The endpoint returns an SSE response with anti-buffering headers."""
        router = create_stream_router(PriceCache())
        [route] = router.routes
        response = await route.endpoint(FakeRequest(polls=0))

        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
