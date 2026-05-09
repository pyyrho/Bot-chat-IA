import asyncio
import logging
import os
import platform
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from utils.database import db

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("Revolux")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
intents.moderation = True


class RevoluxBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=os.getenv("BOT_PREFIX", "!"),
            intents=intents,
            help_command=None,
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="/ajuda | Revolux",
            ),
        )
        self.start_time = datetime.now(timezone.utc)

    async def setup_hook(self) -> None:
        await db.connect()

        # Arquivos removidos de propósito: cogs.anti_raid e cogs.partnership.
        # Se você apagar esses arquivos do GitHub, o bot não vai quebrar por falta deles.
        cogs = (
            "cogs.ai_chat",
            "cogs.moderation",
            "cogs.utility",
        )

        loaded = 0
        for cog in cogs:
            try:
                await self.load_extension(cog)
                loaded += 1
                logger.info("Cog carregado: %s", cog)
            except Exception as exc:
                logger.exception("Erro ao carregar %s: %s", cog, exc)

        synced = await self.tree.sync()
        logger.info("Slash commands sincronizados: %s | Cogs: %s/%s", len(synced), loaded, len(cogs))

    async def close(self) -> None:
        await db.close()
        await super().close()

    async def on_ready(self) -> None:
        guild_count = len(self.guilds)
        member_count = sum(g.member_count or 0 for g in self.guilds)
        logger.info("=" * 54)
        logger.info("Revolux online como %s | ID: %s", self.user, self.user.id if self.user else "?")
        logger.info("Servidores: %s | Usuários vistos: %s", guild_count, member_count)
        logger.info("Python %s | discord.py %s", platform.python_version(), discord.__version__)
        logger.info("=" * 54)

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
            title="Revolux chegou.",
            description=(
                "Sou um assistente de conversa, estudo e moderação para Discord.\n\n"
                "**IA**: use `/chat`, me mencione ou ative um canal com `/canal-ia`.\n"
                "**Acadêmico**: filosofia, lógica, matemática, programação e explicações rigorosas.\n"
                "**Moderação**: avisos, mute, ban, purge e filtros configuráveis.\n\n"
                "Use `/ajuda` para ver os comandos."
            ),
            color=discord.Color.blurple(),
        )
        if self.user:
            embed.set_thumbnail(url=self.user.display_avatar.url)
        embed.set_footer(text="Revolux • claro, rápido e organizado")
        await channel.send(embed=embed)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        logger.info("Saí do servidor: %s (%s)", guild.name, guild.id)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        logger.exception("Erro em comando prefixado: %s", error)

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = "Ocorreu um erro ao executar esse comando."
        if isinstance(error, app_commands.MissingPermissions):
            message = "Você não tem permissão para usar esse comando."
        elif isinstance(error, app_commands.BotMissingPermissions):
            message = "Eu não tenho permissão suficiente para fazer isso."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"Aguarde {error.retry_after:.1f}s antes de usar esse comando de novo."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception:
            pass

        logger.exception("Erro em slash command %s: %s", interaction.command, error)


bot = RevoluxBot()


async def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN não encontrado nas variáveis de ambiente.")
        return

    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
