import discord
from discord.ext import commands
from discord import app_commands
from groq import Groq
import os
import asyncio
import logging
from datetime import datetime
from collections import defaultdict
from utils.database import db

logger = logging.getLogger("AIChat")

SYSTEM_PROMPT = """Você é o Cordyx, um bot de Discord brasileiro cheio de personalidade!

Suas características:
- Fala de forma descontraída, com gírias brasileiras naturais (mano, cara, brother, galera, etc.)
- Usa emojis com moderação mas de forma expressiva (🚀✨🎵😄🔥)
- É prestativo, divertido e sempre animado
- Quando responde, vai direto ao ponto mas com simpatia
- Conhece bem sobre Discord, games, música, cultura brasileira
- Nunca é rude ou grosseiro, mesmo que provoquem
- Quando não sabe algo, admite com bom humor
- Respostas não muito longas — direto, objetivo e com personalidade
- JAMAIS menciona que é feito com Groq/Meta/LLaMA/IA — você é o CORDYX e ponto final!
- Quando pedem coisas inapropriadas, recusa com bom humor sem ser chato

Exemplos de como falar:
- "E aí, meu consagrado! 😄"
- "Cara, que pergunta top! Bora lá:"
- "Mano, isso é fácil! Olha só:"
- "Galera, vamos nessa! 🚀"
"""

class AIChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            logger.error("❌ GROQ_API_KEY não encontrada!")
        else:
            logger.info(f"✅ GROQ_API_KEY carregada ({self.api_key[:8]}...)")

        self.client = Groq(api_key=self.api_key)
        # Histórico por usuário em memória
        self.conversation_history = defaultdict(list)
        self.cooldowns = defaultdict(lambda: datetime.min)

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

        # Limita histórico a 20 mensagens
        if len(history) > 20:
            history = history[-20:]
            self.conversation_history[user_id] = history

        history.append({"role": "user", "content": f"[{user_name}]: {user_message}"})

        try:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=600,
                temperature=0.85,
            )

            reply = response.choices[0].message.content
            history.append({"role": "assistant", "content": reply})
            return reply

        except Exception as e:
            logger.error(f"Erro Groq: {type(e).__name__}: {e}")
            error = str(e).lower()
            if "429" in error or "quota" in error or "rate" in error:
                return "Ei, tô com muita demanda agora! 😅 Tenta de novo em uns segundinhos, mano!"
            if "401" in error or "403" in error or "invalid" in error or "api_key" in error:
                return "Eita, tem um problema com minha configuração! 😬 Chama o admin do servidor."
            return f"Eita, deu um bug aqui! 😬 Tenta de novo, brother. (Erro: {type(e).__name__})"

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
            await message.reply(f"⏳ Espera {wait:.1f}s antes de me chamar de novo!")
            return

        self.cooldowns[message.author.id] = datetime.now()

        content = message.content
        for mention in message.mentions:
            content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        content = content.strip() or "Oi!"

        async with message.channel.typing():
            response = await self.get_ai_response(content, message.author.id, message.author.display_name)

        if len(response) > 1900:
            for chunk in [response[i:i+1900] for i in range(0, len(response), 1900)]:
                await message.reply(chunk)
        else:
            await message.reply(response)

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
        embed.set_footer(text="Cordyx AI 🤖")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="limpar-conversa", description="🗑️ Limpa seu histórico de conversa com a IA")
    async def clear_history(self, interaction: discord.Interaction):
        self.conversation_history[interaction.user.id] = []
        await interaction.response.send_message("✅ Histórico limpo! Começamos do zero! 🚀", ephemeral=True)

    @app_commands.command(name="canal-ia", description="⚙️ Ativa/desativa IA automática em um canal [ADMIN]")
    @app_commands.describe(canal="Canal para ativar/desativar a IA")
    @app_commands.default_permissions(administrator=True)
    async def set_ai_channel(self, interaction: discord.Interaction, canal: discord.TextChannel):
        ai_channels = await self._get_ai_channels(interaction.guild_id)
        adding = canal.id not in ai_channels
        await self._set_ai_channel(interaction.guild_id, canal.id, adding)
        status = "✅ ativada" if adding else "❌ desativada"
        await interaction.response.send_message(f"IA {status} no canal {canal.mention}!", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AIChat(bot))
