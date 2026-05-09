import asyncio
import base64
import logging
import os
import re
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from groq import Groq

from utils.database import db

logger = logging.getLogger("AIChat")

BOT_NAME = os.getenv("BOT_NAME", "Revolux")
OWNER_NAME = os.getenv("OWNER_NAME", "Isabelle")
OWNER_ID = int(os.getenv("OWNER_ID", "1317406607776288872"))

# Só dois modelos, como combinado: rápido, simples e sem painel de nave espacial.
GROQ_PRIMARY_MODEL = os.getenv("AI_MODEL_PRIMARY", "openai/gpt-oss-120b")
GROQ_FALLBACK_MODEL = os.getenv("AI_MODEL_FALLBACK", "llama-3.3-70b-versatile")
GROQ_MODELS = (GROQ_PRIMARY_MODEL, GROQ_FALLBACK_MODEL)

MAX_HISTORY_MESSAGES = int(os.getenv("AI_MAX_HISTORY", "12"))
NORMAL_MAX_TOKENS = int(os.getenv("AI_NORMAL_MAX_TOKENS", "900"))
ACADEMIC_MAX_TOKENS = int(os.getenv("AI_ACADEMIC_MAX_TOKENS", "1500"))
COOLDOWN_SECONDS = int(os.getenv("AI_COOLDOWN_SECONDS", "3"))

ACADEMIC_KEYWORDS = {
    "filosofia", "lógica", "logica", "argumento", "silogismo", "falácia", "falacia",
    "epistemologia", "ontologia", "metafísica", "metafisica", "ética", "etica", "moral",
    "platão", "platao", "aristóteles", "aristoteles", "kant", "hegel", "nietzsche",
    "descartes", "hume", "wittgenstein", "leibniz", "spinoza", "quine", "kripke",
    "dedução", "deducao", "indução", "inducao", "axioma", "premissa", "conclusão",
    "matemática", "matematica", "teorema", "prova", "demonstração", "demonstracao",
    "álgebra", "algebra", "cálculo", "calculo", "equação", "equacao", "integral",
    "derivada", "matriz", "vetor", "conjunto", "função", "funcao", "limite",
    "estatística", "estatistica", "geometria", "topologia", "probabilidade",
    "programação", "programacao", "algoritmo", "complexidade", "big o", "recursão",
    "recursao", "banco de dados", "sql", "grafos", "árvore", "arvore", "física",
    "fisica", "química", "quimica", "biologia", "neurociência", "neurociencia",
}

CODE_KEYWORDS = {
    "python", "javascript", "typescript", "java", "c++", "rust", "go", "discord.py",
    "bot", "cog", "slash command", "api", "endpoint", "async", "await", "github",
    "railway", "deploy", "postgres", "postgresql", "erro", "bug", "debug", "refatorar",
}

CURRENT_INFO_KEYWORDS = {
    "hoje", "agora", "atual", "recente", "último", "ultima", "notícia", "noticias",
    "notícia", "aconteceu", "lançou", "lancou", "morreu", "campeão", "campeao",
    "placar", "resultado", "preço", "preco", "versão", "versao", "2025", "2026",
}

SEARCH_REQUEST_KEYWORDS = {
    "pesquise", "pesquisa", "procure", "busque", "busca", "googla", "fonte", "link",
    "artigo", "sep", "stanford", "philpapers", "arxiv", "wikipedia", "onde posso ler",
}

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _contains_any(text: str, words: set[str]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in words)


def detect_query_type(message: str) -> str:
    msg = message.lower()
    if _contains_any(msg, SEARCH_REQUEST_KEYWORDS) or _contains_any(msg, CURRENT_INFO_KEYWORDS):
        return "search"
    if _contains_any(msg, CODE_KEYWORDS):
        return "code"
    if _contains_any(msg, ACADEMIC_KEYWORDS):
        return "academic"
    return "chat"


def is_academic(message: str) -> bool:
    return detect_query_type(message) in {"academic", "code"}


