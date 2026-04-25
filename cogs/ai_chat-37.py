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

# ══════════════════════════════════════════════════════════════
#  ARQUITETURA DE PROVEDORES
#
#  Chat normal:    Cerebras (qwen-3-235b) → Groq → OpenRouter texto
#  Acadêmico:      OpenRouter (deepseek-r1-0528:free) → Cerebras → Groq
#  Imagem:         OpenRouter (nemotron visão)
#  SambaNova:      disponível apenas no /modelos para teste manual
#
#  Modelos duplicados (Cerebras + OpenRouter) ficam ativos nos dois
#  provedores — se um acabar, o outro assume automaticamente.
#
#  Busca web: Tavily → GNews → Wikipedia → DuckDuckGo (fallback)
#
#  Configure no Railway:
#    CEREBRAS_API_KEY, CEREBRAS_API_KEY_2 … CEREBRAS_API_KEY_5
#    GROQ_API_KEY, GROQ_API_KEY_2 … GROQ_API_KEY_5
#    OPENROUTER_API_KEY, OPENROUTER_API_KEY_2, OPENROUTER_API_KEY_3
#    SAMBANOVA_API_KEY  (opcional, só para /modelos)
#    TAVILY_API_KEY     → https://app.tavily.com
#    GNEWS_API_KEY      → https://gnews.io
# ══════════════════════════════════════════════════════════════

# ── Modelos Groq ──────────────────────────────────────────────
# Documentação: https://console.groq.com/docs/models
# Groq é o fallback geral após Cerebras
GROQ_MODELS = [
    "llama-3.3-70b-versatile",   # principal — muito capaz, contexto longo
    "llama3-70b-8192",            # alternativa sólida
    "gemma2-9b-it",               # leve e rápido
    "mixtral-8x7b-32768",         # bom contexto longo
    "llama3-8b-8192",             # mais leve, última opção
]

# ── Modelos Cerebras ──────────────────────────────────────────
# Documentação: https://inference-docs.cerebras.ai/models/overview
# Cerebras tem hardware próprio (WSE-3) — latência extremamente baixa
# qwen-3-235b é o modelo principal para chat normal
# gpt-oss-120b fica ativo pois também existe no OpenRouter (redundância cruzada)
CEREBRAS_MODELS = [
    "qwen-3-235b-a22b-instruct-2507",    # Qwen 3 235B — principal, excelente raciocínio
    "gpt-oss-120b",                       # GPT OSS 120B — também no OpenRouter (redundância)
    "zai-glm-4.7",                        # GLM 4.7 355B — preview, muito capaz
    "llama3.1-8b",                        # 8B — leve, último recurso
]

# ── Modelos SambaNova ─────────────────────────────────────────
# Documentação: https://docs.sambanova.ai/docs/en/models/sambacloud-models
# SambaNova usa RDUs — muito rápido para modelos grandes
SAMBANOVA_MODELS = [
    "DeepSeek-V3.1",                     # DeepSeek V3.1 671B — principal, muito capaz
    "Meta-Llama-3.3-70B-Instruct",       # Llama 3.3 70B — robusto e rápido
    "gpt-oss-120b",                       # GPT-OSS 120B da OpenAI — ótimo raciocínio
    "MiniMax-M2.5",                       # MiniMax M2.5 — contexto longo (160k)
]

# ── Modelos OpenRouter ────────────────────────────────────────
# deepseek-r1-0528 é o modelo acadêmico principal
# gpt-oss-120b e llama-3.3-70b também existem no Cerebras/Groq (redundância cruzada)
OPENROUTER_MODELS = {
    "vision":    "nvidia/nemotron-nano-12b-v2-vl:free",
    "academic":  "deepseek/deepseek-r1-0528:free",          # DeepSeek R1 — raciocínio profundo
    "academic2": "deepseek/deepseek-r1:free",               # R1 original como backup acadêmico
    "chat":      "openai/gpt-oss-120b:free",                # também no Cerebras (redundância)
    "chat2":     "meta-llama/llama-3.3-70b-instruct:free",  # também no Groq (redundância)
    "code":      "qwen/qwen3-coder:free",
    "fast":      "inclusionai/ling-2.6-flash:free",
}

