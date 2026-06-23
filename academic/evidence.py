from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import AcademicWork
from .utils import token_set

_NEGATION = {"não", "nao", "never", "not", "no", "without", "fails", "failed", "refuta", "refute"}


@dataclass(slots=True)
class EvidenceLink:
    source_index: int
    support: str
    score: float
    rationale: str


@dataclass(slots=True)
class ClaimEvidence:
    claim: str
    links: list[EvidenceLink] = field(default_factory=list)
    confidence: str = "insuficiente"


@dataclass(slots=True)
class EvidenceGraph:
    claims: list[ClaimEvidence]

    def compact(self) -> str:
        lines: list[str] = []
        for index, claim in enumerate(self.claims, start=1):
            links = ", ".join(f"S{x.source_index}:{x.support}/{x.score:.2f}" for x in claim.links[:4]) or "sem correspondência"
            lines.append(f"C{index} [{claim.confidence}] {claim.claim} -> {links}")
        return "\n".join(lines)


def extract_claims(text: str, *, limit: int = 8) -> list[str]:
    value = re.sub(r"\s+", " ", text or "").strip()
    parts = re.split(r"(?<=[.!?;])\s+|\n+", value)
    claims: list[str] = []
    for part in parts:
        clean = part.strip(" -*•\t")
        words = clean.split()
        if 5 <= len(words) <= 55 and not clean.endswith("?"):
            claims.append(clean)
        if len(claims) >= limit:
            break
    if not claims and value:
        claims.append(value[:400])
    return claims


def build_evidence_graph(claims: list[str], works: list[AcademicWork]) -> EvidenceGraph:
    output: list[ClaimEvidence] = []
    for claim in claims:
        claim_terms = token_set(claim)
        claim_neg = bool(claim_terms & _NEGATION)
        links: list[EvidenceLink] = []
        for index, work in enumerate(works, start=1):
            evidence_text = f"{work.title} {work.abstract}"
            evidence_terms = token_set(evidence_text)
            if not claim_terms or not evidence_terms:
                continue
            overlap = len(claim_terms & evidence_terms) / max(1, len(claim_terms))
            title_overlap = len(claim_terms & token_set(work.title)) / max(1, len(claim_terms))
            score = min(1.0, overlap * 0.72 + title_overlap * 0.55)
            if score < 0.16:
                continue
            evidence_neg = bool(evidence_terms & _NEGATION)
            support = "contesta" if claim_neg != evidence_neg and score >= 0.32 else ("apoia" if score >= 0.32 else "relaciona")
            links.append(EvidenceLink(index, support, round(score, 3), f"sobreposição temática {score:.0%}"))
        links.sort(key=lambda item: item.score, reverse=True)
        strong = sum(1 for link in links if link.score >= 0.42 and link.support == "apoia")
        conflicts = sum(1 for link in links if link.support == "contesta")
        if strong >= 2 and not conflicts:
            confidence = "forte"
        elif strong >= 1 or len(links) >= 3:
            confidence = "moderada"
        elif links:
            confidence = "fraca"
        else:
            confidence = "insuficiente"
        output.append(ClaimEvidence(claim, links[:5], confidence))
    return EvidenceGraph(output)


def evidence_instructions(works: list[AcademicWork]) -> str:
    if not works:
        return ""
    return (
        "REGRAS DO RASTRO DE EVIDÊNCIA:\n"
        "1. Trate cada fonte como evidência, não como verdade automática.\n"
        "2. Ao fazer afirmação factual importante, cite [S1], [S2] etc.\n"
        "3. Não atribua à fonte algo que não aparece no título/resumo/metadados.\n"
        "4. Separe fato, interpretação e inferência.\n"
        "5. Sinalize retratações, preprints, ausência de resumo e divergências.\n"
        "6. Não invente página, DOI, autor, ano ou consenso.\n"
    )
