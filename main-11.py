import asyncio
import logging
import os
import platform
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from utils.database import db

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
BOT_NAME = os.getenv("BOT_NAME", "Revolutx")
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(BOT_NAME)

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
intents.moderation = True


def _configured_cogs() -> tuple[str, ...]:
    """Permite ativar/desativar cogs pelo Railway sem editar código.

    Variável opcional:
    BOT_COGS=cogs.ai_chat,cogs.moderation,cogs.utility
    """
    raw = os.getenv("BOT_COGS")
    if raw:
        return tuple(cog.strip() for cog in raw.split(",") if cog.strip())
    return (
        "cogs.ai_chat",
        "cogs.moderation",
        "cogs.utility",
    )



# ── Status duplo ───────────────────────────────────────────────────────────────
# Streaming (embaixo, fixo): sempre "Copa do mundo rolando"
_STREAMING_URL  = "https://www.twitch.tv/directory"
_STREAMING_NAME = "⚽ Copa do mundo rolando"

# Custom status (em cima, rotativo)
CUSTOM_STATUSES: list[str] = [
    "🚀 RevolutX — IA no Discord",
    "🛡️ Moderação v2 ativa",
    "🧊 Frio como uma foca na neve",
    "🤍 Me adicione ao seu servidor",
]
_status_index = 0


class RevolutxBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=BOT_PREFIX,
            intents=intents,
            help_command=None,
        )
        self.start_time = datetime.now(timezone.utc)
        self.loaded_cogs_count = 0
        self.failed_cogs: list[str] = []

    async def setup_hook(self) -> None:
        await db.connect()

        cogs = _configured_cogs()
        loaded = 0
        failed: list[str] = []

        for cog in cogs:
            try:
                await self.load_extension(cog)
                loaded += 1
                logger.info("Cog carregado: %s", cog)
            except Exception as exc:
                failed.append(cog)
                logger.exception("Erro ao carregar %s: %s", cog, exc)

        self.loaded_cogs_count = loaded
        self.failed_cogs = failed

        try:
            synced = await self.tree.sync()
            logger.info("Slash commands sincronizados: %s | Cogs: %s/%s", len(synced), loaded, len(cogs))
        except Exception as exc:
            logger.exception("Erro ao sincronizar slash commands: %s", exc)

    async def close(self) -> None:
        try:
            await db.close()
        finally:
            await super().close()

    @tasks.loop(seconds=20)
    async def _rotate_status(self) -> None:
        global _status_index
        custom_text = CUSTOM_STATUSES[_status_index % len(CUSTOM_STATUSES)]
        _status_index += 1

        streaming = discord.Streaming(name=_STREAMING_NAME, url=_STREAMING_URL)
        try:
            # Tenta setar os dois: custom em cima + streaming embaixo
            await self.change_presence(
                status=discord.Status.online,
                activities=[
                    discord.CustomActivity(name=custom_text),
                    streaming,
                ],
            )
        except TypeError:
            # discord.py desta versão não aceita lista — usa só o Streaming
            await self.change_presence(
                status=discord.Status.online,
                activity=streaming,
            )

    @_rotate_status.error
    async def _rotate_status_error(self, error: Exception) -> None:
        logger.exception("Erro na task _rotate_status: %s", error)
        # Reinicia a task automaticamente após 30s
        await asyncio.sleep(30)
        if not self._rotate_status.is_running():
            self._rotate_status.start()
        guild_count = len(self.guilds)
        member_count = sum(g.member_count or 0 for g in self.guilds)
        logger.info("=" * 60)
        logger.info("%s online como %s | ID: %s", BOT_NAME, self.user, self.user.id if self.user else "?")
        logger.info("Servidores: %s | Usuários vistos: %s", guild_count, member_count)
        logger.info("Python %s | discord.py %s", platform.python_version(), discord.__version__)
        logger.info("Cogs carregados: %s | Falhas: %s", self.loaded_cogs_count, ", ".join(self.failed_cogs) or "nenhuma")
        logger.info("=" * 60)

        if not self._rotate_status.is_running():
            self._rotate_status.start()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        logger.info("Entrei no servidor: %s (%s)", guild.name, guild.id)

        channel = next(
            (
                ch for ch in guild.text_channels
                if ch.permissions_for(guild.me).send_messages and ch.permissions_for(guild.me).embed_links
            ),
            None,
        )
        if not channel:
            return

        embed = discord.Embed(
            title=f"<a:1000032071:1507947918752092301> {BOT_NAME} chegou.",
            description=(
                "Sou uma IA para conversa, estudo, produtividade e moderação no Discord.\n\n"
                "<:1000032072:1507947958723809340> **IA**: use `/chat`, me mencione ou ative um canal com `/canal-ia`.\n"
                "<:1000032049:1507946904124919949> **Acadêmico pesado**: use `/estudar` ou `/analisar-argumento`.\n"
                "<:1000032082:1507948289444544512> **Personalização**: use `/perfil-ia` para definir seu estilo de resposta.\n"
                "<:1000032048:1507946854405505034> **Administração**: use `/status-ia` para ver performance, cache e biblioteca.\n\n"
                "Use `/ajuda` para ver os comandos gerais."
            ),
            color=discord.Color.blurple(),
        )
        if self.user:
            embed.set_thumbnail(url=self.user.display_avatar.url)
        embed.set_footer(text=f"{BOT_NAME} • IA modular, rápida e organizada")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            logger.warning("Não consegui enviar boas-vindas em %s: %s", guild.name, exc)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        logger.info("Saí do servidor: %s (%s)", guild.name, guild.id)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        logger.exception("Erro em comando prefixado: %s", error)
        try:
            await ctx.reply("<:1000032079:1507948213741813972> Ocorreu um erro ao executar esse comando.", mention_author=False)
        except Exception:
            pass

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = "<:1000032079:1507948213741813972> Ocorreu um erro ao executar esse comando."
        if isinstance(error, app_commands.MissingPermissions):
            message = "<a:1000032057:1507947249873719497> Você não tem permissão para usar esse comando."
        elif isinstance(error, app_commands.BotMissingPermissions):
            message = "<a:1000032057:1507947249873719497> Eu não tenho permissão suficiente para fazer isso."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"<:1000032059:1507947381096714260> Aguarde {error.retry_after:.1f}s antes de usar esse comando de novo."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception:
            pass

        logger.exception("Erro em slash command %s: %s", interaction.command, error)


bot = RevolutxBot()


async def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN não encontrado nas variáveis de ambiente.")
        return

    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