OPENROUTER_FALLBACK_ORDER = [
    "deepseek/deepseek-r1-0528:free",
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-r1:free",
    "inclusionai/ling-2.6-flash:free",
    "qwen/qwen3-coder:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
]

# Modelos OpenRouter específicos para modo acadêmico (waterfall)
OPENROUTER_ACADEMIC_ORDER = [
    "deepseek/deepseek-r1-0528:free",
    "deepseek/deepseek-r1:free",
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
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
- Mencionar que é IA, Groq, Cerebras, SambaNova, OpenRouter, LLaMA ou qualquer tecnologia — você é o RevolutX
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
    "notícia", "notícias", "aconteceu", "acontecendo", "lançou", "estreou", "morreu", "nasceu",
    "campeão", "copa", "oscar", "grammy", "premio", "guerra", "ataque",
    "semana", "mês", "ano", "sábado", "domingo", "segunda", "terça", "quarta", "quinta", "sexta",
    "resultado", "placar", "jogo", "partida", "classificou", "eliminou",
    "tendência", "viral", "bombando", "tá rolando",
]

# Pedidos explícitos de busca — sempre vai pesquisar independente do tema
SEARCH_REQUEST_KEYWORDS = [
    "pesquisa", "pesquise", "pesquisar", "busca", "busque", "buscar",
    "procura", "procure", "procurar", "googla", "googlar",
    "o que tá", "o que está", "o que aconteceu", "o que rolou",
    "me fala sobre", "me conta sobre", "o que é", "quem é",
    "me diz", "descobre", "descubra",
]

LINK_KEYWORDS = [
    "artigo", "link", "fonte", "wikipedia", "referência", "me manda",
    "me passa", "me indica", "onde posso ler", "onde encontro", "sep",
    "stanford", "philpapers", "pubmed", "arxiv", "site", "leitura"
]


def detect_query_type(message: str) -> str:
    msg = message.lower()
    # Pedido explícito de busca → sempre trata como news (vai pesquisar)
    if any(k in msg for k in SEARCH_REQUEST_KEYWORDS):
        return "news"
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
    """Retorna lista de modelos OpenRouter em ordem de preferência."""
    if has_image:
        return [OPENROUTER_MODELS["vision"]]
    if query_type in ("academic", "link"):
        return OPENROUTER_ACADEMIC_ORDER
    if query_type == "news":
        return [OPENROUTER_MODELS["academic"], OPENROUTER_MODELS["chat"], OPENROUTER_MODELS["chat2"]]
    if query_type == "code":
        return [OPENROUTER_MODELS["code"], OPENROUTER_MODELS["chat"]]
    return [OPENROUTER_MODELS["chat"], OPENROUTER_MODELS["chat2"]]


def needs_web_search(message: str) -> bool:
    return detect_query_type(message) in ("news", "link")


def is_academic(message: str) -> bool:
    return detect_query_type(message) in ("academic", "link")


# ── Busca na web ──────────────────────────────────────────────

# ── Busca na web ──────────────────────────────────────────────

async def search_web(query: str, academic: bool = False) -> str:
    """Busca em camadas: Tavily → GNews → Wikipedia → DuckDuckGo (fallback)."""
    results = []

    # Camada 1: Tavily (consultas gerais e acadêmicas — já formatado pra LLM)
    tavily = await _search_tavily(query)
    if tavily:
        results.append(tavily)

    # Camada 2: GNews (notícias e atualidades)
    gnews = await _search_gnews(query)
    if gnews:
        results.append(gnews)

    # Camada 3: Wikipedia (conceitos e contexto)
    wiki = await _search_wikipedia(query)
    if wiki:
        results.append(wiki)

    # Camada 4: DuckDuckGo (fallback sem chave, sem limite)
    if not results:
        ddg = await _search_ddg(query)
        if ddg:
            results.append(ddg)

    # Camada extra: SEP para consultas acadêmicas
    if academic:
        sep = await _search_sep(query)
        if sep:
            results.append(sep)

    return "\n".join(results) if results else ""


