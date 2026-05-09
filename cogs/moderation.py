import asyncio
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from groq import Groq

from utils.database import db

logger = logging.getLogger("Moderation")

SPAM_THRESHOLD = int(os.getenv("MOD_SPAM_THRESHOLD", "8"))
SPAM_WINDOW = int(os.getenv("MOD_SPAM_WINDOW", "6"))
CAPS_THRESHOLD = float(os.getenv("MOD_CAPS_THRESHOLD", "0.70"))
MENTION_LIMIT = int(os.getenv("MOD_MENTION_LIMIT", "5"))
LINK_WHITELIST = {"discord.com", "discord.gg"}
MOD_AI_MODEL = os.getenv("MOD_AI_MODEL", "openai/gpt-oss-120b")


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        key = os.getenv("GROQ_API_KEY")
        self.client = Groq(api_key=key) if key else None
        self.message_tracker: defaultdict[int, list[datetime]] = defaultdict(list)
        self.clean_trackers.start()

    def cog_unload(self) -> None:
        self.clean_trackers.cancel()

    async def get_config(self, guild_id: int) -> dict:
        row = await db.pool.fetchrow("SELECT * FROM mod_config WHERE guild_id = $1", guild_id)
        if row:
            return dict(row)
        await db.pool.execute("INSERT INTO mod_config (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id)
        row = await db.pool.fetchrow("SELECT * FROM mod_config WHERE guild_id = $1", guild_id)
        return dict(row)

    async def save_config(self, guild_id: int, **kwargs) -> None:
        if not kwargs:
            return
        sets = ", ".join(f"{key} = ${index + 2}" for index, key in enumerate(kwargs))
        await db.pool.execute(f"UPDATE mod_config SET {sets} WHERE guild_id = $1", guild_id, *kwargs.values())

    async def get_warn_count(self, guild_id: int, user_id: int) -> int:
        row = await db.pool.fetchrow(
            "SELECT COUNT(*) AS total FROM warnings WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )
        return int(row["total"]) if row else 0

    async def add_warning(self, guild_id: int, user_id: int, reason: str, moderator_id: Optional[int] = None) -> None:
        await db.pool.execute(
            "INSERT INTO warnings (guild_id, user_id, reason, moderator) VALUES ($1, $2, $3, $4)",
            guild_id,
            user_id,
            reason,
            moderator_id,
        )

    async def clear_warnings(self, guild_id: int, user_id: int) -> None:
        await db.pool.execute("DELETE FROM warnings WHERE guild_id = $1 AND user_id = $2", guild_id, user_id)

    async def log_action(self, guild: discord.Guild, embed: discord.Embed) -> None:
        config = await self.get_config(guild.id)
        channel_id = config.get("log_channel")
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if channel:
            try:
                await channel.send(embed=embed)
            except discord.Forbidden:
                pass

    async def safe_delete(self, message: discord.Message) -> bool:
        try:
            await message.delete()
            return True
        except (discord.Forbidden, discord.NotFound):
            return False

    def can_act_on(self, guild: discord.Guild, actor: discord.Member, target: discord.Member) -> tuple[bool, str]:
        if target == guild.owner:
            return False, "Não posso agir contra o dono do servidor."
        if actor != guild.owner and target.top_role >= actor.top_role:
            return False, "Seu cargo não é alto o suficiente para agir sobre esse membro."
        me = guild.me
        if me and target.top_role >= me.top_role:
            return False, "Meu cargo não é alto o suficiente para agir sobre esse membro."
        return True, ""

    async def warn_user(self, guild: discord.Guild, member: discord.Member, reason: str, moderator: Optional[discord.Member] = None) -> None:
        await self.add_warning(guild.id, member.id, reason, moderator.id if moderator else None)
        warn_count = await self.get_warn_count(guild.id, member.id)
        config = await self.get_config(guild.id)
        threshold = int(config.get("warn_threshold") or 3)

        embed = discord.Embed(title="Aviso registrado", color=discord.Color.yellow(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Usuário", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Avisos", value=f"{warn_count}/{threshold}", inline=True)
        embed.add_field(name="Motivo", value=reason, inline=False)
        if moderator:
            embed.add_field(name="Moderador", value=moderator.mention, inline=True)
        await self.log_action(guild, embed)

        try:
            await member.send(f"Você recebeu um aviso em **{guild.name}**.\nMotivo: {reason}\nTotal: {warn_count}/{threshold}")
        except discord.Forbidden:
            pass

        if warn_count >= threshold:
            action = config.get("warn_action") or "mute"
            if action == "mute":
                await self.mute_member(guild, member, 3600, "Limite de avisos atingido")
            elif action == "kick":
                await member.kick(reason="Limite de avisos atingido")
            elif action == "ban":
                await member.ban(reason="Limite de avisos atingido", delete_message_days=1)
            await self.clear_warnings(guild.id, member.id)

    async def mute_member(self, guild: discord.Guild, member: discord.Member, duration_seconds: int, reason: str) -> bool:
        try:
            until = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
            await member.timeout(until, reason=reason)
            embed = discord.Embed(title="Usuário silenciado", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
            embed.add_field(name="Usuário", value=member.mention, inline=True)
            embed.add_field(name="Duração", value=f"{duration_seconds // 60} minuto(s)", inline=True)
            embed.add_field(name="Motivo", value=reason, inline=False)
            await self.log_action(guild, embed)
            return True
        except discord.Forbidden:
            return False

    async def ai_analyze_message(self, content: str) -> dict:
        if not self.client:
            return {"violation": False, "severity": "none", "reason": "", "action": "none"}
        try:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=MOD_AI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Analise a mensagem para moderação de Discord. Retorne APENAS JSON válido no formato: "
                            "{\"violation\": true/false, \"severity\": \"low/medium/high\", "
                            "\"reason\": \"motivo curto\", \"action\": \"warn/mute/kick/ban/none\"}. "
                            "Marque violation=true só para ameaça explícita, assédio severo, hate speech grave, "
                            "conteúdo sexual explícito, spam malicioso ou incentivo real a dano. Palavrão leve e discussão comum não são violação."
                        ),
                    },
                    {"role": "user", "content": content[:1800]},
                ],
                max_tokens=120,
                temperature=0.1,
            )
            text = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as exc:
            logger.error("Erro na moderação por IA: %s", exc)
            return {"violation": False, "severity": "none", "reason": "", "action": "none"}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        if message.author.guild_permissions.administrator:
            return

        guild = message.guild
        me = guild.me
        if not me or not me.guild_permissions.manage_messages:
            return

        config = await self.get_config(guild.id)
        content = message.content or ""
        member = message.author

        if config.get("anti_spam"):
            now = datetime.now()
            timestamps = [t for t in self.message_tracker[member.id] if (now - t).total_seconds() < SPAM_WINDOW]
            timestamps.append(now)
            self.message_tracker[member.id] = timestamps
            if len(timestamps) >= SPAM_THRESHOLD:
                await self.safe_delete(message)
                await self.warn_user(guild, member, "Spam detectado")
                self.message_tracker[member.id] = []
                return

        if config.get("anti_caps") and len(content) > 12:
            letters = [char for char in content if char.isalpha()]
            if letters:
                caps_ratio = sum(1 for char in letters if char.isupper()) / len(letters)
                if caps_ratio > CAPS_THRESHOLD:
                    await self.safe_delete(message)
                    await self.warn_user(guild, member, "Excesso de letras maiúsculas")
                    return

        banned_words = config.get("banned_words") or []
        lowered = content.lower()
        for word in banned_words:
            if word and word.lower() in lowered:
                await self.safe_delete(message)
                await self.warn_user(guild, member, f"Palavra proibida: {word}")
                return

        if config.get("anti_mention") and len(message.mentions) > MENTION_LIMIT:
            await self.safe_delete(message)
            await self.warn_user(guild, member, f"Excesso de menções ({len(message.mentions)})")
            return

        if config.get("anti_links"):
            urls = re.findall(r"https?://[^\s]+|discord\.gg/[^\s]+", content, flags=re.IGNORECASE)
            for url in urls:
                if not any(domain in url.lower() for domain in LINK_WHITELIST):
                    await self.safe_delete(message)
                    await self.warn_user(guild, member, "Link não permitido")
                    return

        if config.get("ai_moderation") and len(content) > 20:
            result = await self.ai_analyze_message(content)
            if result.get("violation"):
                reason = result.get("reason") or "Violação detectada"
                action = result.get("action") or "warn"
                severity = result.get("severity") or "low"
                if severity in {"medium", "high"}:
                    await self.safe_delete(message)
                if action == "mute":
                    await self.warn_user(guild, member, f"[IA] {reason}")
                    await self.mute_member(guild, member, 1800, f"[IA] {reason}")
                elif action == "kick":
                    await member.kick(reason=f"[IA] {reason}")
                elif action == "ban" and severity == "high":
                    await member.ban(reason=f"[IA] {reason}", delete_message_days=1)
                else:
                    await self.warn_user(guild, member, f"[IA] {reason}")

    @tasks.loop(minutes=5)
    async def clean_trackers(self) -> None:
        now = datetime.now()
        for user_id in list(self.message_tracker):
            self.message_tracker[user_id] = [t for t in self.message_tracker[user_id] if (now - t).total_seconds() < SPAM_WINDOW]
            if not self.message_tracker[user_id]:
                del self.message_tracker[user_id]

    @app_commands.command(name="ban", description="Bane um usuário do servidor.")
    @app_commands.describe(membro="Usuário a ser banido", motivo="Motivo do ban", deletar_msgs="Dias de mensagens para deletar, de 0 a 7")
    @app_commands.default_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, membro: discord.Member, motivo: str = "Sem motivo informado", deletar_msgs: int = 1) -> None:
        ok, msg = self.can_act_on(interaction.guild, interaction.user, membro)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await membro.ban(reason=f"{motivo} | Por: {interaction.user}", delete_message_days=max(0, min(deletar_msgs, 7)))
        embed = discord.Embed(title="Usuário banido", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Usuário", value=f"{membro} (`{membro.id}`)", inline=True)
        embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        await interaction.response.send_message(embed=embed)
        await self.log_action(interaction.guild, embed)

    @app_commands.command(name="kick", description="Expulsa um usuário do servidor.")
    @app_commands.describe(membro="Usuário a ser expulso", motivo="Motivo da expulsão")
    @app_commands.default_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, membro: discord.Member, motivo: str = "Sem motivo informado") -> None:
        ok, msg = self.can_act_on(interaction.guild, interaction.user, membro)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await membro.kick(reason=f"{motivo} | Por: {interaction.user}")
        embed = discord.Embed(title="Usuário expulso", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Usuário", value=f"{membro} (`{membro.id}`)", inline=True)
        embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        await interaction.response.send_message(embed=embed)
        await self.log_action(interaction.guild, embed)

    @app_commands.command(name="mute", description="Silencia um usuário temporariamente.")
    @app_commands.describe(membro="Usuário a silenciar", duracao="Duração em minutos", motivo="Motivo do mute")
    @app_commands.default_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, membro: discord.Member, duracao: int = 10, motivo: str = "Sem motivo informado") -> None:
        ok, msg = self.can_act_on(interaction.guild, interaction.user, membro)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        success = await self.mute_member(interaction.guild, membro, max(1, duracao) * 60, motivo)
        await interaction.response.send_message(f"{membro.mention} foi silenciado por {duracao} minuto(s)." if success else "Não consegui silenciar esse usuário.")

    @app_commands.command(name="unmute", description="Remove o silenciamento de um usuário.")
    @app_commands.default_permissions(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, membro: discord.Member) -> None:
        await membro.timeout(None, reason=f"Unmute por {interaction.user}")
        await interaction.response.send_message(f"{membro.mention} foi desmutado.")

    @app_commands.command(name="warn", description="Registra um aviso para um usuário.")
    @app_commands.describe(membro="Usuário a ser avisado", motivo="Motivo do aviso")
    @app_commands.default_permissions(manage_messages=True)
    async def warn(self, interaction: discord.Interaction, membro: discord.Member, motivo: str = "Sem motivo informado") -> None:
        await self.warn_user(interaction.guild, membro, motivo, interaction.user)
        await interaction.response.send_message(f"Aviso registrado para {membro.mention}.", ephemeral=True)

    @app_commands.command(name="avisos", description="Mostra os avisos de um usuário.")
    @app_commands.describe(membro="Usuário consultado")
    @app_commands.default_permissions(manage_messages=True)
    async def avisos(self, interaction: discord.Interaction, membro: discord.Member) -> None:
        rows = await db.pool.fetch(
            "SELECT reason, moderator, created_at FROM warnings WHERE guild_id = $1 AND user_id = $2 ORDER BY created_at DESC LIMIT 10",
            interaction.guild.id,
            membro.id,
        )
        if not rows:
            await interaction.response.send_message(f"{membro.mention} não possui avisos.", ephemeral=True)
            return
        lines = []
        for index, row in enumerate(rows, start=1):
            ts = int(row["created_at"].timestamp())
            mod = f"<@{row['moderator']}>" if row["moderator"] else "Sistema"
            lines.append(f"**{index}.** {row['reason']} | {mod} | <t:{ts}:R>")
        embed = discord.Embed(title=f"Avisos de {membro.display_name}", description="\n".join(lines), color=discord.Color.yellow())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="limpar-avisos", description="Remove todos os avisos de um usuário.")
    @app_commands.default_permissions(manage_messages=True)
    async def limpar_avisos(self, interaction: discord.Interaction, membro: discord.Member) -> None:
        await self.clear_warnings(interaction.guild.id, membro.id)
        await interaction.response.send_message(f"Avisos de {membro.mention} removidos.", ephemeral=True)

    @app_commands.command(name="purge", description="Apaga mensagens em massa.")
    @app_commands.describe(quantidade="Quantidade de mensagens, de 1 a 100")
    @app_commands.default_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, quantidade: int) -> None:
        quantidade = max(1, min(quantidade, 100))
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=quantidade)
        await interaction.followup.send(f"{len(deleted)} mensagem(ns) apagada(s).", ephemeral=True)

    @app_commands.command(name="palavra-proibida", description="Adiciona ou remove uma palavra proibida.")
    @app_commands.describe(acao="add ou remove", palavra="Palavra ou termo")
    @app_commands.default_permissions(manage_messages=True)
    async def palavra_proibida(self, interaction: discord.Interaction, acao: str, palavra: str) -> None:
        config = await self.get_config(interaction.guild.id)
        words = list(config.get("banned_words") or [])
        normalized = palavra.strip().lower()
        if acao.lower() in {"add", "adicionar"}:
            if normalized not in [w.lower() for w in words]:
                words.append(palavra.strip())
            msg = f"Palavra adicionada: `{palavra}`"
        elif acao.lower() in {"remove", "remover"}:
            words = [w for w in words if w.lower() != normalized]
            msg = f"Palavra removida: `{palavra}`"
        else:
            await interaction.response.send_message("Use `add` ou `remove`.", ephemeral=True)
            return
        await self.save_config(interaction.guild.id, banned_words=words)
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="config-mod", description="Configura a moderação automática.")
    @app_commands.describe(
        log_channel="Canal de logs",
        anti_spam="Ativar anti-spam",
        anti_caps="Ativar anti-caps",
        anti_links="Bloquear links externos",
        anti_mention="Bloquear excesso de menções",
        ai_moderation="Ativar moderação por IA",
        warn_threshold="Avisos até punição",
        warn_action="Ação ao atingir limite: mute, kick ou ban",
    )
    @app_commands.default_permissions(administrator=True)
    async def config_mod(
        self,
        interaction: discord.Interaction,
        log_channel: Optional[discord.TextChannel] = None,
        anti_spam: Optional[bool] = None,
        anti_caps: Optional[bool] = None,
        anti_links: Optional[bool] = None,
        anti_mention: Optional[bool] = None,
        ai_moderation: Optional[bool] = None,
        warn_threshold: Optional[int] = None,
        warn_action: Optional[str] = None,
    ) -> None:
        updates = {}
        if log_channel is not None:
            updates["log_channel"] = log_channel.id
        for key, value in {
            "anti_spam": anti_spam,
            "anti_caps": anti_caps,
            "anti_links": anti_links,
            "anti_mention": anti_mention,
            "ai_moderation": ai_moderation,
        }.items():
            if value is not None:
                updates[key] = value
        if warn_threshold is not None:
            updates["warn_threshold"] = max(1, min(warn_threshold, 20))
        if warn_action is not None:
            if warn_action.lower() not in {"mute", "kick", "ban"}:
                await interaction.response.send_message("warn_action precisa ser `mute`, `kick` ou `ban`.", ephemeral=True)
                return
            updates["warn_action"] = warn_action.lower()

        if updates:
            await self.save_config(interaction.guild.id, **updates)
        config = await self.get_config(interaction.guild.id)
        embed = discord.Embed(title="Configuração de moderação", color=discord.Color.blurple())
        embed.add_field(name="Logs", value=f"<#{config['log_channel']}>" if config.get("log_channel") else "Não definido", inline=False)
        embed.add_field(name="Anti-spam", value=str(config.get("anti_spam")), inline=True)
        embed.add_field(name="Anti-caps", value=str(config.get("anti_caps")), inline=True)
        embed.add_field(name="Anti-links", value=str(config.get("anti_links")), inline=True)
        embed.add_field(name="Anti-menções", value=str(config.get("anti_mention")), inline=True)
        embed.add_field(name="IA moderação", value=str(config.get("ai_moderation")), inline=True)
        embed.add_field(name="Avisos", value=f"{config.get('warn_threshold')} → {config.get('warn_action')}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
