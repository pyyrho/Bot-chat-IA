import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Optional
from utils.database import db

class AntiRaid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.join_tracker = defaultdict(list)
        self.clean_trackers.start()

    async def get_config(self, guild_id: int) -> dict:
        row = await db.pool.fetchrow("SELECT * FROM antiraid_config WHERE guild_id = $1", guild_id)
        if row:
            return dict(row)
        await db.pool.execute("INSERT INTO antiraid_config (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
        return await self.get_config(guild_id)

    async def save_config(self, guild_id: int, **kwargs):
        sets = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(kwargs))
        await db.pool.execute(f"UPDATE antiraid_config SET {sets} WHERE guild_id = $1", guild_id, *kwargs.values())

    async def log_event(self, guild: discord.Guild, embed: discord.Embed):
        config = await self.get_config(guild.id)
        ch_id = config.get("log_channel")
        if ch_id:
            ch = guild.get_channel(ch_id)
            if ch:
                try:
                    await ch.send(embed=embed)
                except discord.Forbidden:
                    pass

    async def activate_lockdown(self, guild: discord.Guild):
        await self.save_config(guild.id, lockdown_active=True)
        locked = 0
        for channel in guild.text_channels:
            ow = channel.overwrites_for(guild.default_role)
            if ow.send_messages is not False:
                ow.send_messages = False
                try:
                    await channel.set_permissions(guild.default_role, overwrite=ow, reason="[Anti-Raid] Lockdown automático")
                    locked += 1
                except discord.Forbidden:
                    pass
        embed = discord.Embed(title="🔒 LOCKDOWN ATIVADO", description=f"⚠️ Raid detectado! {locked} canais bloqueados.\nUse `/lockdown off` para desbloquear.", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
        await self.log_event(guild, embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        config = await self.get_config(guild.id)
        if not config.get("enabled", True):
            return

        # Verificação de conta nova
        account_age = (datetime.now(timezone.utc) - member.created_at).days
        min_age = config.get("min_account_age", 7)
        if account_age < min_age:
            embed = discord.Embed(title="⚠️ Conta Nova Detectada", color=discord.Color.yellow(), timestamp=datetime.now(timezone.utc))
            embed.add_field(name="Usuário", value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.add_field(name="Idade da Conta", value=f"{account_age} dia(s)", inline=True)
            await self.log_event(guild, embed)
            if account_age < 1:
                try:
                    await member.send(f"❌ Conta muito nova para entrar em **{guild.name}**.")
                except discord.Forbidden:
                    pass
                try:
                    await member.kick(reason="Conta muito nova — suspeita de bot/raid")
                except discord.Forbidden:
                    pass
                return

        # Detecção de raid
        now = datetime.now()
        gid = guild.id
        window = config.get("raid_window", 10)
        threshold = config.get("raid_threshold", 10)
        self.join_tracker[gid] = [t for t in self.join_tracker[gid] if (now - t).total_seconds() < window]
        self.join_tracker[gid].append(now)

        if len(self.join_tracker[gid]) >= threshold:
            action = config.get("action", "kick")
            if action == "lockdown":
                await self.activate_lockdown(guild)
            else:
                try:
                    if action == "ban":
                        await member.ban(reason="[Anti-Raid] Raid detectado")
                    else:
                        await member.kick(reason="[Anti-Raid] Raid detectado")
                except discord.Forbidden:
                    pass
            self.join_tracker[gid] = []

    @app_commands.command(name="lockdown", description="🔒 Ativa/desativa o lockdown do servidor [ADMIN]")
    @app_commands.choices(acao=[
        app_commands.Choice(name="Ativar", value="on"),
        app_commands.Choice(name="Desativar", value="off"),
    ])
    @app_commands.default_permissions(administrator=True)
    async def lockdown(self, interaction: discord.Interaction, acao: str):
        await interaction.response.defer()
        guild = interaction.guild
        if acao == "on":
            await self.activate_lockdown(guild)
            await interaction.followup.send("🔒 **Lockdown ativado!**")
        else:
            await self.save_config(guild.id, lockdown_active=False)
            unlocked = 0
            for channel in guild.text_channels:
                ow = channel.overwrites_for(guild.default_role)
                if ow.send_messages is False:
                    ow.send_messages = None
                    try:
                        await channel.set_permissions(guild.default_role, overwrite=ow, reason="[Anti-Raid] Lockdown encerrado")
                        unlocked += 1
                    except discord.Forbidden:
                        pass
            await interaction.followup.send(f"🔓 **Lockdown desativado!** {unlocked} canais desbloqueados.")

    @app_commands.command(name="config-antiraid", description="⚙️ Configura o Anti-Raid [ADMIN]")
    @app_commands.choices(acao=[
        app_commands.Choice(name="Expulsar (kick)", value="kick"),
        app_commands.Choice(name="Banir (ban)", value="ban"),
        app_commands.Choice(name="Lockdown", value="lockdown"),
    ])
    @app_commands.default_permissions(administrator=True)
    async def config_antiraid(self, interaction: discord.Interaction,
                               ativar: Optional[bool] = None,
                               limite_entradas: Optional[int] = None,
                               janela: Optional[int] = None,
                               idade_minima: Optional[int] = None,
                               acao: Optional[str] = None,
                               canal_alertas: Optional[discord.TextChannel] = None):
        await self.get_config(interaction.guild_id)
        updates = {}
        if ativar is not None: updates["enabled"] = ativar
        if limite_entradas: updates["raid_threshold"] = max(3, limite_entradas)
        if janela: updates["raid_window"] = max(5, janela)
        if idade_minima is not None: updates["min_account_age"] = max(0, idade_minima)
        if acao: updates["action"] = acao
        if canal_alertas: updates["log_channel"] = canal_alertas.id
        if updates:
            await self.save_config(interaction.guild_id, **updates)
        config = await self.get_config(interaction.guild_id)
        embed = discord.Embed(title="⚙️ Anti-Raid Configurado!", color=discord.Color.green())
        embed.add_field(name="Status", value="✅ Ativo" if config["enabled"] else "❌ Inativo", inline=True)
        embed.add_field(name="Limite", value=f"{config['raid_threshold']} em {config['raid_window']}s", inline=True)
        embed.add_field(name="Idade Mínima", value=f"{config['min_account_age']} dias", inline=True)
        embed.add_field(name="Ação", value=config["action"].capitalize(), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="status-seguranca", description="🛡️ Mostra o status de segurança do servidor")
    @app_commands.default_permissions(manage_guild=True)
    async def security_status(self, interaction: discord.Interaction):
        config = await self.get_config(interaction.guild_id)
        guild = interaction.guild
        embed = discord.Embed(title=f"🛡️ Status de Segurança — {guild.name}", color=discord.Color.blue(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Anti-Raid", value="✅ Ativo" if config["enabled"] else "❌ Inativo", inline=True)
        embed.add_field(name="Lockdown", value="🔒 Ativo" if config.get("lockdown_active") else "🔓 Normal", inline=True)
        embed.add_field(name="Detecção", value=f"{config['raid_threshold']} entradas em {config['raid_window']}s", inline=True)
        embed.add_field(name="Idade Mínima", value=f"{config['min_account_age']} dias", inline=True)
        embed.add_field(name="Ação", value=config["action"].capitalize(), inline=True)
        embed.add_field(name="Verificação Discord", value=str(guild.verification_level).replace("_"," ").title(), inline=True)
        await interaction.response.send_message(embed=embed)

    @tasks.loop(minutes=10)
    async def clean_trackers(self):
        now = datetime.now()
        for gid in list(self.join_tracker.keys()):
            self.join_tracker[gid] = [t for t in self.join_tracker[gid] if (now - t).total_seconds() < 60]

async def setup(bot):
    await bot.add_cog(AntiRaid(bot))
