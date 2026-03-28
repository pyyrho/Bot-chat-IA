import discord
from discord.ext import commands, tasks
from discord import app_commands
from groq import Groq
import asyncio
import re
import json
import os
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Optional
from utils.database import db

logger = logging.getLogger("Moderation")

SPAM_THRESHOLD = 5
SPAM_WINDOW = 5
CAPS_THRESHOLD = 0.70
MENTION_LIMIT = 5
LINK_WHITELIST = ["discord.com", "discord.gg"]

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        self.message_tracker = defaultdict(list)
        self.clean_trackers.start()

    async def get_config(self, guild_id: int) -> dict:
        row = await db.pool.fetchrow("SELECT * FROM mod_config WHERE guild_id = $1", guild_id)
        if row:
            return dict(row)
        await db.pool.execute("INSERT INTO mod_config (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
        return await self.get_config(guild_id)

    async def save_config(self, guild_id: int, **kwargs):
        sets = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(kwargs))
        await db.pool.execute(f"UPDATE mod_config SET {sets} WHERE guild_id = $1", guild_id, *kwargs.values())

    async def get_warn_count(self, guild_id: int, user_id: int) -> int:
        row = await db.pool.fetchrow(
            "SELECT COUNT(*) as c FROM warnings WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        )
        return row["c"] if row else 0

    async def add_warning(self, guild_id: int, user_id: int, reason: str, moderator_id: int = None):
        await db.pool.execute(
            "INSERT INTO warnings (guild_id, user_id, reason, moderator) VALUES ($1,$2,$3,$4)",
            guild_id, user_id, reason, moderator_id
        )

    async def clear_warnings(self, guild_id: int, user_id: int):
        await db.pool.execute("DELETE FROM warnings WHERE guild_id = $1 AND user_id = $2", guild_id, user_id)

    async def log_action(self, guild: discord.Guild, embed: discord.Embed):
        config = await self.get_config(guild.id)
        if config.get("log_channel"):
            ch = guild.get_channel(config["log_channel"])
            if ch:
                try:
                    await ch.send(embed=embed)
                except discord.Forbidden:
                    pass

    async def warn_user(self, guild: discord.Guild, member: discord.Member, reason: str, moderator=None):
        await self.add_warning(guild.id, member.id, reason, moderator.id if moderator else None)
        warn_count = await self.get_warn_count(guild.id, member.id)
        config = await self.get_config(guild.id)
        threshold = config.get("warn_threshold", 3)

        embed = discord.Embed(title="⚠️ Aviso Registrado", color=discord.Color.yellow(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Usuário", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Avisos", value=f"{warn_count}/{threshold}", inline=True)
        embed.add_field(name="Motivo", value=reason, inline=False)
        if moderator:
            embed.add_field(name="Moderador", value=moderator.mention, inline=True)
        await self.log_action(guild, embed)

        try:
            await member.send(f"⚠️ Você recebeu um aviso em **{guild.name}**!\n**Motivo:** {reason}\n**Total:** {warn_count}/{threshold}")
        except discord.Forbidden:
            pass

        if warn_count >= threshold:
            action = config.get("warn_action", "mute")
            if action == "mute":
                await self.mute_member(guild, member, 3600, "Limite de avisos atingido")
            elif action == "kick":
                try:
                    await member.kick(reason="Limite de avisos atingido")
                except discord.Forbidden:
                    pass
            elif action == "ban":
                try:
                    await member.ban(reason="Limite de avisos atingido")
                except discord.Forbidden:
                    pass
            await self.clear_warnings(guild.id, member.id)

    async def mute_member(self, guild: discord.Guild, member: discord.Member, duration: int, reason: str):
        try:
            until = datetime.now(timezone.utc) + timedelta(seconds=duration)
            await member.timeout(until, reason=reason)
            embed = discord.Embed(title="🔇 Usuário Silenciado", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
            embed.add_field(name="Usuário", value=member.mention, inline=True)
            embed.add_field(name="Duração", value=f"{duration//60} minutos", inline=True)
            embed.add_field(name="Motivo", value=reason, inline=False)
            await self.log_action(guild, embed)
        except discord.Forbidden:
            pass

    async def ai_analyze_message(self, content: str) -> dict:
        try:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": """Você é um moderador de Discord. Analise a mensagem e retorne APENAS um JSON:
{"violation": true/false, "severity": "low/medium/high", "reason": "motivo curto em português", "action": "warn/mute/kick/ban/none"}
Considere violação: hate speech, ameaças, conteúdo sexual explícito, bullying severo, spam malicioso.
Seja equilibrado — não puna conversas normais. Retorne APENAS o JSON sem mais nada."""},
                    {"role": "user", "content": f"Mensagem: {content}"}
                ],
                max_tokens=100,
                temperature=0.1,
            )
            text = response.choices[0].message.content.strip()
            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            logger.error(f"Erro moderação IA: {e}")
            return {"violation": False, "severity": "none", "reason": "", "action": "none"}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        if message.author.guild_permissions.administrator:
            return

        config = await self.get_config(message.guild.id)
        member = message.author
        content = message.content
        guild = message.guild

        if config.get("anti_spam"):
            now = datetime.now()
            uid = member.id
            self.message_tracker[uid] = [t for t in self.message_tracker[uid] if (now - t).total_seconds() < SPAM_WINDOW]
            self.message_tracker[uid].append(now)
            if len(self.message_tracker[uid]) >= SPAM_THRESHOLD:
                await message.delete()
                await self.warn_user(guild, member, "Spam detectado")
                self.message_tracker[uid] = []
                return

        if config.get("anti_caps") and len(content) > 10:
            upper = sum(1 for c in content if c.isupper())
            if upper / len(content) > CAPS_THRESHOLD:
                await message.delete()
                await self.warn_user(guild, member, "Excesso de letras maiúsculas")
                return

        banned = config.get("banned_words") or []
        for word in banned:
            if word.lower() in content.lower():
                await message.delete()
                await self.warn_user(guild, member, f"Palavra proibida: {word}")
                return

        if config.get("anti_mention") and len(message.mentions) > MENTION_LIMIT:
            await message.delete()
            await self.warn_user(guild, member, f"Excesso de menções ({len(message.mentions)})")
            return

        if config.get("anti_links"):
            urls = re.findall(r'https?://[^\s]+|discord\.gg/[^\s]+', content)
            for url in urls:
                if not any(d in url for d in LINK_WHITELIST):
                    await message.delete()
                    await self.warn_user(guild, member, "Link não permitido")
                    return

        if config.get("ai_moderation") and len(content) > 20:
            result = await self.ai_analyze_message(content)
            if result.get("violation"):
                severity = result.get("severity", "low")
                reason = result.get("reason", "Violação detectada pela IA")
                action = result.get("action", "warn")
                if severity in ["medium", "high"]:
                    try:
                        await message.delete()
                    except discord.NotFound:
                        pass
                if action == "warn":
                    await self.warn_user(guild, member, f"[IA] {reason}")
                elif action == "mute":
                    await self.warn_user(guild, member, f"[IA] {reason}")
                    await self.mute_member(guild, member, 1800, f"[IA] {reason}")
                elif action == "kick":
                    try:
                        await member.kick(reason=f"[IA] {reason}")
                    except discord.Forbidden:
                        pass
                elif action == "ban" and severity == "high":
                    try:
                        await member.ban(reason=f"[IA] {reason}", delete_message_days=1)
                    except discord.Forbidden:
                        pass

    @tasks.loop(minutes=5)
    async def clean_trackers(self):
        now = datetime.now()
        for uid in list(self.message_tracker.keys()):
            self.message_tracker[uid] = [t for t in self.message_tracker[uid] if (now - t).total_seconds() < SPAM_WINDOW]

    @app_commands.command(name="ban", description="🔨 Bane um usuário do servidor")
    @app_commands.describe(membro="Usuário a ser banido", motivo="Motivo do ban", deletar_msgs="Dias de mensagens para deletar (0-7)")
    @app_commands.default_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, membro: discord.Member, motivo: str = "Sem motivo informado", deletar_msgs: int = 1):
        if membro.top_role >= interaction.user.top_role:
            await interaction.response.send_message("❌ Cargo insuficiente!", ephemeral=True)
            return
        try:
            await membro.send(f"🔨 Você foi banido de **{interaction.guild.name}**!\n**Motivo:** {motivo}")
        except discord.Forbidden:
            pass
        await membro.ban(reason=f"{motivo} | Por: {interaction.user}", delete_message_days=min(deletar_msgs, 7))
        embed = discord.Embed(title="🔨 Usuário Banido", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Usuário", value=f"{membro} (`{membro.id}`)", inline=True)
        embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        await interaction.response.send_message(embed=embed)
        await self.log_action(interaction.guild, embed)

    @app_commands.command(name="kick", description="👢 Expulsa um usuário do servidor")
    @app_commands.describe(membro="Usuário a ser expulso", motivo="Motivo da expulsão")
    @app_commands.default_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, membro: discord.Member, motivo: str = "Sem motivo informado"):
        if membro.top_role >= interaction.user.top_role:
            await interaction.response.send_message("❌ Cargo insuficiente!", ephemeral=True)
            return
        try:
            await membro.send(f"👢 Você foi expulso de **{interaction.guild.name}**!\n**Motivo:** {motivo}")
        except discord.Forbidden:
            pass
        await membro.kick(reason=f"{motivo} | Por: {interaction.user}")
        embed = discord.Embed(title="👢 Usuário Expulso", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Usuário", value=f"{membro} (`{membro.id}`)", inline=True)
        embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        await interaction.response.send_message(embed=embed)
        await self.log_action(interaction.guild, embed)

    @app_commands.command(name="mute", description="🔇 Silencia um usuário temporariamente")
    @app_commands.describe(membro="Usuário a ser silenciado", duracao="Duração em minutos", motivo="Motivo do mute")
    @app_commands.default_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, membro: discord.Member, duracao: int = 10, motivo: str = "Sem motivo informado"):
        await self.mute_member(interaction.guild, membro, duracao * 60, motivo)
        embed = discord.Embed(title="🔇 Usuário Silenciado", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Usuário", value=membro.mention, inline=True)
        embed.add_field(name="Duração", value=f"{duracao} minutos", inline=True)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="unmute", description="🔊 Remove o silenciamento de um usuário")
    @app_commands.default_permissions(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, membro: discord.Member):
        await membro.timeout(None)
        await interaction.response.send_message(f"✅ {membro.mention} foi desmutado!")

    @app_commands.command(name="warn", description="⚠️ Avisa um usuário")
    @app_commands.describe(membro="Usuário a ser avisado", motivo="Motivo do aviso")
    @app_commands.default_permissions(manage_messages=True)
    async def warn(self, interaction: discord.Interaction, membro: discord.Member, motivo: str = "Comportamento inadequado"):
        await self.warn_user(interaction.guild, membro, motivo, interaction.user)
        await interaction.response.send_message(f"⚠️ {membro.mention} foi avisado! **Motivo:** {motivo}")

    @app_commands.command(name="avisos", description="📋 Veja os avisos de um usuário")
    @app_commands.default_permissions(manage_messages=True)
    async def check_warns(self, interaction: discord.Interaction, membro: discord.Member):
        count = await self.get_warn_count(interaction.guild_id, membro.id)
        config = await self.get_config(interaction.guild_id)
        threshold = config.get("warn_threshold", 3)
        rows = await db.pool.fetch(
            "SELECT reason, created_at FROM warnings WHERE guild_id=$1 AND user_id=$2 ORDER BY created_at DESC LIMIT 5",
            interaction.guild_id, membro.id
        )
        embed = discord.Embed(title=f"📋 Avisos de {membro.display_name}", color=discord.Color.yellow())
        embed.add_field(name="Total", value=f"{count}/{threshold}", inline=True)
        embed.set_thumbnail(url=membro.display_avatar.url)
        for r in rows:
            embed.add_field(name=f"• {r['created_at'].strftime('%d/%m/%Y')}", value=r['reason'], inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="limpar-avisos", description="🗑️ Remove os avisos de um usuário")
    @app_commands.default_permissions(administrator=True)
    async def clear_warns(self, interaction: discord.Interaction, membro: discord.Member):
        await self.clear_warnings(interaction.guild_id, membro.id)
        await interaction.response.send_message(f"✅ Avisos de {membro.mention} limpos!", ephemeral=True)

    @app_commands.command(name="purge", description="🧹 Deleta várias mensagens de uma vez")
    @app_commands.describe(quantidade="Quantidade de mensagens (1-100)")
    @app_commands.default_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, quantidade: int):
        if not 1 <= quantidade <= 100:
            await interaction.response.send_message("❌ Entre 1 e 100!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=quantidade)
        await interaction.followup.send(f"✅ {len(deleted)} mensagens deletadas!", ephemeral=True)

    @app_commands.command(name="config-mod", description="⚙️ Configura o sistema de moderação [ADMIN]")
    @app_commands.choices(acao_avisos=[
        app_commands.Choice(name="Silenciar", value="mute"),
        app_commands.Choice(name="Expulsar", value="kick"),
        app_commands.Choice(name="Banir", value="ban"),
    ])
    @app_commands.default_permissions(administrator=True)
    async def config_mod(self, interaction: discord.Interaction,
                         canal_log: Optional[discord.TextChannel] = None,
                         limite_avisos: Optional[int] = None,
                         acao_avisos: Optional[str] = None):
        await self.get_config(interaction.guild_id)
        updates = {}
        if canal_log: updates["log_channel"] = canal_log.id
        if limite_avisos: updates["warn_threshold"] = max(1, limite_avisos)
        if acao_avisos: updates["warn_action"] = acao_avisos
        if updates:
            await self.save_config(interaction.guild_id, **updates)
        config = await self.get_config(interaction.guild_id)
        embed = discord.Embed(title="⚙️ Moderação Configurada!", color=discord.Color.green())
        embed.add_field(name="Canal de Logs", value=f"<#{config['log_channel']}>" if config.get("log_channel") else "Não definido", inline=True)
        embed.add_field(name="Limite de Avisos", value=str(config.get("warn_threshold", 3)), inline=True)
        embed.add_field(name="Ação Automática", value=(config.get("warn_action") or "mute").capitalize(), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="palavra-proibida", description="🚫 Adiciona/remove palavra proibida")
    @app_commands.choices(acao=[
        app_commands.Choice(name="Adicionar", value="add"),
        app_commands.Choice(name="Remover", value="remove"),
    ])
    @app_commands.default_permissions(administrator=True)
    async def banned_word(self, interaction: discord.Interaction, palavra: str, acao: str):
        await self.get_config(interaction.guild_id)
        row = await db.pool.fetchrow("SELECT banned_words FROM mod_config WHERE guild_id = $1", interaction.guild_id)
        words = list(row["banned_words"] or [])
        if acao == "add":
            if palavra.lower() not in words:
                words.append(palavra.lower())
                msg = f"✅ `{palavra}` adicionada!"
            else:
                msg = "⚠️ Já está na lista!"
        else:
            if palavra.lower() in words:
                words.remove(palavra.lower())
                msg = f"✅ `{palavra}` removida!"
            else:
                msg = "⚠️ Não está na lista!"
        await db.pool.execute("UPDATE mod_config SET banned_words = $1 WHERE guild_id = $2", words, interaction.guild_id)
        await interaction.response.send_message(msg, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Moderation(bot))
