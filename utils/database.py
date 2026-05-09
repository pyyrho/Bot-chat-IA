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
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mod_config (
                    guild_id        BIGINT PRIMARY KEY,
                    log_channel     BIGINT,
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
                    id          SERIAL PRIMARY KEY,
                    guild_id    BIGINT NOT NULL,
                    user_id     BIGINT NOT NULL,
                    reason      TEXT,
                    moderator   BIGINT,
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_warnings_guild_user
                    ON warnings(guild_id, user_id);

                CREATE TABLE IF NOT EXISTS ai_config (
                    guild_id    BIGINT PRIMARY KEY,
                    ai_channels BIGINT[] DEFAULT '{}'
                );
                """
            )

            # Defaults seguros: nada de filtro agressivo ativado sem log/configuração.
            await conn.execute(
                """
                UPDATE mod_config
                SET anti_caps = FALSE,
                    anti_links = FALSE,
                    ai_moderation = FALSE
                WHERE anti_caps = TRUE OR anti_links = TRUE OR ai_moderation = TRUE;
                """
            )

        logger.info("Tabelas verificadas/criadas.")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Conexão com PostgreSQL encerrada.")


db = Database()
