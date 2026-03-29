import discord
from discord.ext import commands
from discord import app_commands
from groq import Groq
import os
import asyncio
import logging
import random
import aiohttp
import urllib.parse
from datetime import datetime
from collections import defaultdict
from utils.database import db

logger = logging.getLogger("AIChat")

def get_system_prompt():
    now = datetime.now()
    data_atual = now.strftime("%d/%m/%Y")
    hora_atual = now.strftime("%H:%M")
    dia_semana = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"][now.weekday()]

    return f"""Você é o Cordyx, um assistente de Discord brasileiro inteligente e descontraído.

DATA E HORA ATUAL: {dia_semana}, {data_atual} às {hora_atual} (horário de Brasília)
Use essa informação quando alguém perguntar sobre datas, eventos atuais ou "quem é o presidente" etc.

PERSONALIDADE:
- Tom natural e descontraído, como um amigo inteligente
- Use gírias brasileiras com moderação — não em toda frase
- Emojis: use NO MÁXIMO 1 por mensagem, e só quando fizer sentido. Muitas vezes não use nenhum
- Seja direto e objetivo, sem enrolação
- Varie muito o estilo das respostas — nunca comece duas respostas da mesma forma
- Nunca use estrutura repetitiva como "Boa pergunta! [resposta] Vamos nessa?"
- Às vezes seja mais sério, às vezes mais descontraído, dependendo do contexto
- Quando não souber algo recente, admita e sugira buscar na internet

PROIBIDO:
- Mencionar que é IA, Groq, LLaMA ou qualquer tecnologia — você é o Cordyx
- Começar respostas sempre da mesma forma
- Usar mais de 1 emoji por mensagem
- Ser excessivamente animado ou usar "Vamos nessa!" toda hora
- Repetir a estrutura da resposta anterior

VOCÊ SABE:
- Sua data de conhecimento tem um limite, então para eventos muito recentes pode não ter certeza
- Quando perguntarem sobre notícias atuais ou eventos recentes, avise que pode estar desatualizado"""

# Variações de abertura pra evitar repetição
OPENERS = [
    "", "", "", "",  # maioria sem abertura especial
    "Olha, ", "Então, ", "Cara, ", "Na real, ",
    "Pensa assim: ", "É o seguinte: ", "Depende muito, mas ",
]

async def search_web(query: str) -> str:
    """Busca no DuckDuckGo e retorna um resumo dos resultados"""
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    abstract = data.get("AbstractText", "")
                    answer = data.get("Answer", "")
                    result = answer or abstract
                    if result:
                        return f"[Info atualizada da web: {result[:500]}]"
        return ""
    except Exception:
        return ""

def needs_web_search(message: str) -> bool:
    """Detecta se a pergunta precisa de informação atual"""
    keywords = [
        "presidente", "eleição", "atual", "hoje", "agora", "recente",
        "último", "ultima", "2024", "2025", "2026", "quem é o",
        "notícia", "aconteceu", "lançou", "estreou", "morreu", "nasceu",
        "campeão", "copa", "oscar", "grammy", "premio"
    ]
    msg_lower = message.lower()
    return any(k in msg_lower for k in keywords)

class AIChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            logger.error("❌ GROQ_API_KEY não encontrada!")
        else:
            logger.info(f"✅ GROQ_API_KEY carregada ({self.api_key[:8]}...)")

        self.client = Groq(api_key=self.api_key)
        self.conversation_history = defaultdict(list)
        self.cooldowns = defaultdict(lambda: datetime.min)
        # Rastreia última estrutura de resposta por usuário
        self.last_response_style = defaultdict(str)

    async def _get_ai_channels(self, guild_id: int) -> set:
        row = await db.pool.fetchrow(
            "SELECT ai_channels FROM ai_config WHERE guild_id = $1", guild_id
        )
        if row and row["ai_channels"]:
            return set(row["ai_channels"])
        return set()

    async def _set_ai_channel(self, guild_id: int, channel_id: int, add: bool):
        channels = await self._get_ai_channels(guild_id)
        if add:
            channels.add(channel_id)
        else:
            channels.discard(channel_id)
        await db.pool.execute("""
            INSERT INTO ai_config (guild_id, ai_channels)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET ai_channels = $2
        """, guild_id, list(channels))

    async def get_ai_response(self, user_message: str, user_id: int, user_name: str) -> str:
        history = self.conversation_history[user_id]

        if len(history) > 16:
            history = history[-16:]
            self.conversation_history[user_id] = history

        # Busca na web se necessário
        web_context = ""
        if needs_web_search(user_message):
            web_context = await search_web(user_message)

        # Monta a mensagem com contexto web se houver
        full_message = f"[{user_name}]: {user_message}"
        if web_context:
            full_message += f"\n\n{web_context}"

        history.append({"role": "user", "content": full_message})

        # Instrução anti-repetição dinâmica
        last_style = self.last_response_style[user_id]
        anti_repeat = ""
        if last_style:
            anti_repeat = f"\n\nIMPORTANTE: Sua última resposta começou com '{last_style}'. Comece de forma COMPLETAMENTE diferente desta vez."

        try:
            messages = [
                {"role": "system", "content": get_system_prompt() + anti_repeat}
            ] + history

            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=500,
                temperature=0.95,
                top_p=0.9,
                frequency_penalty=0.6,
                presence_penalty=0.4,
            )

            reply = response.choices[0].message.content

            # Salva primeiras palavras pra evitar repetição
            first_words = " ".join(reply.split()[:4])
            self.last_response_style[user_id] = first_words

            history.append({"role": "assistant", "content": reply})
            return reply

        except Exception as e:
            logger.error(f"Erro Groq: {type(e).__name__}: {e}")
            error = str(e).lower()
            if "429" in error or "quota" in error or "rate" in error:
                return "Tô sobrecarregado agora, tenta de novo em alguns minutos."
            if "401" in error or "403" in error or "invalid" in error:
                return "Tem um problema na minha configuração. Chama o admin."
            return "Deu um erro aqui, tenta de novo."

    def check_cooldown(self, user_id: int) -> float:
        diff = (datetime.now() - self.cooldowns[user_id]).total_seconds()
        return max(0, 3 - diff)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        is_mentioned = self.bot.user in message.mentions
        is_reply_to_bot = (
            message.reference and
            message.reference.resolved and
            isinstance(message.reference.resolved, discord.Message) and
            message.reference.resolved.author == self.bot.user
        )
        ai_channels = await self._get_ai_channels(message.guild.id)
        is_ai_channel = message.channel.id in ai_channels

        if not (is_mentioned or is_ai_channel or is_reply_to_bot):
            return

        wait = self.check_cooldown(message.author.id)
        if wait > 0:
            await message.reply(f"Espera {wait:.1f}s antes de falar de novo.")
            return

        self.cooldowns[message.author.id] = datetime.now()

        content = message.content
        for mention in message.mentions:
            content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        content = content.strip() or "Oi!"

        async with message.channel.typing():
            response = await self.get_ai_response(content, message.author.id, message.author.display_name)

        # Trata mensagem deletada antes de responder
        try:
            if len(response) > 1900:
                for chunk in [response[i:i+1900] for i in range(0, len(response), 1900)]:
                    await message.reply(chunk)
            else:
                await message.reply(response)
        except discord.NotFound:
            await message.channel.send(response)
        except discord.Forbidden:
            pass

    @app_commands.command(name="chat", description="💬 Conversa com a IA do bot!")
    @app_commands.describe(mensagem="O que você quer perguntar ou falar?")
    async def chat_command(self, interaction: discord.Interaction, mensagem: str):
        await interaction.response.defer()
        response = await self.get_ai_response(mensagem, interaction.user.id, interaction.user.display_name)
        embed = discord.Embed(description=response, color=discord.Color.purple())
        embed.set_author(
            name=f"Respondendo para {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="limpar-conversa", description="🗑️ Limpa seu histórico de conversa com a IA")
    async def clear_history(self, interaction: discord.Interaction):
        self.conversation_history[interaction.user.id] = []
        self.last_response_style[interaction.user.id] = ""
        await interaction.response.send_message("Histórico limpo!", ephemeral=True)

    @app_commands.command(name="canal-ia", description="⚙️ Ativa/desativa IA automática em um canal [ADMIN]")
    @app_commands.describe(canal="Canal para ativar/desativar a IA")
    @app_commands.default_permissions(administrator=True)
    async def set_ai_channel(self, interaction: discord.Interaction, canal: discord.TextChannel):
        ai_channels = await self._get_ai_channels(interaction.guild_id)
        adding = canal.id not in ai_channels
        await self._set_ai_channel(interaction.guild_id, canal.id, adding)
        status = "ativada" if adding else "desativada"
        await interaction.response.send_message(f"IA {status} no canal {canal.mention}.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AIChat(bot))
