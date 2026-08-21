from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


logger = logging.getLogger("Revolutx.AcademicSessions")


def _safe_int(value: str | None, default: int) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


ACADEMIC_SESSION_TTL_SECONDS = max(
    900,
    min(_safe_int(os.getenv("AI_ACADEMIC_SESSION_TTL_SECONDS"), 14_400), 86_400),
)
ACADEMIC_SESSION_MAX_TURNS = max(
    4,
    min(_safe_int(os.getenv("AI_ACADEMIC_SESSION_MAX_TURNS"), 20), 40),
)
ACADEMIC_SESSION_HISTORY_CHARS = max(
    2_000,
    min(_safe_int(os.getenv("AI_ACADEMIC_SESSION_HISTORY_CHARS"), 10_000), 20_000),
)


GOAL_LABELS = {
    "aprender": "Aprender",
    "resolver": "Resolver um problema",
    "verificar": "Verificar uma solução",
    "artigo": "Analisar um artigo",
    "argumento": "Analisar um argumento",
}
LEVEL_LABELS = {
    "auto": "Automático",
    "iniciante": "Iniciante",
    "intermediario": "Intermediário",
    "graduacao": "Graduação",
    "avancado": "Avançado",
    "pos-graduacao": "Pós-graduação",
}
METHOD_LABELS = {
    "direto": "Direto",
    "socratico": "Socrático",
    "evidencias": "Baseado em evidências",
}


def fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").lower())
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", normalized).strip()


_GOAL_ALIASES = {
    "aprender": "aprender",
    "estudar": "aprender",
    "estudo": "aprender",
    "tutor": "aprender",
    "resolver": "resolver",
    "resolucao": "resolver",
    "problema": "resolver",
    "verificar": "verificar",
    "conferir": "verificar",
    "corrigir": "verificar",
    "solucao": "verificar",
    "artigo": "artigo",
    "paper": "artigo",
    "documento": "artigo",
    "argumento": "argumento",
    "debate": "argumento",
    "logica": "argumento",
}
_LEVEL_ALIASES = {
    "auto": "auto",
    "automatico": "auto",
    "iniciante": "iniciante",
    "basico": "iniciante",
    "intermediario": "intermediario",
    "medio": "intermediario",
    "graduacao": "graduacao",
    "universitario": "graduacao",
    "avancado": "avancado",
    "pos": "pos-graduacao",
    "pos graduacao": "pos-graduacao",
    "pos-graduacao": "pos-graduacao",
    "mestrado": "pos-graduacao",
    "doutorado": "pos-graduacao",
}
_METHOD_ALIASES = {
    "direto": "direto",
    "objetivo": "direto",
    "socratico": "socratico",
    "perguntas": "socratico",
    "evidencias": "evidencias",
    "evidencia": "evidencias",
    "fontes": "evidencias",
    "pesquisa": "evidencias",
}


def _canonical_alias(value: str, aliases: Mapping[str, str]) -> str | None:
    folded = fold_text(value).strip(" .,:;!?")
    if folded in aliases:
        return aliases[folded]
    # Permite instruções naturais como "nível de graduação" sem aceitar uma
    # palavra parcialmente coincidente dentro de outro termo.
    for alias in sorted(aliases, key=len, reverse=True):
        if re.search(rf"(?:^|\s){re.escape(alias)}(?:$|\s)", folded):
            return aliases[alias]
    return None


def normalize_goal(value: str) -> str | None:
    return _canonical_alias(value, _GOAL_ALIASES)


def normalize_level(value: str) -> str | None:
    return _canonical_alias(value, _LEVEL_ALIASES)


def normalize_method(value: str) -> str | None:
    return _canonical_alias(value, _METHOD_ALIASES)


@dataclass(slots=True, frozen=True)
class AcademicMentionCommand:
    action: str
    value: str = ""


_ACADEMIC_PREFIX_RE = re.compile(
    r"^(?:(?:modo|sala)\s+(?:de\s+)?)?acad[eê]mic[oa]\b\s*",
    re.IGNORECASE,
)


