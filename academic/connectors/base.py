from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import aiohttp

from academic.models import AcademicWork

logger = logging.getLogger("Revolutx.AcademicConnector")


class ConnectorError(RuntimeError):
    pass


class AcademicConnector(ABC):
    name = "base"
    source_weight = 0.0

    def __init__(self, *, timeout: float = 10.0) -> None:
        self.timeout = timeout

    @abstractmethod
    async def search(self, session: aiohttp.ClientSession, query: str, limit: int = 8) -> list[AcademicWork]:
        raise NotImplementedError

    async def get_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retries: int = 1,
    ) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=self.timeout)) as response:
                    body = await response.text()
                    if response.status == 429 and attempt < retries:
                        await asyncio.sleep(min(2.5, 0.5 * (2 ** attempt)))
                        continue
                    if response.status >= 400:
                        raise ConnectorError(f"HTTP {response.status}: {body[:220]}")
                    data = json.loads(body)
                    if not isinstance(data, dict):
                        raise ConnectorError("Resposta JSON não é objeto")
                    return data
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError, ConnectorError) as exc:
                last = exc
                if attempt < retries:
                    await asyncio.sleep(0.25 * (attempt + 1))
                    continue
                raise ConnectorError(str(exc)) from exc
        raise ConnectorError(str(last or "Falha desconhecida"))