def build_system_prompt() -> str:
    now = datetime.now()
    weekday = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"][now.weekday()]
    stamp = now.strftime(f"%d/%m/%Y, {weekday}, %H:%M")

    return f"""Você é {BOT_NAME}, uma IA conversacional para Discord.

Data local de referência: {stamp}.
Criadora registrada: {OWNER_NAME}. Se ela falar com você, seja mais próximo, mas sem exagero.

MISSÃO
Responda de forma útil, clara, rápida e confiável. Você conversa bem em chat casual, mas possui um lado acadêmico forte para filosofia, lógica, matemática, programação, ciência e temas conceituais difíceis.

ESTILO PADRÃO
- Fale em português brasileiro, a menos que o usuário peça outro idioma.
- Seja natural para Discord: direto, humano, leve e sem parecer redação escolar em perguntas simples.
- Respostas simples pedem respostas curtas. Perguntas complexas pedem estrutura e profundidade.
- Use poucos emojis. Nunca encha a resposta de decoração.
- Não seja arrogante, ofensivo, pedante ou passivo-agressivo.
- Não comece todas as respostas do mesmo jeito.

CONFIABILIDADE
- Sua prioridade é responder certo.
- Se não souber, diga que não sabe.
- Se estiver incerto, diga que há incerteza.
- Nunca invente fonte, link, citação, livro, artigo, autor, estatística, teorema ou consenso acadêmico.
- Não finja ter pesquisado. Se uma ferramenta de busca foi usada, utilize o contexto recebido. Se não foi usada, responda com conhecimento geral e deixe limites claros.
- Em temas atuais, versões de APIs, preços, notícias, política, leis, resultados esportivos e disponibilidade de serviços, avise quando não houver fonte atual suficiente.

MODO ACADÊMICO
Ative internamente quando o assunto envolver filosofia, lógica, matemática, ciência, programação ou debate conceitual.
Nesse modo:
1. Defina os termos importantes.
2. Dê a resposta direta primeiro.
3. Explique o raciocínio.
4. Mostre objeções, exceções ou debates quando existirem.
5. Diferencie fato, interpretação, opinião e especulação.
6. Cite autores e obras apenas quando tiver segurança.
7. Quando houver contexto externo fornecido, use-o com prioridade.

FILOSOFIA
Não reduza filósofos a frases de efeito. Explique problema, tese, argumento e crítica. Quando houver escolas diferentes, apresente as principais leituras com equilíbrio.

LÓGICA
Diferencie verdade, validade, solidez, consistência, contradição, implicação e equivalência. Ao analisar argumento: identifique premissas, conclusão, validade e possível contraexemplo.

MATEMÁTICA
Confira contas, hipóteses e unidades. Mostre etapas quando o usuário estiver aprendendo. Não invente teoremas.

PROGRAMAÇÃO
Dê soluções práticas, código copiável e explicação da causa. Considere ambiente, versão, permissões e segurança. Nunca recomende expor API keys no GitHub.

FONTES ACADÊMICAS PREFERIDAS
Quando houver busca/biblioteca, priorize SEP, IEP, PhilPapers, arXiv, documentação oficial, universidades e artigos acadêmicos. Blogs e fóruns não devem ser tratados como autoridade.

SEGURANÇA
Não ajude com roubo de contas, malware, abuso de API, vazamento de dados, exposição de chaves, burlar sistemas ou instruções perigosas. Recuse brevemente e ofereça alternativa segura.

IDENTIDADE
Você é {BOT_NAME}. Não explique bastidores de provedor, modelo, Groq ou API, a menos que o usuário pergunte sobre a configuração técnica do bot.
"""


async def fetch_json(session: aiohttp.ClientSession, url: str, *, timeout: int = 5) -> Optional[dict]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
    except Exception:
        return None
    return None