def parse_academic_mention_command(content: str) -> AcademicMentionCommand | None:
    """Interpreta somente comandos explícitos que começam por "acadêmico".

    Perguntas comuns como "explique um artigo acadêmico" não são capturadas e
    continuam seguindo o chat normal. A menção ao bot deve ser removida antes.
    """
    raw = (content or "").strip()
    prefix = _ACADEMIC_PREFIX_RE.match(raw)
    if not prefix:
        return None

    remainder = raw[prefix.end():].strip(" \t:,-")
    folded = fold_text(remainder)
    if not remainder or folded in {"ajuda", "help", "comandos", "menu"}:
        return AcademicMentionCommand("help")

    command_patterns: Sequence[tuple[str, str]] = (
        ("start", r"^(?:iniciar|inicie|comecar|comece|abrir|abra|nova sessao|novo estudo)\b"),
        ("status", r"^(?:status|ver|mostrar|painel)\b"),
        ("end", r"^(?:encerrar|encerre|finalizar|finalize|fechar|feche|sair)\b"),
        ("export", r"^(?:exportar|exporte|baixar|download)\b"),
        ("goal", r"^(?:objetivo|meta)\b"),
        ("level", r"^(?:nivel)\b"),
        ("method", r"^(?:metodo|metodologia)\b"),
    )
    for action, pattern in command_patterns:
        match = re.match(pattern, folded)
        if not match:
            continue
        # O índice do texto dobrado também cobre comandos acentuados como
        # "começar", "nível" e "método". Um re.sub com o padrão sem acentos
        # deixaria o próprio verbo dentro do valor.
        value = remainder[match.end():].strip(" \t:,-")
        return AcademicMentionCommand(action, value)

    # "acadêmico, explique ..." é uma pergunta, não um comando de controle.
    # Devolvemos None para o fluxo conversacional normal assumir a mensagem.
    return None


