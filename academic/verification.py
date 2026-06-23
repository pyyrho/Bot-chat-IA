from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import AcademicWork
from .utils import normalize_title

_DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.I)


@dataclass(slots=True)
class ReferenceCheck:
    source_index: int
    valid_doi_shape: bool
    metadata_complete: float
    cross_indexed: bool
    retracted: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReferenceAudit:
    checks: list[ReferenceCheck]

    @property
    def trustworthy_count(self) -> int:
        return sum(
            check.valid_doi_shape
            and check.metadata_complete >= 0.6
            and not check.retracted
            for check in self.checks
        )

    def compact(self) -> str:
        lines = [
            f"Auditoria de metadados: {self.trustworthy_count}/{len(self.checks)} registros com metadados mínimos e sem alerta de retratação."
        ]
        for check in self.checks[:12]:
            flags = []
            if not check.valid_doi_shape:
                flags.append("DOI ausente/inválido")
            if check.cross_indexed:
                flags.append("indexado em múltiplas bases")
            if check.retracted:
                flags.append("RETRATADO")
            flags.extend(check.warnings[:2])
            lines.append(
                f"S{check.source_index}: completude={check.metadata_complete:.0%}"
                + ("; " + ", ".join(flags) if flags else "; sem alerta estrutural")
            )
        return "\n".join(lines)


def audit_references(works: list[AcademicWork]) -> ReferenceAudit:
    checks: list[ReferenceCheck] = []
    seen_titles: set[str] = set()
    for index, work in enumerate(works, 1):
        fields = [
            bool(work.title.strip()), bool(work.authors), bool(work.year),
            bool(work.abstract.strip()), bool(work.url or work.doi), bool(work.venue),
        ]
        completeness = sum(fields) / len(fields)
        warnings: list[str] = []
        normalized = normalize_title(work.title)
        if normalized in seen_titles:
            warnings.append("título duplicado após normalização")
        seen_titles.add(normalized)
        if work.year and not (1500 <= work.year <= 2100):
            warnings.append("ano fora da faixa esperada")
        if not work.authors:
            warnings.append("autoria ausente")
        if not work.abstract:
            warnings.append("resumo indisponível")
        doi = work.doi.lower().removeprefix("https://doi.org/").strip()
        checks.append(
            ReferenceCheck(
                source_index=index,
                valid_doi_shape=bool(doi and _DOI_RE.match(doi)),
                metadata_complete=completeness,
                cross_indexed=" + " in work.source,
                retracted=work.is_retracted,
                warnings=warnings,
            )
        )
    return ReferenceAudit(checks)
