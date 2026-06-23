from __future__ import annotations

import asyncio
import os
import time

import aiohttp

from academic.models import AcademicWork
from academic.utils import clean_text
from .base import AcademicConnector


class PubMedConnector(AcademicConnector):
    name = "PubMed"
    source_weight = 1.55
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(self, *, timeout: float = 10.0) -> None:
        super().__init__(timeout=timeout)
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def _polite_json(self, session, url: str, *, params):
        async with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < 0.36:
                await asyncio.sleep(0.36 - elapsed)
            data = await self.get_json(session, url, params=params)
            self._last_request_at = time.monotonic()
            return data

    def _common(self) -> dict[str, str]:
        params = {"tool": "revolutx_discord_bot"}
        email = (os.getenv("NCBI_EMAIL") or "").strip()
        api_key = (os.getenv("NCBI_API_KEY") or "").strip()
        if email:
            params["email"] = email
        if api_key:
            params["api_key"] = api_key
        return params

    async def search(self, session: aiohttp.ClientSession, query: str, limit: int = 8) -> list[AcademicWork]:
        params = {**self._common(), "db": "pubmed", "term": query, "retmode": "json", "retmax": str(max(1, min(limit, 20))), "sort": "relevance"}
        found = await self._polite_json(session, f"{self.base}/esearch.fcgi", params=params)
        ids = (found.get("esearchresult") or {}).get("idlist") or []
        if not ids:
            return []
        summary_params = {**self._common(), "db": "pubmed", "id": ",".join(ids), "retmode": "json"}
        data = await self._polite_json(session, f"{self.base}/esummary.fcgi", params=summary_params)
        result = data.get("result") or {}
        works: list[AcademicWork] = []
        for uid in ids:
            item = result.get(str(uid)) or {}
            title = clean_text(item.get("title"))
            if not title:
                continue
            authors = [clean_text(a.get("name")) for a in (item.get("authors") or []) if clean_text(a.get("name"))]
            year = None
            pubdate = clean_text(item.get("pubdate"))
            if pubdate[:4].isdigit():
                year = int(pubdate[:4])
            doi = ""
            for article_id in item.get("articleids") or []:
                if str(article_id.get("idtype") or "").lower() == "doi":
                    doi = clean_text(article_id.get("value")).lower()
                    break
            works.append(AcademicWork(
                title=title,
                authors=authors,
                year=year,
                abstract="",
                url=f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                doi=doi,
                source=self.name,
                venue=clean_text(item.get("fulljournalname") or item.get("source")),
                work_type="article",
                identifiers={k: v for k, v in {"pmid": str(uid), "doi": doi}.items() if v},
                raw=item,
            ))
        return works
