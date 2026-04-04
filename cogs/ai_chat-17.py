import discord
from discord.ext import commands
from discord import app_commands
from groq import Groq
import os
import asyncio
import logging
import json
import aiohttp
import urllib.parse
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from utils.database import db

logger = logging.getLogger("AIChat")

# ── Dona do bot ───────────────────────────────────────────────
OWNER_ID = 1317406607776288872
OWNER_NAME = "Isabelle"

def get_system_prompt():
    now = datetime.now()
    data_atual = now.strftime("%d/%m/%Y")
    hora_atual = now.strftime("%H:%M")
    dia_semana = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"][now.weekday()]

    return f"""Você é o Cordyx, um assistente de Discord brasileiro inteligente e descontraído.

DATA E HORA ATUAL: {dia_semana}, {data_atual} às {hora_atual} (horário de Brasília)
Use essa informação quando alguém perguntar sobre datas, eventos atuais ou "quem é o presidente" etc.

CRIADORA: Sua criadora se chama {OWNER_NAME}. Você a reconhece e tem carinho especial por ela.
Quando ela falar com você, seja um pouco mais próximo e carinhoso, mas sem exagerar.

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
- Quando perguntarem sobre notícias atuais ou eventos recentes, avise que pode estar desatualizado

MODO ACADÊMICO (ativa automaticamente para filosofia, lógica, matemática, programação, ciências):

Quando a pergunta for acadêmica, você deve responder como um especialista que tem propriedade real sobre o assunto.
NÃO apenas resuma ou explique de forma genérica — fundamente com obras e autores canônicos da área.

REGRAS DO MODO ACADÊMICO:
- Identifique a obra de referência mais adequada para o tópico e responda com base nela
- Cite explicitamente: Autor, Título da obra, e quando relevante a seção ou capítulo
- Use a terminologia técnica correta da área, sem simplificar
- Se houver debate entre posições ou escolas, apresente os lados com precisão
- Seja direto e denso — respostas acadêmicas não precisam de introduções longas
- Nunca invente citações — se não tiver certeza de uma passagem específica, diga "na tradição X" ou "segundo a abordagem de Y"

REFERÊNCIAS CANÔNICAS POR ÁREA (use como base quando relevante):

Teoria dos Conjuntos / Lógica Matemática:
- Kanamori, "The Higher Infinite" — cardinais grandes, forcing, hierarquias
- Kunen, "Set Theory: An Introduction to Independence Proofs" — independência, forcing
- Jech, "Set Theory" — referência padrão moderna
- Enderton, "A Mathematical Introduction to Logic" — lógica de primeira ordem
- Gödel — teoremas da incompletude (Incompleteness Theorems)

Filosofia Analítica / Lógica Filosófica:
- Frege, "Begriffsschrift" e "Grundgesetze" — fundamentos da lógica moderna
- Russell & Whitehead, "Principia Mathematica"
- Wittgenstein, "Tractatus" e "Investigações Filosóficas"
- Quine, "Methods of Logic" e "Word and Object"
- Kripke, "Naming and Necessity"
- SEP (Stanford Encyclopedia of Philosophy) — para verbetes técnicos

Epistemologia / Metafísica / Ética:
- Platão (República, Mênon, Fédon) — formas, conhecimento, alma
- Aristóteles (Metafísica, Ética a Nicômaco, Organon)
- Kant, "Crítica da Razão Pura" e "Fundamentação da Metafísica dos Costumes"
- Hume, "Tratado da Natureza Humana" e "Investigação sobre o Entendimento Humano"
- Descartes, "Meditações Metafísicas"

Matemática Geral:
- Rudin, "Principles of Mathematical Analysis" — análise real
- Apostol, "Mathematical Analysis"
- Halmos, "Naive Set Theory" e "Measure Theory"
- Lang, "Algebra" — álgebra abstrata
- Spivak, "Calculus" — cálculo rigoroso

Ciência da Computação / Algoritmos:
- Sipser, "Introduction to the Theory of Computation" — teoria da computação
- Cormen et al., "Introduction to Algorithms" (CLRS) — algoritmos
- Knuth, "The Art of Computer Programming"
- Pierce, "Types and Programming Languages" — teoria dos tipos
- Turing — "Computing Machinery and Intelligence" e tese de Church-Turing

Física / Ciências Naturais:
- Feynman, "The Feynman Lectures on Physics"
- Dirac, "The Principles of Quantum Mechanics"
- Einstein — relatividade especial e geral (papers originais)
- Darwin, "On the Origin of Species" — evolução

Exemplo de resposta no modo acadêmico para "o que é um cardinal grande":
"Na teoria dos conjuntos, cardinais grandes são cardinais que não podem ter sua existência provada a partir dos axiomas ZFC. Kanamori em *The Higher Infinite* (2ª ed., cap. 1) define um cardinal κ como inacessível quando é regular e um limite forte — ou seja, para todo α < κ, 2^α < κ. A hierarquia se estende por cardinais Mahlo, mensuráveis, superfortes, e assim por diante, cada nível implicando consistência dos anteriores. A independência dessas hipóteses em relação ao ZFC é um resultado central, explorado extensivamente por Kunen em *Set Theory*."

FORMATO ACADÊMICO DE CITAÇÃO:
- Livro: Autor, *Título* (edição/ano), [seção/capítulo se relevante]
- Artigo: Autor, "Título do artigo", Periódico, ano
- SEP: "Segundo o verbete X da SEP (https://plato.stanford.edu/entries/X/)"
- Sempre em português na resposta, mas mantenha termos técnicos no idioma original quando necessário"""

