from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import logging
import math
import os
import random
import re
import time
import urllib.parse
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    genai_types = None
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

MAX_HISTORY_MESSAGES = max(2, min(_safe_int(os.getenv("AI_MAX_HISTORY"), 10), 30))

# Limites de saída. O orçamento real é recalculado por requisição para nunca
# ultrapassar o teto de tokens do provedor acadêmico.
NORMAL_MAX_TOKENS = max(256, min(_safe_int(os.getenv("AI_NORMAL_MAX_TOKENS"), 1200), 4096))
ACADEMIC_MAX_TOKENS = max(384, min(_safe_int(os.getenv("AI_ACADEMIC_MAX_TOKENS"), 1500), 4096))
DEEP_MAX_TOKENS = max(512, min(_safe_int(os.getenv("AI_DEEP_MAX_TOKENS"), 1900), 6144))
MAX_CONTINUATIONS = max(0, min(_safe_int(os.getenv("AI_MAX_CONTINUATIONS"), 1), 3))
COOLDOWN_SECONDS = max(0, _safe_int(os.getenv("AI_COOLDOWN_SECONDS"), 2))
MODEL_TIMEOUT_SECONDS = max(10.0, float(os.getenv("AI_MODEL_TIMEOUT", "50")))
DISCORD_TEXT_LIMIT = 1900
DISCORD_EMBED_LIMIT = 3900
KNOWLEDGE_CUTOFF_LABEL = "janeiro de 2025"

# O plano gratuito/on-demand do openai/gpt-oss-120b expõe 8K TPM. A aplicação
# trabalha abaixo do teto, deixando margem para diferenças entre estimativa local
# e tokenização real do provedor.
GROQ_TPM_LIMIT = max(2000, _safe_int(os.getenv("GROQ_TPM_LIMIT"), 8000))
GROQ_TARGET_TOTAL_TOKENS = max(
    1500,
    min(_safe_int(os.getenv("GROQ_TARGET_TOTAL_TOKENS"), 7350), GROQ_TPM_LIMIT - 200),
)
GROQ_TOKEN_SAFETY_MARGIN = max(100, _safe_int(os.getenv("GROQ_TOKEN_SAFETY_MARGIN"), 450))
GROQ_MIN_OUTPUT_TOKENS = max(128, _safe_int(os.getenv("GROQ_MIN_OUTPUT_TOKENS"), 384))
GROQ_MAX_OUTPUT_TOKENS = max(
    GROQ_MIN_OUTPUT_TOKENS,
    min(_safe_int(os.getenv("GROQ_MAX_OUTPUT_TOKENS"), 1500), 3000),
)
GROQ_COMPACT_RETRIES = max(1, min(_safe_int(os.getenv("GROQ_COMPACT_RETRIES"), 3), 5))
GROQ_REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "medium").strip().lower()
if GROQ_REASONING_EFFORT not in {"low", "medium", "high"}:
    GROQ_REASONING_EFFORT = "medium"

# Limites de contexto antes da etapa de orçamento. A compactação final é feita em
# tokens estimados, e não apenas em caracteres.
ACADEMIC_CONTEXT_MAX_CHARS_DEFAULT = max(
    1800,
    min(_safe_int(os.getenv("ACADEMIC_CONTEXT_MAX_CHARS"), 5200), 12000),
)
WEB_CONTEXT_MAX_CHARS_DEFAULT = max(
    1400,
    min(_safe_int(os.getenv("WEB_CONTEXT_MAX_CHARS"), 4800), 10000),
)
HISTORY_MAX_CHARS = max(1500, min(_safe_int(os.getenv("AI_HISTORY_MAX_CHARS"), 7000), 20000))
USER_MESSAGE_MAX_CHARS = max(1000, min(_safe_int(os.getenv("AI_USER_MESSAGE_MAX_CHARS"), 12000), 30000))
SOURCE_LINE_MAX_CHARS = max(280, min(_safe_int(os.getenv("AI_SOURCE_LINE_MAX_CHARS"), 850), 1600))

# Concorrência controlada evita uma rajada de requisições consumir todo o TPM em
# poucos milissegundos quando vários membros perguntam ao mesmo tempo.
GEMINI_MAX_CONCURRENCY = max(1, min(_safe_int(os.getenv("GEMINI_MAX_CONCURRENCY"), 5), 20))
GROQ_MAX_CONCURRENCY = max(1, min(_safe_int(os.getenv("GROQ_MAX_CONCURRENCY"), 2), 8))
SEARCH_MAX_CONCURRENCY = max(1, min(_safe_int(os.getenv("SEARCH_MAX_CONCURRENCY"), 4), 12))
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "low").strip().lower()
GEMINI_ACADEMIC_THINKING_LEVEL = os.getenv("GEMINI_ACADEMIC_THINKING_LEVEL", "medium").strip().lower()
for _thinking_name in ("GEMINI_THINKING_LEVEL", "GEMINI_ACADEMIC_THINKING_LEVEL"):
    if globals()[_thinking_name] not in {"low", "medium", "high"}:
        globals()[_thinking_name] = "low" if _thinking_name == "GEMINI_THINKING_LEVEL" else "medium"

# Backoff e circuit breaker por chave.
PROVIDER_FAILURE_THRESHOLD = max(2, _safe_int(os.getenv("AI_PROVIDER_FAILURE_THRESHOLD"), 4))
PROVIDER_COOLDOWN_SECONDS = max(10, _safe_int(os.getenv("AI_PROVIDER_COOLDOWN_SECONDS"), 45))
PROVIDER_AUTH_COOLDOWN_SECONDS = max(60, _safe_int(os.getenv("AI_PROVIDER_AUTH_COOLDOWN_SECONDS"), 900))
PROVIDER_TRANSIENT_RETRIES = max(0, min(_safe_int(os.getenv("AI_PROVIDER_TRANSIENT_RETRIES"), 1), 3))

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
    provider: str = ""
    input_tokens_estimate: int = 0
    output_tokens_requested: int = 0
    compaction_level: int = 0
    fallback_used: bool = False


@dataclass
class AIResponse:
    text: str
    mode: str
    sources: list[str] = field(default_factory=list)
    used_tools: list[str] = field(default_factory=list)
    model: str = ""
    latency_ms: int = 0
    cache_hit: bool = False
    trace_id: str = ""
    finish_reason: str = ""
    input_tokens_estimate: int = 0
    output_tokens_requested: int = 0
    compaction_level: int = 0
    fallback_used: bool = False

# ══════════════════════════════════════════════════════════════════════════════
# INFRAESTRUTURA DE CONFIABILIDADE, ORÇAMENTO E OBSERVABILIDADE
# ══════════════════════════════════════════════════════════════════════════════

class ProviderErrorKind(str, Enum):
    """Categorias internas para decidir retry, compactação ou failover."""

    OVERSIZED = "oversized"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    TIMEOUT = "timeout"
    TRANSIENT = "transient"
    SAFETY = "safety"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ProviderErrorInfo:
    """Descrição normalizada de uma falha de provedor."""

    kind: ProviderErrorKind
    status: int | None = None
    message: str = ""
    retry_after: float | None = None
    requested_tokens: int | None = None
    token_limit: int | None = None
    raw_headers: dict[str, str] = field(default_factory=dict)

    @property
    def can_retry_same_key(self) -> bool:
        return self.kind in {
            ProviderErrorKind.TIMEOUT,
            ProviderErrorKind.TRANSIENT,
        }

    @property
    def should_rotate_key(self) -> bool:
        return self.kind in {
            ProviderErrorKind.RATE_LIMIT,
            ProviderErrorKind.AUTH,
            ProviderErrorKind.TIMEOUT,
            ProviderErrorKind.TRANSIENT,
        }

    @property
    def needs_compaction(self) -> bool:
        return self.kind == ProviderErrorKind.OVERSIZED


