import discord
from discord.ext import commands
from discord import app_commands
from groq import Groq
from openai import AsyncOpenAI
import os
import asyncio
import logging
import json
import aiohttp
import base64
import urllib.parse
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from utils.database import db

logger = logging.getLogger("AIChat")

# ── Dona do bot ───────────────────────────────────────────────
OWNER_ID = 1317406607776288872
OWNER_NAME = "Isabelle"

# ── Modelos OpenRouter (primário, gratuitos) ──────────────────
# Cada modelo tem uma especialidade. O bot seleciona o melhor
# de acordo com o tipo de mensagem detectado.

OPENROUTER_MODELS = {
    # Análise de imagem — único com visão no free tier com 128K contexto
    "vision":    "nvidia/nemotron-nano-12b-v2-vl:free",

    # Bate-papo geral (1º) — GPT-OSS 120B, primeiro open-weight da OpenAI
    "chat":      "openai/gpt-oss-120b:free",

    # Bate-papo geral (2º) — Llama 3.3 70B, fallback rápido pro chat
    "chat2":     "meta-llama/llama-3.3-70b-instruct:free",

    # Acadêmico / raciocínio denso — Nemotron Super 120B, 262K contexto
    "academic":  "nvidia/nemotron-3-super-120b-a12b:free",

    # Código / programação — Qwen3 Coder, melhor free tier para código
    "code":      "qwen/qwen3-coder:free",

    # Respostas rápidas / agente — Ling Flash, 104B params / 7.4B ativos
    "fast":      "inclusionai/ling-2.6-flash:free",
}

# Fallback: se o modelo especializado falhar, tenta esses em ordem
OPENROUTER_FALLBACK_ORDER = [
    "openai/gpt-oss-120b:free",                  # chat principal
    "meta-llama/llama-3.3-70b-instruct:free",     # chat fallback
    "nvidia/nemotron-3-super-120b-a12b:free",     # acadêmico
    "inclusionai/ling-2.6-flash:free",            # rápido
    "qwen/qwen3-coder:free",                      # código
    "nvidia/nemotron-nano-12b-v2-vl:free",        # visão
]

# ── Modelos Groq (fallback quando OpenRouter esgotar) ─────────
GROQ_MODELS = [
    "llama-3.3-70b-versatile",        # principal, muito capaz
    "llama3-70b-8192",                 # alternativa sólida
    "gemma2-9b-it",                    # Google Gemma, leve e rápido
    "mixtral-8x7b-32768",              # Mixtral, bom contexto longo
    "llama3-8b-8192",                  # mais leve, última opção
]