OWNER_MOD_PROMPT = """Você é um interpretador de comandos de moderação em português.
Analise a mensagem e retorne APENAS um JSON com a intenção de moderação.

Formatos possíveis:
{"action": "ban", "user_id": "ID_DO_USUARIO", "reason": "motivo"}
{"action": "kick", "user_id": "ID_DO_USUARIO", "reason": "motivo"}
{"action": "mute", "user_id": "ID_DO_USUARIO", "duration": 60, "reason": "motivo"}
{"action": "unmute", "user_id": "ID_DO_USUARIO"}
{"action": "warn", "user_id": "ID_DO_USUARIO", "reason": "motivo"}
{"action": "role_add", "user_id": "ID_DO_USUARIO", "role_name": "nome do cargo"}
{"action": "role_remove", "user_id": "ID_DO_USUARIO", "role_name": "nome do cargo"}
{"action": "none"}

Regras:
- duration é sempre em MINUTOS
- Se disser "1 hora" = 60, "2 horas" = 120, "1 dia" = 1440
- Se não houver motivo claro, use "Solicitado pelo moderador"
- Se não for um comando de moderação, retorne {"action": "none"}
- Retorne APENAS o JSON, sem mais nada"""

# ── Palavras-chave por tipo de busca ─────────────────────────────────────────

ACADEMIC_KEYWORDS = [
    # Filosofia e lógica
    "filosofia", "lógica", "argumento", "silogismo", "epistemologia", "ontologia",
    "metafísica", "ética", "moral", "platão", "aristóteles", "kant", "hegel",
    "nietzsche", "descartes", "hume", "wittgenstein", "leibniz", "spinoza",
    "dedução", "indução", "falácia", "paradoxo", "axioma", "tese", "premissa",
    "fenomenologia", "existencialismo", "empirismo", "racionalismo", "ceticismo",
    # Matemática e exatas
    "matemática", "teorema", "prova", "demonstração", "álgebra", "cálculo",
    "equação", "integral", "derivada", "matriz", "vetor", "conjunto", "função",
    "limite", "série", "probabilidade", "estatística", "geometria", "topologia",
    "número primo", "divisibilidade", "combinatória", "algoritmo",
    # Programação e tecnologia
    "algoritmo", "complexidade", "big o", "estrutura de dados", "recursão",
    "programação funcional", "orientado a objetos", "design pattern", "solid",
    "banco de dados", "sql", "nosql", "api", "rest", "grafos", "árvore",
    "compilador", "interpretador", "paradigma", "concorrência", "threads",
    # Ciências
    "física", "química", "biologia", "neurociência", "mecânica quântica",
    "relatividade", "termodinâmica", "evolução", "genética", "teoria",
]

