from __future__ import annotations
import asyncio
import base64
import json
import logging
import os
import re
import time
import urllib.parse
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

try:
    import google.generativeai as genai
    from google.generativeai.types import HarmCategory, HarmBlockThreshold
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    GENAI_AVAILABLE = False

from utils.database import db

logger = logging.getLogger("AIChat")


def _safe_int(value: str | None, default: int) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


BOT_NAME = os.getenv("BOT_NAME", "Revolutx")
OWNER_NAME = os.getenv("OWNER_NAME", "Isabelle")
OWNER_ID = _safe_int(os.getenv("OWNER_ID"), 1317406607776288872)

# ── Gemini model ───────────────────────────────────────────────────────────────
GEMINI_MODEL          = os.getenv("GEMINI_MODEL",          "gemini-2.5-flash")

# ── Groq model (gatilho acadêmico) ──────────────────────────────────────────────
GROQ_ACADEMIC_MODEL  = os.getenv("GROQ_ACADEMIC_MODEL", "openai/gpt-oss-120b")
GROQ_API_URL          = "https://api.groq.com/openai/v1/chat/completions"

MAX_HISTORY_MESSAGES = _safe_int(os.getenv("AI_MAX_HISTORY"), 14)
# Limites maiores reduzem respostas cortadas. Variáveis já existentes no Railway
# continuam tendo prioridade sobre estes valores-padrão.
NORMAL_MAX_TOKENS    = _safe_int(os.getenv("AI_NORMAL_MAX_TOKENS"), 1300)
ACADEMIC_MAX_TOKENS  = _safe_int(os.getenv("AI_ACADEMIC_MAX_TOKENS"), 2400)
DEEP_MAX_TOKENS      = _safe_int(os.getenv("AI_DEEP_MAX_TOKENS"), 3200)
MAX_CONTINUATIONS    = max(0, min(_safe_int(os.getenv("AI_MAX_CONTINUATIONS"), 2), 4))
COOLDOWN_SECONDS     = _safe_int(os.getenv("AI_COOLDOWN_SECONDS"), 2)
MODEL_TIMEOUT_SECONDS = float(os.getenv("AI_MODEL_TIMEOUT", "45"))
DISCORD_TEXT_LIMIT   = 1900
DISCORD_EMBED_LIMIT  = 3900
KNOWLEDGE_CUTOFF_LABEL = "janeiro de 2025"

ACADEMIC_SEARCH_ENABLED = os.getenv("ACADEMIC_SEARCH_ENABLED", "true").lower() not in {"0", "false", "no", "off"}
AI_DB_EXTRAS_ENABLED    = os.getenv("AI_DB_EXTRAS_ENABLED",    "true").lower() not in {"0", "false", "no", "off"}
AI_BUTTONS_ENABLED      = os.getenv("AI_BUTTONS_ENABLED",      "true").lower() not in {"0", "false", "no", "off"}
AI_FAKE_TYPING_ENABLED  = os.getenv("AI_FAKE_TYPING_ENABLED",  "true").lower() not in {"0", "false", "no", "off"}

ACADEMIC_KEYWORDS = {
    "filosofia", "lógica", "logica", "argumento", "silogismo", "falácia", "falacia",
    "epistemologia", "ontologia", "metafísica", "metafisica", "ética", "etica", "moral",
    "platão", "platao", "aristóteles", "aristoteles", "sócrates", "socrates", "kant",
    "hegel", "nietzsche", "descartes", "hume", "locke", "rousseau", "marx", "wittgenstein",
    "leibniz", "spinoza", "quine", "kripke", "heidegger", "sartre", "camus", "foucault",
    "dedução", "deducao", "indução", "inducao", "axioma", "premissa", "conclusão", "conclusao",
    "matemática", "matematica", "teorema", "prova", "demonstração", "demonstracao",
    "álgebra", "algebra", "cálculo", "calculo", "equação", "equacao", "integral",
    "derivada", "matriz", "vetor", "conjunto", "função", "funcao", "limite", "estatística",
    "estatistica", "geometria", "topologia", "probabilidade", "física", "fisica", "química",
    "quimica", "biologia", "neurociência", "neurociencia", "história", "historia", "sociologia",
    "psicologia", "economia", "tese", "dissertação", "dissertacao", "fichamento", "abnt",
}

CODE_KEYWORDS = {
    "python", "javascript", "typescript", "java", "c++", "cpp", "c#", "rust", "go", "discord.py",
    "bot", "cog", "slash command", "api", "endpoint", "async", "await", "github", "railway",
    "deploy", "postgres", "postgresql", "sql", "erro", "bug", "debug", "refatorar", "stack trace",
    "exception", "traceback", "docker", "requirements", "pip", "npm", "webhook", "json", "regex",
}

CURRENT_INFO_KEYWORDS = {
    "hoje", "agora", "atual", "atuais", "recente", "recentes", "último", "ultima", "última",
    "notícia", "noticias", "notícias", "aconteceu", "lançou", "lancou", "morreu", "campeão",
    "campeao", "placar", "resultado", "preço", "preco", "versão", "versao", "2025", "2026",
    "cotação", "cotacao", "dólar", "dolar", "euro", "agenda", "data de lançamento",
    "presidente atual", "governador atual", "prefeito atual", "ceo atual", "diretor atual",
    "quem é o presidente", "quem é a presidente", "quem ganhou", "ranking", "temporada atual",
    "última atualização", "ultima atualização", "estado atual", "disponível hoje", "disponivel hoje",
}

FACTUAL_QUESTION_PREFIXES = (
    "quem é ", "quem foi ", "o que é ", "o que foi ", "qual é ", "qual foi ",
    "quando foi ", "quando aconteceu ", "onde fica ", "onde aconteceu ",
    "fale sobre ", "me fale sobre ", "explique ", "me explique ", "conte sobre ",
)

SEARCH_REQUEST_KEYWORDS = {
    "pesquise", "pesquisa", "procure", "busque", "busca", "googla", "fonte", "fontes", "link",
    "artigo", "paper", "sep", "stanford", "philpapers", "arxiv", "wikipedia", "onde posso ler",
}

WRITING_KEYWORDS = {
    "escreva", "reescreva", "melhore", "melhorar texto", "corrija", "corrigir", "resuma", "resumir",
    "traduza", "traduzir", "email", "mensagem", "anúncio", "anuncio", "roteiro", "post", "copy",
}

PLANNING_KEYWORDS = {
    "plano", "planeje", "planejar", "cronograma", "rotina", "agenda", "checklist", "organize", "organizar",
    "estratégia", "estrategia", "roadmap", "passo a passo", "tarefas", "estudar em", "dias",
}

CREATIVE_KEYWORDS = {
    "história", "historia", "personagem", "rpg", "narração", "narracao", "ideias", "criativo", "nome para",
    "slogan", "meme", "poema", "conto", "lore", "universo", "campanha",
}

MODERATION_KEYWORDS = {
    "ban", "mute", "timeout", "kick", "moderação", "moderacao", "cargo", "canal", "servidor",
    "spam", "flood", "warn", "punição", "punicao", "regras do servidor",
}

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
YEAR_RE = re.compile(r"\b(20\d{2})\b")


@dataclass
class TTLValue:
    value: Any
    expires_at: float


class TTLCache:
    def __init__(self, max_size: int = 512) -> None:
        self.max_size = max_size
        self._store: dict[str, TTLValue] = {}

    def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        now = time.monotonic()
        if not item:
            return None
        if item.expires_at < now:
            self._store.pop(key, None)
            return None
        return item.value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if len(self._store) >= self.max_size:
            oldest_key = min(self._store, key=lambda k: self._store[k].expires_at)
            self._store.pop(oldest_key, None)
        self._store[key] = TTLValue(value=value, expires_at=time.monotonic() + ttl_seconds)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        self._purge_expired()
        return len(self._store)

    def _purge_expired(self) -> None:
        now = time.monotonic()
        for key in list(self._store):
            if self._store[key].expires_at < now:
                self._store.pop(key, None)


SEARCH_CACHE   = TTLCache(max_size=512)
ACADEMIC_CACHE = TTLCache(max_size=512)
LIBRARY_INDEX_CACHE = TTLCache(max_size=8)
RESPONSE_CACHE = TTLCache(max_size=256)


@dataclass
class SourceItem:
    title: str
    url: str = ""
    content: str = ""
    kind: str = "fonte"

    def compact(self, limit: int = 900) -> str:
        body = re.sub(r"\s+", " ", self.content or "").strip()[:limit]
        url_part = f" | {self.url}" if self.url else ""
        return f"[{self.kind} | {self.title}: {body}{url_part}]"


@dataclass
class LibraryChunk:
    path: str
    title: str
    text: str


@dataclass
class ModelResult:
    text: str
    model: str
    key_number: int
    latency_ms: int
    finish_reason: str = ""
    continuations: int = 0
    truncated: bool = False


@dataclass
class AIResponse:
    text: str
    mode: str
    sources: list[str] = field(default_factory=list)
    used_tools: list[str] = field(default_factory=list)
    model: str = ""
    latency_ms: int = 0
    cache_hit: bool = False


def _contains_any(text: str, words: set[str]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in words)


def _clean_text(text: str, max_chars: int | None = None) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if max_chars:
        return text[:max_chars]
    return text


def _normalize_key(text: str) -> str:
    text = text.lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9áàâãéèêíïóôõöúçñ\s-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:240]


def _finish_reason_name(value: Any) -> str:
    if value is None:
        return ""
    name = getattr(value, "name", None)
    if name:
        return str(name).upper()
    numeric = getattr(value, "value", value)
    if isinstance(numeric, int):
        return {
            0: "UNSPECIFIED",
            1: "STOP",
            2: "MAX_TOKENS",
            3: "SAFETY",
            4: "RECITATION",
            5: "OTHER",
            6: "LANGUAGE",
        }.get(numeric, str(numeric))
    return str(value).upper()


