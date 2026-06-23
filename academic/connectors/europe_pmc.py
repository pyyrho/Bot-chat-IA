from __future__ import annotations

import aiohttp

from academic.models import AcademicWork
from academic.utils import clean_text, normalize_doi
from .base import AcademicConnector


class EuropePMCConnector(AcademicConnector):
    name = "Europe PMC"
    source_weight = 1.45
    endpoint = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    async def search(self, session: aiohttp.ClientSession, query: str, limit: int = 8) -> list[AcademicWork]:
        params = {"query": query, "format": "json", "pageSize": max(1, min(limit, 20)), "resultType": "core"}
        data = await self.get_json(session, self.endpoint, params=params)
        works: list[AcademicWork] = []
        result_list = ((data.get("resultList") or {}).get("result") or [])
        for item in result_list:
            title = clean_text(item.get("title"))
            if not title:
                continue
            authors = [clean_text(a.get("fullName")) for a in (item.get("authorList") or {}).get("author", []) if clean_text(a.get("fullName"))]
            if not authors:
                authors = [clean_text(a) for a in str(item.get("authorString") or "").split(",") if clean_text(a)]
            year = None
            try:
                year = int(item.get("pubYear")) if item.get("pubYear") else None
            except (TypeError, ValueError):
                pass
            doi = normalize_doi(item.get("doi"))
            pmid = clean_text(item.get("pmid"))
            pmcid = clean_text(item.get("pmcid"))
            url = f"https://europepmc.org/article/MED/{pmid}" if pmid else (f"https://europepmc.org/article/PMC/{pmcid}" if pmcid else "")
            works.append(AcademicWork(
                title=title,
                authors=authors,
                year=year,
                abstract=clean_text(item.get("abstractText"), 3200),
                url=url or (f"https://doi.org/{doi}" if doi else ""),
                doi=doi,
                source=self.name,
                venue=clean_text(item.get("journalTitle")),
                work_type=clean_text(item.get("pubType")) or "article",
                citation_count=int(item.get("citedByCount")) if str(item.get("citedByCount") or "").isdigit() else None,
                open_access=str(item.get("isOpenAccess") or "").upper() == "Y",
                identifiers={k: v for k, v in {"doi": doi, "pmid": pmid, "pmcid": pmcid}.items() if v},
                language=clean_text(item.get("language")),
                raw=item,
            ))
        return works