async def _search_tavily(query: str) -> str:
    """Tavily — busca geral otimizada para LLMs."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return ""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": 3,
                "include_answer": True,
            }
            async with session.post(
                "https://api.tavily.com/search",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=6)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    answer = data.get("answer", "")
                    results = data.get("results", [])
                    parts = []
                    if answer:
                        parts.append(f"[Tavily resumo: {answer[:400]}]")
                    for r in results[:2]:
                        title = r.get("title", "")
                        content = r.get("content", "")
                        url = r.get("url", "")
                        if content:
                            parts.append(f"[Tavily — {title}: {content[:300]} | {url}]")
                    return "\n".join(parts) if parts else ""
        return ""
    except Exception:
        return ""


async def _search_gnews(query: str) -> str:
    """GNews — notícias e atualidades."""
    api_key = os.getenv("GNEWS_API_KEY")
    if not api_key:
        return ""
    try:
        encoded = urllib.parse.quote(query)
        url = (
            f"https://gnews.io/api/v4/search"
            f"?q={encoded}&lang=pt&max=3&token={api_key}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    articles = data.get("articles", [])
                    parts = []
                    for art in articles[:2]:
                        title = art.get("title", "")
                        desc = art.get("description", "")
                        src_url = art.get("url", "")
                        pub = art.get("publishedAt", "")[:10]
                        if title:
                            parts.append(f"[GNews ({pub}) — {title}: {desc[:250]} | {src_url}]")
                    return "\n".join(parts) if parts else ""
        return ""
    except Exception:
        return ""


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

        # ── 1. Groq (primário) — 5 chaves, rotação automática ──
        # Pegue suas chaves em: https://console.groq.com/keys
        self.groq_keys = []
        for var in ["GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3", "GROQ_API_KEY_4", "GROQ_API_KEY_5"]:
            key = os.getenv(var)
            if key:
                self.groq_keys.append(Groq(api_key=key))
                logger.info(f"✅ {var} carregada ({key[:8]}...)")

        if self.groq_keys:
            logger.info(f"✅ {len(self.groq_keys)} chave(s) Groq carregada(s)")
        else:
            logger.warning("⚠️ Nenhuma GROQ_API_KEY encontrada")

        self.groq_key_index = 0
        self.groq_model_index = 0

        # ── 2. Cerebras (secundário) — 5 chaves, rotação automática ──
        # Pegue suas chaves em: https://cloud.cerebras.ai/ → Settings > API Keys
        self.cerebras_clients = []
        for var in ["CEREBRAS_API_KEY", "CEREBRAS_API_KEY_2", "CEREBRAS_API_KEY_3", "CEREBRAS_API_KEY_4", "CEREBRAS_API_KEY_5"]:
            key = os.getenv(var)
            if key:
                # Cerebras é compatível com a API OpenAI
                self.cerebras_clients.append(AsyncOpenAI(
                    api_key=key,
                    base_url="https://api.cerebras.ai/v1",
                ))
                logger.info(f"✅ {var} carregada ({key[:8]}...)")

        if self.cerebras_clients:
            logger.info(f"✅ {len(self.cerebras_clients)} chave(s) Cerebras carregada(s)")
        else:
            logger.warning("⚠️ Nenhuma CEREBRAS_API_KEY encontrada")

        self.cerebras_key_index = 0
        self.cerebras_model_index = 0

        # ── 3. SambaNova (terciário) — 5 chaves, rotação automática ──
        # Pegue suas chaves em: https://cloud.sambanova.ai/ → API > API Keys
        self.sambanova_clients = []
        for var in ["SAMBANOVA_API_KEY", "SAMBANOVA_API_KEY_2", "SAMBANOVA_API_KEY_3", "SAMBANOVA_API_KEY_4", "SAMBANOVA_API_KEY_5"]:
            key = os.getenv(var)
            if key:
                # SambaNova é compatível com a API OpenAI
                self.sambanova_clients.append(AsyncOpenAI(
                    api_key=key,
                    base_url="https://api.sambanova.ai/v1",
                ))
                logger.info(f"✅ {var} carregada ({key[:8]}...)")

        if self.sambanova_clients:
            logger.info(f"✅ {len(self.sambanova_clients)} chave(s) SambaNova carregada(s)")
        else:
            logger.warning("⚠️ Nenhuma SAMBANOVA_API_KEY encontrada")

        self.sambanova_key_index = 0
        self.sambanova_model_index = 0

        # ── 4. OpenRouter (fallback extra, opcional) ──────────
        # Pegue suas chaves em: https://openrouter.ai/keys
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

        self.or_index = 0

        # Verifica se há ao menos um provedor disponível
        has_any = any([self.groq_keys, self.cerebras_clients, self.sambanova_clients, self.openrouter_clients])
        if not has_any:
            logger.error("❌ Nenhuma chave de API encontrada! Configure ao menos GROQ_API_KEY no Railway.")

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

    # ══════════════════════════════════════════════════════════
    #  CHAMADAS AOS PROVEDORES
    # ══════════════════════════════════════════════════════════

    async def _call_groq(self, messages: list, max_tokens: int, temperature: float) -> str | None:
        """Groq — primário. Rotação de chaves E modelos."""
        if not self.groq_keys:
            return None

        num_keys = len(self.groq_keys)
        num_models = len(GROQ_MODELS)

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
                        logger.warning(f"Groq modelo [{model}] não disponível — próximo modelo")
                        break
                    elif is_limit:
                        logger.warning(f"Groq chave #{key_num} com limite [{model}] — próxima chave")
                        self.groq_key_index += 1
                    else:
                        logger.error(f"Erro Groq #{key_num} [{model}]: {type(e).__name__}: {e}")
                        self.groq_key_index += 1

            self.groq_model_index += 1

        logger.warning("Groq: todos os modelos e chaves falharam")
        return None

    async def _call_cerebras(self, messages: list, max_tokens: int, temperature: float) -> str | None:
        """Cerebras — secundário. Latência ultra-baixa (hardware WSE-3 próprio)."""
        if not self.cerebras_clients:
            return None

        num_keys = len(self.cerebras_clients)
        num_models = len(CEREBRAS_MODELS)

        for model_attempt in range(num_models):
            model = CEREBRAS_MODELS[self.cerebras_model_index % num_models]

            for key_attempt in range(num_keys):
                client = self.cerebras_clients[self.cerebras_key_index % num_keys]
                key_num = (self.cerebras_key_index % num_keys) + 1
                try:
                    response = await client.chat.completions.create(
                        model=model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=0.9,
                    )
                    logger.info(f"✅ Cerebras respondeu via [{model}] chave #{key_num}")
                    return response.choices[0].message.content
                except Exception as e:
                    error = str(e).lower()
                    is_limit = "429" in error or "quota" in error or "rate" in error
                    is_model_error = "model" in error or "not found" in error or "invalid" in error

                    if is_model_error:
                        logger.warning(f"Cerebras modelo [{model}] não disponível — próximo modelo")
                        break
                    elif is_limit:
                        logger.warning(f"Cerebras chave #{key_num} com limite [{model}] — próxima chave")
                        self.cerebras_key_index += 1
                    else:
                        logger.error(f"Erro Cerebras #{key_num} [{model}]: {type(e).__name__}: {e}")
                        self.cerebras_key_index += 1

            self.cerebras_model_index += 1

        logger.warning("Cerebras: todos os modelos e chaves falharam")
        return None

    async def _call_sambanova(self, messages: list, max_tokens: int, temperature: float) -> str | None:
        """SambaNova — terciário. Roda modelos enormes (405B) no free tier."""
        if not self.sambanova_clients:
            return None

        num_keys = len(self.sambanova_clients)
        num_models = len(SAMBANOVA_MODELS)

        for model_attempt in range(num_models):
            model = SAMBANOVA_MODELS[self.sambanova_model_index % num_models]

            for key_attempt in range(num_keys):
                client = self.sambanova_clients[self.sambanova_key_index % num_keys]
                key_num = (self.sambanova_key_index % num_keys) + 1
                try:
                    response = await client.chat.completions.create(
                        model=model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=0.9,
                    )
                    logger.info(f"✅ SambaNova respondeu via [{model}] chave #{key_num}")
                    return response.choices[0].message.content
                except Exception as e:
                    error = str(e).lower()
                    is_limit = "429" in error or "quota" in error or "rate" in error
                    is_model_error = "model" in error or "not found" in error or "invalid" in error

                    if is_model_error:
                        logger.warning(f"SambaNova modelo [{model}] não disponível — próximo modelo")
                        break
                    elif is_limit:
                        logger.warning(f"SambaNova chave #{key_num} com limite [{model}] — próxima chave")
                        self.sambanova_key_index += 1
                    else:
                        logger.error(f"Erro SambaNova #{key_num} [{model}]: {type(e).__name__}: {e}")
                        self.sambanova_key_index += 1

            self.sambanova_model_index += 1

        logger.warning("SambaNova: todos os modelos e chaves falharam")
        return None

    async def _call_openrouter(self, messages: list, max_tokens: int, temperature: float,
                                image_data: tuple | None = None,
                                preferred_models: list | None = None) -> str | None:
        """OpenRouter — fallback extra (também o único com suporte a imagem)."""
        if not self.openrouter_clients:
            return None

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
            models_to_try = [OPENROUTER_MODELS["vision"]]
        else:
            if preferred_models:
                seen = set(preferred_models)
                models_to_try = list(preferred_models) + [m for m in OPENROUTER_FALLBACK_ORDER if m not in seen]
            else:
                models_to_try = list(OPENROUTER_FALLBACK_ORDER)

        for model in models_to_try:
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
                        logger.warning(f"OpenRouter chave #{key_num} com limite [{model}] — próxima chave")
                        self.or_index += 1
                    elif is_unavailable:
                        logger.warning(f"Modelo [{model}] indisponível — próximo modelo")
                        break
                    else:
                        logger.error(f"Erro OpenRouter #{key_num} [{model}]: {type(e).__name__}: {e}")
                        self.or_index += 1

        logger.warning("OpenRouter: todos os modelos e chaves falharam")
        return None

    # ══════════════════════════════════════════════════════════
    #  ORQUESTRADOR — Waterfall inteligente por tipo de query
    #
    #  Imagem:    OpenRouter (único com visão no free tier)
    #  Acadêmico: OpenRouter (DeepSeek R1) → Cerebras → Groq
    #  Chat/Code: Cerebras (qwen-3-235b) → Groq → OpenRouter
    #
    #  Modelos duplicados (ex: gpt-oss-120b no Cerebras e OpenRouter,
    #  llama-3.3-70b no Groq e OpenRouter) ficam ativos em ambos —
    #  se um provedor acabar, o outro ainda tem o mesmo modelo.
    # ══════════════════════════════════════════════════════════

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

        full_message = user_message
        if web_context:
            full_message += f"\n\n{web_context}"
        elif query_type in ("news", "link"):
            # Busca não retornou nada — instrui o modelo a NÃO inventar
            full_message += (
                "\n\n[AVISO IMPORTANTE: a busca na internet não retornou resultados para essa pergunta. "
                "NÃO invente notícias, datas, resultados ou fatos. "
                "Informe ao usuário que não conseguiu encontrar informações atualizadas e peça para ele verificar em uma fonte confiável.]"
            )
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

        reply = None

        if image_data:
            # Imagem: só OpenRouter tem suporte no free tier
            logger.info("Imagem detectada → usando OpenRouter (visão)")
            preferred = select_openrouter_models(query_type, has_image=True)
            reply = await self._call_openrouter(messages, max_tokens, temperature, image_data, preferred)

        elif academic:
            # Acadêmico/raciocínio: OpenRouter (DeepSeek R1) → Cerebras → Groq
            logger.info(f"Tipo detectado: [{query_type}] → waterfall OpenRouter (R1) → Cerebras → Groq")

            preferred = select_openrouter_models(query_type, has_image=False)
            reply = await self._call_openrouter(messages, max_tokens, temperature, None, preferred)

            if reply is None:
                logger.info("OpenRouter (acadêmico) esgotado — tentando Cerebras")
                reply = await self._call_cerebras(messages, max_tokens, temperature)

            if reply is None:
                logger.info("Cerebras esgotado — usando Groq (reserva acadêmica)")
                reply = await self._call_groq(messages, max_tokens, temperature)

        else:
            # Chat normal / código / notícias: Cerebras → Groq → OpenRouter
            logger.info(f"Tipo detectado: [{query_type}] → waterfall Cerebras → Groq → OpenRouter")

            reply = await self._call_cerebras(messages, max_tokens, temperature)

            if reply is None:
                logger.info("Cerebras esgotado — tentando Groq")
                reply = await self._call_groq(messages, max_tokens, temperature)

            if reply is None:
                logger.info("Groq esgotado — tentando OpenRouter (último recurso)")
                preferred = select_openrouter_models(query_type, has_image=False)
                reply = await self._call_openrouter(messages, max_tokens, temperature, None, preferred)

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
            # Usa Cerebras primeiro, depois Groq, depois OpenRouter
            text = await self._call_cerebras(messages, max_tokens=150, temperature=0.1)
            if text is None:
                text = await self._call_groq(messages, max_tokens=150, temperature=0.1)
            if text is None:
                text = await self._call_openrouter(
                    messages, max_tokens=150, temperature=0.1,
                    preferred_models=[OPENROUTER_MODELS["fast"]]
                )
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

    @app_commands.command(name="chat", description="Conversa com a IA do bot.")
    @app_commands.describe(mensagem="Mensagem que deseja enviar para a IA.")
    async def chat_command(self, interaction: discord.Interaction, mensagem: str):
        await interaction.response.defer()
        response = await self.get_ai_response(mensagem, interaction.user.id, interaction.user.display_name)
        embed = discord.Embed(description=response, color=discord.Color.purple())
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="limpar-conversa", description="Apaga o histórico da sua conversa com a IA.")
    async def clear_history(self, interaction: discord.Interaction):
        self.conversation_history[interaction.user.id] = []
        self.last_response_style[interaction.user.id] = ""
        embed = discord.Embed(
            description="Seu histórico de conversa foi apagado.",
            color=discord.Color.purple()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="canal-ia", description="Ativa ou desativa a IA automática em um canal.")
    @app_commands.describe(canal="Canal que deseja configurar.")
    @app_commands.default_permissions(administrator=True)
    async def set_ai_channel(self, interaction: discord.Interaction, canal: discord.TextChannel):
        ai_channels = await self._get_ai_channels(interaction.guild_id)
        adding = canal.id not in ai_channels
        await self._set_ai_channel(interaction.guild_id, canal.id, adding)
        status = "ativada" if adding else "desativada"
        embed = discord.Embed(
            description=f"IA {status} no canal {canal.mention}.",
            color=discord.Color.purple()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="status-ia", description="Exibe o status atual dos provedores de IA.")
    @app_commands.default_permissions(administrator=True)
    async def status_ia(self, interaction: discord.Interaction):
        groq_count = len(self.groq_keys)
        cerebras_count = len(self.cerebras_clients)
        sambanova_count = len(self.sambanova_clients)
        or_count = len(self.openrouter_clients)

        def status(count): return f"{'Ativo' if count > 0 else 'Inativo'} — {count} chave(s)"

        embed = discord.Embed(title="Status dos provedores de IA", color=discord.Color.purple())
        embed.add_field(name="Cerebras (chat principal)", value=status(cerebras_count), inline=True)
        embed.add_field(name="Groq (fallback geral)", value=status(groq_count), inline=True)
        embed.add_field(name="OpenRouter (acadêmico + visão)", value=status(or_count), inline=True)
        embed.add_field(
            name="SambaNova (só /modelos)",
            value=f"{'Disponível' if sambanova_count > 0 else 'Sem chave'} — {sambanova_count} chave(s)",
            inline=True
        )
        embed.add_field(
            name="Visão (imagens)",
            value="Ativo via OpenRouter" if or_count > 0 else "Inativo — sem chave OpenRouter",
            inline=True
        )
        embed.add_field(
            name="Busca web",
            value=(
                f"Tavily: {'✅' if os.getenv('TAVILY_API_KEY') else '❌'} | "
                f"GNews: {'✅' if os.getenv('GNEWS_API_KEY') else '❌'} | "
                f"Wikipedia + DDG: ✅"
            ),
            inline=False
        )

        embed.add_field(
            name="Fluxo — Chat normal",
            value="Cerebras → Groq → OpenRouter",
            inline=False
        )
        embed.add_field(
            name="Fluxo — Acadêmico/Raciocínio",
            value="OpenRouter (DeepSeek R1) → Cerebras → Groq",
            inline=False
        )

        embed.add_field(name="Modelos — Cerebras", value="\n".join([f"`{m}`" for m in CEREBRAS_MODELS]), inline=False)
        embed.add_field(name="Modelos — Groq", value="\n".join([f"`{m}`" for m in GROQ_MODELS]), inline=False)
        embed.add_field(
            name="Modelos — OpenRouter",
            value="\n".join([f"`{m}`" for m in OPENROUTER_ACADEMIC_ORDER + [OPENROUTER_MODELS['vision'], OPENROUTER_MODELS['code']]]),
            inline=False
        )

        embed.set_footer(text="Redundância cruzada: gpt-oss-120b (Cerebras+OpenRouter) | llama-3.3-70b (Groq+OpenRouter)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="modelos", description="Testa um provedor e modelo de IA específico.")
    @app_commands.describe(
        provedor="Provedor a ser testado.",
        modelo="Nome exato do modelo. Se não informado, usa o primeiro da lista.",
        mensagem="Mensagem de teste. Se não informada, usa uma mensagem padrão."
    )
    @app_commands.choices(provedor=[
        app_commands.Choice(name="Cerebras", value="cerebras"),
        app_commands.Choice(name="SambaNova", value="sambanova"),
        app_commands.Choice(name="Groq", value="groq"),
        app_commands.Choice(name="OpenRouter", value="openrouter"),
    ])
    async def modelos(self, interaction: discord.Interaction, provedor: str,
                      modelo: str = "", mensagem: str = "Responda apenas: funcionando."):
        await interaction.response.defer(ephemeral=True)

        messages = [
            {"role": "system", "content": "Você é um assistente de teste. Responda de forma curta."},
            {"role": "user", "content": mensagem}
        ]

        modelo_usado = modelo or "(padrão da lista)"
        resultado = ""

        try:
            if provedor == "cerebras":
                if not self.cerebras_clients:
                    resultado = "Nenhuma chave Cerebras configurada."
                else:
                    client = self.cerebras_clients[0]
                    m = modelo or CEREBRAS_MODELS[0]
                    resp = await client.chat.completions.create(
                        model=m, messages=messages, max_tokens=100, temperature=0.5
                    )
                    resultado = f"**Cerebras** — `{m}`\n\n{resp.choices[0].message.content}"

            elif provedor == "sambanova":
                if not self.sambanova_clients:
                    resultado = "Nenhuma chave SambaNova configurada."
                else:
                    client = self.sambanova_clients[0]
                    m = modelo or SAMBANOVA_MODELS[0]
                    resp = await client.chat.completions.create(
                        model=m, messages=messages, max_tokens=100, temperature=0.5
                    )
                    resultado = f"**SambaNova** — `{m}`\n\n{resp.choices[0].message.content}"

            elif provedor == "groq":
                if not self.groq_keys:
                    resultado = "Nenhuma chave Groq configurada."
                else:
                    client = self.groq_keys[0]
                    m = modelo or GROQ_MODELS[0]
                    resp = await asyncio.to_thread(
                        client.chat.completions.create,
                        model=m, messages=messages, max_tokens=100, temperature=0.5
                    )
                    resultado = f"**Groq** — `{m}`\n\n{resp.choices[0].message.content}"

            elif provedor == "openrouter":
                if not self.openrouter_clients:
                    resultado = "Nenhuma chave OpenRouter configurada."
                else:
                    client = self.openrouter_clients[0]
                    m = modelo or OPENROUTER_MODELS["chat"]
                    resp = await client.chat.completions.create(
                        model=m, messages=messages, max_tokens=100, temperature=0.5
                    )
                    resultado = f"**OpenRouter** — `{m}`\n\n{resp.choices[0].message.content}"

        except Exception as e:
            resultado = f"Erro ao testar **{provedor}** com o modelo `{modelo_usado}`.\n\n`{type(e).__name__}: {e}`"

        embed = discord.Embed(title="Teste de modelo", description=resultado, color=discord.Color.purple())
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(AIChat(bot))