def _extract_model_text(response: Any) -> str:
    """Extrai texto mesmo quando response.text não está disponível."""
    try:
        value = response.text
        if value:
            return str(value).strip()
    except Exception:
        pass

    parts: list[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                parts.append(str(part_text))
    return "\n".join(parts).strip()


def _extract_gemini_finish_reason(response: Any) -> str:
    candidates = getattr(response, "candidates", []) or []
    if not candidates:
        return ""
    return _finish_reason_name(getattr(candidates[0], "finish_reason", None))


def _looks_cut_off(text: str, finish_reason: str = "") -> bool:
    reason = (finish_reason or "").upper()
    if "MAX_TOKENS" in reason or reason == "LENGTH":
        return True

    stripped = (text or "").rstrip()
    if not stripped:
        return False
    if stripped.count("```") % 2:
        return True
    if stripped.count("**") % 2:
        return True

    # Heurística conservadora para finais claramente interrompidos.
    if len(stripped) >= 900:
        unfinished = (
            r"(?:\b(?:e|ou|de|da|do|das|dos|para|porque|que|como|com|em|no|na)|"
            r"[,;:/\(\[\{]|[-–—]|\*\*)$"
        )
        if re.search(unfinished, stripped, re.IGNORECASE):
            return True
        last_line = stripped.splitlines()[-1].strip()
        if re.fullmatch(r"(?:[-*•]|\d+[.)])\s*", last_line):
            return True
    return False


def _strip_continuation_preamble(text: str) -> str:
    value = (text or "").strip()
    value = re.sub(
        r"^(?:claro[,!.]?\s*)?(?:continuando(?: exatamente)?(?: de onde parou)?|"
        r"continuação|seguindo com a resposta)\s*[:.-]?\s*",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip()


def _join_continuation(previous: str, continuation: str) -> str:
    previous = (previous or "").rstrip()
    continuation = _strip_continuation_preamble(continuation)
    if not continuation or continuation.upper() in {"[FIM]", "FIM"}:
        return previous

    # O prompt pede repetição de algumas palavras; removemos a sobreposição aqui.
    max_overlap = min(700, len(previous), len(continuation))
    previous_lower = previous.lower()
    continuation_lower = continuation.lower()
    for size in range(max_overlap, 15, -1):
        if previous_lower[-size:] == continuation_lower[:size]:
            return previous + continuation[size:]

    # Tenta sobreposição por palavras, tolerando espaços diferentes.
    prev_words = previous.split()
    next_words = continuation.split()
    max_words = min(35, len(prev_words), len(next_words))
    for count in range(max_words, 3, -1):
        left = " ".join(prev_words[-count:]).lower()
        right = " ".join(next_words[:count]).lower()
        if left == right:
            remainder = " ".join(next_words[count:])
            return previous + (" " + remainder if remainder else "")

    return previous + "\n\n" + continuation


def _normalize_ai_output(text: str) -> str:
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\x00", "")
    value = re.sub(r"\n[ \t]+\n", "\n\n", value)
    value = re.sub(r"\n{4,}", "\n\n\n", value).strip()
    value = re.sub(r"\n?\[FIM\]\s*$", "", value, flags=re.IGNORECASE).rstrip()

    # Evita deixar bloco de código aberto quando uma API encerra abruptamente.
    if value.count("```") % 2:
        value += "\n```"
    return value


def _split_discord_text(text: str, limit: int = DISCORD_TEXT_LIMIT) -> list[str]:
    """Divide sem cortar palavras e preserva blocos de código entre mensagens."""
    value = (text or "").strip()
    if not value:
        return ["Não consegui gerar uma resposta agora."]
    if len(value) <= limit:
        return [value]

    # Reserva espaço para fechar/reabrir cercas de código quando necessário.
    raw_limit = max(100, limit - 24)
    raw_chunks: list[str] = []
    remaining = value

    while remaining:
        if len(remaining) <= raw_limit:
            raw_chunks.append(remaining.strip())
            break

        window = remaining[:raw_limit]
        cut_candidates = [
            window.rfind("\n\n"),
            window.rfind("\n"),
            window.rfind(". "),
            window.rfind("! "),
            window.rfind("? "),
            window.rfind("; "),
            window.rfind(" "),
        ]
        cut = max(cut_candidates)
        if cut < max(250, int(raw_limit * 0.45)):
            cut = raw_limit
        elif window[cut:cut + 2] in {". ", "! ", "? ", "; "}:
            cut += 1

        raw_chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    rendered: list[str] = []
    code_open = False
    code_language = ""

    for raw in raw_chunks:
        prefix = f"```{code_language}\n" if code_open else ""
        combined = prefix + raw
        state = code_open
        language = code_language

        for match in re.finditer(r"```([^\n`]*)", raw):
            if state:
                state = False
                language = ""
            else:
                state = True
                language = match.group(1).strip()

        if state:
            combined += "\n```"

        rendered.append(combined.strip())
        code_open = state
        code_language = language

    return [chunk for chunk in rendered if chunk]

def _needs_fresh_information(message: str) -> bool:
    """Detecta pedidos que podem depender de informação posterior ao corte do modelo."""
    lowered = message.lower().strip()
    if _contains_any(lowered, CURRENT_INFO_KEYWORDS):
        return True

    years = [int(match) for match in YEAR_RE.findall(lowered)]
    if years and max(years) >= 2025:
        return True

    current_patterns = (
        r"\bquem (?:é|e) (?:o|a) atual\b",
        r"\bqual (?:é|e) (?:o|a) atual\b",
        r"\b(?:última|ultima|mais recente) versão\b",
        r"\b(?:último|ultimo|mais recente) lançamento\b",
        r"\b(?:ainda está|ainda esta|continua) disponível\b",
        r"\b(?:neste ano|este ano|ano passado)\b",
    )
    return any(re.search(pattern, lowered) for pattern in current_patterns)


def _looks_like_factual_question(message: str) -> bool:
    lowered = re.sub(r"\s+", " ", message.lower()).strip()
    return any(lowered.startswith(prefix) for prefix in FACTUAL_QUESTION_PREFIXES)


def detect_query_type(message: str) -> str:
    msg = message.lower()

    # Pedidos explicitamente atuais ou de pesquisa devem receber contexto externo.
    if _contains_any(msg, SEARCH_REQUEST_KEYWORDS) or _needs_fresh_information(msg) or URL_RE.search(msg):
        return "search"

    # Código e assuntos acadêmicos mantêm seus fluxos especializados.
    if _contains_any(msg, CODE_KEYWORDS) or CODE_BLOCK_RE.search(message):
        return "code"
    if _contains_any(msg, ACADEMIC_KEYWORDS):
        return "academic"

    # Perguntas factuais curtas, inclusive sobre personagens, obras e pessoas,
    # passam pela busca para reduzir alucinações de nomes e detalhes.
    if len(message) <= 500 and _looks_like_factual_question(message):
        return "search"

    if _contains_any(msg, WRITING_KEYWORDS):
        return "writing"
    if _contains_any(msg, PLANNING_KEYWORDS):
        return "planning"
    if _contains_any(msg, MODERATION_KEYWORDS):
        return "moderation"
    if _contains_any(msg, CREATIVE_KEYWORDS):
        return "creative"
    return "chat"


def is_academic(message: str) -> bool:
    return detect_query_type(message) in {"academic", "code"}


def _mode_label(mode: str) -> str:
    labels = {
        "chat": "conversa",
        "academic": "acadêmico",
        "code": "código",
        "search": "busca",
        "writing": "texto",
        "planning": "planejamento",
        "moderation": "moderação",
        "creative": "criativo",
        "study": "estudo",
        "argument": "argumento",
    }
    return labels.get(mode, mode)


def _academic_search_terms(query: str) -> str:
    cleaned = query.lower()
    cleaned = re.sub(
        r"\b(o que é|oq é|explique|explica|me explica|resuma|resumo|fale sobre|me fala sobre|qual é|quem foi|pesquise|procure|busque|fonte|link|defina|conceitue)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"[^a-z0-9áàâãéèêíïóôõöúçñ\s-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or query


def _looks_math_or_logic(query: str) -> bool:
    msg = query.lower()
    markers = {
        "lógica", "logica", "matemática", "matematica", "teorema", "prova", "demonstração",
        "demonstracao", "cálculo", "calculo", "álgebra", "algebra", "conjunto", "função",
        "funcao", "complexidade", "algoritmo", "computação", "computacao", "formal", "modal",
        "probabilidade", "estatística", "estatistica", "derivada", "integral", "matriz",
    }
    return _contains_any(msg, markers)


def _extract_terms(query: str) -> list[str]:
    cleaned = _normalize_key(query)
    stopwords = {
        "para", "com", "uma", "uns", "das", "dos", "que", "como", "por", "qual", "quais",
        "sobre", "isso", "esse", "essa", "esse", "dele", "dela", "mais", "muito", "pouco", "explique",
        "resuma", "fale", "me", "o", "a", "os", "as", "de", "do", "da", "em", "no", "na", "e", "ou",
    }
    return [w for w in cleaned.split() if len(w) >= 3 and w not in stopwords][:16]


def build_system_prompt(profile: dict[str, Any] | None = None, forced_style: str | None = None) -> str:
    now = datetime.now()
    weekday = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"][now.weekday()]
    stamp = now.strftime(f"%d/%m/%Y, {weekday}, %H:%M")

    profile = profile or {}
    preferred_style = forced_style or profile.get("style") or "normal"
    memory_note = profile.get("notes") or ""

    style_rules = {
        "curto": "Responda de forma curta, direta e sem rodeios, exceto quando o usuário pedir profundidade.",
        "normal": "Responda com equilíbrio: direto quando simples, estruturado quando complexo.",
        "profundo": "Responda com profundidade, etapas, nuances, exemplos e limites da resposta.",
        "professor": "Atue como tutor: explique, verifique pressupostos, dê exemplos e faça perguntas úteis quando couber.",
        "criativo": "Use criatividade controlada, boas analogias e soluções menos óbvias, mantendo precisão.",
    }.get(preferred_style, "Responda com equilíbrio: direto quando simples, estruturado quando complexo.")

    return f"""Você é {BOT_NAME}, uma IA conversacional avançada para Discord.

Data local de referência: {stamp}.
Criadora registrada: {OWNER_NAME}. Se ela falar com você, seja mais próximo, mas sem exagero.
Preferência do usuário, quando disponível: {preferred_style}.
Notas úteis de memória, se houver: {memory_note}

MISSÃO
Você deve ser rápido, confiável, útil e adaptável. Você conversa bem em chat casual, mas também opera como tutor acadêmico, copiloto de código, planejador, resumidor, revisor, analista de argumentos, assistente de servidor e ferramenta de produtividade.

ESTILO PADRÃO
- Fale em português brasileiro, a menos que o usuário peça outro idioma.
- {style_rules}
- Seja natural para Discord: claro, humano, sem parecer redação escolar em perguntas simples.
- Respostas simples pedem respostas curtas. Perguntas complexas pedem estrutura e profundidade.
- Use poucos emojis e apenas quando combinarem com o tom.
- Em conversa normal com pessoas, quando a resposta for amigável, descontraída ou levemente sarcástica, use exatamente uma vez o emoji <:1000032124:1508195012175728720>, no meio ou no fim da mensagem. Não use esse emoji em respostas sérias, técnicas, acadêmicas, moderação, avisos de erro ou comandos administrativos.
- Não seja arrogante, ofensivo, pedante ou passivo-agressivo.
- Não comece todas as respostas do mesmo jeito.
- Evite começar com interjeições vazias como "Ah", "Aah", "Oh" ou "Ooh". Comece pela informação útil.

COMPLETUDE E FORMATAÇÃO
- Termine a resposta inteira. Nunca pare no meio de uma frase, item, título, citação, negrito ou bloco de código.
- Antes de encerrar, confira mentalmente se listas numeradas foram concluídas e se o Markdown foi fechado.
- Prefira parágrafos curtos, títulos úteis e listas apenas quando realmente organizarem a resposta.
- Não use fragmentos soltos, títulos quebrados ou numeração sem conteúdo.
- Se o tema for grande demais, entregue uma versão completa e mais concisa em vez de começar uma resposta enorme e deixá-la incompleta.

CONFIABILIDADE
- Sua prioridade é responder certo.
- Se não souber, diga que não sabe.
- Se estiver incerto, diga que há incerteza.
- Nunca invente fonte, link, citação, livro, artigo, autor, estatística, teorema ou consenso acadêmico.
- Não finja ter pesquisado. Se uma ferramenta de busca foi usada, utilize o contexto recebido. Se não foi usada, responda com conhecimento geral e deixe limites claros.
- Em temas atuais, versões de APIs, preços, notícias, política, leis, resultados esportivos e disponibilidade de serviços, avise quando não houver fonte atual suficiente.
- Para personagens, obras, acontecimentos históricos e nomes próprios, confirme detalhes pelo contexto externo quando ele estiver disponível; não complete lacunas por associação.

BASE TEMPORAL
- A data atual desta conversa é {stamp}.
- Seu conhecimento interno de referência vai até {KNOWLEDGE_CUTOFF_LABEL}. Portanto, 2022, 2023 e 2024 são anos passados conhecidos, não são datas futuras.
- Para fatos posteriores a {KNOWLEDGE_CUTOFF_LABEL}, ou para qualquer pedido sobre "hoje", "atual", preços, cargos, versões, notícias e resultados, dependa do contexto externo de busca.
- Se a busca não trouxer fonte suficiente, diga claramente que não conseguiu confirmar o dado atual. Não transforme memória antiga em fato presente.
- Quando usar contexto externo em uma resposta factual, cite de forma curta apenas as fontes que realmente aparecem nesse contexto.

MODO ACADÊMICO PESADO
Quando o assunto envolver filosofia, lógica, matemática, ciência, programação, redação acadêmica ou debate conceitual:
1. Dê a resposta direta primeiro.
2. Defina termos importantes.
3. Explique o raciocínio com etapas.
4. Diferencie fato, interpretação, opinião e especulação.
5. Mostre objeções, exceções ou debates quando existirem.
6. Cite autores e obras apenas quando tiver segurança ou quando estiverem no contexto externo.
7. Quando houver contexto de biblioteca/busca, use-o com prioridade e não extrapole além dele.
8. Se o usuário estiver aprendendo, explique sem humilhar e sem pular degraus.

FILOSOFIA
Não reduza filósofos a frases de efeito. Explique problema, tese, argumento, consequência e crítica. Quando houver escolas diferentes, apresente as principais leituras com equilíbrio.

LÓGICA E ARGUMENTAÇÃO
Diferencie verdade, validade, solidez, consistência, contradição, implicação e equivalência. Ao analisar argumento: identifique premissas, conclusão, forma lógica, validade e possível contraexemplo.

MATEMÁTICA
Confira contas, hipóteses e unidades. Mostre etapas quando o usuário estiver aprendendo. Não invente teoremas. Se houver ambiguidade matemática, declare a interpretação usada.

PROGRAMAÇÃO
Dê soluções práticas, código copiável e explicação da causa. Considere ambiente, versão, permissões, logs e segurança. Nunca recomende expor API keys no GitHub.

PRODUTIVIDADE E TEXTO
Quando revisar, resumir, planejar ou escrever: preserve a intenção do usuário, organize melhor e entregue algo copiável.

FONTES ACADÊMICAS PREFERIDAS
Quando houver busca/biblioteca, priorize SEP, IEP, PhilPapers, PhilArchive, arXiv, documentação oficial, universidades e artigos acadêmicos. Blogs e fóruns não devem ser tratados como autoridade principal.

SEGURANÇA
Não ajude com roubo de contas, malware, abuso de API, vazamento de dados, exposição de chaves, burlar sistemas ou instruções perigosas. Recuse brevemente e ofereça alternativa segura.

IDENTIDADE
Você é {BOT_NAME}. Não explique bastidores de provedor, modelo ou API, a menos que o usuário pergunte sobre a configuração técnica do bot.
"""


async def fetch_json(session: aiohttp.ClientSession, url: str, *, timeout: int = 5) -> Optional[dict]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
    except Exception as exc:
        logger.debug("fetch_json falhou: %s", exc)
    return None


async def fetch_text(session: aiohttp.ClientSession, url: str, *, timeout: int = 6, max_chars: int = 1800) -> str:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                return ""
            raw = await resp.text(errors="ignore")
    except Exception as exc:
        logger.debug("fetch_text falhou: %s", exc)
        return ""

    raw = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<nav.*?</nav>|<footer.*?</footer>", " ", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:max_chars]


def _read_library_files() -> list[LibraryChunk]:
    cached = LIBRARY_INDEX_CACHE.get("library_index")
    if cached is not None:
        return cached

    library_paths = [p.strip() for p in os.getenv("AI_LIBRARY_PATH", "data/library,library,books").split(",") if p.strip()]
    allowed_ext = {".txt", ".md", ".markdown", ".rst", ".csv", ".json"}
    max_files = _safe_int(os.getenv("AI_LIBRARY_MAX_FILES"), 120)
    max_file_chars = _safe_int(os.getenv("AI_LIBRARY_MAX_FILE_CHARS"), 180_000)
    chunk_size = _safe_int(os.getenv("AI_LIBRARY_CHUNK_CHARS"), 1600)
    chunks: list[LibraryChunk] = []

    for raw_path in library_paths:
        root = Path(raw_path)
        if not root.exists():
            continue
        files = []
        if root.is_file() and root.suffix.lower() in allowed_ext:
            files.append(root)
        elif root.is_dir():
            files.extend([p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in allowed_ext])
        for path in files[:max_files]:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")[:max_file_chars]
            except Exception:
                continue
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 80:
                continue
            title = path.stem.replace("_", " ").replace("-", " ").strip()
            for start in range(0, len(text), chunk_size):
                part = text[start:start + chunk_size]
                if len(part) >= 120:
                    chunks.append(LibraryChunk(path=str(path), title=title, text=part))

    LIBRARY_INDEX_CACHE.set("library_index", chunks, ttl_seconds=_safe_int(os.getenv("AI_LIBRARY_REFRESH_SECONDS"), 300))
    return chunks


async def search_local_library(query: str, *, max_results: int = 5) -> list[SourceItem]:
    terms = _extract_terms(query)
    if not terms:
        return []

    chunks = await asyncio.to_thread(_read_library_files)
    if not chunks:
        return []

    scored: list[tuple[int, LibraryChunk]] = []
    for chunk in chunks:
        haystack = f"{chunk.title} {chunk.text}".lower()
        score = 0
        for term in terms:
            count = haystack.count(term.lower())
            if count:
                score += count * (3 if term.lower() in chunk.title.lower() else 1)
        if score:
            scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    results: list[SourceItem] = []
    used_paths: Counter[str] = Counter()
    for score, chunk in scored:
        if used_paths[chunk.path] >= 2:
            continue
        used_paths[chunk.path] += 1
        results.append(SourceItem(title=chunk.title, url=chunk.path, content=chunk.text, kind="Biblioteca local"))
        if len(results) >= max_results:
            break
    return results


async def _search_tavily(session: aiohttp.ClientSession, query: str, *, academic: bool = False) -> list[SourceItem]:
    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        return []

    terms = _academic_search_terms(query) if academic else query
    if not academic and _needs_fresh_information(query) and not YEAR_RE.search(query):
        terms = f"{terms} {datetime.now().year}"
    if academic:
        queries = [
            f"site:plato.stanford.edu/entries {terms}",
            f"site:iep.utm.edu {terms}",
            f"site:philpapers.org {terms}",
            f"site:philarchive.org {terms}",
        ]
        if _looks_math_or_logic(query):
            queries.insert(0, f"site:arxiv.org {terms}")
    else:
        queries = [query]

    results: list[SourceItem] = []
    for tq in queries[:4]:
        try:
            payload = {
                "api_key": tavily_key,
                "query": tq,
                "search_depth": "basic",
                "max_results": 3 if not academic else 2,
                "include_answer": not academic,
            }
            async with session.post("https://api.tavily.com/search", json=payload, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                if data.get("answer"):
                    results.append(SourceItem(title="Resumo Tavily", content=data["answer"][:600], kind="Tavily"))
                for item in data.get("results", [])[:3]:
                    title = item.get("title") or "Sem título"
                    content = (item.get("content") or "")[:650]
                    url = item.get("url") or ""
                    if content:
                        results.append(SourceItem(title=title, url=url, content=content, kind="Fonte acadêmica" if academic else "Web"))
        except Exception as exc:
            logger.debug("Falha Tavily: %s", exc)
    return results


async def _search_arxiv(session: aiohttp.ClientSession, query: str) -> list[SourceItem]:
    if not _looks_math_or_logic(query):
        return []
    encoded = urllib.parse.quote(_academic_search_terms(query))
    arxiv_url = f"https://export.arxiv.org/api/query?search_query=all:{encoded}&start=0&max_results=3"
    results: list[SourceItem] = []
    try:
        async with session.get(arxiv_url, timeout=aiohttp.ClientTimeout(total=7)) as resp:
            if resp.status != 200:
                return []
            xml = await resp.text(errors="ignore")
            entries = re.findall(r"(?is)<entry>(.*?)</entry>", xml)[:3]
            for entry in entries:
                title_match = re.search(r"(?is)<title>(.*?)</title>", entry)
                summary_match = re.search(r"(?is)<summary>(.*?)</summary>", entry)
                id_match = re.search(r"(?is)<id>(.*?)</id>", entry)
                title = re.sub(r"\s+", " ", re.sub(r"(?is)<.*?>", " ", title_match.group(1))).strip() if title_match else "Artigo arXiv"
                summary = re.sub(r"\s+", " ", re.sub(r"(?is)<.*?>", " ", summary_match.group(1))).strip() if summary_match else ""
                link = id_match.group(1).strip() if id_match else "https://arxiv.org"
                if summary:
                    results.append(SourceItem(title=title, url=link, content=summary[:850], kind="arXiv"))
    except Exception as exc:
        logger.debug("Falha arXiv: %s", exc)
    return results


async def _search_duckduckgo_academic(session: aiohttp.ClientSession, query: str) -> list[SourceItem]:
    terms = _academic_search_terms(query)
    ddg_queries = [
        f"site:plato.stanford.edu/entries {terms}",
        f"site:iep.utm.edu {terms}",
        f"site:philpapers.org {terms}",
        f"site:philarchive.org {terms}",
    ]
    results: list[SourceItem] = []
    for ddg_query in ddg_queries:
        url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(ddg_query)}&format=json&no_html=1&skip_disambig=1"
        data = await fetch_json(session, url, timeout=5)
        if not data:
            continue
        text = data.get("Answer") or data.get("AbstractText")
        link = data.get("AbstractURL", "")
        if text:
            results.append(SourceItem(title="DuckDuckGo acadêmico", url=link, content=text[:700], kind="Busca acadêmica"))
    return results


async def _search_sep_direct(session: aiohttp.ClientSession, query: str) -> list[SourceItem]:
    terms = _academic_search_terms(query)
    slug = re.sub(r"[^a-z0-9\- ]", "", terms.lower()).strip().replace(" ", "-")
    if not slug:
        return []
    sep_url = f"https://plato.stanford.edu/entries/{slug}/"
    sep_text = await fetch_text(session, sep_url, timeout=5, max_chars=1600)
    if sep_text:
        return [SourceItem(title="Stanford Encyclopedia of Philosophy", url=sep_url, content=sep_text, kind="SEP")]
    return []


async def _search_wikipedia(session: aiohttp.ClientSession, query: str) -> list[SourceItem]:
    """Pesquisa o título antes de pedir o resumo, evitando 404 em perguntas inteiras."""
    terms = _academic_search_terms(query)
    results: list[SourceItem] = []

    for lang in ("pt", "en"):
        search_url = (
            f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search&"
            f"srsearch={urllib.parse.quote(terms)}&utf8=1&format=json&srlimit=2"
        )
        search_data = await fetch_json(session, search_url, timeout=5)
        titles = [
            item.get("title", "")
            for item in (search_data or {}).get("query", {}).get("search", [])[:2]
            if item.get("title")
        ]

        # Fallback direto para consultas que já sejam um título válido.
        if not titles:
            titles = [terms]

        for title in titles:
            encoded_title = urllib.parse.quote(title.replace(" ", "_"), safe="")
            data = await fetch_json(
                session,
                f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{encoded_title}",
                timeout=5,
            )
            if not data or not data.get("extract"):
                continue
            extract = data.get("extract", "")[:950]
            page = data.get("content_urls", {}).get("desktop", {}).get("page", "")
            results.append(
                SourceItem(
                    title=f"Wikipedia {lang.upper()} · {data.get('title') or title}",
                    url=page,
                    content=extract,
                    kind="Wikipedia fallback",
                )
            )
            if len(results) >= 2:
                return results
    return results


async def _search_duckduckgo_general(session: aiohttp.ClientSession, query: str) -> list[SourceItem]:
    url = (
        "https://api.duckduckgo.com/?q="
        f"{urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
    )
    data = await fetch_json(session, url, timeout=5)
    if not data:
        return []

    results: list[SourceItem] = []
    answer = data.get("Answer") or data.get("AbstractText")
    if answer:
        results.append(
            SourceItem(
                title=data.get("Heading") or "DuckDuckGo",
                url=data.get("AbstractURL", ""),
                content=str(answer)[:850],
                kind="DuckDuckGo",
            )
        )

    def walk(items: list[Any]) -> None:
        for item in items:
            if len(results) >= 3:
                return
            if isinstance(item, dict) and item.get("Topics"):
                walk(item.get("Topics") or [])
                continue
            if not isinstance(item, dict):
                continue
            body = item.get("Text")
            if body:
                results.append(
                    SourceItem(
                        title="DuckDuckGo relacionado",
                        url=item.get("FirstURL", ""),
                        content=str(body)[:650],
                        kind="DuckDuckGo",
                    )
                )

    walk(data.get("RelatedTopics") or [])
    return results


async def _search_gnews(session: aiohttp.ClientSession, query: str) -> list[SourceItem]:
    gnews_key = os.getenv("GNEWS_API_KEY")
    if not gnews_key:
        return []
    encoded = urllib.parse.quote(query)
    url = f"https://gnews.io/api/v4/search?q={encoded}&lang=pt&max=3&token={gnews_key}"
    data = await fetch_json(session, url, timeout=6)
    results: list[SourceItem] = []
    if data:
        for article in data.get("articles", [])[:3]:
            title = article.get("title", "Sem título")
            desc = article.get("description", "")[:450]
            link = article.get("url", "")
            pub = article.get("publishedAt", "")[:10]
            if desc:
                results.append(SourceItem(title=f"{pub} | {title}", url=link, content=desc, kind="GNews"))
    return results


async def search_academic_sources(query: str) -> str:
    max_chars = _safe_int(os.getenv("ACADEMIC_CONTEXT_MAX_CHARS"), 8000)
    cache_key = f"academic:{_normalize_key(query)}"
    cached = ACADEMIC_CACHE.get(cache_key)
    if cached is not None:
        return cached

    async with aiohttp.ClientSession(headers={"User-Agent": f"{BOT_NAME}/2.0 academic helper"}) as session:
        tasks = [
            search_local_library(query, max_results=5),
            _search_tavily(session, query, academic=True),
            _search_arxiv(session, query),
            _search_duckduckgo_academic(session, query),
            _search_sep_direct(session, query),
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        sources: list[SourceItem] = []
        for item in gathered:
            if isinstance(item, Exception):
                logger.debug("Busca acadêmica parcial falhou: %s", item)
                continue
            sources.extend(item)

        if not sources:
            sources.extend(await _search_wikipedia(session, query))

    seen: set[str] = set()
    compacted: list[str] = []
    for source in sources:
        signature = (source.title.lower(), source.url.lower())
        sig = "|".join(signature)
        if sig in seen:
            continue
        seen.add(sig)
        compacted.append(source.compact())
        if sum(len(x) for x in compacted) >= max_chars:
            break

    result = "\n".join(compacted)[:max_chars]
    academic_ttl = 1800 if _needs_fresh_information(query) else _safe_int(os.getenv("AI_ACADEMIC_CACHE_SECONDS"), 86400)
    if result:
        ACADEMIC_CACHE.set(cache_key, result, ttl_seconds=academic_ttl)
    return result


async def search_web(query: str, academic: bool = False) -> str:
    if academic:
        return await search_academic_sources(query)

    max_chars = _safe_int(os.getenv("WEB_CONTEXT_MAX_CHARS"), 5500)
    cache_key = f"web:{_normalize_key(query)}"
    cached = SEARCH_CACHE.get(cache_key)
    if cached is not None:
        return cached

    async with aiohttp.ClientSession(headers={"User-Agent": f"{BOT_NAME}/2.0 web helper"}) as session:
        tasks = [
            _search_tavily(session, query, academic=False),
            _search_gnews(session, query),
            _search_wikipedia(session, query),
            _search_duckduckgo_general(session, query),
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

    sources: list[SourceItem] = []
    for item in gathered:
        if isinstance(item, Exception):
            logger.debug("Busca web parcial falhou: %s", item)
            continue
        sources.extend(item)

    compacted = []
    seen: set[str] = set()
    for source in sources:
        sig = f"{source.title.lower()}|{source.url.lower()}"
        if sig in seen:
            continue
        seen.add(sig)
        compacted.append(source.compact())
        if sum(len(x) for x in compacted) >= max_chars:
            break

    result = "\n".join(compacted)[:max_chars]
    web_ttl = 300 if _needs_fresh_information(query) else _safe_int(os.getenv("AI_WEB_CACHE_SECONDS"), 1800)
    if result:
        SEARCH_CACHE.set(cache_key, result, ttl_seconds=web_ttl)
    return result


async def fetch_image_base64(url: str) -> Optional[tuple[str, str]]:
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


class AIActionView(discord.ui.View):
    def __init__(self, cog: "AIChat", response: AIResponse, original_prompt: str, *, timeout: int = 300) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self.response = response
        self.original_prompt = original_prompt

    async def _run_action(self, interaction: discord.Interaction, instruction: str, *, mode: str = "chat") -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        prompt = (
            f"Pedido original do usuário: {self.original_prompt[:1200]}\n\n"
            f"Resposta anterior do {BOT_NAME}: {self.response.text[:2800]}\n\n"
            f"Agora faça isto: {instruction}"
        )
        answer = await self.cog.get_ai_response(
            prompt,
            interaction.user.id,
            interaction.user.display_name,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            forced_query_type=mode,
            disable_buttons=True,
        )
        await self.cog._send_interaction_followup(interaction, answer, ephemeral=True, view=None)

    @discord.ui.button(label="Resumir", style=discord.ButtonStyle.secondary, emoji="<:1000032049:1507946904124919949>")
    async def summarize_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._run_action(interaction, "Resuma em poucos tópicos, sem perder o essencial.", mode="writing")

    @discord.ui.button(label="Aprofundar", style=discord.ButtonStyle.primary, emoji="<:1000032072:1507947958723809340>")
    async def deepen_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._run_action(interaction, "Aprofunde com mais detalhes, exemplos, limites e nuances.", mode="academic")

    @discord.ui.button(label="Exemplo", style=discord.ButtonStyle.secondary, emoji="<:1000032054:1507947088590274580>")
    async def example_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._run_action(interaction, "Dê exemplos concretos e fáceis de entender.", mode="chat")

    @discord.ui.button(label="Quiz", style=discord.ButtonStyle.success, emoji="<:1000032075:1507948047269888001>")
    async def quiz_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._run_action(interaction, "Crie um quiz curto com 5 perguntas e gabarito no final.", mode="academic")

    @discord.ui.button(label="Fontes", style=discord.ButtonStyle.secondary, emoji="<:1000032078:1507948115338985512>")
    async def sources_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        sources = self.response.sources[:8]
        if not sources:
            text = "Não usei fontes externas ou biblioteca local nesta resposta."
        else:
            text = "Fontes/contextos usados:\n" + "\n".join(f"- {s[:350]}" for s in sources)
        await interaction.response.send_message(text, ephemeral=True)


class AIChat(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.gemini_keys: list[str] = []        # chaves normais → gemini-2.5-flash
        self.gemini_key_index = 0
        self.groq_academic_keys: list[str] = []  # chaves acadêmicas → groq gpt-oss-120b
        self.groq_academic_key_index = 0
        self.history: defaultdict[int, list[dict[str, str]]] = defaultdict(list)
        self.cooldowns: defaultdict[int, datetime] = defaultdict(lambda: datetime.min)
        self.user_profiles: dict[int, dict[str, Any]] = {}
        self.recent_metrics: deque[dict[str, Any]] = deque(maxlen=500)
        self.send_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.db_extras_ready = False

        if not GENAI_AVAILABLE:
            logger.error("Biblioteca 'google-generativeai' não instalada. Execute: pip install google-generativeai")
            return

        for env_name in (
            "GEMINI_API_KEY",
            "GEMINI_API_KEY_2",
            "GEMINI_API_KEY_3",
            "GEMINI_API_KEY_4",
            "GEMINI_API_KEY_5",
        ):
            key = os.getenv(env_name)
            if key:
                self.gemini_keys.append(key)
                logger.info("%s carregada (flash).", env_name)

        for env_name in (
            "GROQ_ACADEMIC_API_KEY",
            "GROQ_ACADEMIC_API_KEY_2",
            "GROQ_ACADEMIC_API_KEY_3",
        ):
            key = os.getenv(env_name)
            if key:
                self.groq_academic_keys.append(key)
                logger.info("%s carregada (groq/acadêmico).", env_name)

        if not self.gemini_keys:
            logger.error("Nenhuma GEMINI_API_KEY encontrada. Configure no Railway/Hospedagem.")
        else:
            logger.info("Gemini ativo com %s chave(s) flash + %s chave(s) Groq (acadêmico).",
                        len(self.gemini_keys), len(self.groq_academic_keys))

    async def prepare(self) -> None:
        if not AI_DB_EXTRAS_ENABLED:
            return
        try:
            await db.pool.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_user_profiles (
                    user_id BIGINT PRIMARY KEY,
                    profile JSONB NOT NULL DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await db.pool.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_metrics (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT,
                    channel_id BIGINT,
                    user_id BIGINT,
                    mode TEXT,
                    model TEXT,
                    latency_ms INTEGER,
                    used_search BOOLEAN DEFAULT FALSE,
                    cache_hit BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            self.db_extras_ready = True
            logger.info("Tabelas extras de IA prontas.")
        except Exception as exc:
            self.db_extras_ready = False
            logger.warning("Não consegui preparar tabelas extras de IA. Seguindo sem persistência extra: %s", exc)

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

    async def _get_user_profile(self, user_id: int) -> dict[str, Any]:
        if user_id in self.user_profiles:
            return self.user_profiles[user_id]
        profile: dict[str, Any] = {"style": "normal", "notes": ""}
        if self.db_extras_ready:
            try:
                row = await db.pool.fetchrow("SELECT profile FROM ai_user_profiles WHERE user_id = $1", user_id)
                if row and row["profile"]:
                    raw = row["profile"]
                    if isinstance(raw, str):
                        profile.update(json.loads(raw))
                    elif isinstance(raw, dict):
                        profile.update(raw)
            except Exception as exc:
                logger.debug("Falha ao carregar perfil IA: %s", exc)
        self.user_profiles[user_id] = profile
        return profile

    async def _save_user_profile(self, user_id: int, profile: dict[str, Any]) -> None:
        self.user_profiles[user_id] = profile
        if self.db_extras_ready:
            try:
                await db.pool.execute(
                    """
                    INSERT INTO ai_user_profiles (user_id, profile, updated_at)
                    VALUES ($1, $2::jsonb, NOW())
                    ON CONFLICT (user_id) DO UPDATE SET profile = $2::jsonb, updated_at = NOW()
                    """,
                    user_id,
                    json.dumps(profile, ensure_ascii=False),
                )
            except Exception as exc:
                logger.debug("Falha ao salvar perfil IA: %s", exc)

    async def _call_gemini(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        temperature: float,
    ) -> Optional[ModelResult]:
        """Chama o Gemini com rotação de chaves e continuação automática."""
        keys = self.gemini_keys
        key_index_attr = "gemini_key_index"
        model_name = GEMINI_MODEL
        pool_label = "normal"

        if not keys:
            return None

        total_keys = len(keys)
        start_index = getattr(self, key_index_attr) % total_keys

        system_prompt = ""
        chat_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg["content"]
            else:
                role = "user" if msg["role"] == "user" else "model"
                chat_messages.append({"role": role, "parts": [msg["content"]]})

        if chat_messages and chat_messages[0]["role"] == "model":
            chat_messages = chat_messages[1:]

        for offset in range(total_keys):
            key_index = (start_index + offset) % total_keys
            api_key = keys[key_index]
            key_number = key_index + 1
            started = time.monotonic()

            try:
                genai.configure(api_key=api_key)
                model_obj = genai.GenerativeModel(
                    model_name=model_name,
                    system_instruction=system_prompt or None,
                    generation_config=genai.types.GenerationConfig(
                        max_output_tokens=max_tokens,
                        temperature=temperature,
                        top_p=0.9,
                    ),
                )

                history_msgs = chat_messages[:-1] if len(chat_messages) > 1 else []
                last_msg = chat_messages[-1]["parts"][0] if chat_messages else "Olá."
                chat = model_obj.start_chat(history=history_msgs)

                response = await asyncio.to_thread(chat.send_message, last_msg)
                combined = _extract_model_text(response)
                finish_reason = _extract_gemini_finish_reason(response)
                continuations = 0

                while combined and _looks_cut_off(combined, finish_reason) and continuations < MAX_CONTINUATIONS:
                    tail = " ".join(combined.split()[-18:])
                    continuation_prompt = (
                        "A resposta anterior foi interrompida pelo limite. Continue exatamente do ponto em que parou, "
                        "sem reiniciar a explicação e sem fazer uma nova introdução. Comece repetindo exatamente o "
                        f"trecho final a seguir para permitir uma emenda segura: {tail!r}. "
                        "Depois conclua todas as seções, listas e marcações Markdown."
                    )
                    next_response = await asyncio.to_thread(chat.send_message, continuation_prompt)
                    addition = _extract_model_text(next_response)
                    if not addition:
                        break
                    combined = _join_continuation(combined, addition)
                    finish_reason = _extract_gemini_finish_reason(next_response)
                    continuations += 1

                # Última tentativa curta de fechamento, caso a continuação também atinja o limite.
                if combined and _looks_cut_off(combined, finish_reason):
                    tail = " ".join(combined.split()[-18:])
                    final_prompt = (
                        "Finalize agora somente o que ainda falta, em no máximo 250 palavras. "
                        "Não repita a resposta inteira. Comece repetindo exatamente este trecho: "
                        f"{tail!r}. Termine a frase, a lista e o Markdown de forma completa."
                    )
                    final_response = await asyncio.to_thread(chat.send_message, final_prompt)
                    addition = _extract_model_text(final_response)
                    if addition:
                        combined = _join_continuation(combined, addition)
                        finish_reason = _extract_gemini_finish_reason(final_response)
                        continuations += 1

                combined = _normalize_ai_output(combined)
                truncated = _looks_cut_off(combined, finish_reason)
                latency_ms = int((time.monotonic() - started) * 1000)
                setattr(self, key_index_attr, key_index)
                logger.info(
                    "Gemini respondeu | pool=%s modelo=%s chave #%s | %sms | continuações=%s | fim=%s",
                    pool_label,
                    model_name,
                    key_number,
                    latency_ms,
                    continuations,
                    finish_reason or "n/a",
                )
                return ModelResult(
                    text=combined,
                    model=model_name,
                    key_number=key_number,
                    latency_ms=latency_ms,
                    finish_reason=finish_reason,
                    continuations=continuations,
                    truncated=truncated,
                )

            except Exception as exc:
                error = str(exc).lower()
                is_quota = (
                    "quota" in error
                    or "resource_exhausted" in error
                    or "429" in error
                    or "rate limit" in error
                    or "too many requests" in error
                    or "daily limit" in error
                    or "exceeded" in error
                )
                is_auth = (
                    "api_key" in error
                    or "invalid" in error
                    or "unauthorized" in error
                    or "403" in error
                    or "401" in error
                    or "permission" in error
                )

                if is_quota or is_auth:
                    setattr(self, key_index_attr, (key_index + 1) % total_keys)
                    logger.warning(
                        "Chave Gemini #%s (pool=%s) indisponível (%s). Trocando para chave #%s silenciosamente.",
                        key_number,
                        pool_label,
                        "quota" if is_quota else "auth",
                        getattr(self, key_index_attr) + 1,
                    )
                    continue

                logger.error(
                    "Erro Gemini chave #%s (pool=%s): %s: %s",
                    key_number,
                    pool_label,
                    type(exc).__name__,
                    exc,
                )
                setattr(self, key_index_attr, (key_index + 1) % total_keys)
                continue

        logger.error("Todas as %s chaves Gemini (pool=%s) falharam nesta requisição.", total_keys, pool_label)
        return None

    async def _call_groq_academic(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        temperature: float,
    ) -> Optional[ModelResult]:
        """Chama o Groq acadêmico com rotação e continuação automática."""
        keys = self.groq_academic_keys
        if not keys:
            return None

        total_keys = len(keys)
        start_index = self.groq_academic_key_index % total_keys
        base_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

        async with aiohttp.ClientSession() as session:
            for offset in range(total_keys):
                key_index = (start_index + offset) % total_keys
                api_key = keys[key_index]
                key_number = key_index + 1
                started = time.monotonic()

                async def request(payload_messages: list[dict[str, str]]) -> dict[str, Any]:
                    async with session.post(
                        GROQ_API_URL,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": GROQ_ACADEMIC_MODEL,
                            "messages": payload_messages,
                            "max_tokens": max_tokens,
                            "temperature": temperature,
                            "top_p": 0.9,
                        },
                        timeout=aiohttp.ClientTimeout(total=MODEL_TIMEOUT_SECONDS),
                    ) as resp:
                        if resp.status != 200:
                            body = await resp.text()
                            raise RuntimeError(f"HTTP {resp.status}: {body[:300]}")
                        return await resp.json()

                try:
                    working_messages = list(base_messages)
                    data = await request(working_messages)
                    choice = data["choices"][0]
                    current_text = (choice.get("message", {}).get("content") or "").strip()
                    combined = current_text
                    finish_reason = str(choice.get("finish_reason") or "").upper()
                    continuations = 0

                    while combined and _looks_cut_off(combined, finish_reason) and continuations < MAX_CONTINUATIONS:
                        tail = " ".join(combined.split()[-18:])
                        working_messages.append({"role": "assistant", "content": current_text})
                        working_messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Continue exatamente do ponto em que a resposta foi interrompida. "
                                    "Não reinicie e não faça introdução. Comece repetindo exatamente este trecho final: "
                                    f"{tail!r}. Depois conclua listas, frases e Markdown."
                                ),
                            }
                        )
                        data = await request(working_messages)
                        choice = data["choices"][0]
                        current_text = (choice.get("message", {}).get("content") or "").strip()
                        if not current_text:
                            break
                        combined = _join_continuation(combined, current_text)
                        finish_reason = str(choice.get("finish_reason") or "").upper()
                        continuations += 1

                    if combined and _looks_cut_off(combined, finish_reason):
                        tail = " ".join(combined.split()[-18:])
                        working_messages.append({"role": "assistant", "content": current_text})
                        working_messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Finalize somente o ponto restante em no máximo 250 palavras. "
                                    "Comece repetindo exatamente este trecho: "
                                    f"{tail!r}. Não repita o restante da resposta."
                                ),
                            }
                        )
                        data = await request(working_messages)
                        choice = data["choices"][0]
                        current_text = (choice.get("message", {}).get("content") or "").strip()
                        if current_text:
                            combined = _join_continuation(combined, current_text)
                            finish_reason = str(choice.get("finish_reason") or "").upper()
                            continuations += 1

                    combined = _normalize_ai_output(combined)
                    truncated = _looks_cut_off(combined, finish_reason)
                    latency_ms = int((time.monotonic() - started) * 1000)
                    self.groq_academic_key_index = key_index
                    logger.info(
                        "Groq respondeu | pool=acadêmico modelo=%s chave #%s | %sms | continuações=%s | fim=%s",
                        GROQ_ACADEMIC_MODEL,
                        key_number,
                        latency_ms,
                        continuations,
                        finish_reason or "n/a",
                    )
                    return ModelResult(
                        text=combined,
                        model=GROQ_ACADEMIC_MODEL,
                        key_number=key_number,
                        latency_ms=latency_ms,
                        finish_reason=finish_reason,
                        continuations=continuations,
                        truncated=truncated,
                    )

                except Exception as exc:
                    error = str(exc).lower()
                    is_quota = (
                        "quota" in error
                        or "429" in error
                        or "rate limit" in error
                        or "too many requests" in error
                    )
                    is_auth = (
                        "401" in error
                        or "403" in error
                        or "invalid_api_key" in error
                        or "unauthorized" in error
                        or "permission" in error
                    )

                    if is_quota or is_auth:
                        self.groq_academic_key_index = (key_index + 1) % total_keys
                        logger.warning(
                            "Chave Groq #%s (pool=acadêmico) indisponível (%s). Trocando para chave #%s silenciosamente.",
                            key_number,
                            "quota" if is_quota else "auth",
                            self.groq_academic_key_index + 1,
                        )
                        continue

                    logger.error(
                        "Erro Groq chave #%s (pool=acadêmico): %s: %s",
                        key_number,
                        type(exc).__name__,
                        exc,
                    )
                    self.groq_academic_key_index = (key_index + 1) % total_keys
                    continue

        logger.error("Todas as %s chaves Groq (pool=acadêmico) falharam nesta requisição.", total_keys)
        return None

    def _model_order_for_mode(self, mode: str) -> str:
        # Com Gemini, sempre usamos o mesmo modelo; método mantido por compatibilidade
        return GEMINI_MODEL

    def _temperature_for_mode(self, mode: str) -> float:
        return {
            "academic": 0.28,
            "argument": 0.25,
            "code": 0.22,
            "search": 0.32,
            "study": 0.32,
            "moderation": 0.25,
            "writing": 0.58,
            "planning": 0.5,
            "creative": 0.82,
            "chat": 0.72,
        }.get(mode, 0.65)

    def _max_tokens_for_mode(self, mode: str) -> int:
        if mode in {"academic", "code", "study", "argument"}:
            return ACADEMIC_MAX_TOKENS
        if mode == "search":
            return ACADEMIC_MAX_TOKENS
        if mode in {"planning", "creative"}:
            return DEEP_MAX_TOKENS
        return NORMAL_MAX_TOKENS

    def _style_instruction_for_mode(self, mode: str) -> str:
        return {
            "academic": "Modo acadêmico ativo: seja preciso, defina termos, aponte limites, use exemplos e evite citações inventadas.",
            "argument": "Modo análise de argumento: extraia premissas, conclusão, forma lógica, validade, solidez e objeções.",
            "code": "Modo código ativo: explique causa, solução, riscos, e entregue código copiável quando útil.",
            "search": "Modo busca ativo: use o contexto externo fornecido. Não invente dados atuais fora dele.",
            "writing": "Modo texto ativo: entregue versão revisada/copiável, preservando intenção e tom pedido.",
            "planning": "Modo planejamento ativo: organize em etapas, prioridades, riscos e próximos passos.",
            "creative": "Modo criativo ativo: seja original, mas mantenha coerência com o pedido.",
            "moderation": "Modo servidor/moderação ativo: seja prático, seguro, claro e compatível com administração de comunidade.",
            "study": "Modo estudo ativo: ensine como tutor, com explicação, exemplos, revisão e checagem de entendimento.",
        }.get(mode, "")

    async def _maybe_build_external_context(self, user_message: str, mode: str) -> tuple[str, list[str], list[str], bool]:
        web_context = ""
        used_tools: list[str] = []
        sources: list[str] = []
        cache_hit = False

        academic = mode in {"academic", "code", "study", "argument"}
        should_search = mode == "search" or (academic and ACADEMIC_SEARCH_ENABLED)

        if should_search:
            before_cache_count = len(ACADEMIC_CACHE) if academic else len(SEARCH_CACHE)
            web_context = await search_web(user_message, academic=academic)
            after_cache_count = len(ACADEMIC_CACHE) if academic else len(SEARCH_CACHE)
            cache_hit = before_cache_count == after_cache_count and bool(web_context)
            if web_context:
                used_tools.append("biblioteca/busca acadêmica" if academic else "busca web")
                sources = [line for line in web_context.splitlines() if line.strip()]

        return web_context, sources, used_tools, cache_hit

    async def get_ai_response(
        self,
        user_message: str,
        user_id: int,
        user_name: str,
        image_data=None,
        *,
        guild_id: int | None = None,
        channel_id: int | None = None,
        forced_query_type: str | None = None,
        extra_instruction: str | None = None,
        disable_buttons: bool = False,
    ) -> AIResponse:
        started = time.monotonic()
        mode = forced_query_type if forced_query_type and forced_query_type != "auto" else detect_query_type(user_message)
        profile = await self._get_user_profile(user_id)

        if image_data:
            text = "No momento estou configurado só com modelos de texto. Posso analisar a descrição da imagem se você escrever o que aparece nela."
            return AIResponse(text=text, mode=mode, latency_ms=int((time.monotonic() - started) * 1000))

        cache_allowed = mode in {"chat", "writing", "planning"} and len(user_message) < 260 and not extra_instruction
        response_cache_key = f"response:{mode}:{_normalize_key(user_message)}"
        cached_response = RESPONSE_CACHE.get(response_cache_key) if cache_allowed else None
        if cached_response:
            cached_response.cache_hit = True
            return cached_response

        web_context, sources, used_tools, cache_hit = await self._maybe_build_external_context(user_message, mode)

        history = self.history[user_id][-MAX_HISTORY_MESSAGES:]
        self.history[user_id] = history

        user_content = user_message.strip() or "Olá."
        if web_context:
            user_content += f"\n\nCONTEXTO EXTERNO/BIBLIOTECA, use sem inventar além dele:\n{web_context}"
        elif mode == "search":
            user_content += "\n\nAviso interno: a busca não trouxe resultado confiável. Não invente dados atuais."

        mode_instruction = self._style_instruction_for_mode(mode)
        if mode_instruction:
            user_content += f"\n\n{mode_instruction}"
        if extra_instruction:
            user_content += f"\n\nINSTRUÇÃO EXTRA DO COMANDO: {extra_instruction}"

        messages = [{"role": "system", "content": build_system_prompt(profile)}] + history + [
            {"role": "user", "content": f"Usuário: {user_name}\nMensagem: {user_content}"}
        ]

        is_academic_mode = mode in {"academic", "code", "study", "argument"}
        if is_academic_mode and self.groq_academic_keys:
            model_result = await self._call_groq_academic(
                messages,
                max_tokens=self._max_tokens_for_mode(mode),
                temperature=self._temperature_for_mode(mode),
            )
        else:
            model_result = await self._call_gemini(
                messages,
                max_tokens=self._max_tokens_for_mode(mode),
                temperature=self._temperature_for_mode(mode),
            )

        total_latency_ms = int((time.monotonic() - started) * 1000)
        if not model_result:
            text = "Estou temporariamente indisponível. Tente de novo em instantes."
            response = AIResponse(text=text, mode=mode, sources=sources, used_tools=used_tools, latency_ms=total_latency_ms, cache_hit=cache_hit)
            await self._record_metric(guild_id, channel_id, user_id, response)
            return response

        reply = _normalize_ai_output(model_result.text)
        if model_result.truncated:
            logger.warning(
                "Resposta ainda sinalizada como truncada após %s continuação(ões) | modelo=%s | fim=%s",
                model_result.continuations,
                model_result.model,
                model_result.finish_reason or "n/a",
            )
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": reply})
        self.history[user_id] = history[-MAX_HISTORY_MESSAGES:]

        response = AIResponse(
            text=reply,
            mode=mode,
            sources=sources,
            used_tools=used_tools,
            model=model_result.model,
            latency_ms=total_latency_ms,
            cache_hit=cache_hit,
        )
        if cache_allowed:
            RESPONSE_CACHE.set(response_cache_key, response, ttl_seconds=_safe_int(os.getenv("AI_RESPONSE_CACHE_SECONDS"), 300))

        await self._record_metric(guild_id, channel_id, user_id, response)
        return response

    async def _record_metric(self, guild_id: int | None, channel_id: int | None, user_id: int, response: AIResponse) -> None:
        metric = {
            "guild_id": guild_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "mode": response.mode,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "used_search": bool(response.sources),
            "cache_hit": response.cache_hit,
            "created_at": datetime.utcnow().isoformat(),
        }
        self.recent_metrics.append(metric)

        if self.db_extras_ready:
            async def _write() -> None:
                try:
                    await db.pool.execute(
                        """
                        INSERT INTO ai_metrics (guild_id, channel_id, user_id, mode, model, latency_ms, used_search, cache_hit)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                        """,
                        guild_id,
                        channel_id,
                        user_id,
                        response.mode,
                        response.model,
                        response.latency_ms,
                        bool(response.sources),
                        response.cache_hit,
                    )
                except Exception as exc:
                    logger.debug("Falha ao registrar métrica IA: %s", exc)
            asyncio.create_task(_write())

    def _make_view(self, response: AIResponse, original_prompt: str, disable_buttons: bool = False) -> Optional[discord.ui.View]:
        if disable_buttons or not AI_BUTTONS_ENABLED:
            return None
        if response.mode in {"moderation"}:
            return None
        return AIActionView(self, response, original_prompt)

    def _make_embed(
        self,
        response: AIResponse,
        *,
        author: discord.abc.User | discord.Member | None = None,
        text: str | None = None,
        page: tuple[int, int] | None = None,
    ) -> discord.Embed:
        color_map = {
            "academic": discord.Color.dark_teal(),
            "argument": discord.Color.dark_teal(),
            "study": discord.Color.dark_teal(),
            "code": discord.Color.green(),
            "search": discord.Color.gold(),
            "creative": discord.Color.purple(),
            "planning": discord.Color.blue(),
            "writing": discord.Color.blurple(),
            "moderation": discord.Color.red(),
            "chat": discord.Color.blurple(),
        }
        body = (text if text is not None else response.text).strip()
        embed = discord.Embed(
            description=body[:4090],
            color=color_map.get(response.mode, discord.Color.blurple()),
        )
        if author:
            avatar_url = getattr(author, "display_avatar", None)
            embed.set_author(
                name=getattr(author, "display_name", str(author)),
                icon_url=avatar_url.url if avatar_url else None,
            )
        tool_note = ""
        if response.used_tools:
            tool_note = " • " + ", ".join(response.used_tools[:2])
        cache_note = " • cache" if response.cache_hit else ""
        page_note = f" • parte {page[0]}/{page[1]}" if page and page[1] > 1 else ""
        embed.set_footer(
            text=(
                f"{BOT_NAME} • modo {_mode_label(response.mode)} • "
                f"{response.latency_ms / 1000:.1f}s{tool_note}{cache_note}{page_note}"
            )
        )
        return embed

    async def _send_long_reply(
        self,
        message: discord.Message,
        response: AIResponse,
        *,
        view: Optional[discord.ui.View] = None,
    ) -> None:
        safe_text = _normalize_ai_output(response.text) or "Não consegui gerar uma resposta agora."
        chunks = _split_discord_text(safe_text, DISCORD_TEXT_LIMIT)

        async with self.send_locks[message.channel.id]:
            try:
                await message.reply(chunks[0], mention_author=False)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                logger.warning("Falha ao enviar reply; enviando no canal. Erro: %s", exc)
                try:
                    await message.channel.send(chunks[0])
                except (discord.Forbidden, discord.HTTPException) as send_exc:
                    logger.error("Falha ao enviar resposta no canal: %s", send_exc)
                    return

            for chunk in chunks[1:]:
                try:
                    await message.channel.send(chunk)
                except (discord.Forbidden, discord.HTTPException) as exc:
                    logger.error("Falha ao enviar continuação da resposta: %s", exc)
                    break

    async def _send_interaction_followup(
        self,
        interaction: discord.Interaction,
        response: AIResponse,
        *,
        ephemeral: bool = False,
        view: Optional[discord.ui.View] = None,
    ) -> None:
        text = _normalize_ai_output(response.text) or "Não consegui gerar uma resposta agora."
        chunks = _split_discord_text(text, DISCORD_EMBED_LIMIT)
        total = len(chunks)

        for index, chunk in enumerate(chunks, start=1):
            embed = self._make_embed(
                response,
                author=interaction.user,
                text=chunk,
                page=(index, total),
            )
            await interaction.followup.send(
                embed=embed,
                view=view if index == total else None,
                ephemeral=ephemeral,
            )

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

        async with message.channel.typing() if AI_FAKE_TYPING_ENABLED else _NullAsyncContext():
            response = await self.get_ai_response(
                content,
                message.author.id,
                message.author.display_name,
                image_data=image_data,
                guild_id=message.guild.id,
                channel_id=message.channel.id,
            )

        await self._send_long_reply(message, response)

    @app_commands.command(name="chat", description=f"Conversa com o {BOT_NAME}.")
    @app_commands.describe(mensagem="Mensagem que deseja enviar para a IA.", modo="Força um modo de resposta.", privado="Se ativado, só você vê a resposta.")
    @app_commands.choices(modo=[
        app_commands.Choice(name="Auto", value="auto"),
        app_commands.Choice(name="Acadêmico", value="academic"),
        app_commands.Choice(name="Código", value="code"),
        app_commands.Choice(name="Busca", value="search"),
        app_commands.Choice(name="Texto", value="writing"),
        app_commands.Choice(name="Planejamento", value="planning"),
        app_commands.Choice(name="Criativo", value="creative"),
    ])
    async def chat_command(self, interaction: discord.Interaction, mensagem: str, modo: str = "auto", privado: bool = False) -> None:
        await interaction.response.defer(ephemeral=privado, thinking=True)
        selected_mode = modo or "auto"
        response = await self.get_ai_response(
            mensagem,
            interaction.user.id,
            interaction.user.display_name,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            forced_query_type=selected_mode,
        )
        view = self._make_view(response, mensagem)
        await self._send_interaction_followup(interaction, response, ephemeral=privado, view=view)

    @app_commands.command(name="estudar", description="Modo acadêmico pesado: explica, resume, cria quiz, plano ou banca avaliadora.")
    @app_commands.describe(tema="Tema, texto ou dúvida.", modo="Tipo de ajuda acadêmica.", privado="Se ativado, só você vê a resposta.")
    @app_commands.choices(modo=[
        app_commands.Choice(name="Explicar", value="explicar"),
        app_commands.Choice(name="Resumo", value="resumo"),
        app_commands.Choice(name="Quiz", value="quiz"),
        app_commands.Choice(name="Debate", value="debate"),
        app_commands.Choice(name="Plano de estudo", value="plano"),
        app_commands.Choice(name="Banca avaliadora", value="banca"),
    ])
    async def estudar_command(self, interaction: discord.Interaction, tema: str, modo: str, privado: bool = False) -> None:
        await interaction.response.defer(ephemeral=privado, thinking=True)
        instructions = {
            "explicar": "Explique como tutor acadêmico: definição, resposta direta, raciocínio, exemplo e limites.",
            "resumo": "Faça um resumo acadêmico organizado, com conceitos principais e revisão final.",
            "quiz": "Crie um quiz de estudo com perguntas graduais e gabarito comentado no final.",
            "debate": "Monte um debate: tese, antítese, argumentos fortes, objeções e síntese equilibrada.",
            "plano": "Crie um plano de estudo prático, com sequência, revisões e exercícios.",
            "banca": "Avalie como banca examinadora: clareza, precisão conceitual, argumentação, problemas e versão melhorada se houver texto.",
        }
        response = await self.get_ai_response(
            tema,
            interaction.user.id,
            interaction.user.display_name,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            forced_query_type="study",
            extra_instruction=instructions.get(modo),
        )
        view = self._make_view(response, tema)
        await self._send_interaction_followup(interaction, response, ephemeral=privado, view=view)

    @app_commands.command(name="analisar-argumento", description="Analisa premissas, conclusão, validade, solidez e falácias.")
    @app_commands.describe(argumento="Cole o argumento que deseja analisar.", privado="Se ativado, só você vê a resposta.")
    async def analisar_argumento_command(self, interaction: discord.Interaction, argumento: str, privado: bool = False) -> None:
        await interaction.response.defer(ephemeral=privado, thinking=True)
        response = await self.get_ai_response(
            argumento,
            interaction.user.id,
            interaction.user.display_name,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            forced_query_type="argument",
            extra_instruction="Extraia premissas, conclusão, forma lógica quando possível, validade, solidez, falácias e uma versão mais forte do argumento.",
        )
        view = self._make_view(response, argumento)
        await self._send_interaction_followup(interaction, response, ephemeral=privado, view=view)

    @app_commands.command(name="perfil-ia", description=f"Define seu estilo de resposta preferido no {BOT_NAME}.")
    @app_commands.describe(estilo="Como você prefere que a IA responda.")
    @app_commands.choices(estilo=[
        app_commands.Choice(name="Curto", value="curto"),
        app_commands.Choice(name="Normal", value="normal"),
        app_commands.Choice(name="Profundo", value="profundo"),
        app_commands.Choice(name="Professor", value="professor"),
        app_commands.Choice(name="Criativo", value="criativo"),
    ])
    async def perfil_ia_command(self, interaction: discord.Interaction, estilo: str) -> None:
        profile = await self._get_user_profile(interaction.user.id)
        profile["style"] = estilo
        await self._save_user_profile(interaction.user.id, profile)
        await interaction.response.send_message(f"<:1000032082:1507948289444544512> Perfil de IA atualizado para: **{estilo}**.", ephemeral=True)

    @app_commands.command(name="memoria-ia", description="Mostra ou limpa seu histórico de conversa e preferências da IA.")
    @app_commands.describe(acao="Escolha o que fazer com sua memória local da IA.")
    @app_commands.choices(acao=[
        app_commands.Choice(name="Ver perfil", value="ver"),
        app_commands.Choice(name="Limpar conversa", value="limpar_conversa"),
        app_commands.Choice(name="Resetar perfil", value="resetar_perfil"),
    ])
    async def memoria_ia_command(self, interaction: discord.Interaction, acao: str) -> None:
        if acao == "limpar_conversa":
            self.history[interaction.user.id] = []
            await interaction.response.send_message("<:1000032056:1507947210057322637> Histórico de conversa apagado.", ephemeral=True)
            return
        if acao == "resetar_perfil":
            profile = {"style": "normal", "notes": ""}
            await self._save_user_profile(interaction.user.id, profile)
            await interaction.response.send_message("<:1000032056:1507947210057322637> Perfil de IA resetado.", ephemeral=True)
            return
        profile = await self._get_user_profile(interaction.user.id)
        await interaction.response.send_message(
            f"Seu perfil de IA:\n- estilo: `{profile.get('style', 'normal')}`\n- mensagens no histórico curto: `{len(self.history[interaction.user.id])}`",
            ephemeral=True,
        )

    @app_commands.command(name="limpar-conversa", description=f"Apaga seu histórico de conversa com o {BOT_NAME}.")
    async def clear_history(self, interaction: discord.Interaction) -> None:
        self.history[interaction.user.id] = []
        await interaction.response.send_message("<:1000032056:1507947210057322637> Histórico apagado.", ephemeral=True)

    @app_commands.command(name="canal-ia", description="Ativa ou desativa a IA automática em um canal.")
    @app_commands.describe(canal="Canal que deseja configurar.")
    @app_commands.default_permissions(administrator=True)
    async def set_ai_channel(self, interaction: discord.Interaction, canal: discord.TextChannel) -> None:
        channels = await self._get_ai_channels(interaction.guild_id)
        enabled = canal.id not in channels
        await self._set_ai_channel(interaction.guild_id, canal.id, enabled)
        status = "ativada" if enabled else "desativada"
        await interaction.response.send_message(f"<:1000032072:1507947958723809340> IA {status} em {canal.mention}.", ephemeral=True)

    @app_commands.command(name="status-ia", description=f"Mostra o status avançado da IA do {BOT_NAME}.")
    @app_commands.default_permissions(administrator=True)
    async def status_ia(self, interaction: discord.Interaction) -> None:
        metrics = list(self.recent_metrics)
        avg_latency = sum(m["latency_ms"] for m in metrics) / len(metrics) if metrics else 0
        modes = Counter(m["mode"] for m in metrics)
        cache_hits = sum(1 for m in metrics if m.get("cache_hit"))
        searches = sum(1 for m in metrics if m.get("used_search"))
        library_chunks = len(await asyncio.to_thread(_read_library_files))

        embed = discord.Embed(title="<:1000032072:1507947958723809340> Status avançado da IA", color=discord.Color.blurple())
        embed.add_field(name="<:1000032066:1507947724560011375> Provedor (geral)", value="Google Gemini", inline=True)
        embed.add_field(name="<:1000032074:1507948021013549166> Chaves flash (normais)", value=str(len(self.gemini_keys)), inline=True)
        embed.add_field(name="<:1000032074:1507948021013549166> Chaves Groq (acadêmico)", value=str(len(self.groq_academic_keys)), inline=True)
        embed.add_field(name="<:1000032054:1507947088590274580> Cliente", value="google-generativeai + Groq (REST)", inline=True)
        embed.add_field(name="Modelo geral", value=f"`{GEMINI_MODEL}`", inline=True)
        embed.add_field(name="Modelo acadêmico", value=f"`{GROQ_ACADEMIC_MODEL}`", inline=True)
        embed.add_field(
            name="Busca externa",
            value=(
                f"Tavily: {'ativo' if os.getenv('TAVILY_API_KEY') else 'sem chave'}\n"
                f"GNews: {'ativo' if os.getenv('GNEWS_API_KEY') else 'sem chave'}\n"
                "Wikipedia/DDG/arXiv/SEP: fallback sem chave"
            ),
            inline=False,
        )
        embed.add_field(
            name="Performance recente",
            value=(
                f"Requisições medidas: `{len(metrics)}`\n"
                f"Latência média: `{avg_latency / 1000:.1f}s`\n"
                f"Buscas usadas: `{searches}`\n"
                f"Cache hits: `{cache_hits}`\n"
                f"Modo mais usado: `{modes.most_common(1)[0][0] if modes else 'n/a'}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Biblioteca local",
            value=f"Trechos indexados: `{library_chunks}`\nCaminhos: `{os.getenv('AI_LIBRARY_PATH', 'data/library,library,books')}`",
            inline=False,
        )
        embed.add_field(
            name="Caches",
            value=f"Acadêmico: `{len(ACADEMIC_CACHE)}` • Web: `{len(SEARCH_CACHE)}` • Respostas: `{len(RESPONSE_CACHE)}`",
            inline=False,
        )
        embed.set_footer(text=f"DB extras: {'ativo' if self.db_extras_ready else 'inativo'} • Botões: {'ativo' if AI_BUTTONS_ENABLED else 'inativo'}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="testar-ia", description="Testa rapidamente os modelos configurados.")
    @app_commands.default_permissions(administrator=True)
    async def testar_ia(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        messages = [
            {"role": "system", "content": "Responda apenas: funcionando."},
            {"role": "user", "content": "teste"},
        ]
        lines = []
        if self.gemini_keys:
            result = await self._call_gemini(messages, max_tokens=20, temperature=0.1)
            lines.append(f"`{GEMINI_MODEL}` (geral): {'ok' if result else 'falhou'}")
        else:
            lines.append("Nenhuma chave Gemini configurada.")

        if self.groq_academic_keys:
            result = await self._call_groq_academic(messages, max_tokens=20, temperature=0.1)
            lines.append(f"`{GROQ_ACADEMIC_MODEL}` (acadêmico): {'ok' if result else 'falhou'}")
        else:
            lines.append("Nenhuma chave Groq acadêmica configurada.")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(name="limpar-cache-ia", description="Limpa caches de busca, biblioteca e respostas da IA.")
    @app_commands.default_permissions(administrator=True)
    async def limpar_cache_ia(self, interaction: discord.Interaction) -> None:
        SEARCH_CACHE.clear()
        ACADEMIC_CACHE.clear()
        LIBRARY_INDEX_CACHE.clear()
        RESPONSE_CACHE.clear()
        await interaction.response.send_message("<:1000032056:1507947210057322637> Caches da IA limpos.", ephemeral=True)


class _NullAsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def setup(bot: commands.Bot) -> None:
    cog = AIChat(bot)
    await bot.add_cog(cog)
    await cog.prepare()