class ProviderRequestError(RuntimeError):
    """Erro HTTP enriquecido, preservando status, corpo e cabeçalhos."""

    def __init__(
        self,
        status: int,
        body: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status = int(status)
        self.body = body or ""
        self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        super().__init__(f"HTTP {self.status}: {self.body[:600]}")


@dataclass(slots=True)
class TokenEstimate:
    """Estimativa conservadora de tokens antes de chamar uma API."""

    text_tokens: int
    message_overhead: int = 0
    image_tokens: int = 0

    @property
    def total(self) -> int:
        return max(0, self.text_tokens + self.message_overhead + self.image_tokens)


@dataclass(slots=True)
class RequestPlan:
    """Payload já ajustado para caber em um limite de TPM/contexto."""

    messages: list[dict[str, str]]
    max_output_tokens: int
    estimated_input_tokens: int
    estimated_total_tokens: int
    compaction_level: int
    dropped_history_messages: int = 0
    context_chars_before: int = 0
    context_chars_after: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def compacted(self) -> bool:
        return self.compaction_level > 0 or bool(self.notes)


@dataclass(slots=True)
class KeyHealth:
    """Estado em memória de uma chave sem registrar o segredo em logs."""

    provider: str
    key_number: int
    consecutive_failures: int = 0
    successes: int = 0
    failures: int = 0
    cooldown_until: float = 0.0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    last_error_kind: str = ""
    last_error_message: str = ""
    remaining_tokens: int | None = None
    token_limit: int | None = None
    token_reset_seconds: float | None = None

    @property
    def available(self) -> bool:
        return time.monotonic() >= self.cooldown_until

    @property
    def cooldown_remaining(self) -> float:
        return max(0.0, self.cooldown_until - time.monotonic())


@dataclass(slots=True)
class RequestTrace:
    """Registro leve de uma resposta para diagnóstico administrativo."""

    trace_id: str
    user_id: int
    mode: str
    provider: str
    model: str
    started_at: float
    latency_ms: int
    input_tokens_estimate: int = 0
    output_tokens_requested: int = 0
    compaction_level: int = 0
    fallback_used: bool = False
    continuation_count: int = 0
    finish_reason: str = ""
    error: str = ""


@dataclass(slots=True)
class QualityReport:
    """Resultado da inspeção local da resposta antes de enviá-la ao Discord."""

    text: str
    empty: bool = False
    likely_cut_off: bool = False
    repaired_markdown: bool = False
    repeated_ratio: float = 0.0
    warnings: list[str] = field(default_factory=list)


class ProviderHealthRegistry:
    """
    Circuit breaker simples por chave.

    Ele evita insistir em uma chave que acabou de responder com 401, 403, 429 ou
    uma sequência de 5xx. As chaves nunca são armazenadas aqui, apenas o número
    ordinal usado nos logs.
    """

    def __init__(self) -> None:
        self._items: dict[tuple[str, int], KeyHealth] = {}

    def get(self, provider: str, key_number: int) -> KeyHealth:
        key = (provider, int(key_number))
        if key not in self._items:
            self._items[key] = KeyHealth(provider=provider, key_number=int(key_number))
        return self._items[key]

    def ordered_indexes(
        self,
        provider: str,
        total_keys: int,
        preferred_index: int = 0,
    ) -> list[int]:
        if total_keys <= 0:
            return []

        indexes = list(range(total_keys))
        preferred_index %= total_keys
        indexes = indexes[preferred_index:] + indexes[:preferred_index]

        available = [
            index
            for index in indexes
            if self.get(provider, index + 1).available
        ]
        cooling = [index for index in indexes if index not in available]

        # Chaves disponíveis com menor número de falhas vêm primeiro.
        available.sort(
            key=lambda index: (
                self.get(provider, index + 1).consecutive_failures,
                -self.get(provider, index + 1).last_success_at,
                index,
            )
        )
        cooling.sort(
            key=lambda index: (
                self.get(provider, index + 1).cooldown_until,
                index,
            )
        )

        # Se todas estiverem em cooldown, permite testar a que ficará disponível
        # primeiro. Isso evita uma indisponibilidade artificial permanente.
        return available or cooling[:1]

    def mark_success(
        self,
        provider: str,
        key_number: int,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        state = self.get(provider, key_number)
        state.successes += 1
        state.consecutive_failures = 0
        state.cooldown_until = 0.0
        state.last_success_at = time.monotonic()
        state.last_error_kind = ""
        state.last_error_message = ""
        self._apply_rate_headers(state, headers or {})

    def mark_failure(
        self,
        provider: str,
        key_number: int,
        error: ProviderErrorInfo,
    ) -> None:
        state = self.get(provider, key_number)
        state.failures += 1
        state.consecutive_failures += 1
        state.last_failure_at = time.monotonic()
        state.last_error_kind = error.kind.value
        state.last_error_message = error.message[:250]
        self._apply_rate_headers(state, error.raw_headers)

        cooldown = 0.0
        if error.kind == ProviderErrorKind.AUTH:
            cooldown = float(PROVIDER_AUTH_COOLDOWN_SECONDS)
        elif error.kind == ProviderErrorKind.RATE_LIMIT:
            cooldown = max(float(error.retry_after or 0), float(PROVIDER_COOLDOWN_SECONDS))
        elif error.kind in {ProviderErrorKind.TIMEOUT, ProviderErrorKind.TRANSIENT}:
            if state.consecutive_failures >= PROVIDER_FAILURE_THRESHOLD:
                cooldown = float(PROVIDER_COOLDOWN_SECONDS)

        if cooldown > 0:
            state.cooldown_until = max(
                state.cooldown_until,
                time.monotonic() + cooldown,
            )

    def snapshot(self) -> list[KeyHealth]:
        return sorted(
            (copy.copy(item) for item in self._items.values()),
            key=lambda item: (item.provider, item.key_number),
        )

    @staticmethod
    def _apply_rate_headers(
        state: KeyHealth,
        headers: Mapping[str, str],
    ) -> None:
        lowered = {str(k).lower(): str(v) for k, v in headers.items()}
        state.remaining_tokens = _parse_optional_int(
            lowered.get("x-ratelimit-remaining-tokens")
        )
        state.token_limit = _parse_optional_int(
            lowered.get("x-ratelimit-limit-tokens")
        )
        state.token_reset_seconds = _parse_duration_seconds(
            lowered.get("x-ratelimit-reset-tokens")
        )


class TokenEstimator:
    """
    Estimador local conservador.

    A tokenização exata depende do modelo. Para não depender de uma biblioteca
    pesada, o cálculo combina caracteres, palavras, URLs, código e Unicode. O
    objetivo não é reproduzir o tokenizer, e sim ficar um pouco acima da média
    real para evitar novamente o erro 413 de 8K TPM.
    """

    _WORD_RE = re.compile(r"\S+")
    _URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
    _CODE_RE = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)

    def estimate_text(self, text: str | None) -> int:
        if not text:
            return 0

        value = str(text)
        chars = len(value)
        words = len(self._WORD_RE.findall(value))
        non_ascii = sum(1 for char in value if ord(char) > 127)
        punctuation = sum(1 for char in value if char in "{}[]()<>:;,.!?/\\|=+-_*")
        urls = self._URL_RE.findall(value)
        code_blocks = self._CODE_RE.findall(value)

        # Português e Markdown costumam ficar perto de 3 a 4 caracteres/token.
        # O maior dos dois cálculos é usado para não subestimar textos com muitas
        # palavras curtas.
        by_chars = math.ceil(chars / 3.15)
        by_words = math.ceil(words * 1.34)

        unicode_penalty = math.ceil(non_ascii / 10)
        punctuation_penalty = math.ceil(punctuation / 18)
        url_penalty = sum(max(2, math.ceil(len(url) / 8)) for url in urls)
        code_penalty = sum(max(4, math.ceil(len(block) / 22)) for block in code_blocks)

        return max(1, max(by_chars, by_words) + unicode_penalty + punctuation_penalty + url_penalty + code_penalty)

    def estimate_messages(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        image_count: int = 0,
    ) -> TokenEstimate:
        text_tokens = 0
        overhead = 3

        for message in messages:
            role = str(message.get("role") or "user")
            content = message.get("content") or ""
            text_tokens += self.estimate_text(str(content))
            overhead += 5 + self.estimate_text(role)

        # Uma imagem pode variar bastante. Esta reserva é deliberadamente alta
        # para diagnósticos, mas o limite Groq não recebe imagens neste projeto.
        image_tokens = image_count * 1200
        return TokenEstimate(
            text_tokens=text_tokens,
            message_overhead=overhead,
            image_tokens=image_tokens,
        )

    def truncate_to_tokens(
        self,
        text: str,
        max_tokens: int,
        *,
        preserve_tail: bool = False,
        marker: str = "\n[… conteúdo compactado …]\n",
    ) -> str:
        value = str(text or "")
        max_tokens = max(1, int(max_tokens))
        if self.estimate_text(value) <= max_tokens:
            return value

        # Busca binária por caracteres, usando o próprio estimador como régua.
        low = 1
        high = len(value)
        best = ""
        while low <= high:
            middle = (low + high) // 2
            candidate = value[-middle:] if preserve_tail else value[:middle]
            if self.estimate_text(candidate) <= max_tokens:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1

        if not best:
            return value[-32:] if preserve_tail else value[:32]

        if preserve_tail:
            result = marker.strip() + "\n" + best.lstrip()
        else:
            result = best.rstrip() + "\n" + marker.strip()
        return result.strip()

    def fit_head_and_tail(
        self,
        text: str,
        max_tokens: int,
        *,
        head_ratio: float = 0.72,
    ) -> str:
        value = str(text or "").strip()
        if self.estimate_text(value) <= max_tokens:
            return value

        head_budget = max(1, int(max_tokens * head_ratio))
        tail_budget = max(1, max_tokens - head_budget - 12)
        head = self.truncate_to_tokens(value, head_budget)
        tail = self.truncate_to_tokens(value, tail_budget, preserve_tail=True)
        return f"{head}\n\n{tail}".strip()


class PromptBudgeter:
    """Cria versões progressivamente menores do mesmo pedido."""

    EXTERNAL_MARKER = "CONTEXTO EXTERNO/BIBLIOTECA, use sem inventar além dele:"
    EXTRA_MARKER = "INSTRUÇÃO EXTRA DO COMANDO:"

    def __init__(self, estimator: TokenEstimator) -> None:
        self.estimator = estimator

    def plan_groq(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        requested_output_tokens: int,
        compaction_level: int = 0,
    ) -> RequestPlan:
        level = max(0, min(int(compaction_level), 5))
        original = [
            {
                "role": str(message.get("role") or "user"),
                "content": str(message.get("content") or ""),
            }
            for message in messages
        ]

        context_before = sum(
            len(message["content"])
            for message in original
            if self.EXTERNAL_MARKER in message["content"]
        )

        compacted, dropped, notes = self._compact_messages(original, level)
        estimate = self.estimator.estimate_messages(compacted)

        requested = max(
            GROQ_MIN_OUTPUT_TOKENS,
            min(int(requested_output_tokens), GROQ_MAX_OUTPUT_TOKENS),
        )
        available = (
            GROQ_TARGET_TOTAL_TOKENS
            - GROQ_TOKEN_SAFETY_MARGIN
            - estimate.total
        )
        output_tokens = min(requested, max(0, available))

        # Se ainda não há espaço mínimo para uma resposta útil, faz uma última
        # compactação por orçamento, mesmo que o nível solicitado fosse baixo.
        if output_tokens < GROQ_MIN_OUTPUT_TOKENS:
            hard_input_limit = max(
                600,
                GROQ_TARGET_TOTAL_TOKENS
                - GROQ_TOKEN_SAFETY_MARGIN
                - GROQ_MIN_OUTPUT_TOKENS,
            )
            compacted, hard_notes = self._hard_fit(compacted, hard_input_limit)
            notes.extend(hard_notes)
            estimate = self.estimator.estimate_messages(compacted)
            available = (
                GROQ_TARGET_TOTAL_TOKENS
                - GROQ_TOKEN_SAFETY_MARGIN
                - estimate.total
            )
            output_tokens = min(requested, max(128, available))

        output_tokens = max(128, min(output_tokens, GROQ_MAX_OUTPUT_TOKENS))
        total = estimate.total + output_tokens + GROQ_TOKEN_SAFETY_MARGIN
        context_after = sum(
            len(message["content"])
            for message in compacted
            if self.EXTERNAL_MARKER in message["content"]
        )

        return RequestPlan(
            messages=compacted,
            max_output_tokens=output_tokens,
            estimated_input_tokens=estimate.total,
            estimated_total_tokens=total,
            compaction_level=level,
            dropped_history_messages=dropped,
            context_chars_before=context_before,
            context_chars_after=context_after,
            notes=notes,
        )

    def continuation_plan(
        self,
        *,
        previous_text: str,
        original_question: str,
        requested_output_tokens: int = 600,
    ) -> RequestPlan:
        tail = self.estimator.truncate_to_tokens(
            previous_text,
            900,
            preserve_tail=True,
        )
        question = self.estimator.truncate_to_tokens(original_question, 550)
        messages = [
            {
                "role": "system",
                "content": (
                    "Continue uma resposta acadêmica interrompida. Não reinicie, "
                    "não repita introduções e não invente fontes. Termine em formato "
                    "Markdown válido, priorizando conclusão e itens pendentes."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Pergunta original resumida:\n{question}\n\n"
                    f"Final da resposta anterior:\n{tail}\n\n"
                    "Continue somente do ponto interrompido e conclua."
                ),
            },
        ]
        return self.plan_groq(
            messages,
            requested_output_tokens=requested_output_tokens,
            compaction_level=2,
        )

    def _compact_messages(
        self,
        messages: list[dict[str, str]],
        level: int,
    ) -> tuple[list[dict[str, str]], int, list[str]]:
        if not messages:
            return [], 0, []

        notes: list[str] = []
        system_messages = [m for m in messages if m["role"] == "system"]
        dialogue = [m for m in messages if m["role"] != "system"]

        system = system_messages[-1]["content"] if system_messages else ""
        if level >= 1:
            target = {1: 1500, 2: 1050, 3: 780, 4: 560, 5: 420}[level]
            system = self._compact_system_prompt(system, target)
            notes.append(f"system compactado nível {level}")

        history_limits = {
            0: min(MAX_HISTORY_MESSAGES, 10),
            1: 8,
            2: 6,
            3: 4,
            4: 2,
            5: 0,
        }
        keep_history = history_limits[level]

        last_user_index = max(
            (index for index, item in enumerate(dialogue) if item["role"] == "user"),
            default=len(dialogue) - 1,
        )
        last_user = dialogue[last_user_index] if dialogue else {"role": "user", "content": "Olá."}
        history = dialogue[:last_user_index]
        kept_history = history[-keep_history:] if keep_history else []
        dropped = max(0, len(history) - len(kept_history))
        if dropped:
            notes.append(f"{dropped} mensagem(ns) antigas removidas")

        # Limita caracteres totais do histórico, mantendo o trecho mais recente.
        history_char_limits = {
            0: HISTORY_MAX_CHARS,
            1: 5600,
            2: 3900,
            3: 2500,
            4: 1200,
            5: 0,
        }
        kept_history = self._fit_history_chars(
            kept_history,
            history_char_limits[level],
        )

        user_limits = {
            0: USER_MESSAGE_MAX_CHARS,
            1: 10000,
            2: 7600,
            3: 5400,
            4: 3600,
            5: 2300,
        }
        context_limits = {
            0: ACADEMIC_CONTEXT_MAX_CHARS_DEFAULT,
            1: 4200,
            2: 3200,
            3: 2300,
            4: 1500,
            5: 900,
        }
        compact_user, user_notes = self._compact_user_message(
            last_user["content"],
            total_char_limit=user_limits[level],
            context_char_limit=context_limits[level],
        )
        notes.extend(user_notes)

        result: list[dict[str, str]] = []
        if system:
            result.append({"role": "system", "content": system})
        result.extend(kept_history)
        result.append({"role": "user", "content": compact_user})
        return result, dropped, notes

    def _hard_fit(
        self,
        messages: list[dict[str, str]],
        token_limit: int,
    ) -> tuple[list[dict[str, str]], list[str]]:
        notes: list[str] = []
        if self.estimator.estimate_messages(messages).total <= token_limit:
            return messages, notes

        system = next((m for m in messages if m["role"] == "system"), None)
        last_user = next((m for m in reversed(messages) if m["role"] == "user"), None)
        reduced: list[dict[str, str]] = []

        system_budget = max(180, int(token_limit * 0.24))
        user_budget = max(320, token_limit - system_budget - 30)

        if system:
            reduced.append(
                {
                    "role": "system",
                    "content": self.estimator.truncate_to_tokens(
                        system["content"],
                        system_budget,
                    ),
                }
            )
        if last_user:
            reduced.append(
                {
                    "role": "user",
                    "content": self.estimator.fit_head_and_tail(
                        last_user["content"],
                        user_budget,
                    ),
                }
            )
        notes.append("hard-fit aplicado para respeitar TPM")
        return reduced, notes

    def _compact_system_prompt(self, text: str, target_tokens: int) -> str:
        compact = (
            f"Você é {BOT_NAME}, assistente em português brasileiro. "
            "Responda certo, completo e sem inventar fontes. Dê a resposta direta "
            "primeiro; em assuntos acadêmicos, defina termos, explique raciocínio, "
            "diferencie fato de interpretação e mostre objeções relevantes. Use o "
            "contexto externo apenas quando fornecido e não extrapole. Para fatos "
            "atuais, diga quando não houver confirmação. Termine frases, listas, "
            "negritos e blocos de código. Seja seguro e não revele chaves ou dados "
            "internos. A data atual está no pedido e o corte interno é "
            f"{KNOWLEDGE_CUTOFF_LABEL}."
        )
        if target_tokens >= self.estimator.estimate_text(compact):
            return compact
        return self.estimator.truncate_to_tokens(compact, target_tokens)

    def _compact_user_message(
        self,
        content: str,
        *,
        total_char_limit: int,
        context_char_limit: int,
    ) -> tuple[str, list[str]]:
        notes: list[str] = []
        value = str(content or "").strip()
        if self.EXTERNAL_MARKER in value:
            before, after = value.split(self.EXTERNAL_MARKER, 1)
            external, suffix = self._split_external_suffix(after)
            compact_external = self._compact_source_lines(
                external,
                context_char_limit,
            )
            if len(compact_external) < len(external.strip()):
                notes.append("contexto externo reduzido")
            value = (
                before.rstrip()
                + "\n\n"
                + self.EXTERNAL_MARKER
                + "\n"
                + compact_external
                + suffix
            ).strip()

        if len(value) > total_char_limit:
            # Mantém começo e final, porque instruções extras ficam normalmente no
            # final do payload.
            head_chars = int(total_char_limit * 0.76)
            tail_chars = max(200, total_char_limit - head_chars - 35)
            value = (
                value[:head_chars].rstrip()
                + "\n\n[… mensagem compactada …]\n\n"
                + value[-tail_chars:].lstrip()
            )
            notes.append("mensagem do usuário compactada")
        return value, notes

    def _split_external_suffix(self, after: str) -> tuple[str, str]:
        if self.EXTRA_MARKER in after:
            external, suffix = after.split(self.EXTRA_MARKER, 1)
            return external.strip(), f"\n\n{self.EXTRA_MARKER} {suffix.strip()}"
        return after.strip(), ""

    def _compact_source_lines(self, context: str, char_limit: int) -> str:
        lines = [line.strip() for line in context.splitlines() if line.strip()]
        if not lines:
            return ""

        unique: list[str] = []
        seen: set[str] = set()
        for line in lines:
            normalized = re.sub(r"\s+", " ", line).lower()[:300]
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(line[:SOURCE_LINE_MAX_CHARS])

        result: list[str] = []
        used = 0
        for line in unique:
            extra = len(line) + (1 if result else 0)
            if used + extra > char_limit:
                remaining = char_limit - used
                if remaining >= 180:
                    result.append(line[:remaining].rstrip() + "…")
                break
            result.append(line)
            used += extra
        return "\n".join(result)

    @staticmethod
    def _fit_history_chars(
        history: list[dict[str, str]],
        char_limit: int,
    ) -> list[dict[str, str]]:
        if char_limit <= 0:
            return []
        kept_reversed: list[dict[str, str]] = []
        used = 0
        for message in reversed(history):
            content = message["content"]
            if used + len(content) > char_limit:
                remaining = char_limit - used
                if remaining >= 300:
                    kept_reversed.append(
                        {
                            "role": message["role"],
                            "content": content[-remaining:],
                        }
                    )
                break
            kept_reversed.append(dict(message))
            used += len(content)
        return list(reversed(kept_reversed))


class ResponseQualityGate:
    """Normaliza e verifica respostas sem pedir outra geração desnecessária."""

    def inspect(self, text: str, finish_reason: str = "") -> QualityReport:
        value = _normalize_ai_output(text)
        report = QualityReport(text=value)

        if not value.strip():
            report.empty = True
            report.warnings.append("resposta vazia")
            return report

        report.likely_cut_off = _looks_cut_off(value, finish_reason)
        if report.likely_cut_off:
            report.warnings.append("possível corte no final")

        repaired, changed = self._repair_markdown(value)
        if changed:
            report.text = repaired
            report.repaired_markdown = True
            report.warnings.append("markdown reparado")

        report.repeated_ratio = self._repetition_ratio(report.text)
        if report.repeated_ratio > 0.42:
            report.text = self._deduplicate_paragraphs(report.text)
            report.warnings.append("parágrafos repetidos removidos")

        return report

    def safe_fallback_text(self, mode: str) -> str:
        if mode in {"academic", "study", "argument", "code"}:
            return (
                "Não consegui concluir a análise acadêmica nesta tentativa. "
                "O serviço externo recusou o tamanho ou ficou indisponível. "
                "Tente novamente com a pergunta um pouco mais específica."
            )
        return "Estou temporariamente indisponível. Tente novamente em instantes."

    @staticmethod
    def _repair_markdown(text: str) -> tuple[str, bool]:
        value = text.rstrip()
        changed = False

        if value.count("```") % 2:
            value += "\n```"
            changed = True

        # Fecha negrito apenas quando há um delimitador órfão. Não tenta corrigir
        # itálico simples, pois listas com asterisco poderiam ser alteradas.
        if len(re.findall(r"(?<!\\)\*\*", value)) % 2:
            value += "**"
            changed = True

        return value, changed

    @staticmethod
    def _repetition_ratio(text: str) -> float:
        paragraphs = [
            re.sub(r"\s+", " ", paragraph.strip()).lower()
            for paragraph in re.split(r"\n\s*\n", text)
            if len(paragraph.strip()) >= 40
        ]
        if len(paragraphs) < 3:
            return 0.0
        duplicates = len(paragraphs) - len(set(paragraphs))
        return duplicates / len(paragraphs)

    @staticmethod
    def _deduplicate_paragraphs(text: str) -> str:
        output: list[str] = []
        seen: set[str] = set()
        for paragraph in re.split(r"(\n\s*\n)", text):
            if not paragraph.strip() or re.fullmatch(r"\n\s*\n", paragraph):
                output.append(paragraph)
                continue
            normalized = re.sub(r"\s+", " ", paragraph.strip()).lower()
            if len(normalized) >= 40 and normalized in seen:
                continue
            if len(normalized) >= 40:
                seen.add(normalized)
            output.append(paragraph)
        return "".join(output).strip()


class SourceRanker:
    """Reordena fontes locais/web pela aderência lexical à pergunta."""

    TRUSTED_DOMAIN_WEIGHTS = {
        "plato.stanford.edu": 8,
        "iep.utm.edu": 7,
        "philpapers.org": 7,
        "philarchive.org": 7,
        "arxiv.org": 6,
        "doi.org": 6,
        "wikipedia.org": 2,
        "github.com": 3,
        "docs.python.org": 6,
        "discord.com": 6,
        "ai.google.dev": 6,
        "console.groq.com": 6,
    }

    def rank_context(
        self,
        query: str,
        context: str,
        *,
        max_chars: int,
    ) -> str:
        lines = [line.strip() for line in context.splitlines() if line.strip()]
        if not lines:
            return ""

        terms = set(_extract_terms(query))
        scored: list[tuple[float, int, str]] = []
        seen: set[str] = set()

        for index, line in enumerate(lines):
            signature = re.sub(r"\s+", " ", line).lower()[:500]
            if signature in seen:
                continue
            seen.add(signature)
            lower = line.lower()
            lexical = sum(1.5 for term in terms if term in lower)
            domain = self._domain_weight(line)
            local_bonus = 2.0 if "biblioteca local" in lower else 0.0
            score = lexical + domain + local_bonus
            scored.append((score, -index, line))

        scored.sort(reverse=True)
        output: list[str] = []
        used = 0
        for _, _, line in scored:
            line = line[:SOURCE_LINE_MAX_CHARS]
            extra = len(line) + (1 if output else 0)
            if used + extra > max_chars:
                remaining = max_chars - used
                if remaining >= 180:
                    output.append(line[:remaining].rstrip() + "…")
                break
            output.append(line)
            used += extra
        return "\n".join(output)

    def source_labels(self, context: str, limit: int = 8) -> list[str]:
        labels: list[str] = []
        for line in context.splitlines():
            clean = line.strip()
            if not clean:
                continue
            labels.append(clean[:SOURCE_LINE_MAX_CHARS])
            if len(labels) >= limit:
                break
        return labels

    def _domain_weight(self, line: str) -> float:
        lower = line.lower()
        for domain, weight in self.TRUSTED_DOMAIN_WEIGHTS.items():
            if domain in lower:
                return float(weight)
        return 0.0


class ConversationStore:
    """Gerencia histórico curto com cópias defensivas e limite de caracteres."""

    def __init__(
        self,
        backing: defaultdict[int, list[dict[str, str]]],
        max_messages: int,
        max_chars: int,
    ) -> None:
        self.backing = backing
        self.max_messages = max_messages
        self.max_chars = max_chars

    def get(self, user_id: int) -> list[dict[str, str]]:
        history = [dict(item) for item in self.backing[user_id][-self.max_messages:]]
        history = self._fit_chars(history)
        self.backing[user_id] = [dict(item) for item in history]
        return history

    def append_exchange(self, user_id: int, user_text: str, assistant_text: str) -> None:
        history = self.get(user_id)
        history.append({"role": "user", "content": user_text[:USER_MESSAGE_MAX_CHARS]})
        history.append({"role": "assistant", "content": assistant_text[:12000]})
        history = history[-self.max_messages:]
        self.backing[user_id] = self._fit_chars(history)

    def clear(self, user_id: int) -> None:
        self.backing[user_id] = []

    def count(self, user_id: int) -> int:
        return len(self.backing[user_id])

    def _fit_chars(self, history: list[dict[str, str]]) -> list[dict[str, str]]:
        kept: list[dict[str, str]] = []
        used = 0
        for item in reversed(history):
            content = item.get("content", "")
            if used + len(content) > self.max_chars:
                break
            kept.append(dict(item))
            used += len(content)
        return list(reversed(kept))


def _parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_duration_seconds(value: str | None) -> float | None:
    if not value:
        return None
    raw = str(value).strip().lower()
    try:
        return float(raw)
    except ValueError:
        pass

    total = 0.0
    matched = False
    for amount, unit in re.findall(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h)", raw):
        matched = True
        number = float(amount)
        if unit == "ms":
            total += number / 1000
        elif unit == "s":
            total += number
        elif unit == "m":
            total += number * 60
        elif unit == "h":
            total += number * 3600
    return total if matched else None


def _extract_retry_after(headers: Mapping[str, str], body: str = "") -> float | None:
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    retry = _parse_optional_float(lowered.get("retry-after"))
    if retry is not None:
        return max(0.0, retry)

    match = re.search(r"try again in\s+([0-9.]+)s", body, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _extract_token_limit_details(message: str) -> tuple[int | None, int | None]:
    requested = None
    limit = None

    requested_match = re.search(r"requested\s+(\d+)", message, re.IGNORECASE)
    limit_match = re.search(r"limit\s+(\d+)", message, re.IGNORECASE)
    if requested_match:
        requested = int(requested_match.group(1))
    if limit_match:
        limit = int(limit_match.group(1))
    return requested, limit


def classify_provider_error(exc: Exception) -> ProviderErrorInfo:
    status = getattr(exc, "status", None)
    body = getattr(exc, "body", None)
    headers = getattr(exc, "headers", None) or {}
    message = str(body if body is not None else exc)
    lowered = message.lower()

    requested, token_limit = _extract_token_limit_details(message)
    retry_after = _extract_retry_after(headers, message)

    if status == 413 or "request too large" in lowered or "requested" in lowered and "tokens per minute" in lowered:
        kind = ProviderErrorKind.OVERSIZED
    elif status == 429 or any(
        marker in lowered
        for marker in (
            "429",
            "rate limit",
            "too many requests",
            "resource_exhausted",
            "quota exceeded",
            "tokens per minute",
        )
    ):
        kind = ProviderErrorKind.RATE_LIMIT
    elif status in {401, 403} or any(
        marker in lowered
        for marker in (
            "401",
            "403",
            "invalid_api_key",
            "invalid api key",
            "unauthorized",
            "permission denied",
            "forbidden",
        )
    ):
        kind = ProviderErrorKind.AUTH
    elif isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in lowered:
        kind = ProviderErrorKind.TIMEOUT
    elif status == 404 or "model_not_found" in lowered or "not found" in lowered:
        kind = ProviderErrorKind.NOT_FOUND
    elif status in {400, 409, 422}:
        kind = ProviderErrorKind.INVALID_REQUEST
    elif status is not None and status >= 500:
        kind = ProviderErrorKind.TRANSIENT
    elif any(marker in lowered for marker in ("connection reset", "server disconnected", "temporarily unavailable")):
        kind = ProviderErrorKind.TRANSIENT
    elif "safety" in lowered or "blocked" in lowered:
        kind = ProviderErrorKind.SAFETY
    else:
        kind = ProviderErrorKind.UNKNOWN

    return ProviderErrorInfo(
        kind=kind,
        status=status,
        message=message[:800],
        retry_after=retry_after,
        requested_tokens=requested,
        token_limit=token_limit,
        raw_headers={str(k).lower(): str(v) for k, v in headers.items()},
    )


def _new_trace_id(user_id: int, message: str) -> str:
    seed = f"{user_id}:{time.time_ns()}:{message[:200]}".encode("utf-8", errors="ignore")
    return hashlib.blake2s(seed, digest_size=6).hexdigest()


def _safe_copy_response(response: AIResponse) -> AIResponse:
    return AIResponse(
        text=response.text,
        mode=response.mode,
        sources=list(response.sources),
        used_tools=list(response.used_tools),
        model=response.model,
        latency_ms=response.latency_ms,
        cache_hit=response.cache_hit,
        trace_id=response.trace_id,
        finish_reason=response.finish_reason,
        input_tokens_estimate=response.input_tokens_estimate,
        output_tokens_requested=response.output_tokens_requested,
        compaction_level=response.compaction_level,
        fallback_used=response.fallback_used,
    )


def _compact_exception_message(exc: Exception, limit: int = 320) -> str:
    value = re.sub(r"\s+", " ", str(exc)).strip()
    return value[:limit] + ("…" if len(value) > limit else "")



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
        queries = [terms]

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
    max_chars = ACADEMIC_CONTEXT_MAX_CHARS_DEFAULT
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

    max_chars = WEB_CONTEXT_MAX_CHARS_DEFAULT
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
    """Baixa imagem com limite de tamanho para não esgotar memória do container."""
    max_bytes = max(256_000, min(_safe_int(os.getenv("AI_IMAGE_MAX_BYTES"), 8_000_000), 20_000_000))
    allowed_mimes = {"image/png", "image/jpeg", "image/webp", "image/gif"}

    try:
        timeout = aiohttp.ClientTimeout(total=12, connect=5, sock_read=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    return None

                mime = response.headers.get("Content-Type", "image/png").split(";", 1)[0].lower().strip()
                if mime not in allowed_mimes:
                    logger.debug("Anexo ignorado por MIME não suportado: %s", mime)
                    return None

                content_length = _parse_optional_int(response.headers.get("Content-Length"))
                if content_length is not None and content_length > max_bytes:
                    logger.warning("Imagem ignorada: %s bytes excede limite %s.", content_length, max_bytes)
                    return None

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        logger.warning("Imagem interrompida ao exceder %s bytes.", max_bytes)
                        return None
                    chunks.append(chunk)

                data = b"".join(chunks)
                if not data:
                    return None
                return base64.b64encode(data).decode("ascii"), mime
    except Exception as exc:
        logger.debug("Falha ao baixar imagem: %s", _compact_exception_message(exc))
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
            text = "Fontes/contextos usados:\n" + "\n".join(
                f"- {source[:350]}" for source in sources
            )

        # O conjunto de fontes pode ultrapassar 2.000 caracteres. Divide a saída
        # mantendo todas as partes privadas, em vez de deixar o botão falhar.
        chunks = _split_discord_text(text, DISCORD_TEXT_LIMIT)
        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)


class AIChat(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # Chaves e clientes. O novo SDK usa um Client explícito por chave, o que
        # evita o estado global de genai.configure() e torna chamadas concorrentes
        # previsíveis.
        self.gemini_keys: list[str] = []
        self.gemini_clients: list[Any] = []
        self.gemini_key_index = 0
        self.groq_academic_keys: list[str] = []
        self.groq_academic_key_index = 0

        # Estado de conversa e perfis.
        self.history: defaultdict[int, list[dict[str, str]]] = defaultdict(list)
        self.conversation_store = ConversationStore(
            self.history,
            max_messages=MAX_HISTORY_MESSAGES,
            max_chars=HISTORY_MAX_CHARS,
        )
        self.cooldowns: defaultdict[int, datetime] = defaultdict(lambda: datetime.min)
        self.user_profiles: dict[int, dict[str, Any]] = {}

        # Observabilidade e proteção contra condições de corrida.
        self.recent_metrics: deque[dict[str, Any]] = deque(maxlen=500)
        self.recent_traces: deque[RequestTrace] = deque(maxlen=300)
        self.send_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.request_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.db_extras_ready = False

        # Serviços internos reutilizados em todas as requisições.
        self.token_estimator = TokenEstimator()
        self.prompt_budgeter = PromptBudgeter(self.token_estimator)
        self.provider_health = ProviderHealthRegistry()
        self.quality_gate = ResponseQualityGate()
        self.source_ranker = SourceRanker()

        # Semáforos independentes. Busca, Gemini e Groq não bloqueiam o bot inteiro.
        self.gemini_semaphore = asyncio.Semaphore(GEMINI_MAX_CONCURRENCY)
        self.groq_semaphore = asyncio.Semaphore(GROQ_MAX_CONCURRENCY)
        self.search_semaphore = asyncio.Semaphore(SEARCH_MAX_CONCURRENCY)

        # Coleta chaves Gemini em ordem. Variáveis vazias são ignoradas.
        for env_name in (
            "GEMINI_API_KEY",
            "GEMINI_API_KEY_2",
            "GEMINI_API_KEY_3",
            "GEMINI_API_KEY_4",
            "GEMINI_API_KEY_5",
        ):
            key = (os.getenv(env_name) or "").strip()
            if not key:
                continue
            self.gemini_keys.append(key)
            logger.info("%s carregada (flash).", env_name)

        # Coleta chaves Groq acadêmicas. Cada uma pode estar em uma organização
        # diferente, mas o payload é compactado antes da rotação para não repetir
        # a mesma requisição inválida em todas elas.
        for env_name in (
            "GROQ_ACADEMIC_API_KEY",
            "GROQ_ACADEMIC_API_KEY_2",
            "GROQ_ACADEMIC_API_KEY_3",
        ):
            key = (os.getenv(env_name) or "").strip()
            if not key:
                continue
            self.groq_academic_keys.append(key)
            logger.info("%s carregada (groq/acadêmico).", env_name)

        if GENAI_AVAILABLE:
            for index, key in enumerate(self.gemini_keys, start=1):
                try:
                    # O timeout principal também é controlado por asyncio.wait_for,
                    # mantendo compatibilidade entre versões do SDK.
                    client = genai.Client(api_key=key)
                    self.gemini_clients.append(client)
                    self.provider_health.get("gemini", index)
                except Exception as exc:
                    logger.error(
                        "Não foi possível criar cliente Gemini #%s: %s",
                        index,
                        _compact_exception_message(exc),
                    )
        elif self.gemini_keys:
            logger.error(
                "Biblioteca 'google-genai' ausente. Instale google-genai e remova "
                "google-generativeai do requirements.txt."
            )

        for index in range(len(self.groq_academic_keys)):
            self.provider_health.get("groq", index + 1)

        if not self.gemini_clients:
            logger.warning("Nenhum cliente Gemini pronto; o fallback geral pode ficar indisponível.")

        logger.info(
            "IA inicializada com %s cliente(s) Gemini e %s chave(s) Groq acadêmica(s).",
            len(self.gemini_clients),
            len(self.groq_academic_keys),
        )

    async def cog_unload(self) -> None:
        """Fecha os clientes assíncronos do SDK novo quando o cog é descarregado."""
        for client in self.gemini_clients:
            try:
                await client.aio.aclose()
            except Exception:
                pass

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

    def _build_gemini_contents(
        self,
        messages: Sequence[Mapping[str, Any]],
        image_data: Optional[tuple[str, str]] = None,
    ) -> tuple[str, list[Any]]:
        """Converte o formato interno para o formato canônico do google-genai."""
        if genai_types is None:
            return "", []

        system_parts: list[str] = []
        contents: list[Any] = []
        non_system = [message for message in messages if message.get("role") != "system"]

        for message in messages:
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
            if role == "system":
                system_parts.append(content)
                continue

            gemini_role = "model" if role in {"assistant", "model"} else "user"
            parts: list[Any] = [genai_types.Part.from_text(text=content)]

            # A imagem é anexada somente à última mensagem do usuário. O tuple
            # recebido contém base64 e MIME, produzido por fetch_image_base64().
            if image_data and message is non_system[-1] and gemini_role == "user":
                encoded, mime_type = image_data
                try:
                    image_bytes = base64.b64decode(encoded, validate=True)
                    if image_bytes:
                        parts.append(
                            genai_types.Part.from_bytes(
                                data=image_bytes,
                                mime_type=mime_type or "image/png",
                            )
                        )
                except Exception as exc:
                    logger.debug("Imagem ignorada ao montar payload Gemini: %s", exc)

            contents.append(genai_types.Content(role=gemini_role, parts=parts))

        # O Gemini espera que a conversa comece com user. Históricos corrompidos ou
        # antigos que comecem em assistant são descartados até o próximo user.
        while contents and getattr(contents[0], "role", "") == "model":
            contents.pop(0)

        return "\n\n".join(part for part in system_parts if part).strip(), contents

    async def _gemini_generate_once(
        self,
        client: Any,
        *,
        system_prompt: str,
        contents: list[Any],
        max_tokens: int,
        temperature: float,
        thinking_level: str = "low",
    ) -> Any:
        if genai_types is None:
            raise RuntimeError("google-genai não está disponível")

        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            max_output_tokens=max(32, int(max_tokens)),
            temperature=max(0.0, min(float(temperature), 2.0)),
            top_p=0.9,
            thinking_config=genai_types.ThinkingConfig(
                thinking_level=thinking_level,
                include_thoughts=False,
            ),
        )
        coroutine = client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents or "Olá.",
            config=config,
        )
        return await asyncio.wait_for(coroutine, timeout=MODEL_TIMEOUT_SECONDS)

    async def _call_gemini(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        temperature: float,
        image_data: Optional[tuple[str, str]] = None,
        allow_continuation: bool = True,
        thinking_level: str = "low",
    ) -> Optional[ModelResult]:
        """Chama o SDK google-genai com rotação, circuit breaker e continuação curta."""
        if not self.gemini_clients or genai_types is None:
            return None

        system_prompt, contents = self._build_gemini_contents(messages, image_data)
        estimate = self.token_estimator.estimate_messages(messages, image_count=1 if image_data else 0)
        indexes = self.provider_health.ordered_indexes(
            "gemini",
            len(self.gemini_clients),
            self.gemini_key_index,
        )

        for key_index in indexes:
            client = self.gemini_clients[key_index]
            key_number = key_index + 1
            started = time.monotonic()
            last_error: ProviderErrorInfo | None = None

            for attempt in range(PROVIDER_TRANSIENT_RETRIES + 1):
                try:
                    async with self.gemini_semaphore:
                        response = await self._gemini_generate_once(
                            client,
                            system_prompt=system_prompt,
                            contents=contents,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            thinking_level=thinking_level,
                        )

                    combined = _extract_model_text(response)
                    finish_reason = _extract_gemini_finish_reason(response)
                    continuations = 0

                    if (
                        allow_continuation
                        and combined
                        and _looks_cut_off(combined, finish_reason)
                        and MAX_CONTINUATIONS > 0
                    ):
                        addition_result = await self._continue_with_gemini(
                            client,
                            previous_text=combined,
                            max_tokens=min(700, max(280, max_tokens // 2)),
                            temperature=min(temperature, 0.35),
                        )
                        if addition_result:
                            combined = _join_continuation(combined, addition_result.text)
                            finish_reason = addition_result.finish_reason or finish_reason
                            continuations = 1

                    quality = self.quality_gate.inspect(combined, finish_reason)
                    latency_ms = int((time.monotonic() - started) * 1000)
                    self.gemini_key_index = key_index
                    self.provider_health.mark_success("gemini", key_number)

                    logger.info(
                        "Gemini respondeu | modelo=%s chave #%s | %sms | "
                        "entrada≈%s | saída_max=%s | continuações=%s | fim=%s",
                        GEMINI_MODEL,
                        key_number,
                        latency_ms,
                        estimate.total,
                        max_tokens,
                        continuations,
                        finish_reason or "n/a",
                    )
                    return ModelResult(
                        text=quality.text,
                        model=GEMINI_MODEL,
                        key_number=key_number,
                        latency_ms=latency_ms,
                        finish_reason=finish_reason,
                        continuations=continuations,
                        truncated=quality.likely_cut_off,
                        provider="gemini",
                        input_tokens_estimate=estimate.total,
                        output_tokens_requested=max_tokens,
                    )

                except Exception as exc:
                    error = classify_provider_error(exc)
                    last_error = error
                    self.provider_health.mark_failure("gemini", key_number, error)

                    if error.can_retry_same_key and attempt < PROVIDER_TRANSIENT_RETRIES:
                        delay = min(3.0, 0.45 * (2 ** attempt) + random.random() * 0.2)
                        await asyncio.sleep(delay)
                        continue

                    logger.warning(
                        "Gemini chave #%s falhou (%s): %s",
                        key_number,
                        error.kind.value,
                        _compact_exception_message(exc),
                    )
                    break

            if last_error and not last_error.should_rotate_key:
                break

        logger.error("Todos os clientes Gemini disponíveis falharam nesta requisição.")
        return None

    async def _continue_with_gemini(
        self,
        client: Any,
        *,
        previous_text: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[ModelResult]:
        """Continuação isolada, sem reenviar histórico e fontes inteiras."""
        if genai_types is None:
            return None

        tail = self.token_estimator.truncate_to_tokens(
            previous_text,
            850,
            preserve_tail=True,
        )
        contents = [
            genai_types.Content(
                role="user",
                parts=[
                    genai_types.Part.from_text(
                        text=(
                            "A resposta abaixo foi interrompida. Continue somente do ponto "
                            "em que parou, sem nova introdução e sem repetir parágrafos. "
                            "Conclua em Markdown válido.\n\n"
                            f"FINAL DA RESPOSTA:\n{tail}"
                        )
                    )
                ],
            )
        ]
        try:
            response = await self._gemini_generate_once(
                client,
                system_prompt=(
                    "Você completa respostas interrompidas. Seja conciso, preserve o "
                    "sentido e não introduza fatos ou fontes que não estavam na resposta."
                ),
                contents=contents,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking_level="low",
            )
            text = _extract_model_text(response)
            finish = _extract_gemini_finish_reason(response)
            if not text:
                return None
            return ModelResult(
                text=text,
                model=GEMINI_MODEL,
                key_number=0,
                latency_ms=0,
                finish_reason=finish,
                truncated=_looks_cut_off(text, finish),
                provider="gemini",
                output_tokens_requested=max_tokens,
            )
        except Exception as exc:
            logger.debug("Continuação Gemini falhou: %s", _compact_exception_message(exc))
            return None

    async def _groq_request(
        self,
        session: aiohttp.ClientSession,
        *,
        api_key: str,
        plan: RequestPlan,
        temperature: float,
        include_reasoning_effort: bool = True,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        payload: dict[str, Any] = {
            "model": GROQ_ACADEMIC_MODEL,
            "messages": plan.messages,
            "max_tokens": plan.max_output_tokens,
            "temperature": max(0.0, min(float(temperature), 2.0)),
            "top_p": 0.9,
        }
        if include_reasoning_effort:
            payload["reasoning_effort"] = GROQ_REASONING_EFFORT

        async with session.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"{BOT_NAME}/3.0",
            },
            json=payload,
            timeout=aiohttp.ClientTimeout(total=MODEL_TIMEOUT_SECONDS),
        ) as response:
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            body = await response.text()
            if response.status != 200:
                raise ProviderRequestError(response.status, body, headers)
            try:
                return json.loads(body), headers
            except json.JSONDecodeError as exc:
                raise ProviderRequestError(
                    502,
                    f"Resposta JSON inválida do Groq: {body[:400]}",
                    headers,
                ) from exc

    async def _call_groq_academic(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        temperature: float,
    ) -> Optional[ModelResult]:
        """
        Chama o Groq com orçamento preventivo de TPM.

        Um 413 não faz mais o bot repetir o mesmo payload nas três chaves. Primeiro
        ele compacta o pedido no mesmo ciclo; só falhas de limite temporal, autenticação
        ou infraestrutura motivam rotação.
        """
        if not self.groq_academic_keys:
            return None

        indexes = self.provider_health.ordered_indexes(
            "groq",
            len(self.groq_academic_keys),
            self.groq_academic_key_index,
        )
        shared_plan = self.prompt_budgeter.plan_groq(
            messages,
            requested_output_tokens=max_tokens,
            compaction_level=0,
        )

        connector = aiohttp.TCPConnector(limit=max(4, GROQ_MAX_CONCURRENCY * 2), ttl_dns_cache=300)
        async with aiohttp.ClientSession(connector=connector) as session:
            for key_index in indexes:
                key_number = key_index + 1
                api_key = self.groq_academic_keys[key_index]
                started = time.monotonic()
                plan = shared_plan
                include_reasoning = True
                last_error: ProviderErrorInfo | None = None

                for compact_attempt in range(GROQ_COMPACT_RETRIES + 1):
                    try:
                        async with self.groq_semaphore:
                            data, headers = await self._groq_request(
                                session,
                                api_key=api_key,
                                plan=plan,
                                temperature=temperature,
                                include_reasoning_effort=include_reasoning,
                            )

                        choices = data.get("choices") or []
                        if not choices:
                            raise ProviderRequestError(502, "Groq retornou choices vazio", headers)

                        choice = choices[0]
                        text = str((choice.get("message") or {}).get("content") or "").strip()
                        finish_reason = str(choice.get("finish_reason") or "").upper()
                        quality = self.quality_gate.inspect(text, finish_reason)
                        latency_ms = int((time.monotonic() - started) * 1000)

                        self.groq_academic_key_index = key_index
                        self.provider_health.mark_success("groq", key_number, headers)

                        usage = data.get("usage") or {}
                        prompt_tokens = _parse_optional_int(usage.get("prompt_tokens"))
                        logger.info(
                            "Groq respondeu | modelo=%s chave #%s | %sms | "
                            "entrada≈%s%s | saída_max=%s | compactação=%s | fim=%s",
                            GROQ_ACADEMIC_MODEL,
                            key_number,
                            latency_ms,
                            plan.estimated_input_tokens,
                            f"/real={prompt_tokens}" if prompt_tokens is not None else "",
                            plan.max_output_tokens,
                            plan.compaction_level,
                            finish_reason or "n/a",
                        )
                        if plan.notes:
                            logger.debug("Plano Groq: %s", "; ".join(plan.notes))

                        return ModelResult(
                            text=quality.text,
                            model=GROQ_ACADEMIC_MODEL,
                            key_number=key_number,
                            latency_ms=latency_ms,
                            finish_reason=finish_reason,
                            continuations=0,
                            truncated=quality.likely_cut_off,
                            provider="groq",
                            input_tokens_estimate=plan.estimated_input_tokens,
                            output_tokens_requested=plan.max_output_tokens,
                            compaction_level=plan.compaction_level,
                        )

                    except Exception as exc:
                        error = classify_provider_error(exc)
                        last_error = error
                        self.provider_health.mark_failure("groq", key_number, error)

                        if error.needs_compaction and compact_attempt < GROQ_COMPACT_RETRIES:
                            next_level = min(5, plan.compaction_level + 1)
                            logger.warning(
                                "Payload Groq grande demais na chave #%s "
                                "(solicitado=%s limite=%s). Compactando para nível %s.",
                                key_number,
                                error.requested_tokens or "?",
                                error.token_limit or GROQ_TPM_LIMIT,
                                next_level,
                            )
                            plan = self.prompt_budgeter.plan_groq(
                                messages,
                                requested_output_tokens=max_tokens,
                                compaction_level=next_level,
                            )
                            shared_plan = plan
                            continue

                        # Alguns deployments antigos podem recusar reasoning_effort.
                        # Retenta uma vez sem esse campo, sem trocar de chave.
                        if (
                            error.kind == ProviderErrorKind.INVALID_REQUEST
                            and include_reasoning
                            and "reasoning" in error.message.lower()
                        ):
                            include_reasoning = False
                            logger.info("Retentando Groq sem reasoning_effort.")
                            continue

                        if error.can_retry_same_key and compact_attempt < PROVIDER_TRANSIENT_RETRIES:
                            delay = min(3.0, 0.5 * (2 ** compact_attempt) + random.random() * 0.2)
                            await asyncio.sleep(delay)
                            continue

                        log_method = logger.warning if error.should_rotate_key else logger.error
                        log_method(
                            "Groq chave #%s falhou (%s): %s",
                            key_number,
                            error.kind.value,
                            _compact_exception_message(exc),
                        )
                        break

                # Erros de tamanho ou payload inválido não mudam ao trocar a chave.
                # Nesse caso, não repetimos a mesma requisição pelas outras contas.
                if last_error and not last_error.should_rotate_key:
                    logger.error(
                        "Falha Groq não recuperável após compactação; evitando repetir em outras chaves."
                    )
                    return None

                # Limite temporal, autenticação ou falha transitória podem ser
                # específicos da conta/chave, então somente esses casos rotacionam.
                continue

        logger.error("Todas as chaves Groq utilizáveis falharam nesta requisição.")
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

    async def _maybe_build_external_context(
        self,
        user_message: str,
        mode: str,
    ) -> tuple[str, list[str], list[str], bool]:
        """Executa busca quando necessário e devolve contexto já priorizado."""
        academic = mode in {"academic", "code", "study", "argument"}
        should_search = mode == "search" or (academic and ACADEMIC_SEARCH_ENABLED)
        if not should_search:
            return "", [], [], False

        cache = ACADEMIC_CACHE if academic else SEARCH_CACHE
        cache_prefix = "academic" if academic else "web"
        cache_key = f"{cache_prefix}:{_normalize_key(user_message)}"
        cache_hit = cache.get(cache_key) is not None

        try:
            async with self.search_semaphore:
                raw_context = await search_web(user_message, academic=academic)
        except Exception as exc:
            logger.warning("Busca externa falhou: %s", _compact_exception_message(exc))
            return "", [], [], False

        if not raw_context:
            return "", [], [], cache_hit

        max_chars = (
            ACADEMIC_CONTEXT_MAX_CHARS_DEFAULT
            if academic
            else WEB_CONTEXT_MAX_CHARS_DEFAULT
        )
        ranked_context = self.source_ranker.rank_context(
            user_message,
            raw_context,
            max_chars=max_chars,
        )
        sources = self.source_ranker.source_labels(ranked_context, limit=10)
        tools = ["biblioteca/busca acadêmica" if academic else "busca web"]
        return ranked_context, sources, tools, cache_hit

    async def _finish_truncated_academic_result(
        self,
        result: ModelResult,
        *,
        original_question: str,
    ) -> ModelResult:
        """Usa Gemini para fechar uma resposta Groq cortada sem gastar outro TPM Groq."""
        if not result.truncated or not self.gemini_clients:
            return result

        tail = self.token_estimator.truncate_to_tokens(
            result.text,
            900,
            preserve_tail=True,
        )
        question = self.token_estimator.truncate_to_tokens(original_question, 600)
        continuation_messages = [
            {
                "role": "system",
                "content": (
                    "Complete uma resposta acadêmica interrompida. Não repita o texto "
                    "anterior, não invente fontes e finalize frases, listas e Markdown."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Pergunta original:\n{question}\n\n"
                    f"Trecho final já produzido:\n{tail}\n\n"
                    "Escreva apenas a continuação necessária para concluir."
                ),
            },
        ]
        addition = await self._call_gemini(
            continuation_messages,
            max_tokens=650,
            temperature=0.25,
            allow_continuation=False,
            thinking_level="low",
        )
        if not addition or not addition.text:
            return result

        combined = _join_continuation(result.text, addition.text)
        quality = self.quality_gate.inspect(combined, addition.finish_reason)
        return ModelResult(
            text=quality.text,
            model=f"{result.model} + {addition.model}",
            key_number=result.key_number,
            latency_ms=result.latency_ms + addition.latency_ms,
            finish_reason=addition.finish_reason or result.finish_reason,
            continuations=result.continuations + 1,
            truncated=quality.likely_cut_off,
            provider="groq→gemini",
            input_tokens_estimate=result.input_tokens_estimate,
            output_tokens_requested=result.output_tokens_requested + addition.output_tokens_requested,
            compaction_level=result.compaction_level,
            fallback_used=True,
        )

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
        trace_id = _new_trace_id(user_id, user_message)
        started = time.monotonic()
        mode = (
            forced_query_type
            if forced_query_type and forced_query_type != "auto"
            else detect_query_type(user_message)
        )

        # Uma requisição por usuário de cada vez evita o histórico A/B/A/B quando a
        # pessoa envia duas mensagens quase simultâneas.
        async with self.request_locks[user_id]:
            profile = await self._get_user_profile(user_id)
            clean_user_message = (user_message or "Olá.").strip()[:USER_MESSAGE_MAX_CHARS]

            cache_allowed = (
                image_data is None
                and mode in {"chat", "writing", "planning"}
                and len(clean_user_message) < 260
                and not extra_instruction
                and not _needs_fresh_information(clean_user_message)
            )
            response_cache_key = f"response:{mode}:{_normalize_key(clean_user_message)}"
            cached_response = RESPONSE_CACHE.get(response_cache_key) if cache_allowed else None
            if cached_response:
                response = _safe_copy_response(cached_response)
                response.cache_hit = True
                response.trace_id = trace_id
                response.latency_ms = int((time.monotonic() - started) * 1000)
                await self._record_metric(guild_id, channel_id, user_id, response)
                return response

            web_context, sources, used_tools, search_cache_hit = await self._maybe_build_external_context(
                clean_user_message,
                mode,
            )
            history = self.conversation_store.get(user_id)

            user_content = clean_user_message
            if image_data:
                user_content += (
                    "\n\nHá uma imagem anexada. Analise somente o que for visível e "
                    "diferencie observação de inferência."
                )
            if web_context:
                user_content += (
                    "\n\nCONTEXTO EXTERNO/BIBLIOTECA, use sem inventar além dele:\n"
                    + web_context
                )
            elif mode == "search":
                user_content += (
                    "\n\nAviso interno: a busca não trouxe resultado confiável. "
                    "Não invente dados atuais e deixe a limitação explícita."
                )

            mode_instruction = self._style_instruction_for_mode(mode)
            if mode_instruction:
                user_content += f"\n\n{mode_instruction}"
            if extra_instruction:
                user_content += f"\n\nINSTRUÇÃO EXTRA DO COMANDO: {extra_instruction}"

            messages = [
                {"role": "system", "content": build_system_prompt(profile)},
                *history,
                {
                    "role": "user",
                    "content": f"Usuário: {user_name}\nMensagem: {user_content}",
                },
            ]

            is_academic_mode = mode in {"academic", "code", "study", "argument"}
            requested_tokens = self._max_tokens_for_mode(mode)
            temperature = self._temperature_for_mode(mode)
            model_result: ModelResult | None = None
            fallback_used = False

            # Imagens sempre vão ao Gemini. Texto acadêmico prefere Groq, com
            # fallback automático para Gemini após compactação/retries.
            if image_data is not None:
                model_result = await self._call_gemini(
                    messages,
                    max_tokens=requested_tokens,
                    temperature=temperature,
                    image_data=image_data,
                    thinking_level=GEMINI_ACADEMIC_THINKING_LEVEL if is_academic_mode else GEMINI_THINKING_LEVEL,
                )
            elif is_academic_mode and self.groq_academic_keys:
                model_result = await self._call_groq_academic(
                    messages,
                    max_tokens=requested_tokens,
                    temperature=temperature,
                )
                if model_result:
                    model_result = await self._finish_truncated_academic_result(
                        model_result,
                        original_question=clean_user_message,
                    )
                if not model_result and self.gemini_clients:
                    fallback_used = True
                    used_tools.append("fallback Gemini")
                    logger.warning(
                        "Trace %s: Groq indisponível; usando Gemini para modo %s.",
                        trace_id,
                        mode,
                    )
                    model_result = await self._call_gemini(
                        messages,
                        max_tokens=min(requested_tokens, ACADEMIC_MAX_TOKENS),
                        temperature=temperature,
                        thinking_level=GEMINI_ACADEMIC_THINKING_LEVEL,
                    )
            else:
                model_result = await self._call_gemini(
                    messages,
                    max_tokens=requested_tokens,
                    temperature=temperature,
                    thinking_level=GEMINI_THINKING_LEVEL,
                )
                # Fallback inverso somente para texto. Imagens não são enviadas ao
                # endpoint acadêmico Groq deste arquivo.
                if not model_result and self.groq_academic_keys:
                    fallback_used = True
                    used_tools.append("fallback Groq")
                    logger.warning(
                        "Trace %s: Gemini indisponível; usando Groq como fallback textual.",
                        trace_id,
                    )
                    model_result = await self._call_groq_academic(
                        messages,
                        max_tokens=min(requested_tokens, 1000),
                        temperature=min(temperature, 0.55),
                    )

            total_latency_ms = int((time.monotonic() - started) * 1000)
            if not model_result:
                text = self.quality_gate.safe_fallback_text(mode)
                response = AIResponse(
                    text=text,
                    mode=mode,
                    sources=sources,
                    used_tools=used_tools,
                    latency_ms=total_latency_ms,
                    cache_hit=search_cache_hit,
                    trace_id=trace_id,
                    fallback_used=fallback_used,
                )
                self.recent_traces.append(
                    RequestTrace(
                        trace_id=trace_id,
                        user_id=user_id,
                        mode=mode,
                        provider="nenhum",
                        model="",
                        started_at=started,
                        latency_ms=total_latency_ms,
                        fallback_used=fallback_used,
                        error="todos os provedores falharam",
                    )
                )
                await self._record_metric(guild_id, channel_id, user_id, response)
                return response

            fallback_used = fallback_used or model_result.fallback_used
            quality = self.quality_gate.inspect(
                model_result.text,
                model_result.finish_reason,
            )
            reply = quality.text or self.quality_gate.safe_fallback_text(mode)

            if quality.likely_cut_off:
                logger.warning(
                    "Trace %s ainda parece truncada | modelo=%s | fim=%s | "
                    "continuações=%s",
                    trace_id,
                    model_result.model,
                    model_result.finish_reason or "n/a",
                    model_result.continuations,
                )

            self.conversation_store.append_exchange(
                user_id,
                clean_user_message,
                reply,
            )

            response = AIResponse(
                text=reply,
                mode=mode,
                sources=sources,
                used_tools=used_tools,
                model=model_result.model,
                latency_ms=total_latency_ms,
                cache_hit=search_cache_hit,
                trace_id=trace_id,
                finish_reason=model_result.finish_reason,
                input_tokens_estimate=model_result.input_tokens_estimate,
                output_tokens_requested=model_result.output_tokens_requested,
                compaction_level=model_result.compaction_level,
                fallback_used=fallback_used,
            )

            if cache_allowed and reply:
                RESPONSE_CACHE.set(
                    response_cache_key,
                    _safe_copy_response(response),
                    ttl_seconds=_safe_int(os.getenv("AI_RESPONSE_CACHE_SECONDS"), 300),
                )

            self.recent_traces.append(
                RequestTrace(
                    trace_id=trace_id,
                    user_id=user_id,
                    mode=mode,
                    provider=model_result.provider or "desconhecido",
                    model=model_result.model,
                    started_at=started,
                    latency_ms=total_latency_ms,
                    input_tokens_estimate=model_result.input_tokens_estimate,
                    output_tokens_requested=model_result.output_tokens_requested,
                    compaction_level=model_result.compaction_level,
                    fallback_used=fallback_used,
                    continuation_count=model_result.continuations,
                    finish_reason=model_result.finish_reason,
                )
            )
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
            "created_at": datetime.now(timezone.utc).isoformat(),
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
            self.conversation_store.clear(interaction.user.id)
            await interaction.response.send_message("<:1000032056:1507947210057322637> Histórico de conversa apagado.", ephemeral=True)
            return
        if acao == "resetar_perfil":
            profile = {"style": "normal", "notes": ""}
            await self._save_user_profile(interaction.user.id, profile)
            await interaction.response.send_message("<:1000032056:1507947210057322637> Perfil de IA resetado.", ephemeral=True)
            return
        profile = await self._get_user_profile(interaction.user.id)
        await interaction.response.send_message(
            f"Seu perfil de IA:\n- estilo: `{profile.get('style', 'normal')}`\n- mensagens no histórico curto: `{self.conversation_store.count(interaction.user.id)}`",
            ephemeral=True,
        )

    @app_commands.command(name="limpar-conversa", description=f"Apaga seu histórico de conversa com o {BOT_NAME}.")
    async def clear_history(self, interaction: discord.Interaction) -> None:
        self.conversation_store.clear(interaction.user.id)
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
        traces = list(self.recent_traces)
        avg_latency = sum(m["latency_ms"] for m in metrics) / len(metrics) if metrics else 0
        modes = Counter(m["mode"] for m in metrics)
        cache_hits = sum(1 for m in metrics if m.get("cache_hit"))
        searches = sum(1 for m in metrics if m.get("used_search"))
        fallbacks = sum(1 for trace in traces if trace.fallback_used)
        compacted = sum(1 for trace in traces if trace.compaction_level > 0)
        failed = sum(1 for trace in traces if trace.error)
        library_chunks = len(await asyncio.to_thread(_read_library_files))

        health_lines: list[str] = []
        for state in self.provider_health.snapshot():
            status = "disponível" if state.available else f"cooldown {state.cooldown_remaining:.0f}s"
            token_note = ""
            if state.remaining_tokens is not None:
                token_note = f" · TPM restante `{state.remaining_tokens}`"
            health_lines.append(
                f"`{state.provider} #{state.key_number}`: {status} · "
                f"ok `{state.successes}` / falhas `{state.failures}`{token_note}"
            )
        if not health_lines:
            health_lines.append("Nenhuma chave registrada.")

        embed = discord.Embed(
            title="<:1000032072:1507947958723809340> Status avançado da IA",
            color=discord.Color.from_rgb(255, 255, 255),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Provedores",
            value=(
                f"Gemini prontos: `{len(self.gemini_clients)}`\n"
                f"Groq acadêmicas: `{len(self.groq_academic_keys)}`\n"
                f"SDK: `google-genai` + `Groq REST`"
            ),
            inline=True,
        )
        embed.add_field(
            name="Modelos",
            value=f"Geral: `{GEMINI_MODEL}`\nAcadêmico: `{GROQ_ACADEMIC_MODEL}`",
            inline=True,
        )
        embed.add_field(
            name="Orçamento Groq",
            value=(
                f"TPM declarado: `{GROQ_TPM_LIMIT}`\n"
                f"Alvo por pedido: `{GROQ_TARGET_TOTAL_TOKENS}`\n"
                f"Margem: `{GROQ_TOKEN_SAFETY_MARGIN}`\n"
                f"Saída: `{GROQ_MIN_OUTPUT_TOKENS}`–`{GROQ_MAX_OUTPUT_TOKENS}`"
            ),
            inline=True,
        )
        embed.add_field(
            name="Busca externa",
            value=(
                f"Tavily: {'ativo' if os.getenv('TAVILY_API_KEY') else 'sem chave'}\n"
                f"GNews: {'ativo' if os.getenv('GNEWS_API_KEY') else 'sem chave'}\n"
                "Wikipedia, DuckDuckGo, arXiv e SEP: fallback"
            ),
            inline=False,
        )
        embed.add_field(
            name="Performance recente",
            value=(
                f"Requisições medidas: `{len(metrics)}`\n"
                f"Latência média: `{avg_latency / 1000:.1f}s`\n"
                f"Buscas usadas: `{searches}` · cache hits: `{cache_hits}`\n"
                f"Fallbacks: `{fallbacks}` · compactações: `{compacted}` · falhas: `{failed}`\n"
                f"Modo mais usado: `{modes.most_common(1)[0][0] if modes else 'n/a'}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Saúde das chaves",
            value="\n".join(health_lines)[:1024],
            inline=False,
        )
        embed.add_field(
            name="Biblioteca local",
            value=(
                f"Trechos indexados: `{library_chunks}`\n"
                f"Caminhos: `{os.getenv('AI_LIBRARY_PATH', 'data/library,library,books')}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Caches",
            value=(
                f"Acadêmico: `{len(ACADEMIC_CACHE)}` · Web: `{len(SEARCH_CACHE)}` · "
                f"Respostas: `{len(RESPONSE_CACHE)}`"
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                f"DB extras: {'ativo' if self.db_extras_ready else 'inativo'} · "
                f"Botões: {'ativo' if AI_BUTTONS_ENABLED else 'inativo'} · "
                f"Thinking Gemini: {GEMINI_THINKING_LEVEL}/{GEMINI_ACADEMIC_THINKING_LEVEL}"
            )
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="diagnostico-ia",
        description="Simula o orçamento de tokens e mostra os últimos traces da IA.",
    )
    @app_commands.describe(
        texto="Texto usado na simulação de orçamento.",
        modo="Modo de resposta a simular.",
    )
    @app_commands.choices(modo=[
        app_commands.Choice(name="Acadêmico", value="academic"),
        app_commands.Choice(name="Código", value="code"),
        app_commands.Choice(name="Estudo", value="study"),
        app_commands.Choice(name="Argumento", value="argument"),
    ])
    @app_commands.default_permissions(administrator=True)
    async def diagnostico_ia(
        self,
        interaction: discord.Interaction,
        texto: str = "Explique este tema com rigor acadêmico.",
        modo: str = "academic",
    ) -> None:
        profile = await self._get_user_profile(interaction.user.id)
        fake_context = (
            "[Diagnóstico | Fonte simulada: "
            + ("conteúdo acadêmico de teste " * 100)
            + "]"
        )
        messages = [
            {"role": "system", "content": build_system_prompt(profile)},
            *self.conversation_store.get(interaction.user.id),
            {
                "role": "user",
                "content": (
                    f"Usuário: {interaction.user.display_name}\n"
                    f"Mensagem: {texto[:3000]}\n\n"
                    "CONTEXTO EXTERNO/BIBLIOTECA, use sem inventar além dele:\n"
                    f"{fake_context}"
                ),
            },
        ]

        plan_lines: list[str] = []
        for level in range(4):
            plan = self.prompt_budgeter.plan_groq(
                messages,
                requested_output_tokens=self._max_tokens_for_mode(modo),
                compaction_level=level,
            )
            plan_lines.append(
                f"Nível `{level}`: entrada ≈ `{plan.estimated_input_tokens}`, "
                f"saída `{plan.max_output_tokens}`, total protegido `{plan.estimated_total_tokens}`"
            )

        trace_lines: list[str] = []
        for trace in list(self.recent_traces)[-6:]:
            status = f"erro: {trace.error[:80]}" if trace.error else trace.finish_reason or "ok"
            trace_lines.append(
                f"`{trace.trace_id}` · {trace.provider} · {trace.mode} · "
                f"{trace.latency_ms / 1000:.1f}s · comp `{trace.compaction_level}` · {status}"
            )
        if not trace_lines:
            trace_lines.append("Nenhum trace registrado desde o último deploy.")

        embed = discord.Embed(
            title="Diagnóstico de orçamento da IA",
            color=discord.Color.from_rgb(255, 255, 255),
            timestamp=datetime.now(timezone.utc),
        )
        embed.description = (
            "A simulação é conservadora. O payload real é compactado novamente se o "
            "Groq devolver HTTP 413."
        )
        embed.add_field(name="Planos calculados", value="\n".join(plan_lines), inline=False)
        embed.add_field(name="Últimos traces", value="\n".join(trace_lines)[:1024], inline=False)
        embed.add_field(
            name="Configuração ativa",
            value=(
                f"TPM `{GROQ_TPM_LIMIT}` · alvo `{GROQ_TARGET_TOTAL_TOKENS}` · "
                f"margem `{GROQ_TOKEN_SAFETY_MARGIN}` · retries de compactação `{GROQ_COMPACT_RETRIES}`"
            ),
            inline=False,
        )
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
