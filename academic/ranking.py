from __future__ import annotations

from collections import defaultdict

from .models import AcademicWork
from .utils import normalize_title, relevance_score, title_similarity

_SOURCE_WEIGHTS = {
    "PubMed": 1.55,
    "Europe PMC": 1.45,
    "Semantic Scholar": 1.35,
    "OpenAlex": 1.25,
    "arXiv": 1.05,
    "Crossref": 1.0,
}


def merge_works(works: list[AcademicWork]) -> list[AcademicWork]:
    merged: list[AcademicWork] = []
    doi_index: dict[str, AcademicWork] = {}
    id_index: dict[str, AcademicWork] = {}

    for work in works:
        existing = doi_index.get(work.doi.lower()) if work.doi else None
        if not existing:
            for key, value in work.identifiers.items():
                existing = id_index.get(f"{key}:{value.lower()}")
                if existing:
                    break
        if not existing:
            normalized = normalize_title(work.title)
            existing = next(
                (
                    candidate for candidate in merged
                    if abs((candidate.year or 0) - (work.year or 0)) <= 1
                    and title_similarity(normalized, candidate.title) >= 0.88
                ),
                None,
            )
        if existing:
            _merge_into(existing, work)
        else:
            merged.append(work)
            if work.doi:
                doi_index[work.doi.lower()] = work
            for key, value in work.identifiers.items():
                id_index[f"{key}:{value.lower()}"] = work
    return merged


def _merge_into(target: AcademicWork, other: AcademicWork) -> None:
    if len(other.abstract) > len(target.abstract):
        target.abstract = other.abstract
    if not target.doi and other.doi:
        target.doi = other.doi
    if not target.url and other.url:
        target.url = other.url
    if not target.venue and other.venue:
        target.venue = other.venue
    if not target.year and other.year:
        target.year = other.year
    if target.citation_count is None or (other.citation_count or -1) > target.citation_count:
        target.citation_count = other.citation_count
    if target.open_access is None:
        target.open_access = other.open_access
    target.is_retracted = target.is_retracted or other.is_retracted
    target.identifiers.update({k: v for k, v in other.identifiers.items() if v})
    target.topics = list(dict.fromkeys([*target.topics, *other.topics]))[:12]
    target.authors = target.authors or other.authors
    bases = [b.strip() for b in target.source.split(" + ") if b.strip()]
    if other.source not in bases:
        bases.append(other.source)
    target.source = " + ".join(bases)


def rank_works(query: str, works: list[AcademicWork], *, limit: int) -> list[AcademicWork]:
    for work in works:
        weights = [_SOURCE_WEIGHTS.get(base.strip(), 0.7) for base in work.source.split(" + ")]
        source_weight = max(weights or [0.7]) + min(0.7, max(0, len(weights) - 1) * 0.16)
        work.score = relevance_score(
            query,
            work.title,
            work.abstract,
            year=work.year,
            citations=work.citation_count,
            source_weight=source_weight,
            is_retracted=work.is_retracted,
        )
    return sorted(works, key=lambda w: (w.is_retracted, -w.score, -(w.citation_count or 0), -(w.year or 0)))[:limit]


def source_coverage(works: list[AcademicWork]) -> dict[str, int]:
    counts: defaultdict[str, int] = defaultdict(int)
    for work in works:
        for source in work.source.split(" + "):
            counts[source.strip()] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
