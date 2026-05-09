import platform
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands


class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ajuda", description="Mostra os comandos disponíveis.")
    async def help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Revolux: comandos",
            description="Painel limpo dos recursos ativos. Anti-raid e parcerias foram removidos do carregamento.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="IA",
            value=(
                "`/chat` conversa com o Revolux\n"
                "`/limpar-conversa` apaga seu histórico\n"
                "`/canal-ia` ativa/desativa IA automática em um canal\n"
                "`/status-ia` mostra modelos e busca\n"
                "`/testar-ia` testa os modelos configurados"
            ),
            inline=False,
        )
        embed.add_field(
            name="Moderação",
            value=(
                "`/ban` bane um usuário\n"
                "`/kick` expulsa um usuário\n"
                "`/mute` silencia temporariamente\n"
                "`/unmute` remove silenciamento\n"
                "`/warn` registra aviso\n"
                "`/avisos` mostra avisos\n"
                "`/limpar-avisos` remove avisos\n"
                "`/purge` apaga mensagens\n"
                "`/palavra-proibida` gerencia termos bloqueados\n"
                "`/config-mod` ajusta a moderação automática"
            ),
            inline=False,
        )
        embed.add_field(
            name="Utilidades",
            value=(
                "`/userinfo` mostra informações de usuário\n"
                "`/serverinfo` mostra informações do servidor\n"
                "`/avatar` mostra avatar\n"
                "`/ping` mostra latência"
            ),
            inline=False,
        )
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="Revolux • organizado para Discord")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="ping", description="Mostra a latência do bot.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency = round(self.bot.latency * 1000)
        if latency < 120:
            status = "ótima"
            color = discord.Color.green()
        elif latency < 250:
            status = "normal"
            color = discord.Color.gold()
        else:
            status = "alta"
            color = discord.Color.red()

        embed = discord.Embed(
            title="Pong",
            description=f"Latência: `{latency}ms`\nStatus: **{status}**",
            color=color,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo", description="Mostra informações de um usuário.")
    @app_commands.describe(membro="Usuário para consultar. Se vazio, consulta você.")
    async def userinfo(self, interaction: discord.Interaction, membro: discord.Member | None = None) -> None:
        membro = membro or interaction.user
        roles = [role.mention for role in reversed(membro.roles) if role.name != "@everyone"]
        roles_text = ", ".join(roles[:12]) if roles else "Nenhum cargo destacado"
        if len(roles) > 12:
            roles_text += f" e mais {len(roles) - 12}"

        embed = discord.Embed(
            title=membro.display_name,
            color=membro.color if membro.color != discord.Color.default() else discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=membro.display_avatar.url)
        embed.add_field(name="Usuário", value=str(membro), inline=True)
        embed.add_field(name="ID", value=f"`{membro.id}`", inline=True)
        embed.add_field(name="Bot", value="Sim" if membro.bot else "Não", inline=True)
        embed.add_field(name="Conta criada", value=f"<t:{int(membro.created_at.timestamp())}:D>\n<t:{int(membro.created_at.timestamp())}:R>", inline=True)
        if membro.joined_at:
            embed.add_field(name="Entrou no servidor", value=f"<t:{int(membro.joined_at.timestamp())}:D>\n<t:{int(membro.joined_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Cargo mais alto", value=membro.top_role.mention if membro.top_role else "Nenhum", inline=True)
        embed.add_field(name="Cargos", value=roles_text, inline=False)
        if membro.premium_since:
            embed.add_field(name="Boost desde", value=f"<t:{int(membro.premium_since.timestamp())}:D>", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="Mostra informações do servidor.")
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        bots = sum(1 for member in guild.members if member.bot)
        humans = (guild.member_count or 0) - bots

        embed = discord.Embed(title=guild.name, color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)

        embed.add_field(name="ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Dono", value=guild.owner.mention if guild.owner else "Desconhecido", inline=True)
        embed.add_field(name="Criado em", value=f"<t:{int(guild.created_at.timestamp())}:D>", inline=True)
        embed.add_field(name="Membros", value=f"Total: **{guild.member_count}**\nHumanos: **{humans}**\nBots: **{bots}**", inline=True)
        embed.add_field(name="Canais", value=f"Texto: **{len(guild.text_channels)}**\nVoz: **{len(guild.voice_channels)}**\nCategorias: **{len(guild.categories)}**", inline=True)
        embed.add_field(name="Cargos", value=f"**{len(guild.roles)}**", inline=True)
        embed.add_field(name="Verificação", value=str(guild.verification_level).replace("_", " ").title(), inline=True)
        embed.add_field(name="Boost", value=f"Nível **{guild.premium_tier}** | **{guild.premium_subscription_count}** boost(s)", inline=True)
        embed.add_field(name="Sistema", value=f"Python {platform.python_version()}\ndiscord.py {discord.__version__}", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="avatar", description="Mostra o avatar de um usuário.")
    @app_commands.describe(membro="Usuário para consultar. Se vazio, consulta você.")
    async def avatar(self, interaction: discord.Interaction, membro: discord.Member | None = None) -> None:
        membro = membro or interaction.user
        embed = discord.Embed(title=f"Avatar de {membro.display_name}", color=discord.Color.blurple())
        embed.set_image(url=membro.display_avatar.url)
        embed.add_field(name="Link", value=f"[Abrir imagem]({membro.display_avatar.url})", inline=False)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))
