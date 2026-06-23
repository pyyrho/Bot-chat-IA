from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import aiohttp

from academic.models import AcademicWork
from academic.utils import clean_text
from .base import AcademicConnector, ConnectorError


class ArxivConnector(AcademicConnector):
    name = "arXiv"
    source_weight = 1.05
    endpoint = "https://export.arxiv.org/api/query"

    async def search(self, session: aiohttp.ClientSession, query: str, limit: int = 8) -> list[AcademicWork]:
        params = {"search_query": f"all:{query}", "start": "0", "max_results": str(max(1, min(limit, 20))), "sortBy": "relevance", "sortOrder": "descending"}
        try:
            async with session.get(self.endpoint, params=params, timeout=aiohttp.ClientTimeout(total=self.timeout)) as response:
                body = await response.text()
                if response.status >= 400:
                    raise ConnectorError(f"HTTP {response.status}: {body[:180]}")
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ConnectorError(str(exc)) from exc
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise ConnectorError(f"XML inválido: {exc}") from exc
        ns = {"a": "http://www.w3.org/2005/Atom"}
        works: list[AcademicWork] = []
        for entry in root.findall("a:entry", ns):
            title = clean_text(entry.findtext("a:title", default="", namespaces=ns))
            if not title:
                continue
            authors = [clean_text(a.findtext("a:name", default="", namespaces=ns)) for a in entry.findall("a:author", ns)]
            published = clean_text(entry.findtext("a:published", default="", namespaces=ns))
            year = int(published[:4]) if re.match(r"^\d{4}", published) else None
            url = clean_text(entry.findtext("a:id", default="", namespaces=ns))
            arxiv_id = url.rsplit("/", 1)[-1]
            categories = [clean_text(c.attrib.get("term")) for c in entry.findall("a:category", ns) if c.attrib.get("term")]
            works.append(AcademicWork(
                title=title,
                authors=[a for a in authors if a],
                year=year,
                abstract=clean_text(entry.findtext("a:summary", default="", namespaces=ns), 3200),
                url=url,
                source=self.name,
                venue="arXiv",
                work_type="preprint",
                identifiers={"arxiv": arxiv_id},
                topics=categories[:8],
            ))
        return works
