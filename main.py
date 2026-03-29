import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv
import logging
from utils.database import db

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger('Bot')

intents = discord.Intents.all()

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="✨ Animando a galera!"
            )
        )

    async def setup_hook(self):
        # Conecta ao banco ANTES de carregar os cogs
        await db.connect()

        cogs = [
            "cogs.ai_chat",
            "cogs.moderation",
            "cogs.partnership",
            "cogs.utility",
            "cogs.anti_raid",
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"✅ Cog carregado: {cog}")
            except Exception as e:
                logger.error(f"❌ Erro ao carregar {cog}: {e}")

        await self.tree.sync()
        logger.info("✅ Slash commands sincronizados!")

    async def close(self):
        await db.close()
        await super().close()

    async def on_ready(self):
        logger.info(f"🤖 Bot online como {self.user} (ID: {self.user.id})")
        logger.info(f"📡 Conectado a {len(self.guilds)} servidor(es)")

    async def on_guild_join(self, guild: discord.Guild):
        logger.info(f"➕ Entrei no servidor: {guild.name} ({guild.id})")
        # Tenta mandar mensagem de boas vindas no primeiro canal disponível
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                embed = discord.Embed(
                    title="👋 Olá, galera!",
                    description=(
                        "Fala, meu povo! 😄 Eu sou o **Cordyx**, seu novo assistente de IA!\n\n"
                        "🤖 **Chat com IA** — Me marque ou fale comigo que eu respondo!\n"
                        "🛡️ **Moderação Avançada** — Proteção automática pro seu server!\n"
                        "🤝 **Parcerias** — Sistema automático de parceria entre servidores!\n"
                        "⚡ **Anti-Raid** — Proteção contra raids e nukes!\n\n"
                        "Use `/ajuda` pra ver todos os comandos. Bora nessa? 🚀✨"
                    ),
                    color=discord.Color.purple()
                )
                embed.set_thumbnail(url=self.user.display_avatar.url)
                await channel.send(embed=embed)
                break

bot = MyBot()

async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("❌ DISCORD_TOKEN não encontrado no .env!")
        return
    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