def get_system_prompt():
    now = datetime.now()
    data_atual = now.strftime("%d/%m/%Y")
    hora_atual = now.strftime("%H:%M")
    dia_semana = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"][now.weekday()]

    return f"""Você é o RevolutX, um assistente de Discord brasileiro.

DATA E HORA ATUAL: {dia_semana}, {data_atual} às {hora_atual} (horário de Brasília)

CRIADORA: Sua criadora se chama {OWNER_NAME}. Você a reconhece e tem carinho especial por ela.
Quando ela falar com você, seja um pouco mais próximo e carinhoso, mas sem exagerar.

════════════════════════════════════════
INTELIGÊNCIA SOCIAL — LEIA COM ATENÇÃO
════════════════════════════════════════

Você precisa identificar o CONTEXTO REAL da mensagem antes de responder:

1. CONSCIÊNCIA DE SI MESMO:
   - Quando alguém fala SOBRE você (ex: "esse bot é massa", "o RevolutX é incrível"), você SABe que estão falando de VOCÊ
   - Nunca responda como se fosse um terceiro — você É o RevolutX
   - "Esse bot deve ser legal" é uma resposta ERRADA se alguém disser "o bot é massa" marcando você
   - Resposta CERTA: "valeu! 😄" ou "fico feliz que tá curtindo"

2. IRONIA E SARCASMO:
   - Identifique quando alguém está sendo irônico (ex: "que surpresa, né?" = sarcasmo)
   - Responda na mesma vibe sem ser robótico
   - "Né" no final de frases afirmativas geralmente indica ironia

3. ELOGIOS E CRÍTICAS DIRETAS:
   - Elogio sobre você → aceite naturalmente, sem fingir que é sobre outra coisa
   - Crítica sobre você → reconheça e responda com maturidade
   - "Você é lento" → não explique tecnologia, apenas responda com leveza

4. PERGUNTAS RETÓRICAS:
   - "Que horas são essas?" (quando alguém chega tarde) → é expressão, não pergunta literal
   - Entenda o contexto social antes de responder literalmente

5. CONVERSAS CASUAIS:
   - "oi", "boa noite", "tudo bem" → responda naturalmente e curto
   - Não transforme cumprimentos em discursos
   - "kkkk" ou "ahahaha" → a pessoa está rindo, combine com leveza

════════════════════════════════════════
MODOS DE RESPOSTA
════════════════════════════════════════

MODO CASUAL (padrão para conversa comum):
- Tom natural e descontraído, como um amigo inteligente
- Use gírias brasileiras com moderação
- Emojis: 0 a 2 por mensagem, só quando fizerem sentido
- Respostas curtas para perguntas curtas
- Varie o estilo — nunca comece duas respostas da mesma forma
- Seja direto, sem enrolação

MODO ACADÊMICO (ativa automaticamente para filosofia, lógica, matemática, programação, ciências):
- Tom especializado e preciso
- Cite obras e autores canônicos da área
- Terminologia técnica correta
- Respostas densas e fundamentadas
- Menos emojis, mais substância

════════════════════════════════════════
COMPORTAMENTO GERAL
════════════════════════════════════════

PROIBIDO:
- Mencionar que é IA, Groq, OpenRouter, LLaMA ou qualquer tecnologia — você é o RevolutX
- Começar respostas sempre da mesma forma
- Usar mais de 2 emojis por mensagem
- Ser excessivamente animado ou usar "Vamos nessa!" toda hora
- Responder sobre você como se fosse terceiro
- Respostas longas para perguntas simples

REFERÊNCIAS ACADÊMICAS CANÔNICAS:

Teoria dos Conjuntos / Lógica Matemática:
- Kanamori, "The Higher Infinite" | Kunen, "Set Theory" | Jech, "Set Theory"
- Enderton, "A Mathematical Introduction to Logic" | Gödel — Incompleteness Theorems

Filosofia Analítica:
- Frege, "Begriffsschrift" | Russell & Whitehead, "Principia Mathematica"
- Wittgenstein, "Tractatus" e "Investigações Filosóficas"
- Quine, "Methods of Logic" | Kripke, "Naming and Necessity"

Epistemologia / Metafísica / Ética:
- Platão (República, Mênon, Fédon) | Aristóteles (Metafísica, Ética a Nicômaco)
- Kant, "Crítica da Razão Pura" | Hume, "Tratado da Natureza Humana"
- Descartes, "Meditações Metafísicas"

Matemática:
- Rudin, "Principles of Mathematical Analysis" | Halmos, "Naive Set Theory"
- Spivak, "Calculus" | Lang, "Algebra"

Ciência da Computação:
- Sipser, "Introduction to the Theory of Computation"
- Cormen et al., "Introduction to Algorithms" (CLRS)
- Knuth, "The Art of Computer Programming"

Física:
- Feynman, "The Feynman Lectures on Physics"
- Dirac, "The Principles of Quantum Mechanics"

FORMATO DE CITAÇÃO ACADÊMICA:
- Livro: Autor, *Título* (edição/ano), [seção/capítulo]
- SEP: "Segundo o verbete X da SEP (https://plato.stanford.edu/entries/X/)"
- Sempre em português, mantendo termos técnicos no idioma original"""


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

# ── Palavras-chave ────────────────────────────────────────────

