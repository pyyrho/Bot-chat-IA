from __future__ import annotations

import os

import aiohttp

from academic.models import AcademicWork
from academic.utils import clean_text, normalize_doi
from .base import AcademicConnector


class SemanticScholarConnector(AcademicConnector):
    name = "Semantic Scholar"
    source_weight = 1.35
    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search"

    async def search(self, session: aiohttp.ClientSession, query: str, limit: int = 8) -> list[AcademicWork]:
        params = {
            "query": query,
            "limit": max(1, min(limit, 20)),
            "fields": "paperId,title,authors,year,abstract,url,venue,citationCount,externalIds,openAccessPdf,publicationTypes,fieldsOfStudy,isOpenAccess",
        }
        key = (os.getenv("SEMANTIC_SCHOLAR_API_KEY") or "").strip()
        headers = {"x-api-key": key} if key else None
        data = await self.get_json(session, self.endpoint, params=params, headers=headers, retries=2)
        works: list[AcademicWork] = []
        for item in data.get("data") or []:
            title = clean_text(item.get("title"))
            if not title:
                continue
            external = item.get("externalIds") or {}
            doi = normalize_doi(external.get("DOI"))
            pdf = item.get("openAccessPdf") or {}
            publication_types = item.get("publicationTypes") or []
            works.append(AcademicWork(
                title=title,
                authors=[clean_text(a.get("name")) for a in (item.get("authors") or []) if clean_text(a.get("name"))],
                year=item.get("year") if isinstance(item.get("year"), int) else None,
                abstract=clean_text(item.get("abstract"), 3000),
                url=clean_text(pdf.get("url") or item.get("url")) or (f"https://doi.org/{doi}" if doi else ""),
                doi=doi,
                source=self.name,
                venue=clean_text(item.get("venue")),
                work_type=clean_text(publication_types[0] if publication_types else "article"),
                citation_count=item.get("citationCount") if isinstance(item.get("citationCount"), int) else None,
                open_access=item.get("isOpenAccess") if isinstance(item.get("isOpenAccess"), bool) else None,
                identifiers={k: clean_text(v) for k, v in {"doi": doi, "s2": item.get("paperId"), "pmid": external.get("PubMed"), "arxiv": external.get("ArXiv")}.items() if v},
                topics=[clean_text(x) for x in (item.get("fieldsOfStudy") or []) if clean_text(x)][:8],
                raw=item,
            ))
        return works