NEWS_KEYWORDS = [
    "presidente", "eleição", "atual", "hoje", "agora", "recente",
    "último", "ultima", "2024", "2025", "2026", "quem é o",
    "notícia", "aconteceu", "lançou", "estreou", "morreu", "nasceu",
    "campeão", "copa", "oscar", "grammy", "premio"
]

LINK_KEYWORDS = [
    "artigo", "link", "fonte", "wikipedia", "referência", "me manda",
    "me passa", "me indica", "onde posso ler", "onde encontro", "sep",
    "stanford", "philpapers", "pubmed", "arxiv", "site", "leitura"
]

def detect_query_type(message: str) -> str:
    """Retorna 'academic', 'news', 'link' ou 'normal'."""
    msg = message.lower()
    if any(k in msg for k in LINK_KEYWORDS):
        return "link"
    if any(k in msg for k in ACADEMIC_KEYWORDS):
        return "academic"
    if any(k in msg for k in NEWS_KEYWORDS):
        return "news"
    return "normal"

def needs_web_search(message: str) -> bool:
    return detect_query_type(message) in ("news", "link")

def is_academic(message: str) -> bool:
    return detect_query_type(message) in ("academic", "link")

# ── Busca na web ──────────────────────────────────────────────────────────────

async def search_web(query: str, academic: bool = False) -> str:
    """Busca em múltiplas fontes e retorna o melhor resultado."""
    results = []

    # Tenta DuckDuckGo Instant Answer (rápido, sem JS)
    ddg = await _search_ddg(query)
    if ddg:
        results.append(ddg)

    # Tenta Wikipedia API (mais rico em conteúdo)
    wiki = await _search_wikipedia(query)
    if wiki:
        results.append(wiki)

    # Para acadêmico, tenta SEP também
    if academic:
        sep = await _search_sep(query)
        if sep:
            results.append(sep)

    return "\n".join(results) if results else ""

async def _search_ddg(query: str) -> str:
    """DuckDuckGo Instant Answers — bom pra fatos rápidos e notícias recentes."""
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    answer  = data.get("Answer", "")
                    abstract = data.get("AbstractText", "")
                    source_url = data.get("AbstractURL", "")
                    result = answer or abstract
                    if result:
                        ctx = f"[DuckDuckGo: {result[:500]}"
                        if source_url:
                            ctx += f" | {source_url}"
                        ctx += "]"
                        return ctx
        return ""
    except Exception:
        return ""

