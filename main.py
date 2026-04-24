import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncio
import logging
import platform
import psutil
from datetime import datetime, timezone
from dotenv import load_dotenv
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
        self.start_time = datetime.now(timezone.utc)

    async def setup_hook(self):
        await db.connect()

        cogs = [
            "cogs.ai_chat",
            "cogs.moderation",
            "cogs.partnership",
            "cogs.utility",
            "cogs.anti_raid",
        ]
        loaded = 0
        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"✅ Cog carregado: {cog}")
                loaded += 1
            except Exception as e:
                logger.error(f"❌ Erro ao carregar {cog}: {e}")

        await self.tree.sync()
        logger.info(f"✅ Slash commands sincronizados! ({loaded}/{len(cogs)} cogs carregados)")

    async def close(self):
        await db.close()
        await super().close()

    async def on_ready(self):
        logger.info("=" * 50)
        logger.info(f"🤖 Bot online como {self.user} (ID: {self.user.id})")
        logger.info(f"📡 Conectado a {len(self.guilds)} servidor(es)")
        logger.info(f"👥 Usuários totais: {sum(g.member_count for g in self.guilds)}")
        logger.info(f"🐍 Python {platform.python_version()} | discord.py {discord.__version__}")
        logger.info("=" * 50)

    async def on_guild_join(self, guild: discord.Guild):
        logger.info(f"➕ Entrei no servidor: {guild.name} ({guild.id})")
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                embed = discord.Embed(
                    title="👋 Olá, galera!",
                    description=(
                        "Fala, meu povo! 😄 Eu sou o **RevolutX**, seu novo assistente de IA!\n\n"
                        "🤖 **Chat com IA** — Me marque ou fale comigo que eu respondo!\n"
                        "🖼️ **Visão** — Manda uma imagem e eu analiso!\n"
                        "🛡️ **Moderação Avançada** — Proteção automática pro seu server!\n"
                        "🤝 **Parcerias** — Sistema automático de parceria entre servidores!\n"
                        "⚡ **Anti-Raid** — Proteção contra raids e nukes!\n\n"
                        "Use `/ajuda` pra ver todos os comandos. Bora nessa? 🚀"
                    ),
                    color=discord.Color.purple()
                )
                embed.set_thumbnail(url=self.user.display_avatar.url)
                embed.set_footer(text="RevolutX • IA avançada para Discord")
                await channel.send(embed=embed)
                break

    async def on_guild_remove(self, guild: discord.Guild):
        logger.info(f"➖ Saí do servidor: {guild.name} ({guild.id})")

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        logger.error(f"Erro de comando: {error}")

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        msg = "Ocorreu um erro ao executar esse comando."
        if isinstance(error, app_commands.MissingPermissions):
            msg = "Você não tem permissão para usar esse comando."
        elif isinstance(error, app_commands.BotMissingPermissions):
            msg = "Não tenho permissão para fazer isso no servidor."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"Calma! Tenta de novo em {error.retry_after:.1f}s."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

        logger.error(f"Erro em app command '{interaction.command}': {error}")


bot = MyBot()


# ── Comandos globais ──────────────────────────────────────────

@bot.tree.command(name="ping", description="🏓 Mostra a latência do bot")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    color = discord.Color.green() if latency < 100 else discord.Color.orange() if latency < 200 else discord.Color.red()
    embed = discord.Embed(title="🏓 Pong!", color=color)
    embed.add_field(name="Latência", value=f"`{latency}ms`", inline=True)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="uptime", description="⏱️ Mostra há quanto tempo o bot está online")
async def uptime(interaction: discord.Interaction):
    delta = datetime.now(timezone.utc) - bot.start_time
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    days = hours // 24
    hours = hours % 24

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")

    embed = discord.Embed(
        title="⏱️ Uptime",
        description=f"Online há **{' '.join(parts)}**",
        color=discord.Color.purple()
    )
    embed.add_field(name="Desde", value=f"<t:{int(bot.start_time.timestamp())}:F>", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="info", description="ℹ️ Informações sobre o bot")
async def info(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 RevolutX",
        description="Assistente de IA avançado para Discord",
        color=discord.Color.purple()
    )
    embed.add_field(name="Servidores", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Usuários", value=str(sum(g.member_count for g in bot.guilds)), inline=True)
    embed.add_field(name="Latência", value=f"{round(bot.latency * 1000)}ms", inline=True)
    embed.add_field(name="Versão Python", value=platform.python_version(), inline=True)
    embed.add_field(name="discord.py", value=discord.__version__, inline=True)
    embed.add_field(
        name="Recursos",
        value="🤖 Chat IA • 🖼️ Visão • 🛡️ Moderação\n🤝 Parcerias • ⚡ Anti-Raid",
        inline=False
    )
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.set_footer(text="RevolutX • Powered by IA")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ajuda", description="📋 Lista todos os comandos disponíveis")
async def ajuda(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 Comandos do RevolutX",
        color=discord.Color.purple()
    )
    embed.add_field(
        name="🤖 IA",
        value=(
            "`/chat` — Conversa com a IA\n"
            "`/limpar-conversa` — Limpa histórico\n"
            "`/canal-ia` — Ativa IA num canal (admin)\n"
            "`/status-ia` — Status das chaves (admin)"
        ),
        inline=False
    )
    embed.add_field(
        name="🛠️ Utilidade",
        value=(
            "`/ping` — Latência do bot\n"
            "`/uptime` — Tempo online\n"
            "`/info` — Info do bot\n"
            "`/ajuda` — Esta mensagem"
        ),
        inline=False
    )
    embed.add_field(
        name="💡 Como usar a IA",
        value=(
            "• Me **marque** em qualquer canal\n"
            "• **Responda** uma das minhas mensagens\n"
            "• Manda uma **imagem** junto com sua pergunta!\n"
            "• Ative a IA automática num canal com `/canal-ia`"
        ),
        inline=False
    )
    embed.set_footer(text="RevolutX • Use /chat para falar comigo!")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("❌ DISCORD_TOKEN não encontrado no .env!")
        return
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
