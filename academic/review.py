from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


ACADEMIC_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": [
                "consistente",
                "parcialmente_fundamentada",
                "evidencia_limitada",
                "revisao_necessaria",
            ],
        },
        "summary": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "caveats": {"type": "array", "items": {"type": "string"}},
        "sources_supported": {"type": "boolean"},
        "follow_up": {"type": "string"},
    },
    "required": [
        "verdict",
        "summary",
        "strengths",
        "caveats",
        "sources_supported",
        "follow_up",
    ],
    "additionalProperties": False,
}


VERDICT_LABELS = {
    "consistente": "Consistência alta",
    "parcialmente_fundamentada": "Fundamentação parcial",
    "evidencia_limitada": "Evidência limitada",
    "revisao_necessaria": "Revisão necessária",
}


def _clean(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _clean_list(value: Any, *, limit: int = 3, item_chars: int = 220) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    output: list[str] = []
    for item in value:
        clean = _clean(item, item_chars)
        if clean and clean not in output:
            output.append(clean)
        if len(output) >= limit:
            break
    return output


@dataclass(slots=True)
class AcademicReview:
    verdict: str
    summary: str
    strengths: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    sources_supported: bool = False
    follow_up: str = ""
    method: str = "deterministic"

    @property
    def label(self) -> str:
        return VERDICT_LABELS.get(self.verdict, "Revisão interna")

    def as_text(self) -> str:
        lines = [f"**{self.label}**", self.summary]
        if self.strengths:
            lines.append("\n**Pontos conferidos**\n" + "\n".join(f"- {x}" for x in self.strengths))
        if self.caveats:
            lines.append("\n**Limitações**\n" + "\n".join(f"- {x}" for x in self.caveats))
        if self.follow_up:
            lines.append("\n**Próximo passo**\n" + self.follow_up)
        lines.append(
            "\n-# Esta revisão avalia consistência, completude e uso das fontes; "
            "não transforma automaticamente a resposta em verdade comprovada."
        )
        return "\n".join(lines)[:3_800]


def parse_academic_review(payload: str) -> AcademicReview | None:
    raw = (payload or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, Mapping):
        return None
    verdict = str(data.get("verdict") or "")
    if verdict not in VERDICT_LABELS:
        return None
    summary = _clean(data.get("summary"), 320)
    if not summary:
        return None
    return AcademicReview(
        verdict=verdict,
        summary=summary,
        strengths=_clean_list(data.get("strengths")),
        caveats=_clean_list(data.get("caveats")),
        sources_supported=bool(data.get("sources_supported")),
        follow_up=_clean(data.get("follow_up"), 240),
        method="structured-model",
    )


def deterministic_academic_review(
    *,
    answer: str,
    source_count: int,
    needs_sources: bool,
    audit_score: float,
    audit_issue_codes: Sequence[str] = (),
) -> AcademicReview:
    issues = set(audit_issue_codes)
    severe = issues & {
        "empty",
        "cutoff",
        "invalid_source_marker",
        "malformed_doi",
        "no_sources",
    }
    strengths: list[str] = []
    caveats: list[str] = []

    if answer.strip() and "cutoff" not in issues:
        strengths.append("A resposta termina de forma completa e mantém estrutura legível.")
    if source_count:
        strengths.append(f"Foram recuperadas {source_count} fonte(s) para o contexto.")
    if "uncited_sources" in issues:
        caveats.append("As fontes recuperadas não foram ligadas claramente às afirmações.")
    if needs_sources and not source_count:
        caveats.append("A pergunta exigia evidência externa, mas nenhuma fonte foi recuperada.")
    if severe:
        caveats.append("A auditoria detectou um problema que precisa de revisão antes de confiar na resposta.")

    if severe or audit_score < 0.6:
        verdict = "revisao_necessaria"
        summary = "A resposta precisa de revisão adicional antes de ser tratada como conclusão segura."
    elif needs_sources and not source_count:
        verdict = "evidencia_limitada"
        summary = "A explicação pode ser útil, mas a sustentação externa disponível é insuficiente."
    elif needs_sources and ("uncited_sources" in issues or audit_score < 0.84):
        verdict = "parcialmente_fundamentada"
        summary = "A resposta é coerente, porém a ligação entre afirmações e evidências pode melhorar."
    else:
        verdict = "consistente"
        summary = "A checagem interna não encontrou falhas estruturais relevantes na resposta."

    follow_up = (
        "Abra Fontes e confira os materiais originais."
        if source_count
        else "Peça uma checagem com fontes se a conclusão depender de fatos externos."
    )
    return AcademicReview(
        verdict=verdict,
        summary=summary,
        strengths=strengths[:3],
        caveats=caveats[:3],
        sources_supported=bool(source_count and "uncited_sources" not in issues),
        follow_up=follow_up,
        method="deterministic",
    )
