from __future__ import annotations

import os
from typing import Any

import aiohttp

from academic.models import AcademicWork
from academic.utils import clean_text, normalize_doi, reconstruct_openalex_abstract
from .base import AcademicConnector


class OpenAlexConnector(AcademicConnector):
    name = "OpenAlex"
    source_weight = 1.25
    endpoint = "https://api.openalex.org/works"

    async def search(self, session: aiohttp.ClientSession, query: str, limit: int = 8) -> list[AcademicWork]:
        key = (os.getenv("OPENALEX_API_KEY") or "").strip()
        params: dict[str, Any] = {
            "search": query,
            "per_page": max(1, min(limit, 20)),
            "select": "id,doi,display_name,publication_year,authorships,primary_location,type,cited_by_count,open_access,abstract_inverted_index,language,topics,is_retracted,best_oa_location",
        }
        if key:
            params["api_key"] = key
        data = await self.get_json(session, self.endpoint, params=params)
        works: list[AcademicWork] = []
        for item in data.get("results") or []:
            title = clean_text(item.get("display_name"))
            if not title:
                continue
            authors = []
            for authorship in item.get("authorships") or []:
                name = clean_text((authorship.get("author") or {}).get("display_name"))
                if name:
                    authors.append(name)
            location = item.get("primary_location") or {}
            source = location.get("source") or {}
            doi = normalize_doi(item.get("doi"))
            oa = item.get("open_access") or {}
            best_oa = item.get("best_oa_location") or {}
            openalex_id = clean_text(item.get("id")).rsplit("/", 1)[-1]
            topics = [clean_text(t.get("display_name")) for t in (item.get("topics") or []) if clean_text(t.get("display_name"))]
            url = clean_text(best_oa.get("landing_page_url") or location.get("landing_page_url") or item.get("doi") or item.get("id"))
            works.append(AcademicWork(
                title=title,
                authors=authors,
                year=item.get("publication_year") if isinstance(item.get("publication_year"), int) else None,
                abstract=reconstruct_openalex_abstract(item.get("abstract_inverted_index"))[:3000],
                url=url,
                doi=doi,
                source=self.name,
                venue=clean_text(source.get("display_name")),
                work_type=clean_text(item.get("type")) or "article",
                citation_count=item.get("cited_by_count") if isinstance(item.get("cited_by_count"), int) else None,
                open_access=oa.get("is_oa") if isinstance(oa.get("is_oa"), bool) else None,
                identifiers={k: v for k, v in {"doi": doi, "openalex": openalex_id}.items() if v},
                topics=topics[:8],
                language=clean_text(item.get("language")),
                is_retracted=bool(item.get("is_retracted")),
                raw=item,
            ))
        return works