async def search_web(query: str, academic: bool = False) -> str:
    """Busca simples e rápida. Usa chaves se existirem, e cai para Wikipedia/DDG sem chave."""
    results: list[str] = []
    encoded = urllib.parse.quote(query)

    async with aiohttp.ClientSession() as session:
        tavily_key = os.getenv("TAVILY_API_KEY")
        if tavily_key:
            try:
                payload = {
                    "api_key": tavily_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 3,
                    "include_answer": True,
                }
                async with session.post("https://api.tavily.com/search", json=payload, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("answer"):
                            results.append(f"[Tavily resumo: {data['answer'][:500]}]")
                        for item in data.get("results", [])[:2]:
                            title = item.get("title", "Sem título")
                            content = item.get("content", "")[:350]
                            url = item.get("url", "")
                            if content:
                                results.append(f"[Tavily | {title}: {content} | {url}]")
            except Exception as exc:
                logger.debug("Falha Tavily: %s", exc)

        gnews_key = os.getenv("GNEWS_API_KEY")
        if gnews_key:
            url = f"https://gnews.io/api/v4/search?q={encoded}&lang=pt&max=3&token={gnews_key}"
            data = await fetch_json(session, url, timeout=6)
            if data:
                for article in data.get("articles", [])[:2]:
                    title = article.get("title", "Sem título")
                    desc = article.get("description", "")[:280]
                    link = article.get("url", "")
                    pub = article.get("publishedAt", "")[:10]
                    results.append(f"[GNews {pub} | {title}: {desc} | {link}]")

        # Wikipedia funciona melhor para conceitos.
        wiki_langs = ("pt", "en") if academic else ("pt",)
        for lang in wiki_langs:
            data = await fetch_json(session, f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded}")
            if data and data.get("extract"):
                extract = data.get("extract", "")[:650]
                page = data.get("content_urls", {}).get("desktop", {}).get("page", "")
                results.append(f"[Wikipedia {lang.upper()}: {extract} | {page}]")
                break

        # Tentativa direta para SEP em consultas acadêmicas.
        if academic:
            slug = re.sub(r"[^a-z0-9\- ]", "", query.lower()).strip().replace(" ", "-")
            if slug:
                sep_url = f"https://plato.stanford.edu/entries/{slug}/"
                try:
                    async with session.get(sep_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                        if resp.status == 200:
                            results.append(f"[SEP disponível: Stanford Encyclopedia of Philosophy | {sep_url}]")
                except Exception:
                    pass

        if not results:
            data = await fetch_json(session, f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1")
            if data:
                text = data.get("Answer") or data.get("AbstractText")
                link = data.get("AbstractURL", "")
                if text:
                    results.append(f"[DuckDuckGo: {text[:600]} | {link}]")

    return "\n".join(results)


async def fetch_image_base64(url: str) -> Optional[tuple[str, str]]:
    # Mantido para não quebrar mensagens com anexo, mas os dois modelos escolhidos são text-only.
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    mime = resp.headers.get("Content-Type", "image/png").split(";")[0]
                    data = await resp.read()
                    return base64.b64encode(data).decode("utf-8"), mime
    except Exception:
        return None
    return None


class AIChat(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.groq_clients: list[Groq] = []
        self.groq_index = 0
        self.model_index = 0
        self.history: defaultdict[int, list[dict[str, str]]] = defaultdict(list)
        self.cooldowns: defaultdict[int, datetime] = defaultdict(lambda: datetime.min)

        for env_name in ("GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3", "GROQ_API_KEY_4", "GROQ_API_KEY_5"):
            key = os.getenv(env_name)
            if key:
                self.groq_clients.append(Groq(api_key=key))
                logger.info("%s carregada.", env_name)

        if not self.groq_clients:
            logger.error("Nenhuma GROQ_API_KEY encontrada. Configure no Railway/Hospedagem.")
        else:
            logger.info("Groq ativo com %s chave(s). Modelos: %s", len(self.groq_clients), ", ".join(GROQ_MODELS))

    async def _get_ai_channels(self, guild_id: int) -> set[int]:
        row = await db.pool.fetchrow("SELECT ai_channels FROM ai_config WHERE guild_id = $1", guild_id)
        return set(row["ai_channels"] or []) if row else set()

    async def _set_ai_channel(self, guild_id: int, channel_id: int, enabled: bool) -> None:
        channels = await self._get_ai_channels(guild_id)
        if enabled:
            channels.add(channel_id)
        else:
            channels.discard(channel_id)
        await db.pool.execute(
            """
            INSERT INTO ai_config (guild_id, ai_channels)
            VALUES ($1, $2)
            ON CONFLICT (guild_id) DO UPDATE SET ai_channels = $2
            """,
            guild_id,
            list(channels),
        )

    async def _call_groq(self, messages: list[dict], *, max_tokens: int, temperature: float) -> Optional[str]:
        if not self.groq_clients:
            return None

        for _model_attempt in range(len(GROQ_MODELS)):
            model = GROQ_MODELS[self.model_index % len(GROQ_MODELS)]

            for _key_attempt in range(len(self.groq_clients)):
                client = self.groq_clients[self.groq_index % len(self.groq_clients)]
                key_number = (self.groq_index % len(self.groq_clients)) + 1
                try:
                    response = await asyncio.to_thread(
                        client.chat.completions.create,
                        model=model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=0.9,
                        frequency_penalty=0.35,
                        presence_penalty=0.25,
                    )
                    logger.info("Groq respondeu com %s | chave #%s", model, key_number)
                    return response.choices[0].message.content.strip()
                except Exception as exc:
                    error = str(exc).lower()
                    is_limit = any(token in error for token in ("429", "rate", "quota", "limit"))
                    is_model_error = any(token in error for token in ("model", "not found", "invalid"))

                    if is_model_error:
                        logger.warning("Modelo indisponível no Groq: %s", model)
                        break
                    if is_limit:
                        logger.warning("Limite na chave Groq #%s usando %s.", key_number, model)
                    else:
                        logger.error("Erro Groq chave #%s modelo %s: %s", key_number, model, exc)
                    self.groq_index += 1

            self.model_index += 1

        return None

    async def get_ai_response(self, user_message: str, user_id: int, user_name: str, image_data=None) -> str:
        query_type = detect_query_type(user_message)
        academic = query_type in {"academic", "code"}

        if image_data:
            return "No momento estou configurado só com modelos de texto. Posso analisar a descrição da imagem se você escrever o que aparece nela."

        web_context = ""
        if query_type == "search":
            web_context = await search_web(user_message, academic=academic)

        history = self.history[user_id][-MAX_HISTORY_MESSAGES:]
        self.history[user_id] = history

        user_content = user_message
        if web_context:
            user_content += f"\n\nCONTEXTO DE BUSCA, use sem inventar além dele:\n{web_context}"
        elif query_type == "search":
            user_content += "\n\nAviso interno: a busca não trouxe resultado confiável. Não invente dados atuais."

        if academic:
            user_content += "\n\nModo acadêmico ativo: seja preciso, defina termos, aponte limites e evite citações inventadas."

        messages = [{"role": "system", "content": build_system_prompt()}] + history + [
            {"role": "user", "content": f"Usuário: {user_name}\nMensagem: {user_content}"}
        ]

        reply = await self._call_groq(
            messages,
            max_tokens=ACADEMIC_MAX_TOKENS if academic else NORMAL_MAX_TOKENS,
            temperature=0.35 if academic else 0.75,
        )

        if not reply:
            return "Estou sem resposta dos modelos agora. Tente de novo em alguns instantes."

        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})
        self.history[user_id] = history[-MAX_HISTORY_MESSAGES:]
        return reply

    async def _send_long_reply(self, message: discord.Message, text: str) -> None:
        chunks: list[str] = []
        current = ""
        for paragraph in text.split("\n"):
            if len(current) + len(paragraph) + 1 > 1900:
                chunks.append(current)
                current = paragraph
            else:
                current = f"{current}\n{paragraph}" if current else paragraph
        if current:
            chunks.append(current)

        try:
            await message.reply(chunks[0])
        except discord.NotFound:
            await message.channel.send(chunks[0])
        for chunk in chunks[1:]:
            await message.channel.send(chunk)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return

        ai_channels = await self._get_ai_channels(message.guild.id)
        mentioned = self.bot.user in message.mentions if self.bot.user else False
        replied_to_bot = False

        if message.reference and message.reference.resolved:
            resolved = message.reference.resolved
            replied_to_bot = getattr(resolved, "author", None) == self.bot.user

        should_answer = mentioned or replied_to_bot or message.channel.id in ai_channels
        if not should_answer:
            return

        now = datetime.now()
        if now < self.cooldowns[message.author.id]:
            return
        self.cooldowns[message.author.id] = now + timedelta(seconds=COOLDOWN_SECONDS)

        content = message.content
        if self.bot.user:
            content = content.replace(f"<@{self.bot.user.id}>", "").replace(f"<@!{self.bot.user.id}>", "").strip()
        if not content and message.attachments:
            content = "Analise o anexo enviado."
        if not content:
            content = "Olá."

        image_data = None
        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                image_data = await fetch_image_base64(attachment.url)
                break

        async with message.channel.typing():
            reply = await self.get_ai_response(content, message.author.id, message.author.display_name, image_data=image_data)

        await self._send_long_reply(message, reply)

    @app_commands.command(name="chat", description="Conversa com o Revolux.")
    @app_commands.describe(mensagem="Mensagem que deseja enviar para a IA.")
    async def chat_command(self, interaction: discord.Interaction, mensagem: str) -> None:
        await interaction.response.defer()
        response = await self.get_ai_response(mensagem, interaction.user.id, interaction.user.display_name)
        if len(response) <= 3900:
            embed = discord.Embed(description=response, color=discord.Color.blurple())
            embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(response[:1900])
            for i in range(1900, len(response), 1900):
                await interaction.channel.send(response[i:i + 1900])

    @app_commands.command(name="limpar-conversa", description="Apaga seu histórico de conversa com o Revolux.")
    async def clear_history(self, interaction: discord.Interaction) -> None:
        self.history[interaction.user.id] = []
        await interaction.response.send_message("Histórico apagado.", ephemeral=True)

    @app_commands.command(name="canal-ia", description="Ativa ou desativa a IA automática em um canal.")
    @app_commands.describe(canal="Canal que deseja configurar.")
    @app_commands.default_permissions(administrator=True)
    async def set_ai_channel(self, interaction: discord.Interaction, canal: discord.TextChannel) -> None:
        channels = await self._get_ai_channels(interaction.guild_id)
        enabled = canal.id not in channels
        await self._set_ai_channel(interaction.guild_id, canal.id, enabled)
        status = "ativada" if enabled else "desativada"
        await interaction.response.send_message(f"IA {status} em {canal.mention}.", ephemeral=True)

    @app_commands.command(name="status-ia", description="Mostra o status da IA do Revolux.")
    @app_commands.default_permissions(administrator=True)
    async def status_ia(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="Status da IA", color=discord.Color.blurple())
        embed.add_field(name="Provedor", value="Groq", inline=True)
        embed.add_field(name="Chaves carregadas", value=str(len(self.groq_clients)), inline=True)
        embed.add_field(name="Principal", value=f"`{GROQ_PRIMARY_MODEL}`", inline=False)
        embed.add_field(name="Reserva", value=f"`{GROQ_FALLBACK_MODEL}`", inline=False)
        embed.add_field(
            name="Busca externa",
            value=(
                f"Tavily: {'ativo' if os.getenv('TAVILY_API_KEY') else 'sem chave'}\n"
                f"GNews: {'ativo' if os.getenv('GNEWS_API_KEY') else 'sem chave'}\n"
                "Wikipedia/DDG: fallback sem chave"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="testar-ia", description="Testa rapidamente os dois modelos configurados.")
    @app_commands.default_permissions(administrator=True)
    async def testar_ia(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        messages = [
            {"role": "system", "content": "Responda apenas: funcionando."},
            {"role": "user", "content": "teste"},
        ]
        lines = []
        for model in GROQ_MODELS:
            old_index = self.model_index
            self.model_index = GROQ_MODELS.index(model)
            result = await self._call_groq(messages, max_tokens=20, temperature=0.1)
            lines.append(f"`{model}`: {'ok' if result else 'falhou'}")
            self.model_index = old_index
        await interaction.followup.send("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AIChat(bot))
