from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from .models import AcademicWork


@dataclass(slots=True)
class EvidenceLandscape:
    label: str
    confidence: float
    source_diversity: int
    independent_works: int
    review_count: int
    preprint_count: int
    retracted_count: int
    median_year: int | None
    warnings: list[str] = field(default_factory=list)

    def compact(self) -> str:
        details = (
            f"classificação={self.label}; confiança={self.confidence:.0%}; "
            f"trabalhos={self.independent_works}; bases={self.source_diversity}; "
            f"revisões={self.review_count}; preprints={self.preprint_count}; "
            f"retratados={self.retracted_count}"
        )
        if self.median_year:
            details += f"; ano_mediano={self.median_year}"
        if self.warnings:
            details += "; alertas=" + " | ".join(self.warnings[:4])
        return details


_REVIEW_MARKERS = (
    "review", "systematic review", "meta-analysis", "meta analysis",
    "revisão", "revisao", "overview", "survey",
)
_PREPRINT_MARKERS = ("preprint", "arxiv", "biorxiv", "medrxiv", "ssrn")


def _is_review(work: AcademicWork) -> bool:
    value = f"{work.work_type} {work.title} {work.venue}".lower()
    return any(marker in value for marker in _REVIEW_MARKERS)


def _is_preprint(work: AcademicWork) -> bool:
    value = f"{work.work_type} {work.source} {work.venue} {work.url}".lower()
    return any(marker in value for marker in _PREPRINT_MARKERS)


def assess_landscape(works: list[AcademicWork]) -> EvidenceLandscape:
    """
    Descreve a paisagem documental sem fingir que mede consenso científico.

    A classificação usa diversidade, metadados e tipo de publicação. Ela não lê
    resultados completos nem substitui revisão sistemática, portanto os rótulos
    são deliberadamente conservadores.
    """
    if not works:
        return EvidenceLandscape("evidência insuficiente", 0.0, 0, 0, 0, 0, 0, None, ["nenhum trabalho recuperado"])

    bases = {
        source.strip()
        for work in works
        for source in work.source.split(" + ")
        if source.strip()
    }
    reviews = sum(_is_review(work) for work in works)
    preprints = sum(_is_preprint(work) for work in works)
    retracted = sum(work.is_retracted for work in works)
    years = sorted(work.year for work in works if work.year)
    year_median = int(median(years)) if years else None
    with_abstract = sum(bool(work.abstract.strip()) for work in works)
    cross_indexed = sum(" + " in work.source for work in works)

    score = 0.0
    score += min(0.30, len(works) * 0.025)
    score += min(0.22, len(bases) * 0.045)
    score += min(0.18, reviews * 0.06)
    score += min(0.12, cross_indexed * 0.025)
    score += min(0.12, with_abstract / max(1, len(works)) * 0.12)
    score -= min(0.24, retracted * 0.12)
    score -= min(0.12, preprints / max(1, len(works)) * 0.12)
    score = max(0.0, min(0.94, score))

    warnings: list[str] = []
    if len(works) < 4:
        warnings.append("amostra documental pequena")
    if len(bases) < 2:
        warnings.append("baixa diversidade de bases")
    if with_abstract < max(2, len(works) // 2):
        warnings.append("muitos registros sem resumo")
    if preprints:
        warnings.append(f"{preprints} preprint(s) sem garantia de revisão por pares")
    if retracted:
        warnings.append(f"{retracted} trabalho(s) marcado(s) como retratado(s)")
    if not reviews:
        warnings.append("nenhuma revisão identificada pelos metadados")

    if score >= 0.68 and reviews >= 2 and len(bases) >= 3:
        label = "base documental relativamente robusta"
    elif score >= 0.46:
        label = "base documental moderada"
    elif score >= 0.24:
        label = "base documental limitada"
    else:
        label = "evidência insuficiente"

    return EvidenceLandscape(
        label=label,
        confidence=score,
        source_diversity=len(bases),
        independent_works=len(works),
        review_count=reviews,
        preprint_count=preprints,
        retracted_count=retracted,
        median_year=year_median,
        warnings=warnings,
    )
