import discord
from discord.ext import commands
from discord import app_commands
import google.generativeai as genai
import os
import asyncio
from datetime import datetime
from collections import defaultdict
from utils.database import db

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

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
- JAMAIS menciona que é feito com Gemini/Google/IA — você é o CORDYX e ponto final!
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
        self.model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=SYSTEM_PROMPT
        )
        # Histórico de conversa por usuário (em memória)
        self.chat_sessions = {}
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

    def get_chat_session(self, user_id: int):
        if user_id not in self.chat_sessions:
            self.chat_sessions[user_id] = self.model.start_chat(history=[])
        return self.chat_sessions[user_id]

    async def get_ai_response(self, user_message: str, user_id: int, user_name: str) -> str:
        try:
            chat = self.get_chat_session(user_id)
            response = await asyncio.to_thread(
                chat.send_message,
                f"[{user_name}]: {user_message}"
            )
            return response.text
        except Exception as e:
            error = str(e).lower()
            if "quota" in error or "limit" in error:
                return "Ei, tô com muita demanda agora! 😅 Tenta de novo em uns segundinhos, mano!"
            return f"Eita, deu um bug aqui! 😬 Tenta de novo, brother."

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
        self.chat_sessions.pop(interaction.user.id, None)
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