def _clean_single_line(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


def _clean_turn_content(value: str, limit: int = 6_000) -> str:
    return (value or "").replace("\x00", "").strip()[:limit]


@dataclass(slots=True)
class AcademicSession:
    id: int
    user_id: int
    guild_id: int
    channel_id: int
    topic: str
    goal: str = "aprender"
    level: str = "auto"
    method: str = "direto"
    status: str = "active"
    turns: list[dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
        + timedelta(seconds=ACADEMIC_SESSION_TTL_SECONDS)
    )

    @property
    def active(self) -> bool:
        return self.status == "active" and self.expires_at > datetime.now(timezone.utc)

    @property
    def goal_label(self) -> str:
        return GOAL_LABELS.get(self.goal, self.goal)

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS.get(self.level, self.level)

    @property
    def method_label(self) -> str:
        return METHOD_LABELS.get(self.method, self.method)

    @property
    def requires_retrieved_sources(self) -> bool:
        """Indica quando a sala deve recusar fundamentação puramente paramétrica."""
        return self.method == "evidencias" or self.goal in {"aprender", "artigo"}

    def as_history(self) -> list[dict[str, str]]:
        """Retorna somente o histórico desta sala, com teto de mensagens e texto."""
        selected = self.turns[-ACADEMIC_SESSION_MAX_TURNS:]
        output: list[dict[str, str]] = []
        used = 0
        for turn in reversed(selected):
            content = _clean_turn_content(str(turn.get("content") or ""))
            role = "assistant" if turn.get("role") == "assistant" else "user"
            if not content:
                continue
            remaining = ACADEMIC_SESSION_HISTORY_CHARS - used
            if remaining <= 0:
                break
            content = content[-remaining:] if len(content) > remaining else content
            output.append({"role": role, "content": content})
            used += len(content)
        return list(reversed(output))

    def instruction(self, *, media_requires_confirmation: bool = False) -> str:
        goal_instructions = {
            "aprender": (
                "Ensine o tema de forma progressiva. Verifique pré-requisitos, dê um "
                "exemplo curto e termine com uma pergunta útil de checagem."
            ),
            "resolver": (
                "Resolva o problema com rigor. Declare hipóteses, mostre as etapas "
                "essenciais, confira o resultado e destaque a conclusão."
            ),
            "verificar": (
                "Avalie a tentativa do usuário passo a passo. Identifique o primeiro "
                "passo sem justificativa ou incorreto, preserve o que estiver correto e "
                "dê uma correção ou dica precisa."
            ),
            "artigo": (
                "Analise tese, método, resultados, limitações e qualidade da evidência. "
                "Diferencie o que o documento afirma do que você apenas infere."
            ),
            "argumento": (
                "Reconstrua premissas e conclusão, explicite pressupostos, avalie "
                "validade e solidez e apresente a melhor objeção e uma possível resposta."
            ),
        }
        method_instructions = {
            "direto": "Dê a resposta direta antes do desenvolvimento.",
            "socratico": (
                "Use orientação socrática: não entregue toda a solução imediatamente "
                "quando uma pergunta intermediária puder conduzir o usuário."
            ),
            "evidencias": (
                "Priorize afirmações apoiadas pelas fontes recuperadas, ligue-as aos "
                "marcadores [S#] disponíveis e exponha divergências ou lacunas."
            ),
        }
        level_instruction = (
            "Adapte vocabulário, pressupostos e profundidade ao nível "
            f"{self.level_label}."
        )
        confirmation = ""
        if media_requires_confirmation:
            confirmation = (
                " Antes de resolver, escreva uma frase começando por "
                "'Interpretação do enunciado:' e transcreva de modo compacto o que "
                "conseguiu ler. Se algo estiver ilegível, peça confirmação em vez de adivinhar."
            )
        return (
            "SESSÃO ACADÊMICA V3 ISOLADA. Não use assuntos de conversas externas a "
            "esta sessão. Tema: "
            f"{self.topic}. Objetivo: {self.goal_label}. Método: {self.method_label}. "
            "Adote política fonte-primeiro: quando houver fontes [S#], derive delas "
            "as afirmações factuais e exponha qualquer lacuna em vez de preenchê-la "
            "com lembrança incerta do modelo. "
            f"{level_instruction} {goal_instructions.get(self.goal, goal_instructions['aprender'])} "
            f"{method_instructions.get(self.method, method_instructions['direto'])}"
            f"{confirmation} Use rótulos curtos em negrito apenas quando melhorarem a leitura; "
            "não use tabelas. Seja completo dentro do limite do Discord."
        )

    def summary(self) -> str:
        remaining = max(0, int((self.expires_at - datetime.now(timezone.utc)).total_seconds()))
        hours, remainder = divmod(remaining, 3600)
        minutes = remainder // 60
        duration = f"{hours}h {minutes}min" if hours else f"{minutes}min"
        return (
            f"**Tema:** {self.topic}\n"
            f"**Objetivo:** {self.goal_label}\n"
            f"**Nível:** {self.level_label}\n"
            f"**Método:** {self.method_label}\n"
            f"**Fonte primeiro:** {'obrigatória' if self.requires_retrieved_sources else 'assistida'}\n"
            f"**Histórico isolado:** {len(self.turns)} mensagem(ns)\n"
            f"**Expira após inatividade:** {duration}"
        )


class AcademicSessionStore:
    """Sessões acadêmicas persistentes, com fallback local transparente."""

    def __init__(self, pool: Any | None = None) -> None:
        self.pool = pool
        self.ready = False
        self._sessions: dict[int, AcademicSession] = {}
        self._next_id = 1

    def _cache(self, session: AcademicSession) -> AcademicSession:
        self._sessions[session.id] = session
        self._next_id = max(self._next_id, session.id + 1)
        return session

    def _close_cached_scope(
        self, user_id: int, guild_id: int, channel_id: int
    ) -> None:
        for cached in self._sessions.values():
            if (
                cached.user_id == user_id
                and cached.guild_id == guild_id
                and cached.channel_id == channel_id
                and cached.status == "active"
            ):
                cached.status = "closed"
                cached.updated_at = datetime.now(timezone.utc)

    def _degrade(self, operation: str, exc: Exception) -> None:
        self.ready = False
        logger.warning(
            "Banco indisponível em %s; sessões acadêmicas seguirão em memória local: %s",
            operation,
            exc,
        )

    async def prepare(self) -> None:
        if self.pool is None:
            self.ready = False
            return
        try:
            await self.pool.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_academic_sessions (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    guild_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    topic TEXT NOT NULL,
                    goal TEXT NOT NULL DEFAULT 'aprender',
                    level TEXT NOT NULL DEFAULT 'auto',
                    method TEXT NOT NULL DEFAULT 'direto',
                    status TEXT NOT NULL DEFAULT 'active',
                    turns JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            await self.pool.execute(
                """
                CREATE INDEX IF NOT EXISTS ai_academic_sessions_scope_idx
                ON ai_academic_sessions(guild_id, channel_id, user_id, status, updated_at DESC)
                """
            )
            self.ready = True
        except Exception:
            self.ready = False
            raise

    async def start(
        self,
        *,
        user_id: int,
        guild_id: int,
        channel_id: int,
        topic: str,
        goal: str = "aprender",
        level: str = "auto",
        method: str = "direto",
    ) -> AcademicSession:
        topic = _clean_single_line(topic, 180) or "Estudo livre"
        goal = goal if goal in GOAL_LABELS else "aprender"
        level = level if level in LEVEL_LABELS else "auto"
        method = method if method in METHOD_LABELS else "direto"
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=ACADEMIC_SESSION_TTL_SECONDS
        )
        self._close_cached_scope(user_id, guild_id, channel_id)

        if self.ready:
            try:
                async with (
                    self.pool.acquire() as connection,
                    connection.transaction(),
                ):
                    await connection.execute(
                        """
                        UPDATE ai_academic_sessions
                        SET status='closed', updated_at=NOW()
                        WHERE user_id=$1 AND guild_id=$2 AND channel_id=$3 AND status='active'
                        """,
                        user_id,
                        guild_id,
                        channel_id,
                    )
                    row = await connection.fetchrow(
                        """
                        INSERT INTO ai_academic_sessions(
                            user_id,guild_id,channel_id,topic,goal,level,method,expires_at
                        ) VALUES($1,$2,$3,$4,$5,$6,$7,$8)
                        RETURNING *
                        """,
                        user_id,
                        guild_id,
                        channel_id,
                        topic,
                        goal,
                        level,
                        method,
                        expires_at,
                    )
                return self._cache(self._from_row(row))
            except Exception as exc:
                self._degrade("start", exc)

        session = AcademicSession(
            id=self._next_id,
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            topic=topic,
            goal=goal,
            level=level,
            method=method,
            expires_at=expires_at,
        )
        return self._cache(session)

    async def get_active(
        self, user_id: int, guild_id: int, channel_id: int
    ) -> AcademicSession | None:
        if self.ready:
            try:
                row = await self.pool.fetchrow(
                    """
                    SELECT * FROM ai_academic_sessions
                    WHERE user_id=$1 AND guild_id=$2 AND channel_id=$3
                      AND status='active' AND expires_at > NOW()
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    user_id,
                    guild_id,
                    channel_id,
                )
                return self._cache(self._from_row(row)) if row else None
            except Exception as exc:
                self._degrade("get_active", exc)

        candidates = [
            session
            for session in self._sessions.values()
            if session.user_id == user_id
            and session.guild_id == guild_id
            and session.channel_id == channel_id
            and session.active
        ]
        return max(candidates, key=lambda item: item.updated_at) if candidates else None

    async def get_latest(
        self, user_id: int, guild_id: int, channel_id: int
    ) -> AcademicSession | None:
        """Recupera a sessão mais recente, inclusive encerrada, para exportação."""
        if self.ready:
            try:
                row = await self.pool.fetchrow(
                    """
                    SELECT * FROM ai_academic_sessions
                    WHERE user_id=$1 AND guild_id=$2 AND channel_id=$3
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    user_id,
                    guild_id,
                    channel_id,
                )
                return self._cache(self._from_row(row)) if row else None
            except Exception as exc:
                self._degrade("get_latest", exc)
        candidates = [
            session
            for session in self._sessions.values()
            if session.user_id == user_id
            and session.guild_id == guild_id
            and session.channel_id == channel_id
        ]
        return max(candidates, key=lambda item: item.updated_at) if candidates else None

    async def get(
        self, session_id: int, *, user_id: int | None = None, guild_id: int | None = None
    ) -> AcademicSession | None:
        if self.ready:
            try:
                row = await self.pool.fetchrow(
                    "SELECT * FROM ai_academic_sessions WHERE id=$1",
                    session_id,
                )
                session = self._cache(self._from_row(row)) if row else None
            except Exception as exc:
                self._degrade("get", exc)
                session = self._sessions.get(session_id)
        else:
            session = self._sessions.get(session_id)
        if session is None:
            return None
        if user_id is not None and session.user_id != user_id:
            return None
        if guild_id is not None and session.guild_id != guild_id:
            return None
        return session

    async def update_settings(
        self,
        session: AcademicSession,
        *,
        goal: str | None = None,
        level: str | None = None,
        method: str | None = None,
    ) -> AcademicSession:
        new_goal = goal if goal in GOAL_LABELS else session.goal
        new_level = level if level in LEVEL_LABELS else session.level
        new_method = method if method in METHOD_LABELS else session.method
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=ACADEMIC_SESSION_TTL_SECONDS
        )
        if self.ready:
            try:
                row = await self.pool.fetchrow(
                    """
                    UPDATE ai_academic_sessions
                    SET goal=$2, level=$3, method=$4, updated_at=NOW(), expires_at=$5
                    WHERE id=$1 AND status='active'
                    RETURNING *
                    """,
                    session.id,
                    new_goal,
                    new_level,
                    new_method,
                    expires_at,
                )
                return self._cache(self._from_row(row)) if row else session
            except Exception as exc:
                self._degrade("update_settings", exc)
        session.goal = new_goal
        session.level = new_level
        session.method = new_method
        session.updated_at = datetime.now(timezone.utc)
        session.expires_at = expires_at
        return self._cache(session)

    async def append_exchange(
        self, session: AcademicSession, user_text: str, assistant_text: str
    ) -> AcademicSession:
        if not session.active:
            return session
        turns = list(session.turns)
        turns.extend(
            [
                {"role": "user", "content": _clean_turn_content(user_text)},
                {"role": "assistant", "content": _clean_turn_content(assistant_text)},
            ]
        )
        turns = turns[-ACADEMIC_SESSION_MAX_TURNS:]
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=ACADEMIC_SESSION_TTL_SECONDS
        )
        if self.ready:
            try:
                row = await self.pool.fetchrow(
                    """
                    UPDATE ai_academic_sessions
                    SET turns=$2::jsonb, updated_at=NOW(), expires_at=$3
                    WHERE id=$1 AND status='active'
                    RETURNING *
                    """,
                    session.id,
                    json.dumps(turns, ensure_ascii=False),
                    expires_at,
                )
                return self._cache(self._from_row(row)) if row else session
            except Exception as exc:
                self._degrade("append_exchange", exc)
        session.turns = turns
        session.updated_at = datetime.now(timezone.utc)
        session.expires_at = expires_at
        return self._cache(session)

    async def end(self, session: AcademicSession) -> bool:
        if self.ready:
            try:
                result = await self.pool.execute(
                    """
                    UPDATE ai_academic_sessions
                    SET status='closed', updated_at=NOW()
                    WHERE id=$1 AND status='active'
                    """,
                    session.id,
                )
                if str(result).endswith("1"):
                    session.status = "closed"
                    session.updated_at = datetime.now(timezone.utc)
                    self._cache(session)
                    return True
                return False
            except Exception as exc:
                self._degrade("end", exc)
        if session.status != "active":
            return False
        session.status = "closed"
        session.updated_at = datetime.now(timezone.utc)
        self._cache(session)
        return True

    @staticmethod
    def export_markdown(session: AcademicSession) -> str:
        lines = [
            f"# Sessão Acadêmica — {session.topic}",
            "",
            f"- Objetivo: {session.goal_label}",
            f"- Nível: {session.level_label}",
            f"- Método: {session.method_label}",
            f"- Criada em: {session.created_at.astimezone(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}",
            "",
            "## Conversa",
            "",
        ]
        for turn in session.turns:
            role = "Usuário" if turn.get("role") == "user" else "RevolutX"
            lines.extend([f"### {role}", "", str(turn.get("content") or "").strip(), ""])
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _decode_turns(raw: Any) -> list[dict[str, str]]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return []
        if not isinstance(raw, list):
            return []
        turns: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            role = "assistant" if item.get("role") == "assistant" else "user"
            content = _clean_turn_content(str(item.get("content") or ""))
            if content:
                turns.append({"role": role, "content": content})
        return turns[-ACADEMIC_SESSION_MAX_TURNS:]

    @classmethod
    def _from_row(cls, row: Any) -> AcademicSession:
        return AcademicSession(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            topic=str(row["topic"]),
            goal=str(row["goal"]),
            level=str(row["level"]),
            method=str(row["method"]),
            status=str(row["status"]),
            turns=cls._decode_turns(row["turns"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
        )
