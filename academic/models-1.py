from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class AcademicWork:
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    url: str = ""
    doi: str = ""
    source: str = ""
    venue: str = ""
    work_type: str = "article"
    citation_count: int | None = None
    open_access: bool | None = None
    identifiers: dict[str, str] = field(default_factory=dict)
    topics: list[str] = field(default_factory=list)
    language: str = ""
    is_retracted: bool = False
    score: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def canonical_id(self) -> str:
        if self.doi:
            return "doi:" + self.doi.lower().removeprefix("https://doi.org/")
        for key in ("pmid", "arxiv", "openalex", "s2"):
            if value := self.identifiers.get(key):
                return f"{key}:{value.lower()}"
        return "title:" + " ".join(self.title.lower().split())[:220]

    def compact(self, index: int | None = None, *, abstract_chars: int = 700) -> str:
        prefix = f"[S{index}] " if index is not None else ""
        authors = ", ".join(self.authors[:6]) or "autoria não informada"
        if len(self.authors) > 6:
            authors += " et al."
        meta = [authors]
        if self.year:
            meta.append(str(self.year))
        if self.venue:
            meta.append(self.venue)
        if self.work_type:
            meta.append(self.work_type)
        if self.citation_count is not None:
            meta.append(f"{self.citation_count} citações")
        if self.is_retracted:
            meta.append("RETRATADO")
        lines = [f"{prefix}{self.title}", " | ".join(meta)]
        if self.abstract:
            lines.append("Resumo: " + " ".join(self.abstract.split())[:abstract_chars])
        if self.doi:
            lines.append("DOI: " + self.doi.lower().removeprefix("https://doi.org/"))
        if self.url:
            lines.append("URL: " + self.url)
        lines.append("Base: " + self.source)
        return "\n".join(lines)


@dataclass(slots=True)
class SearchPlan:
    original_query: str
    queries: list[str]
    depth: str = "standard"
    max_results: int = 12
    recent_only: bool = False
    include_reviews: bool = True
    domains: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SearchDiagnostics:
    started_at: datetime
    elapsed_ms: int = 0
    providers_attempted: list[str] = field(default_factory=list)
    providers_succeeded: list[str] = field(default_factory=list)
    provider_errors: dict[str, str] = field(default_factory=dict)
    raw_results: int = 0
    deduplicated_results: int = 0
    cache_hit: bool = False


@dataclass(slots=True)
class AcademicSearchResult:
    query: str
    works: list[AcademicWork]
    context: str
    sources: list[str]
    diagnostics: SearchDiagnostics
    evidence_summary: str = ""
