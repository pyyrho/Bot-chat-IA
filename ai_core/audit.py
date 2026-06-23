from __future__ import annotations

import re
from collections import Counter

from .models import AuditIssue, AuditReport, QueryAnalysis

_CITATION_RE = re.compile(r"(?:doi:\s*|https?://doi\.org/)(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.I)
_ABSOLUTE = re.compile(r"\b(sempre|nunca|todos|nenhum|com certeza|provado definitivamente)\b", re.I)
_HEDGING = re.compile(r"\b(pode|sugere|indica|provavelmente|evidência|evidencia|limitação|limita)\b", re.I)


class ResponseAuditor:
    """Auditoria determinística. Não chama outro modelo e não adiciona latência de rede."""

    def audit(
        self,
        question: str,
        answer: str,
        analysis: QueryAnalysis,
        *,
        source_count: int = 0,
    ) -> AuditReport:
        issues: list[AuditIssue] = []
        text = (answer or "").strip()
        if not text:
            return AuditReport(0.0, [AuditIssue("empty", "critical", "Resposta vazia")])

        if text.count("```") % 2:
            issues.append(AuditIssue("markdown_code", "high", "Bloco de código não foi fechado"))
        if text.count("**") % 2:
            issues.append(AuditIssue("markdown_bold", "medium", "Negrito possivelmente não foi fechado"))
        if len(text) > 500 and not re.search(r"[.!?…\])}]$", text.rstrip()):
            issues.append(AuditIssue("cutoff", "high", "A resposta parece ter sido interrompida"))

        paragraphs = [re.sub(r"\s+", " ", p.strip().lower()) for p in text.split("\n\n") if len(p.strip()) > 40]
        duplicates = len(paragraphs) - len(set(paragraphs))
        if duplicates:
            issues.append(AuditIssue("repetition", "medium", "Há parágrafos repetidos"))

        words = re.findall(r"[\wÀ-ÿ-]+", text.lower())
        if words:
            most = Counter(words).most_common(1)[0][1]
            if most / max(1, len(words)) > 0.09 and len(words) > 180:
                issues.append(AuditIssue("lexical_repetition", "low", "Vocabulário excessivamente repetitivo"))

        source_markers = [int(value) for value in re.findall(r"\[S(\d+)\]", text)]
        if analysis.needs_sources and source_count == 0:
            issues.append(AuditIssue("no_sources", "high" if analysis.is_claim_check else "medium", "A resposta exigia fontes, mas nenhuma fonte foi recuperada"))
        elif analysis.needs_sources and source_count > 0 and not source_markers:
            issues.append(AuditIssue("uncited_sources", "medium", "Fontes foram recuperadas, mas a resposta não liga afirmações a [S1], [S2]"))
        invalid_markers = sorted({value for value in source_markers if value < 1 or value > source_count})
        if invalid_markers:
            issues.append(AuditIssue("invalid_source_marker", "high", f"A resposta cita fontes inexistentes: {invalid_markers}"))
        if analysis.is_high_stakes and _ABSOLUTE.search(text) and not _HEDGING.search(text):
            issues.append(AuditIssue("overconfidence", "high", "Linguagem absoluta em tema de alto impacto"))
        if analysis.is_claim_check and not re.search(r"\b(confirmad|parcial|inconclus|incorret|controvers|evidência)\w*", text, re.I):
            issues.append(AuditIssue("claim_verdict", "medium", "A checagem não apresenta um veredito claro"))

        dois = [m.group(1).rstrip(".,;)") for m in _CITATION_RE.finditer(text)]
        malformed = [doi for doi in dois if "/" not in doi or " " in doi]
        if malformed:
            issues.append(AuditIssue("malformed_doi", "high", "Há DOI malformado na resposta"))

        penalty = {"low": 0.04, "medium": 0.10, "high": 0.22, "critical": 0.55}
        score = max(0.0, 1.0 - sum(penalty.get(i.severity, 0.08) for i in issues))
        repaired = self._repair_markdown(text)
        return AuditReport(round(score, 3), issues, repaired_text=repaired if repaired != text else None, metadata={"dois": dois})

    @staticmethod
    def _repair_markdown(text: str) -> str:
        result = text.rstrip()
        if result.count("```") % 2:
            result += "\n```"
        if result.count("**") % 2:
            result += "**"
        return result

    @staticmethod
    def warning_suffix(report: AuditReport) -> str:
        relevant = [i.message for i in report.issues if i.severity in {"high", "critical"}]
        if not relevant:
            return ""
        compact = "; ".join(relevant[:2])
        return f"\n\n-# Verificação interna: {compact}."