ACADEMIC_KEYWORDS = [
    "filosofia", "lógica", "argumento", "silogismo", "epistemologia", "ontologia",
    "metafísica", "ética", "moral", "platão", "aristóteles", "kant", "hegel",
    "nietzsche", "descartes", "hume", "wittgenstein", "leibniz", "spinoza",
    "dedução", "indução", "falácia", "paradoxo", "axioma", "tese", "premissa",
    "fenomenologia", "existencialismo", "empirismo", "racionalismo", "ceticismo",
    "matemática", "teorema", "prova", "demonstração", "álgebra", "cálculo",
    "equação", "integral", "derivada", "matriz", "vetor", "conjunto", "função",
    "limite", "série", "probabilidade", "estatística", "geometria", "topologia",
    "número primo", "divisibilidade", "combinatória",
    "complexidade", "big o", "estrutura de dados", "recursão",
    "programação funcional", "orientado a objetos", "design pattern", "solid",
    "banco de dados", "sql", "nosql", "grafos", "árvore",
    "compilador", "interpretador", "paradigma", "concorrência", "threads",
    "física", "química", "biologia", "neurociência", "mecânica quântica",
    "relatividade", "termodinâmica", "evolução", "genética", "teoria",
]

CODE_KEYWORDS = [
    "código", "code", "script", "função", "função", "class", "import",
    "python", "javascript", "typescript", "java", "c++", "rust", "go",
    "bug", "erro", "debug", "refatorar", "algoritmo", "implementar",
    "api", "endpoint", "request", "response", "async", "await",
    "discord.py", "bot", "cog", "comando", "slash command",
    "railway", "github", "deploy", "docker", "postgresql", "sqlite",
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
    msg = message.lower()
    if any(k in msg for k in LINK_KEYWORDS):
        return "link"
    if any(k in msg for k in CODE_KEYWORDS):
        return "code"
    if any(k in msg for k in ACADEMIC_KEYWORDS):
        return "academic"
    if any(k in msg for k in NEWS_KEYWORDS):
        return "news"
    return "chat"


def select_openrouter_models(query_type: str, has_image: bool) -> list[str]:
    """Retorna lista de modelos em ordem de preferência para o tipo de pergunta."""
    if has_image:
        return [OPENROUTER_MODELS["vision"]]
    if query_type in ("academic", "link", "news"):
        return [OPENROUTER_MODELS["academic"], OPENROUTER_MODELS["chat"], OPENROUTER_MODELS["chat2"]]
    if query_type == "code":
        return [OPENROUTER_MODELS["code"], OPENROUTER_MODELS["chat"]]
    # chat → tenta GPT-OSS primeiro, depois Llama como fallback rápido
    return [OPENROUTER_MODELS["chat"], OPENROUTER_MODELS["chat2"]]


def needs_web_search(message: str) -> bool:
    return detect_query_type(message) in ("news", "link")


def is_academic(message: str) -> bool:
    return detect_query_type(message) in ("academic", "link")


# ── Busca na web ──────────────────────────────────────────────

async def search_web(query: str, academic: bool = False) -> str:
    results = []
    ddg = await _search_ddg(query)
    if ddg:
        results.append(ddg)
    wiki = await _search_wikipedia(query)
    if wiki:
        results.append(wiki)
    if academic:
        sep = await _search_sep(query)
        if sep:
            results.append(sep)
    return "\n".join(results) if results else ""


async def _search_ddg(query: str) -> str:
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    answer = data.get("Answer", "")
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
    try:
        encoded = urllib.parse.quote(query)
        async with aiohttp.ClientSession() as session:
            url = f"https://pt.wikipedia.org/api/rest_v1/page/summary/{encoded}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    extract = data.get("extract", "")
                    page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
                    if extract and len(extract) > 50:
                        return f"[Wikipedia: {extract[:600]} | {page_url}]"
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


# ── Utilitário para baixar imagem e converter em base64 ───────

async def fetch_image_base64(url: str) -> tuple[str, str] | None:
    """Baixa imagem e retorna (base64, mime_type) ou None."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get("Content-Type", "image/png").split(";")[0]
                    data = await resp.read()
                    b64 = base64.b64encode(data).decode("utf-8")
                    return b64, content_type
    except Exception as e:
        logger.error(f"Erro ao baixar imagem: {e}")
    return None


class AIChat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # ── OpenRouter (primário) ─────────────────────────────
        self.openrouter_clients = []
        for var in ["OPENROUTER_API_KEY", "OPENROUTER_API_KEY_2", "OPENROUTER_API_KEY_3"]:
            key = os.getenv(var)
            if key:
                self.openrouter_clients.append(AsyncOpenAI(
                    api_key=key,
                    base_url="https://openrouter.ai/api/v1",
                ))
                logger.info(f"✅ {var} carregada ({key[:8]}...)")

        if self.openrouter_clients:
            logger.info(f"✅ {len(self.openrouter_clients)} chave(s) OpenRouter carregada(s)")
        else:
            logger.warning("⚠️ Nenhuma OPENROUTER_API_KEY encontrada — usando só Groq")

        self.or_index = 0  # índice da chave OpenRouter atual

        # ── Groq (fallback) ───────────────────────────────────
        self.groq_keys = []
        for var in ["GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3", "GROQ_API_KEY_4", "GROQ_API_KEY_5"]:
            key = os.getenv(var)
            if key:
                self.groq_keys.append(Groq(api_key=key))
                logger.info(f"✅ {var} carregada ({key[:8]}...)")

        if not self.groq_keys and not self.openrouter_clients:
            logger.error("❌ Nenhuma chave de API encontrada!")
        elif self.groq_keys:
            logger.info(f"✅ {len(self.groq_keys)} chave(s) Groq carregada(s) (fallback)")

        self.groq_key_index = 0   # índice da chave Groq atual
        self.groq_model_index = 0  # índice do modelo Groq atual

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

    async def _call_openrouter(self, messages: list, max_tokens: int, temperature: float,
                                image_data: tuple | None = None,
                                preferred_models: list | None = None) -> str | None:
        """Tenta chamar OpenRouter com os modelos ideais + fallback entre todos os modelos."""
        if not self.openrouter_clients:
            return None

        # Monta mensagens com imagem se necessário
        if image_data:
            b64, mime = image_data
            last_user = messages[-1]
            messages = messages[:-1] + [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    {"type": "text", "text": last_user["content"]}
                ]
            }]
            # Com imagem: só o modelo de visão funciona
            models_to_try = [OPENROUTER_MODELS["vision"]]
        else:
            # Começa pelos modelos ideais (em ordem), depois fallback pelos demais
            if preferred_models:
                seen = set(preferred_models)
                models_to_try = list(preferred_models) + [m for m in OPENROUTER_FALLBACK_ORDER if m not in seen]
            else:
                models_to_try = list(OPENROUTER_FALLBACK_ORDER)

        for model in models_to_try:
            # Tenta todas as chaves disponíveis para cada modelo
            for attempt in range(len(self.openrouter_clients)):
                client = self.openrouter_clients[self.or_index % len(self.openrouter_clients)]
                key_num = (self.or_index % len(self.openrouter_clients)) + 1
                try:
                    response = await client.chat.completions.create(
                        model=model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=0.9,
                        frequency_penalty=0.5,
                        presence_penalty=0.4,
                    )
                    logger.info(f"✅ OpenRouter respondeu via [{model}] chave #{key_num}")
                    return response.choices[0].message.content
                except Exception as e:
                    error = str(e).lower()
                    is_limit = "429" in error or "quota" in error or "rate" in error or "limit" in error
                    is_auth = "401" in error or "403" in error or "invalid" in error
                    is_unavailable = "503" in error or "unavailable" in error or "404" in error

                    if is_auth:
                        logger.error(f"OpenRouter chave #{key_num} inválida")
                        self.or_index += 1
                    elif is_limit:
                        logger.warning(f"OpenRouter chave #{key_num} com limite no modelo [{model}] — tentando próxima chave")
                        self.or_index += 1
                    elif is_unavailable:
                        logger.warning(f"Modelo [{model}] indisponível — tentando próximo modelo")
                        break  # vai para o próximo modelo
                    else:
                        logger.error(f"Erro OpenRouter #{key_num} [{model}]: {type(e).__name__}: {e}")
                        self.or_index += 1

        logger.warning("OpenRouter: todos os modelos e chaves falharam")
        return None

    async def _call_groq(self, messages: list, max_tokens: int, temperature: float) -> str | None:
        """Fallback para Groq com rotação de chaves E de modelos."""
        if not self.groq_keys:
            return None

        num_keys = len(self.groq_keys)
        num_models = len(GROQ_MODELS)

        # Tenta cada modelo Groq, e para cada modelo, todas as chaves
        for model_attempt in range(num_models):
            model = GROQ_MODELS[self.groq_model_index % num_models]

            for key_attempt in range(num_keys):
                client = self.groq_keys[self.groq_key_index % num_keys]
                key_num = (self.groq_key_index % num_keys) + 1
                try:
                    response = await asyncio.to_thread(
                        client.chat.completions.create,
                        model=model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=0.9,
                        frequency_penalty=0.6,
                        presence_penalty=0.4,
                    )
                    logger.info(f"✅ Groq respondeu via [{model}] chave #{key_num}")
                    return response.choices[0].message.content
                except Exception as e:
                    error = str(e).lower()
                    is_limit = "429" in error or "quota" in error or "rate" in error
                    is_model_error = "model" in error or "not found" in error or "invalid model" in error

                    if is_model_error:
                        logger.warning(f"Groq modelo [{model}] não disponível — tentando próximo")
                        break  # vai para o próximo modelo
                    elif is_limit:
                        logger.warning(f"Groq chave #{key_num} com limite no modelo [{model}] — tentando próxima chave")
                        self.groq_key_index += 1
                    else:
                        logger.error(f"Erro Groq #{key_num} [{model}]: {type(e).__name__}: {e}")
                        self.groq_key_index += 1

            # Passou para o próximo modelo
            self.groq_model_index += 1

        logger.error("Groq: todos os modelos e chaves falharam")
        return None

    async def get_ai_response(self, user_message: str, user_id: int, user_name: str,
                               image_data: tuple | None = None) -> str:
        history = self.conversation_history[user_id]
        if len(history) > 16:
            history = history[-16:]
            self.conversation_history[user_id] = history

        query_type = detect_query_type(user_message)
        academic = query_type in ("academic", "link")

        web_context = ""
        if query_type in ("news", "link"):
            web_context = await search_web(user_message, academic=False)

        full_message = f"[{user_name}]: {user_message}"
        if web_context:
            full_message += f"\n\n{web_context}"
        if academic:
            full_message += "\n\n[MODO ACADÊMICO ATIVO: responda com base em obras canônicas da área, cite autor e obra]"
        if image_data:
            full_message = user_message  # mensagem limpa para visão

        history.append({"role": "user", "content": full_message})

        last_style = self.last_response_style[user_id]
        anti_repeat = ""
        if last_style:
            anti_repeat = f"\n\nIMPORTANTE: Sua última resposta começou com '{last_style}'. Comece de forma COMPLETAMENTE diferente desta vez."

        max_tokens = 1500 if academic else 900
        temperature = 0.4 if academic else 0.9

        messages = [
            {"role": "system", "content": get_system_prompt() + anti_repeat}
        ] + history

        # Seleciona os melhores modelos para o tipo de mensagem (em ordem de prioridade)
        preferred_models = select_openrouter_models(query_type, has_image=image_data is not None)
        logger.info(f"Tipo detectado: [{query_type}] → modelos preferidos: {preferred_models}")

        # Tenta OpenRouter primeiro (com modelos ideais + fallback automático)
        reply = await self._call_openrouter(messages, max_tokens, temperature, image_data, preferred_models)

        # Fallback para Groq se OpenRouter falhou (Groq não suporta imagem)
        if reply is None and not image_data:
            logger.info("OpenRouter esgotado — usando Groq como fallback")
            reply = await self._call_groq(messages, max_tokens, temperature)

        if reply is None:
            if image_data:
                return "Não consegui analisar a imagem agora. Tenta de novo em instantes."
            return "Tô sobrecarregado agora, tenta de novo em alguns minutos."

        first_words = " ".join(reply.split()[:4])
        self.last_response_style[user_id] = first_words
        history.append({"role": "assistant", "content": reply})
        return reply

    async def parse_mod_command(self, message: str, mentions: list) -> dict:
        msg_with_ids = message
        for member in mentions:
            msg_with_ids = msg_with_ids.replace(
                f"<@{member.id}>", f"@{member.display_name} (ID:{member.id})"
            ).replace(
                f"<@!{member.id}>", f"@{member.display_name} (ID:{member.id})"
            )

        messages = [
            {"role": "system", "content": OWNER_MOD_PROMPT},
            {"role": "user", "content": msg_with_ids}
        ]

        try:
            # Tenta OpenRouter (modelo fast é suficiente pra JSON simples)
            text = await self._call_openrouter(
                messages, max_tokens=150, temperature=0.1,
                preferred_models=[OPENROUTER_MODELS["fast"]]
            )
            # Fallback Groq
            if text is None:
                text = await self._call_groq(messages, max_tokens=150, temperature=0.1)
            if text is None:
                return {"action": "none"}
            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except Exception as e:
            logger.error(f"Erro parse_mod: {e}")
            return {"action": "none"}

    async def execute_mod_action(self, message: discord.Message, cmd: dict) -> str:
        guild = message.guild
        action = cmd.get("action", "none")

        if action == "none":
            return ""

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

        # ── Comando de moderação ──────────────────────────────
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

        # ── Cooldown ──────────────────────────────────────────
        wait = self.check_cooldown(message.author.id)
        if wait > 0:
            await message.reply(f"Espera {wait:.1f}s antes de falar de novo.")
            return

        self.cooldowns[message.author.id] = datetime.now()

        # ── Processa conteúdo da mensagem ─────────────────────
        content = message.content
        for mention in message.mentions:
            content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        content = content.strip() or "Oi!"

        # Contexto de reply
        if is_reply_to_bot and message.reference and message.reference.resolved:
            original = message.reference.resolved
            original_content = original.content.strip()
            if original_content and len(original_content) > 10:
                truncated = original_content[:500] + ("..." if len(original_content) > 500 else "")
                content = f"[Contexto — sua resposta anterior foi: '{truncated}'] Agora o usuário diz: {content}"

        # ── Detecta imagem ────────────────────────────────────
        image_data = None
        if message.attachments:
            for att in message.attachments:
                if att.content_type and att.content_type.startswith("image/"):
                    image_data = await fetch_image_base64(att.url)
                    if image_data:
                        logger.info(f"Imagem detectada: {att.filename}")
                        break

        # ── Gera resposta ─────────────────────────────────────
        async with message.channel.typing():
            response = await self.get_ai_response(
                content, message.author.id, message.author.display_name, image_data
            )

        # ── Envia resposta (divide se necessário) ─────────────
        try:
            if len(response) > 1900:
                paragraphs = response.split("\n")
                chunk = ""
                first = True
                for para in paragraphs:
                    if len(chunk) + len(para) + 1 > 1900:
                        if first:
                            await message.reply(chunk)
                            first = False
                        else:
                            await message.channel.send(chunk)
                        chunk = para
                    else:
                        chunk = chunk + "\n" + para if chunk else para
                if chunk:
                    if first:
                        await message.reply(chunk)
                    else:
                        await message.channel.send(chunk)
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

    @app_commands.command(name="status-ia", description="📊 Mostra status das chaves de IA [ADMIN]")
    @app_commands.default_permissions(administrator=True)
    async def status_ia(self, interaction: discord.Interaction):
        or_count = len(self.openrouter_clients)
        groq_count = len(self.groq_keys)
        embed = discord.Embed(title="📊 Status das IAs", color=discord.Color.purple())
        embed.add_field(name="OpenRouter (primário)", value=f"{'✅' if or_count > 0 else '❌'} {or_count} chave(s)", inline=True)
        embed.add_field(name="Groq (fallback)", value=f"{'✅' if groq_count > 0 else '❌'} {groq_count} chave(s)", inline=True)
        embed.add_field(name="Visão (imagens)", value="✅ Ativo" if or_count > 0 else "❌ Inativo", inline=True)

        # Detalhes dos modelos
        model_info = "\n".join([
            f"🖼️ Visão: `{OPENROUTER_MODELS['vision'].split('/')[1]}`",
            f"💬 Chat: `{OPENROUTER_MODELS['chat'].split('/')[1]}`",
            f"🧠 Acadêmico: `{OPENROUTER_MODELS['academic'].split('/')[1]}`",
            f"💻 Código: `{OPENROUTER_MODELS['code'].split('/')[1]}`",
            f"⚡ Rápido: `{OPENROUTER_MODELS['fast'].split('/')[1]}`",
        ])
        embed.add_field(name="Modelos OpenRouter", value=model_info, inline=False)

        groq_info = "\n".join([f"`{m}`" for m in GROQ_MODELS])
        embed.add_field(name="Modelos Groq (fallback)", value=groq_info, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(AIChat(bot))
