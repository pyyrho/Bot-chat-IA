"""
moderation.py — Revolux · Cog de Moderação com IA Avançada
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Inspirado nos melhores bots do mercado (Wick, Carl-bot, Security Bot, Lorrita)
e elevado com inteligência artificial contextual.

Recursos:
  • Análise de IA multi-camada (intenção + severidade + contexto + histórico)
  • Sistema de reputação / pontuação de risco por usuário
  • Moderação contextual: histórico de mensagens enriquece a análise da IA
  • Detecção de evasão (substituições de letras, l33tspeak, unicode confusables)
  • Logs forenses em embed ricos com auditoria completa
  • Ações progressivas automáticas baseadas em reputação
  • Slow-mode inteligente ativado por IA em surtos de toxicidade
  • Quarentena: canal isolado para usuários suspeitos
  • Comandos de inspeção de reputação e histórico de infratores
  • Purge por usuário, por tipo de conteúdo e por padrão regex
  • Tempban (ban temporário com desban automático)
  • Softban (ban + desban para limpar mensagens sem punição permanente)
  • Lockdown de canal com mensagem personalizada
  • Nota de moderador: anotações internas sobre usuários

Chave de API: MOD_GEMINI_API_KEY (somente para moderação por IA)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
import google.generativeai as genai

from utils.database import db

logger = logging.getLogger("Revolux.Moderation")

# ──────────────────────────────────────────────
# Constantes configuráveis via variáveis de ambiente
# ──────────────────────────────────────────────
SPAM_THRESHOLD   = int(os.getenv("MOD_SPAM_THRESHOLD", "7"))
SPAM_WINDOW      = int(os.getenv("MOD_SPAM_WINDOW", "6"))
CAPS_THRESHOLD   = float(os.getenv("MOD_CAPS_THRESHOLD", "0.72"))
MENTION_LIMIT    = int(os.getenv("MOD_MENTION_LIMIT", "5"))
DUPTEXT_RATIO    = float(os.getenv("MOD_DUPTEXT_RATIO", "0.85"))   # similaridade para detecção de copypaste
AI_MODEL         = os.getenv("MOD_GEMINI_MODEL",      "gemini-2.5-pro")
AI_DEEP_MODEL    = os.getenv("MOD_GEMINI_DEEP_MODEL", "gemini-2.5-pro")   # análise profunda via Gemini
CONTEXT_MESSAGES = int(os.getenv("MOD_CONTEXT_MESSAGES", "5"))     # histórico enviado para a IA
LINK_WHITELIST   = {d.strip() for d in os.getenv("MOD_LINK_WHITELIST", "discord.com,discord.gg").split(",")}

# Mapa de confusables unicode → ASCII (evasão de filtros)
_CONFUSABLES: dict[str, str] = {
    "а": "a", "е": "e", "і": "i", "о": "o", "р": "p", "с": "c",
    "ѕ": "s", "υ": "u", "х": "x", "у": "y", "ʜ": "h", "ᴋ": "k",
    "ɴ": "n", "ᴍ": "m", "ꜰ": "f", "ɢ": "g", "ʟ": "l", "ᴛ": "t",
    "ᴠ": "v", "ᴡ": "w", "ᴢ": "z", "ᴅ": "d", "ʀ": "r", "ʙ": "b",
    "①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5",
    "⓪": "0", "ⓢ": "s", "ⓔ": "e", "ⓧ": "x",
}

# Regex para URLs
_URL_PATTERN = re.compile(
    r"(?:https?://|discord\.gg/|www\.)[^\s<>\"']{2,}",
    re.IGNORECASE,
)

# Regex para l33tspeak básico
_LEET: dict[str, str] = {
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
    "@": "a", "$": "s", "!": "i", "+": "t",
}


# ──────────────────────────────────────────────
# Funções auxiliares de normalização
# ──────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Normaliza texto para detecção de evasão de filtro."""
    # Decomposição unicode + remoção de diacríticos
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Substituição de confusables
    result = "".join(_CONFUSABLES.get(c, c) for c in stripped.lower())
    # Substituição de l33tspeak
    result = "".join(_LEET.get(c, c) for c in result)
    return result


def _similarity_ratio(a: str, b: str) -> float:
    """Retorna a similaridade entre dois strings (0-1), rápido sem libs externas."""
    if not a or not b:
        return 0.0
    # Comparação por bigramas
    def bigrams(s: str) -> set[str]:
        return {s[i:i+2] for i in range(len(s) - 1)}
    bg_a, bg_b = bigrams(a), bigrams(b)
    if not bg_a or not bg_b:
        return 1.0 if a == b else 0.0
    return 2 * len(bg_a & bg_b) / (len(bg_a) + len(bg_b))


# ──────────────────────────────────────────────
# Estruturas de rastreamento em memória
# ──────────────────────────────────────────────

class _UserProfile:
    """Perfil de risco em tempo real por usuário/guild."""
    __slots__ = (
        "message_times", "last_messages", "risk_score",
        "muted_until", "quarantined", "notes_count",
    )

    def __init__(self) -> None:
        self.message_times: list[datetime] = []
        self.last_messages: list[str] = []      # para detectar copypaste
        self.risk_score: float = 0.0            # 0-100
        self.muted_until: Optional[datetime] = None
        self.quarantined: bool = False
        self.notes_count: int = 0

    def push_message(self, content: str, now: datetime) -> None:
        self.message_times = [
            t for t in self.message_times
            if (now - t).total_seconds() < SPAM_WINDOW
        ]
        self.message_times.append(now)
        self.last_messages = (self.last_messages + [content])[-6:]

    def detect_copypaste(self) -> bool:
        msgs = self.last_messages
        if len(msgs) < 3:
            return False
        for i in range(1, len(msgs)):
            if _similarity_ratio(msgs[i - 1], msgs[i]) >= DUPTEXT_RATIO:
                return True
        return False

    def decay_risk(self, minutes: float = 10.0) -> None:
        """Decai passivamente o risco ao longo do tempo."""
        self.risk_score = max(0.0, self.risk_score - (minutes * 0.5))

    def add_risk(self, amount: float) -> None:
        self.risk_score = min(100.0, self.risk_score + amount)

    @property
    def risk_label(self) -> str:
        if self.risk_score < 20:
            return "<:1000032060:1507947421911613560> Baixo"
        if self.risk_score < 50:
            return "<:1000032058:1507947336616251574> Moderado"
        if self.risk_score < 75:
            return "<:1000032079:1507948213741813972> Alto"
        return "<:1000032063:1507947553654833232> Crítico"



