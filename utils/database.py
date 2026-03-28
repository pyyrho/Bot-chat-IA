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

        # Railway fornece URL com 'postgres://', asyncpg precisa de 'postgresql://'
        url = url.replace("postgres://", "postgresql://", 1)

        self.pool = await asyncpg.create_pool(url, min_size=2, max_size=10)
        logger.info("✅ Conectado ao PostgreSQL!")
        await self._create_tables()

    async def _create_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                -- Configurações de moderação por servidor
                CREATE TABLE IF NOT EXISTS mod_config (
                    guild_id        BIGINT PRIMARY KEY,
                    log_channel     BIGINT,
                    warn_threshold  INT     DEFAULT 3,
                    warn_action     TEXT    DEFAULT 'mute',
                    anti_spam       BOOLEAN DEFAULT TRUE,
                    anti_caps       BOOLEAN DEFAULT TRUE,
                    anti_links      BOOLEAN DEFAULT TRUE,
                    anti_mention    BOOLEAN DEFAULT TRUE,
                    ai_moderation   BOOLEAN DEFAULT TRUE,
                    banned_words    TEXT[]  DEFAULT '{}'
                );

                -- Avisos de usuários
                CREATE TABLE IF NOT EXISTS warnings (
                    id          SERIAL PRIMARY KEY,
                    guild_id    BIGINT NOT NULL,
                    user_id     BIGINT NOT NULL,
                    reason      TEXT,
                    moderator   BIGINT,
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_warnings_guild_user ON warnings(guild_id, user_id);

                -- Configurações do canal de IA por servidor
                CREATE TABLE IF NOT EXISTS ai_config (
                    guild_id        BIGINT PRIMARY KEY,
                    ai_channels     BIGINT[] DEFAULT '{}'
                );

                -- Configurações de anti-raid por servidor
                CREATE TABLE IF NOT EXISTS antiraid_config (
                    guild_id            BIGINT PRIMARY KEY,
                    enabled             BOOLEAN DEFAULT TRUE,
                    raid_threshold      INT     DEFAULT 10,
                    raid_window         INT     DEFAULT 10,
                    action              TEXT    DEFAULT 'kick',
                    min_account_age     INT     DEFAULT 7,
                    log_channel         BIGINT,
                    lockdown_active     BOOLEAN DEFAULT FALSE
                );

                -- Configurações de parceria por servidor
                CREATE TABLE IF NOT EXISTS partnership_config (
                    guild_id        BIGINT PRIMARY KEY,
                    partner_channel BIGINT,
                    ad_channel      BIGINT,
                    enabled         BOOLEAN DEFAULT FALSE,
                    min_members     INT     DEFAULT 50,
                    require_bot     BOOLEAN DEFAULT TRUE
                );

                -- Parceiros registrados
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
        logger.info("✅ Tabelas verificadas/criadas!")

    async def close(self):
        if self.pool:
            await self.pool.close()

# Instância global
db = Database()
