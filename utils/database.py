import logging
import os
from typing import Optional

import asyncpg

logger = logging.getLogger("Database")


class Database:
    def __init__(self) -> None:
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        url = os.getenv("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL não encontrada nas variáveis de ambiente.")

        # Railway às vezes entrega postgres://. asyncpg prefere postgresql://.
        url = url.replace("postgres://", "postgresql://", 1)
        self.pool = await asyncpg.create_pool(url, min_size=1, max_size=8)
        logger.info("Conectado ao PostgreSQL.")
        await self.create_tables()

    async def create_tables(self) -> None:
        if not self.pool:
            raise RuntimeError("Banco ainda não conectado.")

        async with self.pool.acquire() as conn:
            # ── Tabelas principais ────────────────────────────────────────────
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mod_config (
                    guild_id        BIGINT  PRIMARY KEY,
                    log_channel     BIGINT,
                    mod_ping_role   BIGINT,
                    warn_threshold  INT     DEFAULT 3,
                    warn_action     TEXT    DEFAULT 'mute',
                    anti_spam       BOOLEAN DEFAULT TRUE,
                    anti_caps       BOOLEAN DEFAULT FALSE,
                    anti_links      BOOLEAN DEFAULT FALSE,
                    anti_mention    BOOLEAN DEFAULT TRUE,
                    ai_moderation   BOOLEAN DEFAULT FALSE,
                    banned_words    TEXT[]  DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS warnings (
                    id          BIGSERIAL PRIMARY KEY,
                    guild_id    BIGINT      NOT NULL,
                    user_id     BIGINT      NOT NULL,
                    reason      TEXT,
                    moderator   BIGINT,
                    source      TEXT        DEFAULT 'manual',
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_warnings_guild_user
                    ON warnings (guild_id, user_id);

                CREATE TABLE IF NOT EXISTS mod_notes (
                    id           BIGSERIAL   PRIMARY KEY,
                    guild_id     BIGINT      NOT NULL,
                    user_id      BIGINT      NOT NULL,
                    note         TEXT        NOT NULL,
                    moderator_id BIGINT,
                    created_at   TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_mod_notes_guild_user
                    ON mod_notes (guild_id, user_id);

                CREATE TABLE IF NOT EXISTS ai_config (
                    guild_id    BIGINT   PRIMARY KEY,
                    ai_channels BIGINT[] DEFAULT '{}'
                );
                """
            )

            # ── Migrações seguras: adiciona colunas que podem não existir ─────
            # (ALTER TABLE … ADD COLUMN IF NOT EXISTS é seguro para rodar sempre)
            migrations = [
                # mod_config — novas colunas
                "ALTER TABLE mod_config ADD COLUMN IF NOT EXISTS mod_ping_role BIGINT;",
                # warnings — coluna source
                "ALTER TABLE warnings ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual';",
            ]
            for sql in migrations:
                try:
                    await conn.execute(sql)
                except Exception as exc:
                    logger.warning("Migração ignorada (%s): %s", sql[:60], exc)

        logger.info("Tabelas verificadas/criadas.")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Conexão com PostgreSQL encerrada.")


db = Database()
