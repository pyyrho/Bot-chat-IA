from __future__ import annotations

import os
from typing import Any

import aiohttp

from academic.models import AcademicWork
from academic.utils import clean_text, normalize_doi
from .base import AcademicConnector


class CrossrefConnector(AcademicConnector):
    name = "Crossref"
    source_weight = 1.0
    endpoint = "https://api.crossref.org/works"

    async def search(self, session: aiohttp.ClientSession, query: str, limit: int = 8) -> list[AcademicWork]:
        params: dict[str, Any] = {
            "query.bibliographic": query,
            "rows": max(1, min(limit, 20)),
        }
        email = (os.getenv("CROSSREF_MAILTO") or os.getenv("NCBI_EMAIL") or "").strip()
        if email:
            params["mailto"] = email
        data = await self.get_json(session, self.endpoint, params=params)
        items = ((data.get("message") or {}).get("items") or [])
        works: list[AcademicWork] = []
        for item in items:
            titles = item.get("title") or []
            title = clean_text(titles[0] if titles else "")
            if not title:
                continue
            authors = []
            for author in item.get("author") or []:
                name = clean_text(" ".join(filter(None, [author.get("given"), author.get("family")])))
                if name:
                    authors.append(name)
            year = None
            for field in ("published-print", "published-online", "published"):
                parts = (((item.get(field) or {}).get("date-parts") or [[]])[0] or [])
                if parts:
                    try:
                        year = int(parts[0])
                        break
                    except (TypeError, ValueError):
                        pass
            venue_list = item.get("container-title") or []
            doi = normalize_doi(item.get("DOI"))
            works.append(AcademicWork(
                title=title,
                authors=authors,
                year=year,
                abstract=clean_text(item.get("abstract"), 2400),
                url=clean_text(item.get("URL")) or (f"https://doi.org/{doi}" if doi else ""),
                doi=doi,
                source=self.name,
                venue=clean_text(venue_list[0] if venue_list else ""),
                work_type=clean_text(item.get("type")) or "article",
                citation_count=item.get("is-referenced-by-count") if isinstance(item.get("is-referenced-by-count"), int) else None,
                language=clean_text(item.get("language")),
                identifiers={"doi": doi} if doi else {},
                raw=item,
            ))
        return works
