from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import aiohttp

T = TypeVar("T")
logger = logging.getLogger("Revolutx.AIRuntime")


class SharedHTTPSession:
    def __init__(self, *, user_agent: str, total_timeout: float = 16.0, limit: int = 40) -> None:
        self.user_agent = user_agent
        self.total_timeout = total_timeout
        self.limit = limit
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> aiohttp.ClientSession:
        if self._session and not self._session.closed:
            return self._session
        async with self._lock:
            if self._session and not self._session.closed:
                return self._session
            timeout = aiohttp.ClientTimeout(total=self.total_timeout, connect=5, sock_read=10)
            connector = aiohttp.TCPConnector(limit=self.limit, ttl_dns_cache=300, enable_cleanup_closed=True)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={"User-Agent": self.user_agent, "Accept": "application/json, text/plain, */*"},
            )
            return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class RequestCoalescer:
    """Funde requisições idênticas simultâneas para não gastar cota em duplicidade."""

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            task = self._inflight.get(key)
            if task is None or task.done():
                task = asyncio.create_task(factory())
                self._inflight[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                async with self._lock:
                    if self._inflight.get(key) is task:
                        self._inflight.pop(key, None)
