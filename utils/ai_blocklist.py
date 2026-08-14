"""Bloqueio persistente de respostas do RevolutX por servidor.

O módulo usa o mesmo pool PostgreSQL já inicializado por ``utils.database``.
As consultas de mensagens usam um cache em memória; o banco só é consultado
na primeira utilização e quando um administrador bloqueia ou desbloqueia alguém.
"""

from __future__ import annotations

import asyncio
import logging
import time

from utils.database import db


logger = logging.getLogger("Revolux.AIBlocklist")

AI_BLOCKLIST_BUILD = "2026-08-12-bloqueio-persistente-v1"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai_response_blocklist (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    blocked_by BIGINT NOT NULL,
    blocked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, user_id)
)
"""


class BlocklistUnavailable(RuntimeError):
    """O bloqueio não pôde ser lido ou salvo no banco de dados."""


class AIResponseBlocklist:
    """Cache concorrente da lista persistente de usuários ignorados."""

    def __init__(self) -> None:
        self._blocked: set[tuple[int, int]] = set()
        self._loaded = False
        self._lock = asyncio.Lock()
        self._last_warning_at = 0.0

    @staticmethod
    def _key(guild_id: int, user_id: int) -> tuple[int, int]:
        guild = int(guild_id)
        user = int(user_id)
        if guild <= 0 or user <= 0:
            raise ValueError("guild_id e user_id devem ser IDs positivos")
        return guild, user

    @staticmethod
    def _pool():
        pool = getattr(db, "pool", None)
        if pool is None:
            raise BlocklistUnavailable("pool PostgreSQL ainda não está disponível")
        return pool

    async def initialize(self) -> None:
        """Cria a tabela e carrega o cache uma única vez."""
        if self._loaded:
            return

        async with self._lock:
            if self._loaded:
                return

            try:
                pool = self._pool()
                await pool.execute(_CREATE_TABLE_SQL)
                rows = await pool.fetch(
                    "SELECT guild_id, user_id FROM ai_response_blocklist"
                )
            except BlocklistUnavailable:
                raise
            except Exception as exc:
                raise BlocklistUnavailable(
                    f"falha ao carregar a lista: {type(exc).__name__}"
                ) from exc

            self._blocked = {
                (int(row["guild_id"]), int(row["user_id"])) for row in rows
            }
            self._loaded = True
            logger.info(
                "AI blocklist ativa (%s): %s usuário(s) bloqueado(s).",
                AI_BLOCKLIST_BUILD,
                len(self._blocked),
            )

    async def is_blocked(self, guild_id: int, user_id: int) -> bool:
        """Retorna False em falha transitória para não derrubar o chat inteiro."""
        key = self._key(guild_id, user_id)
        try:
            await self.initialize()
        except BlocklistUnavailable as exc:
            now = time.monotonic()
            if now - self._last_warning_at >= 60:
                logger.warning("Não foi possível consultar a AI blocklist: %s", exc)
                self._last_warning_at = now
            return False
        return key in self._blocked

    async def block(self, guild_id: int, user_id: int, blocked_by: int) -> bool:
        """Bloqueia o usuário e retorna True quando o registro era novo."""
        key = self._key(guild_id, user_id)
        moderator_id = int(blocked_by)
        if moderator_id <= 0:
            raise ValueError("blocked_by deve ser um ID positivo")

        await self.initialize()
        async with self._lock:
            already_blocked = key in self._blocked
            try:
                await self._pool().execute(
                    """
                    INSERT INTO ai_response_blocklist
                        (guild_id, user_id, blocked_by, blocked_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (guild_id, user_id) DO UPDATE
                    SET blocked_by = EXCLUDED.blocked_by,
                        blocked_at = NOW()
                    """,
                    key[0],
                    key[1],
                    moderator_id,
                )
            except Exception as exc:
                raise BlocklistUnavailable(
                    f"falha ao salvar o bloqueio: {type(exc).__name__}"
                ) from exc

            self._blocked.add(key)
            return not already_blocked

    async def unblock(self, guild_id: int, user_id: int) -> bool:
        """Desbloqueia o usuário e informa se havia um registro salvo."""
        key = self._key(guild_id, user_id)
        await self.initialize()

        async with self._lock:
            try:
                row = await self._pool().fetchrow(
                    """
                    DELETE FROM ai_response_blocklist
                    WHERE guild_id = $1 AND user_id = $2
                    RETURNING user_id
                    """,
                    key[0],
                    key[1],
                )
            except Exception as exc:
                raise BlocklistUnavailable(
                    f"falha ao remover o bloqueio: {type(exc).__name__}"
                ) from exc

            self._blocked.discard(key)
            return row is not None


ai_response_blocklist = AIResponseBlocklist()

