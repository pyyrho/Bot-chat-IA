from __future__ import annotations

import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .embeddings import HybridEmbedder, LocalHashEmbedder, cosine
from .extractor import MemoryExtractor
from .models import MemoryContext, MemoryItem
from .repository import MemoryRepository

logger = logging.getLogger("Revolutx.Memory")


class SemanticMemoryManager:
    def __init__(
        self,
        *,
        pool: Any | None = None,
        remote_embedder=None,
        remote_model: str = "gemini-embedding-001",
    ) -> None:
        remote_enabled = os.getenv("AI_MEMORY_REMOTE_EMBEDDINGS", "false").lower() in {"1", "true", "yes", "on"}
        self.repository = MemoryRepository(pool)
        self.embedder = HybridEmbedder(remote_embedder, remote_name=remote_model, remote_enabled=remote_enabled)
        self.local_embedder = LocalHashEmbedder()
        self.extractor = MemoryExtractor()
        self._exchange_counts: defaultdict[tuple[int, int | None], int] = defaultdict(int)
        self.enabled = os.getenv("AI_SEMANTIC_MEMORY_ENABLED", "true").lower() not in {"0", "false", "no", "off"}

    async def prepare(self) -> None:
        if self.enabled:
            await self.repository.prepare()

    async def remember(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        content: str,
        kind: str = "explicit",
        importance: float = 0.8,
        confidence: float = 0.9,
        ttl_days: int | None = None,
        source: str = "conversation",
    ) -> MemoryItem | None:
        if not self.enabled or not content.strip():
            return None
        vector, model = await self.embedder.embed(content, task="document")
        expires = datetime.now(timezone.utc) + timedelta(days=ttl_days) if ttl_days else None
        item = MemoryItem(
            id=None,
            user_id=user_id,
            guild_id=guild_id,
            kind=kind,
            content=content.strip()[:900],
            importance=max(0.0, min(1.0, importance)),
            confidence=max(0.0, min(1.0, confidence)),
            source=source,
            embedding=vector,
            embedding_model=model,
            expires_at=expires,
        )
        return await self.repository.upsert(item)

    async def retrieve(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        query: str,
        limit: int = 6,
        min_score: float = 0.23,
    ) -> MemoryContext:
        if not self.enabled or not query.strip():
            return MemoryContext([])
        candidates = await self.repository.candidates(user_id, guild_id, limit=180)
        if not candidates:
            summary = await self.repository.get_summary(user_id, guild_id)
            return MemoryContext([], summary)

        local_query = await self.local_embedder.embed(query, task="query")
        remote_query: list[float] | None = None
        remote_model = ""
        if self.embedder.remote_enabled:
            remote_query, remote_model = await self.embedder.embed(query, task="query")

        now = datetime.now(timezone.utc)
        ranked: list[MemoryItem] = []
        for item in candidates:
            if item.embedding_model == remote_model and remote_query:
                semantic = cosine(remote_query, item.embedding)
            elif item.embedding_model == self.local_embedder.name:
                semantic = cosine(local_query, item.embedding)
            else:
                # Embeddings antigos/incompatíveis recebem uma representação local sob demanda.
                candidate_local = await self.local_embedder.embed(item.content, task="document")
                semantic = cosine(local_query, candidate_local)
            age_days = max(0.0, (now - (item.updated_at or item.created_at or now)).total_seconds() / 86400)
            recency = math.exp(-age_days / 180)
            scope_bonus = 0.06 if item.guild_id == guild_id and guild_id is not None else 0.0
            use_bonus = min(0.08, math.log1p(item.use_count) * 0.02)
            item.score = round(
                semantic * 0.68
                + item.importance * 0.14
                + item.confidence * 0.08
                + recency * 0.08
                + scope_bonus
                + use_bonus,
                4,
            )
            if item.score >= min_score:
                ranked.append(item)
        ranked.sort(key=lambda item: (-item.score, -item.importance, -(item.updated_at or now).timestamp()))
        selected = ranked[:limit]
        await self.repository.mark_used([item.id for item in selected if item.id is not None])
        summary = await self.repository.get_summary(user_id, guild_id)
        return MemoryContext(selected, summary)

    async def observe_exchange(
        self,
        *,
        user_id: int,
        guild_id: int | None,
        user_text: str,
        assistant_text: str,
        history: list[dict[str, str]],
    ) -> list[MemoryItem]:
        if not self.enabled:
            return []
        stored: list[MemoryItem] = []
        for candidate in self.extractor.extract(user_text):
            item = await self.remember(
                user_id=user_id,
                guild_id=guild_id,
                content=candidate.content,
                kind=candidate.kind,
                importance=candidate.importance,
                confidence=candidate.confidence,
                ttl_days=candidate.ttl_days,
            )
            if item:
                stored.append(item)

        key = (user_id, guild_id)
        self._exchange_counts[key] += 1
        if self._exchange_counts[key] % 6 == 0 and history:
            summary = self.extractor.summarize_session(history)
            if summary:
                await self.repository.set_summary(user_id, guild_id, summary)
        return stored

    async def list_memories(self, user_id: int, guild_id: int | None, limit: int = 20) -> list[MemoryItem]:
        return await self.repository.list_items(user_id, guild_id, limit)

    async def delete_memory(self, user_id: int, memory_id: int) -> bool:
        return await self.repository.delete(user_id, memory_id)

    async def clear(self, user_id: int, guild_id: int | None = None) -> int:
        return await self.repository.clear(user_id, guild_id)
