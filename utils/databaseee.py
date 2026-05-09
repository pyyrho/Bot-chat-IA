import asyncpg
import os
import logging

logger = logging.getLogger("Database")

class Database:
    def __init__(self):
        self.pool: asyncpg.Pool = None

    async def connect(self):
        url = os.getenv("DATABASE_URL")
        if not url:
            raise RuntimeError("❌ DATABASE_URL não encontrada no ambiente!")

        url = url.replace("postgres://", "postgresql://", 1)
        self.pool = await asyncpg.create_pool(url, min_size=2, max_size=10)
        logger.info("✅ Conectado ao PostgreSQL!")
        await self._create_tables()

    async def _create_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
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
                CREATE INDEX IF NOT EXISTS idx_warnings_guild_user ON warnings(guild_id, user_id);

                CREATE TABLE IF NOT EXISTS ai_config (
                    guild_id        BIGINT PRIMARY KEY,
                    ai_channels     BIGINT[] DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS antiraid_config (
                    guild_id            BIGINT PRIMARY KEY,
                    enabled             BOOLEAN DEFAULT FALSE,
                    raid_threshold      INT     DEFAULT 10,
                    raid_window         INT     DEFAULT 10,
                    action              TEXT    DEFAULT 'kick',
                    min_account_age     INT     DEFAULT 7,
                    log_channel         BIGINT,
                    lockdown_active     BOOLEAN DEFAULT FALSE
                );

                CREATE TABLE IF NOT EXISTS partnership_config (
                    guild_id        BIGINT PRIMARY KEY,
                    partner_channel BIGINT,
                    ad_channel      BIGINT,
                    enabled         BOOLEAN DEFAULT FALSE,
                    min_members     INT     DEFAULT 50,
                    require_bot     BOOLEAN DEFAULT TRUE
                );

                CREATE TABLE IF NOT EXISTS partners (
                    id          SERIAL PRIMARY KEY,
                    guild_id    BIGINT NOT NULL,
                    invite      TEXT NOT NULL,
                    description TEXT,
                    proof_url   TEXT,
                    added_by    BIGINT,
                    added_at    TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_partners_guild ON partners(guild_id);
            """)
            # Migração: desativa configs perigosas em servidores existentes
            await conn.execute("""
                UPDATE mod_config
                SET anti_caps     = FALSE,
                    anti_links    = FALSE,
                    ai_moderation = FALSE
                WHERE anti_caps = TRUE OR anti_links = TRUE OR ai_moderation = TRUE
            """)
            await conn.execute("""
                UPDATE antiraid_config
                SET enabled = FALSE
                WHERE enabled = TRUE
            """)
        logger.info("✅ Tabelas verificadas/criadas!")

    async def close(self):
        if self.pool:
            await self.pool.close()

db = Database()
