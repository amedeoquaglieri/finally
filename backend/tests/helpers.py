"""Shared test helpers."""

import asyncio
import time
from collections.abc import Callable


async def wait_until(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll `condition` until it is true, failing after `timeout` seconds.

    Prefer this to fixed sleeps: it is fast on quick machines and tolerant of
    coarse timers (e.g. Windows' ~15.6 ms default resolution) on slow ones.
    """
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"Condition not met within {timeout}s")
        await asyncio.sleep(0.005)