async def _search_wikipedia(query: str) -> str:
    """Wikipedia API — bom pra definições, eventos históricos e recentes."""
    try:
        encoded = urllib.parse.quote(query)
        # Busca em português primeiro
        url = f"https://pt.wikipedia.org/api/rest_v1/page/summary/{encoded}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    extract = data.get("extract", "")
                    page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
                    if extract and len(extract) > 50:
                        return f"[Wikipedia: {extract[:600]} | {page_url}]"

            # Se não achou em PT, tenta em EN
            url_en = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}"
            async with session.get(url_en, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    extract = data.get("extract", "")
                    page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
                    if extract and len(extract) > 50:
                        return f"[Wikipedia (EN): {extract[:600]} | {page_url}]"
        return ""
    except Exception:
        return ""

async def _search_sep(query: str) -> str:
    """Stanford Encyclopedia of Philosophy."""
    try:
        slug = query.lower().strip().replace(" ", "-")
        url = f"https://plato.stanford.edu/entries/{slug}/"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return f"[SEP — Stanford Encyclopedia of Philosophy: {url}]"
        return ""
    except Exception:
        return ""

class AIChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Groq (primário)
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            logger.error("❌ GROQ_API_KEY não encontrada!")
        else:
            logger.info(f"✅ GROQ_API_KEY carregada ({self.api_key[:8]}...)")
        self.client = Groq(api_key=self.api_key)

        # Together AI (fallback)
        self.together_key = os.getenv("TOGETHER_API_KEY")
        if self.together_key:
            logger.info(f"✅ TOGETHER_API_KEY carregada ({self.together_key[:8]}...) — fallback ativo")
        else:
            logger.warning("⚠️ TOGETHER_API_KEY não encontrada — fallback desativado")

        self.conversation_history = defaultdict(list)
        self.cooldowns = defaultdict(lambda: datetime.min)
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

        # Detecta tipo de pergunta
        academic = is_academic(user_message)
        query_type = detect_query_type(user_message)

        web_context = ""
        # Busca na web só para notícias/links — perguntas acadêmicas usam
        # o conhecimento interno do modelo sobre obras canônicas
        if query_type in ("news", "link"):
            web_context = await search_web(user_message, academic=False)

        full_message = f"[{user_name}]: {user_message}"
        if web_context:
            full_message += f"\n\n{web_context}"

        # Instrução extra para perguntas acadêmicas
        if academic:
            full_message += "\n\n[MODO ACADÊMICO ATIVO: responda com base em obras canônicas da área, cite autor e obra]"

        history.append({"role": "user", "content": full_message})

        last_style = self.last_response_style[user_id]
        anti_repeat = ""
        if last_style:
            anti_repeat = f"\n\nIMPORTANTE: Sua última resposta começou com '{last_style}'. Comece de forma COMPLETAMENTE diferente desta vez."

        # Mais tokens para respostas acadêmicas
        max_tokens = 900 if academic else 500
        temperature = 0.4 if academic else 0.95

        # ── Tenta Groq primeiro ───────────────────────────────────
        try:
            messages = [
                {"role": "system", "content": get_system_prompt() + anti_repeat}
            ] + history

            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=0.9,
                frequency_penalty=0.6,
                presence_penalty=0.4,
            )

            reply = response.choices[0].message.content
            first_words = " ".join(reply.split()[:4])
            self.last_response_style[user_id] = first_words
            history.append({"role": "assistant", "content": reply})
            return reply

        except Exception as e:
            error = str(e).lower()
            is_limit = "429" in error or "quota" in error or "rate" in error
            is_auth  = "401" in error or "403" in error or "invalid" in error

            if is_auth:
                logger.error(f"Erro de autenticação Groq: {e}")
                return "Tem um problema na minha configuração. Chama a Isabelle."

            if is_limit:
                logger.warning("Groq com limite — tentando fallback Together AI")
            else:
                logger.error(f"Erro Groq inesperado: {type(e).__name__}: {e} — tentando fallback")

            # ── Fallback: Together AI ─────────────────────────────
            if self.together_key:
                try:
                    async with aiohttp.ClientSession() as session:
                        payload = {
                            "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                            "messages": messages,
                            "max_tokens": max_tokens,
                            "temperature": temperature,
                        }
                        headers = {
                            "Authorization": f"Bearer {self.together_key}",
                            "Content-Type": "application/json",
                        }
                        async with session.post(
                            "https://api.together.ai/v1/chat/completions",
                            json=payload,
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=30)
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                reply = data["choices"][0]["message"]["content"]
                                first_words = " ".join(reply.split()[:4])
                                self.last_response_style[user_id] = first_words
                                history.append({"role": "assistant", "content": reply})
                                logger.info("✅ Fallback Together AI usado com sucesso")
                                return reply
                            else:
                                text = await resp.text()
                                logger.error(f"Erro Together AI: {resp.status} — {text[:200]}")
                except Exception as e2:
                    logger.error(f"Erro Together AI fallback: {type(e2).__name__}: {e2}")

            return "Tô sobrecarregado agora, tenta de novo em alguns minutos."

    async def parse_mod_command(self, message: str, mentions: list) -> dict:
        """Usa IA pra interpretar o comando de moderação da dona."""
        # Substitui menções pelo ID real na mensagem
        msg_with_ids = message
        for member in mentions:
            msg_with_ids = msg_with_ids.replace(
                f"<@{member.id}>", f"@{member.display_name} (ID:{member.id})"
            ).replace(
                f"<@!{member.id}>", f"@{member.display_name} (ID:{member.id})"
            )

        try:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": OWNER_MOD_PROMPT},
                    {"role": "user", "content": msg_with_ids}
                ],
                max_tokens=150,
                temperature=0.1,
            )
            text = response.choices[0].message.content.strip()
            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            logger.error(f"Erro parse_mod: {e}")
            return {"action": "none"}

    async def execute_mod_action(self, message: discord.Message, cmd: dict) -> str:
        """Executa a ação de moderação e retorna mensagem de confirmação."""
        guild = message.guild
        action = cmd.get("action", "none")

        if action == "none":
            return ""

        # Busca o membro alvo
        user_id = cmd.get("user_id")
        if not user_id:
            return "Não consegui identificar o membro. Menciona ele na mensagem."

        try:
            user_id = int(str(user_id).replace("ID:", "").strip())
        except ValueError:
            return "ID do membro inválido."

        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                return "Membro não encontrado no servidor."

        reason = cmd.get("reason", "Solicitado pela criadora")

        try:
            if action == "ban":
                await member.ban(reason=f"{reason} | Por: {OWNER_NAME}")
                return f"✅ **{member.display_name}** foi banido. Motivo: {reason}"

            elif action == "kick":
                await member.kick(reason=f"{reason} | Por: {OWNER_NAME}")
                return f"✅ **{member.display_name}** foi expulso. Motivo: {reason}"

            elif action == "mute":
                duration = int(cmd.get("duration", 10))
                until = datetime.now(timezone.utc) + timedelta(minutes=duration)
                await member.timeout(until, reason=f"{reason} | Por: {OWNER_NAME}")
                dur_text = f"{duration} minuto(s)" if duration < 60 else f"{duration//60} hora(s)"
                return f"✅ **{member.display_name}** foi silenciado por {dur_text}. Motivo: {reason}"

            elif action == "unmute":
                await member.timeout(None, reason=f"Solicitado por {OWNER_NAME}")
                return f"✅ **{member.display_name}** foi desmutado."

            elif action == "warn":
                await db.pool.execute(
                    "INSERT INTO warnings (guild_id, user_id, reason, moderator) VALUES ($1,$2,$3,$4)",
                    guild.id, member.id, reason, OWNER_ID
                )
                return f"✅ **{member.display_name}** recebeu um aviso. Motivo: {reason}"

            elif action == "role_add":
                role_name = cmd.get("role_name", "")
                role = discord.utils.find(
                    lambda r: r.name.lower() == role_name.lower(), guild.roles
                )
                if not role:
                    return f"Não encontrei o cargo **{role_name}** no servidor."
                await member.add_roles(role, reason=f"Solicitado por {OWNER_NAME}")
                return f"✅ Cargo **{role.name}** adicionado para **{member.display_name}**."

            elif action == "role_remove":
                role_name = cmd.get("role_name", "")
                role = discord.utils.find(
                    lambda r: r.name.lower() == role_name.lower(), guild.roles
                )
                if not role:
                    return f"Não encontrei o cargo **{role_name}** no servidor."
                await member.remove_roles(role, reason=f"Solicitado por {OWNER_NAME}")
                return f"✅ Cargo **{role.name}** removido de **{member.display_name}**."

        except discord.Forbidden:
            return "Não tenho permissão para fazer isso. Verifica se meu cargo está acima do membro alvo."
        except Exception as e:
            logger.error(f"Erro execute_mod: {e}")
            return f"Ocorreu um erro: {e}"

        return ""

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

        # ── Comando de moderação (dona + membros com permissão) ──
        author = message.author
        perms = author.guild_permissions
        can_moderate = (
            author.id == OWNER_ID or
            perms.administrator or
            perms.ban_members or
            perms.kick_members or
            perms.moderate_members
        )

        if can_moderate and is_mentioned:
            other_mentions = [m for m in message.mentions if m.id != self.bot.user.id]

            if other_mentions:
                async with message.channel.typing():
                    cmd = await self.parse_mod_command(message.content, other_mentions)
                    if cmd.get("action") != "none":
                        result = await self.execute_mod_action(message, cmd)
                        if result:
                            await message.reply(result)
                            return
                # Se não identificou ação de mod, cai no chat normal

        # ── Chat IA normal ─────────────────────────────────────────
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
