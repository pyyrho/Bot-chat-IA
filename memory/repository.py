from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .models import MemoryItem

logger = logging.getLogger("Revolutx.MemoryRepository")


class MemoryRepository:
    def __init__(self, pool: Any | None = None) -> None:
        self.pool = pool
        self.ready = False
        self._fallback: defaultdict[tuple[int, int | None], list[MemoryItem]] = defaultdict(list)
        self._summaries: dict[tuple[int, int | None], str] = {}
        self._next_id = 1

    async def prepare(self) -> None:
        if self.pool is None:
            return
        try:
            await self.pool.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_memories (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    guild_id BIGINT,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    normalized TEXT NOT NULL,
                    importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.8,
                    source TEXT NOT NULL DEFAULT 'conversation',
                    embedding JSONB NOT NULL DEFAULT '[]'::jsonb,
                    embedding_model TEXT NOT NULL DEFAULT 'local-hash-v1',
                    expires_at TIMESTAMPTZ,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await self.pool.execute(
                """
                CREATE INDEX IF NOT EXISTS ai_memories_lookup_idx
                ON ai_memories (user_id, guild_id, updated_at DESC)
                """
            )
            await self.pool.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_session_summaries (
                    user_id BIGINT NOT NULL,
                    guild_key BIGINT NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, guild_key)
                )
                """
            )
            self.ready = True
        except Exception as exc:
            self.ready = False
            logger.warning("Memória persistente indisponível; usando memória em processo: %s", exc)

    async def upsert(self, item: MemoryItem) -> MemoryItem:
        normalized = " ".join(item.content.lower().split())[:900]
        if self.ready:
            row = await self.pool.fetchrow(
                """
                SELECT id FROM ai_memories
                WHERE user_id=$1 AND guild_id IS NOT DISTINCT FROM $2 AND normalized=$3
                LIMIT 1
                """,
                item.user_id, item.guild_id, normalized,
            )
            if row:
                item.id = int(row["id"])
                await self.pool.execute(
                    """
                    UPDATE ai_memories SET importance=GREATEST(importance,$2), confidence=GREATEST(confidence,$3),
                    embedding=$4::jsonb, embedding_model=$5, updated_at=NOW(), expires_at=$6
                    WHERE id=$1
                    """,
                    item.id, item.importance, item.confidence, json.dumps(item.embedding), item.embedding_model, item.expires_at,
                )
                return item
            row = await self.pool.fetchrow(
                """
                INSERT INTO ai_memories
                (user_id,guild_id,kind,content,normalized,importance,confidence,source,embedding,embedding_model,expires_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11)
                RETURNING id,created_at,updated_at
                """,
                item.user_id, item.guild_id, item.kind, item.content, normalized,
                item.importance, item.confidence, item.source, json.dumps(item.embedding), item.embedding_model, item.expires_at,
            )
            item.id = int(row["id"])
            item.created_at = row["created_at"]
            item.updated_at = row["updated_at"]
            return item

        key = (item.user_id, item.guild_id)
        for existing in self._fallback[key]:
            if " ".join(existing.content.lower().split()) == normalized:
                existing.importance = max(existing.importance, item.importance)
                existing.confidence = max(existing.confidence, item.confidence)
                existing.updated_at = datetime.now(timezone.utc)
                return existing
        item.id = self._next_id
        self._next_id += 1
        item.created_at = item.updated_at = datetime.now(timezone.utc)
        self._fallback[key].append(item)
        self._fallback[key] = self._fallback[key][-200:]
        return item

    async def candidates(self, user_id: int, guild_id: int | None, limit: int = 160) -> list[MemoryItem]:
        now = datetime.now(timezone.utc)
        if self.ready:
            rows = await self.pool.fetch(
                """
                SELECT * FROM ai_memories
                WHERE user_id=$1
                  AND (guild_id IS NULL OR guild_id IS NOT DISTINCT FROM $2)
                  AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY importance DESC, updated_at DESC
                LIMIT $3
                """,
                user_id, guild_id, limit,
            )
            output: list[MemoryItem] = []
            for row in rows:
                raw_embedding = row["embedding"]
                if isinstance(raw_embedding, str):
                    try:
                        raw_embedding = json.loads(raw_embedding)
                    except json.JSONDecodeError:
                        raw_embedding = []
                output.append(MemoryItem(
                    id=row["id"], user_id=row["user_id"], guild_id=row["guild_id"], kind=row["kind"],
                    content=row["content"], importance=float(row["importance"]), confidence=float(row["confidence"]),
                    source=row["source"], embedding=[float(x) for x in (raw_embedding or [])],
                    embedding_model=row["embedding_model"], created_at=row["created_at"], updated_at=row["updated_at"],
                    expires_at=row["expires_at"], use_count=int(row["use_count"] or 0),
                ))
            return output
        output = []
        for key in ((user_id, guild_id), (user_id, None)):
            for item in self._fallback.get(key, []):
                if item.expires_at is None or item.expires_at > now:
                    output.append(item)
        return output[-limit:]

    async def mark_used(self, ids: list[int]) -> None:
        if not ids:
            return
        if self.ready:
            await self.pool.execute(
                "UPDATE ai_memories SET use_count=use_count+1,last_used_at=NOW() WHERE id=ANY($1::bigint[])", ids,
            )

    async def list_items(self, user_id: int, guild_id: int | None, limit: int = 25) -> list[MemoryItem]:
        return (await self.candidates(user_id, guild_id, limit=limit))[:limit]

    async def delete(self, user_id: int, memory_id: int) -> bool:
        if self.ready:
            result = await self.pool.execute("DELETE FROM ai_memories WHERE user_id=$1 AND id=$2", user_id, memory_id)
            return not result.endswith("0")
        for items in self._fallback.values():
            before = len(items)
            items[:] = [m for m in items if not (m.user_id == user_id and m.id == memory_id)]
            if len(items) != before:
                return True
        return False

    async def clear(self, user_id: int, guild_id: int | None = None) -> int:
        if self.ready:
            if guild_id is None:
                rows = await self.pool.fetch("DELETE FROM ai_memories WHERE user_id=$1 RETURNING id", user_id)
                await self.pool.execute("DELETE FROM ai_session_summaries WHERE user_id=$1", user_id)
            else:
                rows = await self.pool.fetch("DELETE FROM ai_memories WHERE user_id=$1 AND guild_id IS NOT DISTINCT FROM $2 RETURNING id", user_id, guild_id)
                await self.pool.execute("DELETE FROM ai_session_summaries WHERE user_id=$1 AND guild_key=$2", user_id, int(guild_id or 0))
            return len(rows)
        count = 0
        for key in list(self._fallback):
            if key[0] == user_id and (guild_id is None or key[1] == guild_id):
                count += len(self._fallback.pop(key, []))
                self._summaries.pop(key, None)
        return count

    async def set_summary(self, user_id: int, guild_id: int | None, summary: str) -> None:
        if self.ready:
            await self.pool.execute(
                """
                INSERT INTO ai_session_summaries(user_id,guild_key,summary,updated_at)
                VALUES($1,$2,$3,NOW())
                ON CONFLICT(user_id,guild_key) DO UPDATE SET summary=$3,updated_at=NOW()
                """,
                user_id, int(guild_id or 0), summary,
            )
        else:
            self._summaries[(user_id, guild_id)] = summary

    async def get_summary(self, user_id: int, guild_id: int | None) -> str:
        if self.ready:
            row = await self.pool.fetchrow(
                "SELECT summary FROM ai_session_summaries WHERE user_id=$1 AND guild_key=$2",
                user_id, int(guild_id or 0),
            )
            return str(row["summary"] or "") if row else ""
        return self._summaries.get((user_id, guild_id), "")