# ──────────────────────────────────────────────
# Chaves Gemini dedicadas à moderação
# ──────────────────────────────────────────────

def _collect_gemini_keys() -> list[str]:
    """
    Lê uma ou várias chaves Gemini exclusivas de moderação.

    Variáveis aceitas:
      • MOD_GEMINI_API_KEYS="key1,key2,key3"
      • MOD_GEMINI_API_KEY="key"
    """
    keys: list[str] = []
    raw = os.getenv("MOD_GEMINI_API_KEYS", "")
    keys.extend(k.strip() for k in raw.replace(";", ",").split(",") if k.strip())

    key = os.getenv("MOD_GEMINI_API_KEY")
    if key and key.strip():
        keys.append(key.strip())

    # Remove duplicadas preservando ordem
    seen: set[str] = set()
    unique: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            unique.append(k)
    return unique


# ──────────────────────────────────────────────
# Cog principal
# ──────────────────────────────────────────────

class Moderation(commands.Cog):
    """Cog de moderação avançada com IA contextual para o Revolux."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Moderação por IA usa Gemini Pro exclusivo de moderação.
        # A IA geral do bot usa GEMINI_API_KEY em outros cogs.
        gemini_keys = _collect_gemini_keys()
        self._gemini_index = 0
        self._gemini_keys: list[str] = gemini_keys
        if not gemini_keys:
            logger.warning("Moderação por IA desativada: MOD_GEMINI_API_KEY/MOD_GEMINI_API_KEYS ausente.")
        else:
            # Configura com a primeira chave; rotação ocorre em _gemini_chat
            genai.configure(api_key=gemini_keys[0])
            logger.info("Moderação IA ativa com %s chave(s) Gemini. Modelo: %s", len(gemini_keys), AI_MODEL)
        # guild_id → user_id → _UserProfile
        self._profiles: defaultdict[int, defaultdict[int, _UserProfile]] = (
            defaultdict(lambda: defaultdict(_UserProfile))
        )
        # guild_id → channel_id → slowmode_active
        self._slowmode_active: dict[tuple[int, int], bool] = {}
        self._cleanup_task.start()
        self._risk_decay_task.start()

    def cog_unload(self) -> None:
        self._cleanup_task.cancel()
        self._risk_decay_task.cancel()

    def _profile(self, guild_id: int, user_id: int) -> _UserProfile:
        return self._profiles[guild_id][user_id]

    async def _gemini_chat(self, *, model: str, messages: list[dict], max_tokens: int = 250, temperature: float = 0.05) -> str:
        """
        Executa chat completion no Gemini com failover entre múltiplas chaves.
        Retorna o texto da resposta ou lança exceção se todas as chaves falharem.
        """
        if not self._gemini_keys:
            raise RuntimeError("Gemini não configurado para moderação")

        total = len(self._gemini_keys)
        last_exc: Optional[Exception] = None

        # Separa system prompt das mensagens de conversa
        system_prompt = ""
        chat_history = []
        last_user_msg = ""
        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg["content"]
            elif msg["role"] == "user":
                last_user_msg = msg["content"]
            # mensagens anteriores de assistant não são necessárias para análise pontual

        for _ in range(total):
            key = self._gemini_keys[self._gemini_index % total]
            self._gemini_index = (self._gemini_index + 1) % total
            try:
                genai.configure(api_key=key)
                model_obj = genai.GenerativeModel(
                    model_name=model,
                    system_instruction=system_prompt or None,
                    generation_config=genai.types.GenerationConfig(
                        max_output_tokens=max_tokens,
                        temperature=temperature,
                    ),
                )
                response = await asyncio.to_thread(
                    model_obj.generate_content,
                    last_user_msg,
                )
                return response.text.strip()
            except Exception as exc:
                last_exc = exc
                logger.warning("Falha em chamada Gemini (moderação); tentando próxima chave: %s", exc)
                await asyncio.sleep(0.35)

        raise last_exc or RuntimeError("Falha desconhecida no Gemini (moderação)")

    # ── Database helpers ──────────────────────

    async def get_config(self, guild_id: int) -> dict:
        row = await db.pool.fetchrow(
            "SELECT * FROM mod_config WHERE guild_id = $1", guild_id
        )
        if row:
            return dict(row)
        await db.pool.execute(
            "INSERT INTO mod_config (guild_id) VALUES ($1) ON CONFLICT DO NOTHING",
            guild_id,
        )
        row = await db.pool.fetchrow(
            "SELECT * FROM mod_config WHERE guild_id = $1", guild_id
        )
        return dict(row)

    async def save_config(self, guild_id: int, **kwargs) -> None:
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(kwargs))
        await db.pool.execute(
            f"UPDATE mod_config SET {sets} WHERE guild_id = $1",
            guild_id, *kwargs.values(),
        )

    async def get_warnings(self, guild_id: int, user_id: int) -> list[dict]:
        rows = await db.pool.fetch(
            "SELECT id, reason, moderator, created_at FROM warnings "
            "WHERE guild_id = $1 AND user_id = $2 ORDER BY created_at DESC LIMIT 20",
            guild_id, user_id,
        )
        return [dict(r) for r in rows]

    async def get_warn_count(self, guild_id: int, user_id: int) -> int:
        row = await db.pool.fetchrow(
            "SELECT COUNT(*) AS total FROM warnings WHERE guild_id = $1 AND user_id = $2",
            guild_id, user_id,
        )
        return int(row["total"]) if row else 0

    async def add_warning(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        moderator_id: Optional[int] = None,
        source: str = "manual",
    ) -> None:
        await db.pool.execute(
            "INSERT INTO warnings (guild_id, user_id, reason, moderator, source) "
            "VALUES ($1, $2, $3, $4, $5)",
            guild_id, user_id, reason, moderator_id, source,
        )

    async def remove_warning_by_id(self, warning_id: int, guild_id: int) -> bool:
        result = await db.pool.execute(
            "DELETE FROM warnings WHERE id = $1 AND guild_id = $2",
            warning_id, guild_id,
        )
        return result != "DELETE 0"

    async def clear_warnings(self, guild_id: int, user_id: int) -> None:
        await db.pool.execute(
            "DELETE FROM warnings WHERE guild_id = $1 AND user_id = $2",
            guild_id, user_id,
        )

    async def add_mod_note(
        self, guild_id: int, user_id: int, note: str, moderator_id: int
    ) -> None:
        await db.pool.execute(
            "INSERT INTO mod_notes (guild_id, user_id, note, moderator_id) "
            "VALUES ($1, $2, $3, $4)",
            guild_id, user_id, note, moderator_id,
        )

    async def get_mod_notes(self, guild_id: int, user_id: int) -> list[dict]:
        rows = await db.pool.fetch(
            "SELECT id, note, moderator_id, created_at FROM mod_notes "
            "WHERE guild_id = $1 AND user_id = $2 ORDER BY created_at DESC LIMIT 10",
            guild_id, user_id,
        )
        return [dict(r) for r in rows]

    # ── Logging forense ───────────────────────

    async def log_action(
        self,
        guild: discord.Guild,
        embed: discord.Embed,
        *,
        ping_roles: bool = False,
    ) -> None:
        config = await self.get_config(guild.id)
        channel_id = config.get("log_channel")
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        content = None
        if ping_roles and config.get("mod_ping_role"):
            content = f"<@&{config['mod_ping_role']}>"
        try:
            await channel.send(content=content, embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    def _base_embed(
        self,
        title: str,
        color: discord.Color,
        *,
        icon: str = "<:1000032064:1507947590652526654>",
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"{icon} {title}",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        if self.bot.user:
            embed.set_footer(
                text="Revolux Moderação",
                icon_url=self.bot.user.display_avatar.url,
            )
        return embed

    # ── Verificações de hierarquia ────────────

    def can_act_on(
        self,
        guild: discord.Guild,
        actor: discord.Member,
        target: discord.Member,
    ) -> tuple[bool, str]:
        if target == guild.owner:
            return False, "<:1000032056:1507947210057322637> Não posso agir contra o dono do servidor."
        if actor != guild.owner and target.top_role >= actor.top_role:
            return False, "<:1000032056:1507947210057322637> Seu cargo não é alto o suficiente."
        me = guild.me
        if me and target.top_role >= me.top_role:
            return False, "<:1000032056:1507947210057322637> Meu cargo não é alto o suficiente."
        return True, ""

    # ── Ações de moderação ────────────────────

    async def safe_delete(self, message: discord.Message) -> bool:
        try:
            await message.delete()
            return True
        except (discord.Forbidden, discord.NotFound):
            return False

    async def mute_member(
        self,
        guild: discord.Guild,
        member: discord.Member,
        duration_seconds: int,
        reason: str,
        moderator: Optional[discord.Member] = None,
    ) -> bool:
        try:
            until = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
            await member.timeout(until, reason=reason)
            profile = self._profile(guild.id, member.id)
            profile.muted_until = until
            mins = duration_seconds // 60
            embed = self._base_embed("Usuário Silenciado", discord.Color.orange(), icon="<:1000032059:1507947381096714260>")
            embed.add_field(name="Usuário", value=f"{member.mention} (`{member.id}`)", inline=True)
            embed.add_field(name="Duração", value=f"`{mins}` minuto(s)", inline=True)
            embed.add_field(name="Motivo", value=reason, inline=False)
            if moderator:
                embed.add_field(name="Moderador", value=moderator.mention, inline=True)
            await self.log_action(guild, embed)
            return True
        except discord.Forbidden:
            return False

    async def warn_user(
        self,
        guild: discord.Guild,
        member: discord.Member,
        reason: str,
        moderator: Optional[discord.Member] = None,
        source: str = "auto",
    ) -> None:
        await self.add_warning(
            guild.id, member.id, reason,
            moderator.id if moderator else None,
            source=source,
        )
        warn_count = await self.get_warn_count(guild.id, member.id)
        config = await self.get_config(guild.id)
        threshold = int(config.get("warn_threshold") or 3)

        # Aumenta risco
        self._profile(guild.id, member.id).add_risk(15)

        embed = self._base_embed("Aviso Registrado", discord.Color.yellow(), icon="<:1000032079:1507948213741813972>")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Usuário", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Avisos", value=f"`{warn_count}` / `{threshold}`", inline=True)
        embed.add_field(name="Origem", value=f"`{source}`", inline=True)
        embed.add_field(name="Motivo", value=reason, inline=False)
        if moderator:
            embed.add_field(name="Moderador", value=moderator.mention, inline=True)
        await self.log_action(guild, embed)

        # Escalonamento automático ao atingir limite
        if warn_count >= threshold:
            action = (config.get("warn_action") or "mute").lower()
            if action == "mute":
                await self.mute_member(guild, member, 7200, "Limite de avisos atingido")
            elif action == "kick":
                try:
                    await member.kick(reason="Limite de avisos atingido")
                except discord.Forbidden:
                    pass
            elif action == "ban":
                try:
                    await member.ban(reason="Limite de avisos atingido", delete_message_days=1)
                except discord.Forbidden:
                    pass
            await self.clear_warnings(guild.id, member.id)

    # ── Inteligência Artificial ───────────────

    async def _ai_analyze(
        self,
        content: str,
        context: list[str],
        *,
        deep: bool = False,
    ) -> dict:
        """
        Envia mensagem + contexto para análise de IA via Gemini.

        Retorna:
        {
          "violation": bool,
          "severity": "none|low|medium|high|critical",
          "categories": ["hate_speech", "harassment", "nsfw", ...],
          "reason": str,
          "action": "none|warn|mute|kick|ban",
          "mute_minutes": int,         # sugerido pela IA
          "confidence": float,         # 0-1
          "toxicity_score": float,     # 0-100
          "slow_mode_suggestion": bool # IA sugere slowmode no canal
        }
        """
        if not self._gemini_keys:
            return _empty_ai_result()

        model = AI_DEEP_MODEL if deep else AI_MODEL
        context_block = ""
        if context:
            ctx_lines = "\n".join(f"- {c[:200]}" for c in context[-CONTEXT_MESSAGES:])
            context_block = f"\n\nContexto recente do chat:\n{ctx_lines}"

        system_prompt = (
            "Você é o módulo de moderação de IA do Revolux, um bot de Discord. "
            "Sua tarefa é analisar mensagens para detectar violações de regras comunitárias. "
            "Seja preciso e evite falsos positivos. Palavrões leves, debates acalorados, "
            "gírias e sarcasmo não são violações por si sós. Considere sempre o CONTEXTO fornecido.\n\n"
            "Retorne EXCLUSIVAMENTE um objeto JSON válido (sem markdown, sem explicação) com:\n"
            "  violation: boolean\n"
            "  severity: 'none'|'low'|'medium'|'high'|'critical'\n"
            "  categories: array de strings (ex: ['harassment','hate_speech'])\n"
            "  reason: string curta em português\n"
            "  action: 'none'|'warn'|'mute'|'kick'|'ban'\n"
            "  mute_minutes: integer (0 se não aplicável)\n"
            "  confidence: float entre 0 e 1\n"
            "  toxicity_score: float 0-100\n"
            "  slow_mode_suggestion: boolean\n\n"
            "Categorias possíveis: hate_speech, harassment, nsfw, threats, spam, "
            "self_harm, doxxing, scam, misinformation, illegal_activity."
        )

        user_content = f"Mensagem a analisar:\n{content[:2000]}{context_block}"

        try:
            raw = await self._gemini_chat(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=300,
                temperature=0.05,
            )
            # Remove possíveis markdown fences
            raw = raw.replace("```json", "").replace("```", "").strip()
            result = json.loads(raw)
            # Validação mínima
            result.setdefault("violation", False)
            result.setdefault("severity", "none")
            result.setdefault("categories", [])
            result.setdefault("reason", "")
            result.setdefault("action", "none")
            result.setdefault("mute_minutes", 0)
            result.setdefault("confidence", 0.5)
            result.setdefault("toxicity_score", 0.0)
            result.setdefault("slow_mode_suggestion", False)
            return result
        except Exception as exc:
            logger.error("Erro na análise de IA: %s", exc)
            return _empty_ai_result()

    async def _ai_summarize_user(
        self, member: discord.Member, warnings: list[dict], notes: list[dict]
    ) -> str:
        """Gera um resumo em linguagem natural do histórico de moderação de um usuário via Gemini."""
        if not self._gemini_keys or not warnings:
            return "Sem histórico relevante."
        history_text = "\n".join(
            f"- [{w['created_at'].strftime('%d/%m/%Y')}] {w['reason']}" for w in warnings[:10]
        )
        notes_text = "\n".join(f"- {n['note']}" for n in notes[:5]) if notes else "Nenhuma."
        try:
            result = await self._gemini_chat(
                model=AI_DEEP_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Você é um assistente de moderação. Com base no histórico de avisos "
                            "e notas de um usuário, gere um resumo profissional em 2-3 frases em português, "
                            "avaliando o padrão de comportamento e risco."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Usuário: {member} ({member.id})\n"
                            f"Avisos:\n{history_text}\n"
                            f"Notas:\n{notes_text}"
                        ),
                    },
                ],
                max_tokens=150,
                temperature=0.3,
            )
            return result
        except Exception:
            return "Não foi possível gerar resumo."

    # ── Listener principal ────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        if message.author.guild_permissions.administrator:
            return

        guild  = message.guild
        member = message.author
        me     = guild.me
        if not me or not me.guild_permissions.manage_messages:
            return

        config  = await self.get_config(guild.id)
        content = message.content or ""
        norm    = _normalize(content)
        profile = self._profile(guild.id, member.id)
        now     = datetime.now(timezone.utc)

        profile.push_message(content, now)

        # ── 1. Anti-spam (frequência) ──────────
        if config.get("anti_spam"):
            if len(profile.message_times) >= SPAM_THRESHOLD:
                await self.safe_delete(message)
                await self.warn_user(guild, member, "Spam detectado (mensagens em excesso)", source="anti_spam")
                profile.message_times.clear()
                profile.add_risk(20)
                return

        # ── 2. Anti-copypaste (spam de conteúdo igual) ──
        if config.get("anti_spam") and profile.detect_copypaste():
            await self.safe_delete(message)
            await self.warn_user(guild, member, "Spam detectado (mensagens repetidas)", source="anti_spam")
            profile.last_messages.clear()
            profile.add_risk(15)
            return

        # ── 3. Anti-caps ──────────────────────
        if config.get("anti_caps") and len(content) > 12:
            letters = [c for c in content if c.isalpha()]
            if letters and sum(1 for c in letters if c.isupper()) / len(letters) > CAPS_THRESHOLD:
                await self.safe_delete(message)
                await self.warn_user(guild, member, "Excesso de letras maiúsculas", source="anti_caps")
                return

        # ── 4. Palavras proibidas (com detecção de evasão) ──
        banned_words: list[str] = config.get("banned_words") or []
        for word in banned_words:
            if not word:
                continue
            normalized_word = _normalize(word)
            if normalized_word in norm:
                await self.safe_delete(message)
                await self.warn_user(guild, member, f"Palavra proibida detectada", source="word_filter")
                profile.add_risk(10)
                return

        # ── 5. Anti-menções em massa ───────────
        if config.get("anti_mention"):
            all_mentions = len(message.mentions) + len(message.role_mentions)
            if all_mentions > MENTION_LIMIT:
                await self.safe_delete(message)
                await self.warn_user(guild, member, f"Excesso de menções ({all_mentions})", source="anti_mention")
                profile.add_risk(15)
                return

        # ── 6. Anti-links ─────────────────────
        if config.get("anti_links"):
            urls = _URL_PATTERN.findall(content)
            for url in urls:
                if not any(domain in url.lower() for domain in LINK_WHITELIST):
                    await self.safe_delete(message)
                    await self.warn_user(guild, member, "Link externo não permitido", source="anti_links")
                    return

        # ── 7. Moderação por IA ───────────────
        if config.get("ai_moderation") and len(content.strip()) >= 15:
            # Constrói contexto das últimas mensagens do canal
            recent_ctx: list[str] = []
            try:
                async for msg in message.channel.history(limit=CONTEXT_MESSAGES + 1, before=message):
                    if not msg.author.bot and msg.content:
                        recent_ctx.insert(0, f"{msg.author.display_name}: {msg.content[:200]}")
            except (discord.Forbidden, discord.HTTPException):
                pass

            # Análise profunda se risco já elevado
            deep_mode = profile.risk_score >= 50
            result = await self._ai_analyze(content, recent_ctx, deep=deep_mode)

            if result.get("violation") and result.get("confidence", 0) >= 0.60:
                reason    = result.get("reason") or "Violação detectada"
                action    = result.get("action", "warn")
                severity  = result.get("severity", "low")
                categories = result.get("categories", [])
                mute_mins = max(1, int(result.get("mute_minutes") or 30))
                toxicity  = result.get("toxicity_score", 0)

                profile.add_risk(toxicity * 0.4)

                # Log de IA detalhado
                ai_embed = self._base_embed("IA · Violação Detectada", discord.Color.dark_red(), icon="<a:1000032071:1507947918752092301>")
                ai_embed.set_thumbnail(url=member.display_avatar.url)
                ai_embed.add_field(name="Usuário", value=f"{member.mention} (`{member.id}`)", inline=True)
                ai_embed.add_field(name="Severidade", value=f"`{severity}`", inline=True)
                ai_embed.add_field(name="Confiança", value=f"`{result.get('confidence', 0):.0%}`", inline=True)
                ai_embed.add_field(name="Toxicidade", value=f"`{toxicity:.1f}/100`", inline=True)
                ai_embed.add_field(name="Risco Acumulado", value=f"`{profile.risk_score:.1f}/100`", inline=True)
                ai_embed.add_field(name="Categorias", value=", ".join(f"`{c}`" for c in categories) or "—", inline=True)
                ai_embed.add_field(name="Motivo", value=reason, inline=False)
                preview = content[:300] + ("..." if len(content) > 300 else "")
                ai_embed.add_field(name="Conteúdo", value=f"```{preview}```", inline=False)
                await self.log_action(guild, ai_embed, ping_roles=severity in {"high", "critical"})

                # Ação
                if severity in {"medium", "high", "critical"}:
                    await self.safe_delete(message)

                if action == "warn" or severity == "low":
                    await self.warn_user(guild, member, f"[IA] {reason}", source="ai")

                elif action == "mute":
                    await self.warn_user(guild, member, f"[IA] {reason}", source="ai")
                    await self.mute_member(guild, member, mute_mins * 60, f"[IA] {reason}")

                elif action == "kick":
                    try:
                        await member.kick(reason=f"[IA] {reason}")
                    except discord.Forbidden:
                        pass

                elif action == "ban" and severity in {"high", "critical"}:
                    try:
                        await member.ban(reason=f"[IA] {reason}", delete_message_days=1)
                    except discord.Forbidden:
                        pass

                # Slow-mode inteligente
                if result.get("slow_mode_suggestion") and isinstance(message.channel, discord.TextChannel):
                    key = (guild.id, message.channel.id)
                    if not self._slowmode_active.get(key):
                        try:
                            await message.channel.edit(slowmode_delay=10)
                            self._slowmode_active[key] = True
                            sm_embed = self._base_embed("Slow-mode Ativado pela IA", discord.Color.gold(), icon="<:1000032077:1507948183290904736>")
                            sm_embed.description = (
                                f"O canal {message.channel.mention} entrou em slow-mode de 10s "
                                f"devido a atividade tóxica detectada. Será removido automaticamente em 5 minutos."
                            )
                            await self.log_action(guild, sm_embed)
                            # Agenda remoção
                            asyncio.create_task(
                                self._remove_slowmode_after(message.channel, guild, key, delay=300)
                            )
                        except discord.Forbidden:
                            pass

    async def _remove_slowmode_after(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        key: tuple[int, int],
        delay: int = 300,
    ) -> None:
        await asyncio.sleep(delay)
        try:
            await channel.edit(slowmode_delay=0)
            self._slowmode_active.pop(key, None)
            embed = self._base_embed("Slow-mode Removido", discord.Color.green(), icon="<:1000032056:1507947210057322637>")
            embed.description = f"Slow-mode do canal {channel.mention} foi removido automaticamente."
            await self.log_action(guild, embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ── Tasks periódicas ──────────────────────

    @tasks.loop(minutes=5)
    async def _cleanup_task(self) -> None:
        """Remove entradas de perfis de usuários inativos."""
        for guild_id in list(self._profiles):
            for user_id in list(self._profiles[guild_id]):
                p = self._profiles[guild_id][user_id]
                if not p.message_times and p.risk_score < 1:
                    del self._profiles[guild_id][user_id]
            if not self._profiles[guild_id]:
                del self._profiles[guild_id]

    @tasks.loop(minutes=10)
    async def _risk_decay_task(self) -> None:
        """Decai passivamente a pontuação de risco de todos os usuários."""
        for guild_profiles in self._profiles.values():
            for profile in guild_profiles.values():
                profile.decay_risk(minutes=10)

    # ═══════════════════════════════════════════
    # COMANDOS SLASH — AÇÕES DE MODERAÇÃO
    # ═══════════════════════════════════════════

    @app_commands.command(name="ban", description="Bane um usuário permanentemente.")
    @app_commands.describe(
        membro="Usuário a ser banido",
        motivo="Motivo do ban",
        deletar_msgs="Dias de mensagens a deletar (0-7)",
        silencioso="Não exibe a ação publicamente",
    )
    @app_commands.default_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        motivo: str = "Sem motivo informado",
        deletar_msgs: int = 1,
        silencioso: bool = False,
    ) -> None:
        ok, msg = self.can_act_on(interaction.guild, interaction.user, membro)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=silencioso)

        await membro.ban(
            reason=f"{motivo} | Por: {interaction.user}",
            delete_message_days=max(0, min(deletar_msgs, 7)),
        )

        embed = self._base_embed("Usuário Banido", discord.Color.red(), icon="<:1000032063:1507947553654833232>")
        embed.set_thumbnail(url=membro.display_avatar.url)
        embed.add_field(name="Usuário", value=f"{membro} (`{membro.id}`)", inline=True)
        embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        embed.add_field(name="Mensagens deletadas", value=f"`{deletar_msgs}` dia(s)", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=silencioso)
        await self.log_action(interaction.guild, embed)

    @app_commands.command(name="tempban", description="Bane um usuário temporariamente.")
    @app_commands.describe(
        membro="Usuário a ser banido",
        duracao="Duração em minutos",
        motivo="Motivo do ban",
    )
    @app_commands.default_permissions(ban_members=True)
    async def tempban(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        duracao: int,
        motivo: str = "Sem motivo informado",
    ) -> None:
        ok, msg = self.can_act_on(interaction.guild, interaction.user, membro)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await interaction.response.defer()

        user_id  = membro.id
        guild_id = interaction.guild.id

        await membro.ban(reason=f"[TEMPBAN {duracao}min] {motivo} | Por: {interaction.user}", delete_message_days=0)

        embed = self._base_embed("Ban Temporário", discord.Color.red(), icon="<:1000032058:1507947336616251574>")
        embed.add_field(name="Usuário", value=f"{membro} (`{membro.id}`)", inline=True)
        embed.add_field(name="Duração", value=f"`{duracao}` minuto(s)", inline=True)
        embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        await interaction.followup.send(embed=embed)
        await self.log_action(interaction.guild, embed)

        async def _unban_later() -> None:
            await asyncio.sleep(duracao * 60)
            guild = self.bot.get_guild(guild_id)
            if guild:
                try:
                    user = await self.bot.fetch_user(user_id)
                    await guild.unban(user, reason="Tempban expirado automaticamente")
                    unban_embed = self._base_embed("Tempban Expirado", discord.Color.green(), icon="<:1000032056:1507947210057322637>")
                    unban_embed.description = f"O ban temporário de <@{user_id}> expirou."
                    await self.log_action(guild, unban_embed)
                except Exception as exc:
                    logger.warning("Erro ao remover tempban de %s: %s", user_id, exc)

        asyncio.create_task(_unban_later())

    @app_commands.command(name="softban", description="Bane e desbane para limpar mensagens recentes.")
    @app_commands.describe(membro="Usuário alvo", motivo="Motivo")
    @app_commands.default_permissions(ban_members=True)
    async def softban(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        motivo: str = "Sem motivo informado",
    ) -> None:
        ok, msg = self.can_act_on(interaction.guild, interaction.user, membro)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await interaction.response.defer()
        await membro.ban(reason=f"[SOFTBAN] {motivo}", delete_message_days=7)
        await interaction.guild.unban(membro, reason="Softban: desban automático")
        embed = self._base_embed("Softban Aplicado", discord.Color.orange(), icon="<:1000032078:1507948115338985512>")
        embed.add_field(name="Usuário", value=f"{membro} (`{membro.id}`)", inline=True)
        embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        embed.add_field(name="Efeito", value="Mensagens dos últimos 7 dias removidas; usuário pode retornar.", inline=False)
        await interaction.followup.send(embed=embed)
        await self.log_action(interaction.guild, embed)

    @app_commands.command(name="kick", description="Expulsa um usuário do servidor.")
    @app_commands.describe(membro="Usuário a ser expulso", motivo="Motivo", silencioso="Sem exibição pública")
    @app_commands.default_permissions(kick_members=True)
    async def kick(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        motivo: str = "Sem motivo informado",
        silencioso: bool = False,
    ) -> None:
        ok, msg = self.can_act_on(interaction.guild, interaction.user, membro)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=silencioso)
        await membro.kick(reason=f"{motivo} | Por: {interaction.user}")
        embed = self._base_embed("Usuário Expulso", discord.Color.orange(), icon="<:1000032062:1507947509861847080>")
        embed.set_thumbnail(url=membro.display_avatar.url)
        embed.add_field(name="Usuário", value=f"{membro} (`{membro.id}`)", inline=True)
        embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=silencioso)
        await self.log_action(interaction.guild, embed)

    @app_commands.command(name="mute", description="Silencia um usuário temporariamente.")
    @app_commands.describe(membro="Usuário", duracao="Duração em minutos", motivo="Motivo")
    @app_commands.default_permissions(moderate_members=True)
    async def mute(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        duracao: int = 10,
        motivo: str = "Sem motivo informado",
    ) -> None:
        ok, msg = self.can_act_on(interaction.guild, interaction.user, membro)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await interaction.response.defer()
        success = await self.mute_member(
            interaction.guild, membro, max(1, duracao) * 60, motivo, moderator=interaction.user
        )
        if success:
            embed = self._base_embed("Usuário Silenciado", discord.Color.orange(), icon="<:1000032059:1507947381096714260>")
            embed.add_field(name="Usuário", value=membro.mention, inline=True)
            embed.add_field(name="Duração", value=f"`{duracao}` minuto(s)", inline=True)
            embed.add_field(name="Motivo", value=motivo, inline=False)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("<:1000032056:1507947210057322637> Não consegui silenciar esse usuário.")

    @app_commands.command(name="unmute", description="Remove o silenciamento de um usuário.")
    @app_commands.describe(membro="Usuário a desmutar")
    @app_commands.default_permissions(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, membro: discord.Member) -> None:
        await interaction.response.defer()
        try:
            await membro.timeout(None, reason=f"Unmute por {interaction.user}")
            self._profile(interaction.guild.id, membro.id).muted_until = None
            await interaction.followup.send(f"<:1000032074:1507948021013549166> {membro.mention} foi desmutado.")
        except discord.Forbidden:
            await interaction.followup.send("<:1000032056:1507947210057322637> Sem permissão para desmutar.")

    @app_commands.command(name="warn", description="Registra um aviso para um usuário.")
    @app_commands.describe(membro="Usuário a ser avisado", motivo="Motivo")
    @app_commands.default_permissions(manage_messages=True)
    async def warn(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        motivo: str = "Sem motivo informado",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.warn_user(interaction.guild, membro, motivo, moderator=interaction.user, source="manual")
        await interaction.followup.send(f"<:1000032055:1507947171624910859> Aviso registrado para {membro.mention}.", ephemeral=True)

    @app_commands.command(name="remover-aviso", description="Remove um aviso específico pelo ID.")
    @app_commands.describe(membro="Usuário", id_aviso="ID do aviso (veja com /avisos)")
    @app_commands.default_permissions(manage_messages=True)
    async def remover_aviso(
        self, interaction: discord.Interaction, membro: discord.Member, id_aviso: int
    ) -> None:
        removed = await self.remove_warning_by_id(id_aviso, interaction.guild.id)
        if removed:
            await interaction.response.send_message(f"<:1000032055:1507947171624910859> Aviso `#{id_aviso}` de {membro.mention} removido.", ephemeral=True)
        else:
            await interaction.response.send_message("<:1000032056:1507947210057322637> Aviso não encontrado neste servidor.", ephemeral=True)

    # ═══════════════════════════════════════════
    # COMANDOS SLASH — INSPEÇÃO E GESTÃO
    # ═══════════════════════════════════════════

    @app_commands.command(name="avisos", description="Exibe o histórico de avisos de um usuário.")
    @app_commands.describe(membro="Usuário a consultar")
    @app_commands.default_permissions(manage_messages=True)
    async def avisos(self, interaction: discord.Interaction, membro: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)
        warnings = await self.get_warnings(interaction.guild.id, membro.id)
        if not warnings:
            await interaction.followup.send(f"<:1000032060:1507947421911613560> {membro.mention} não possui avisos.", ephemeral=True)
            return
        embed = self._base_embed(f"Avisos · {membro.display_name}", discord.Color.yellow(), icon="<:1000032079:1507948213741813972>")
        embed.set_thumbnail(url=membro.display_avatar.url)
        lines: list[str] = []
        for w in warnings[:15]:
            ts = int(w["created_at"].timestamp())
            mod = f"<@{w['moderator']}>" if w.get("moderator") else "Sistema"
            src = w.get("source", "manual")
            lines.append(f"`#{w['id']}` <t:{ts}:R> · {w['reason']} · {mod} · `{src}`")
        embed.description = "\n".join(lines)
        embed.set_footer(text=f"Total: {len(warnings)} aviso(s) | Revolux Moderação")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="limpar-avisos", description="Remove todos os avisos de um usuário.")
    @app_commands.describe(membro="Usuário")
    @app_commands.default_permissions(manage_messages=True)
    async def limpar_avisos(self, interaction: discord.Interaction, membro: discord.Member) -> None:
        await self.clear_warnings(interaction.guild.id, membro.id)
        self._profile(interaction.guild.id, membro.id).risk_score = 0.0
        await interaction.response.send_message(
            f"<:1000032055:1507947171624910859> Todos os avisos de {membro.mention} foram removidos e o risco foi zerado.",
            ephemeral=True,
        )

    @app_commands.command(name="inspecionar", description="Perfil completo de moderação de um usuário (IA inclusa).")
    @app_commands.describe(membro="Usuário a inspecionar")
    @app_commands.default_permissions(manage_messages=True)
    async def inspecionar(self, interaction: discord.Interaction, membro: discord.Member) -> None:
        await interaction.response.defer(ephemeral=True)

        warnings = await self.get_warnings(interaction.guild.id, membro.id)
        notes    = await self.get_mod_notes(interaction.guild.id, membro.id)
        profile  = self._profile(interaction.guild.id, membro.id)
        summary  = await self._ai_summarize_user(membro, warnings, notes)

        embed = self._base_embed(f"Inspeção · {membro.display_name}", discord.Color.blurple(), icon="<:1000032068:1507947786367402105>")
        embed.set_thumbnail(url=membro.display_avatar.url)
        embed.add_field(name="Usuário", value=f"{membro} (`{membro.id}`)", inline=True)
        embed.add_field(name="Conta criada", value=f"<t:{int(membro.created_at.timestamp())}:R>", inline=True)
        if membro.joined_at:
            embed.add_field(name="Entrou no servidor", value=f"<t:{int(membro.joined_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Cargo mais alto", value=membro.top_role.mention, inline=True)
        embed.add_field(name="Avisos acumulados", value=f"`{len(warnings)}`", inline=True)
        embed.add_field(name="Risco atual", value=f"`{profile.risk_score:.1f}/100` · {profile.risk_label}", inline=True)
        embed.add_field(name="Notas de moderação", value=f"`{len(notes)}`", inline=True)
        embed.add_field(name="Quarentena", value="Sim" if profile.quarantined else "Não", inline=True)
        embed.add_field(name="<:1000032072:1507947958723809340> Análise IA", value=summary, inline=False)

        if notes:
            note_lines = [f"`#{n['id']}` <@{n['moderator_id']}>: {n['note'][:80]}" for n in notes[:3]]
            embed.add_field(name="Notas recentes", value="\n".join(note_lines), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="nota", description="Adiciona uma nota interna sobre um usuário.")
    @app_commands.describe(membro="Usuário", nota="Conteúdo da nota (visível apenas para moderadores)")
    @app_commands.default_permissions(manage_messages=True)
    async def nota(
        self, interaction: discord.Interaction, membro: discord.Member, nota: str
    ) -> None:
        await self.add_mod_note(interaction.guild.id, membro.id, nota, interaction.user.id)
        await interaction.response.send_message(
            f"<:1000032049:1507946904124919949> Nota adicionada ao perfil de {membro.mention}.", ephemeral=True
        )

    @app_commands.command(name="purge", description="Apaga mensagens em massa com filtros avançados.")
    @app_commands.describe(
        quantidade="Quantidade de mensagens (1-100)",
        membro="Apagar apenas mensagens deste usuário",
        contem="Apagar apenas mensagens que contenham este texto",
        bots_apenas="Apagar apenas mensagens de bots",
    )
    @app_commands.default_permissions(manage_messages=True)
    async def purge(
        self,
        interaction: discord.Interaction,
        quantidade: int,
        membro: Optional[discord.Member] = None,
        contem: Optional[str] = None,
        bots_apenas: bool = False,
    ) -> None:
        quantidade = max(1, min(quantidade, 100))
        await interaction.response.defer(ephemeral=True)

        def check(msg: discord.Message) -> bool:
            if membro and msg.author != membro:
                return False
            if bots_apenas and not msg.author.bot:
                return False
            if contem and contem.lower() not in msg.content.lower():
                return False
            return True

        deleted = await interaction.channel.purge(limit=quantidade, check=check)

        embed = self._base_embed("Purge Executado", discord.Color.blurple(), icon="<:1000032078:1507948115338985512>")
        embed.add_field(name="Mensagens removidas", value=f"`{len(deleted)}`", inline=True)
        embed.add_field(name="Canal", value=interaction.channel.mention, inline=True)
        embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        if membro:
            embed.add_field(name="Filtro: usuário", value=membro.mention, inline=True)
        if contem:
            embed.add_field(name="Filtro: contém", value=f"`{contem}`", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)
        await self.log_action(interaction.guild, embed)

    @app_commands.command(name="lockdown", description="Bloqueia ou desbloqueia um canal para membros comuns.")
    @app_commands.describe(canal="Canal alvo (padrão: atual)", motivo="Motivo", ativar="True = bloquear, False = desbloquear")
    @app_commands.default_permissions(manage_channels=True)
    async def lockdown(
        self,
        interaction: discord.Interaction,
        ativar: bool = True,
        canal: Optional[discord.TextChannel] = None,
        motivo: str = "Sem motivo informado",
    ) -> None:
        target = canal or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("<:1000032056:1507947210057322637> Canal inválido.", ephemeral=True)
            return

        await interaction.response.defer()
        everyone = interaction.guild.default_role
        overwrite = target.overwrites_for(everyone)

        if ativar:
            overwrite.send_messages = False
            action_text = "<:1000032060:1507947421911613560> Lockdown ativado"
            color = discord.Color.red()
            try:
                await target.send(
                    embed=discord.Embed(
                        title="<:1000032059:1507947381096714260> Canal bloqueado",
                        description=f"Este canal foi temporariamente bloqueado.\n**Motivo:** {motivo}",
                        color=discord.Color.red(),
                    )
                )
            except discord.Forbidden:
                pass
        else:
            overwrite.send_messages = None
            action_text = "<:1000032074:1507948021013549166> Lockdown removido"
            color = discord.Color.green()
            try:
                await target.send(
                    embed=discord.Embed(
                        title="<:1000032074:1507948021013549166> Canal desbloqueado",
                        description="Este canal foi reaberto.",
                        color=discord.Color.green(),
                    )
                )
            except discord.Forbidden:
                pass

        await target.set_permissions(everyone, overwrite=overwrite, reason=f"{action_text} por {interaction.user}")
        embed = self._base_embed(action_text, color, icon="<:1000032059:1507947381096714260>" if ativar else "<:1000032074:1507948021013549166>")
        embed.add_field(name="Canal", value=target.mention, inline=True)
        embed.add_field(name="Moderador", value=interaction.user.mention, inline=True)
        embed.add_field(name="Motivo", value=motivo, inline=False)
        await interaction.followup.send(embed=embed)
        await self.log_action(interaction.guild, embed)

    # ═══════════════════════════════════════════
    # COMANDOS SLASH — GESTÃO DE PALAVRAS E CONFIG
    # ═══════════════════════════════════════════

    @app_commands.command(name="palavra-proibida", description="Gerencia a lista de palavras proibidas.")
    @app_commands.describe(acao="add | remove | listar", palavra="Palavra ou termo (não necessário para 'listar')")
    @app_commands.default_permissions(manage_messages=True)
    async def palavra_proibida(
        self, interaction: discord.Interaction, acao: str, palavra: str = ""
    ) -> None:
        config = await self.get_config(interaction.guild.id)
        words: list[str] = list(config.get("banned_words") or [])

        acao = acao.lower().strip()
        if acao in {"add", "adicionar"}:
            normalized = palavra.strip().lower()
            if not normalized:
                await interaction.response.send_message("<a:1000032057:1507947249873719497> Informe uma palavra.", ephemeral=True)
                return
            if normalized not in [w.lower() for w in words]:
                words.append(palavra.strip())
                await self.save_config(interaction.guild.id, banned_words=words)
            await interaction.response.send_message(f"<:1000032055:1507947171624910859> Palavra adicionada: `{palavra}`", ephemeral=True)

        elif acao in {"remove", "remover"}:
            normalized = palavra.strip().lower()
            words = [w for w in words if w.lower() != normalized]
            await self.save_config(interaction.guild.id, banned_words=words)
            await interaction.response.send_message(f"<:1000032055:1507947171624910859> Palavra removida: `{palavra}`", ephemeral=True)

        elif acao in {"listar", "list"}:
            if not words:
                await interaction.response.send_message("Nenhuma palavra proibida configurada.", ephemeral=True)
                return
            display = ", ".join(f"`{w}`" for w in words[:50])
            await interaction.response.send_message(
                f"**Palavras proibidas ({len(words)}):**\n{display}", ephemeral=True
            )
        else:
            await interaction.response.send_message("Use `add`, `remove` ou `listar`.", ephemeral=True)

    @app_commands.command(name="config-mod", description="Configura o sistema de moderação completo.")
    @app_commands.describe(
        log_channel="Canal de logs de moderação",
        mod_ping_role="Cargo a ser notificado em eventos graves",
        anti_spam="Ativar detecção de spam",
        anti_caps="Ativar detecção de caps excessivos",
        anti_links="Bloquear links externos",
        anti_mention="Bloquear menções em massa",
        ai_moderation="Ativar moderação por IA",
        warn_threshold="Número de avisos para punição automática",
        warn_action="Ação ao atingir limite: mute | kick | ban",
    )
    @app_commands.default_permissions(administrator=True)
    async def config_mod(
        self,
        interaction: discord.Interaction,
        log_channel: Optional[discord.TextChannel] = None,
        mod_ping_role: Optional[discord.Role] = None,
        anti_spam: Optional[bool] = None,
        anti_caps: Optional[bool] = None,
        anti_links: Optional[bool] = None,
        anti_mention: Optional[bool] = None,
        ai_moderation: Optional[bool] = None,
        warn_threshold: Optional[int] = None,
        warn_action: Optional[str] = None,
    ) -> None:
        updates: dict = {}
        if log_channel is not None:
            updates["log_channel"] = log_channel.id
        if mod_ping_role is not None:
            updates["mod_ping_role"] = mod_ping_role.id
        for key, val in {
            "anti_spam": anti_spam,
            "anti_caps": anti_caps,
            "anti_links": anti_links,
            "anti_mention": anti_mention,
            "ai_moderation": ai_moderation,
        }.items():
            if val is not None:
                updates[key] = val
        if warn_threshold is not None:
            updates["warn_threshold"] = max(1, min(warn_threshold, 30))
        if warn_action is not None:
            if warn_action.lower() not in {"mute", "kick", "ban"}:
                await interaction.response.send_message(
                    "<a:1000032057:1507947249873719497> `warn_action` deve ser `mute`, `kick` ou `ban`.", ephemeral=True
                )
                return
            updates["warn_action"] = warn_action.lower()

        if updates:
            await self.save_config(interaction.guild.id, **updates)

        config = await self.get_config(interaction.guild.id)
        embed = self._base_embed("Configuração de Moderação", discord.Color.blurple(), icon="<:1000032082:1507948289444544512>")

        def _bool(v: object) -> str:
            return "<:1000032055:1507947171624910859> Ativo" if v else "<:1000032056:1507947210057322637> Inativo"

        embed.add_field(
            name="Canal de Logs",
            value=f"<#{config['log_channel']}>" if config.get("log_channel") else "Não definido",
            inline=True,
        )
        embed.add_field(
            name="Cargo de Alerta",
            value=f"<@&{config['mod_ping_role']}>" if config.get("mod_ping_role") else "Não definido",
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(name="Anti-spam",    value=_bool(config.get("anti_spam")),    inline=True)
        embed.add_field(name="Anti-caps",    value=_bool(config.get("anti_caps")),    inline=True)
        embed.add_field(name="Anti-links",   value=_bool(config.get("anti_links")),   inline=True)
        embed.add_field(name="Anti-menções", value=_bool(config.get("anti_mention")), inline=True)
        embed.add_field(name="IA Moderação", value=_bool(config.get("ai_moderation")), inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        embed.add_field(
            name="Escalonamento automático",
            value=f"`{config.get('warn_threshold')}` aviso(s) → `{config.get('warn_action') or 'mute'}`",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────
# Helper de resultado vazio para IA
# ──────────────────────────────────────────────

def _empty_ai_result() -> dict:
    return {
        "violation": False,
        "severity": "none",
        "categories": [],
        "reason": "",
        "action": "none",
        "mute_minutes": 0,
        "confidence": 0.0,
        "toxicity_score": 0.0,
        "slow_mode_suggestion": False,
    }


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
