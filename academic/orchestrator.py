from __future__ import annotations

import asyncio
import copy
import logging
import os
import time
from datetime import datetime, timezone


from ai_core.models import QueryAnalysis
from ai_core.runtime import RequestCoalescer, SharedHTTPSession
from ai_core.semantic_cache import SemanticTTLCache
from .evidence import build_evidence_graph, evidence_instructions, extract_claims
from .consensus import assess_landscape
from .verification import audit_references
from .models import AcademicSearchResult, AcademicWork, SearchDiagnostics
from .planner import AcademicQueryPlanner
from .ranking import merge_works, rank_works, source_coverage
from .connectors.arxiv import ArxivConnector
from .connectors.crossref import CrossrefConnector
from .connectors.europe_pmc import EuropePMCConnector
from .connectors.openalex import OpenAlexConnector
from .connectors.pubmed import PubMedConnector
from .connectors.semantic_scholar import SemanticScholarConnector

logger = logging.getLogger("Revolutx.Academic")


class AcademicOrchestrator:
    def __init__(self, *, bot_name: str = "Revolutx") -> None:
        self.http = SharedHTTPSession(user_agent=f"{bot_name}/3.0 academic-research", total_timeout=18, limit=30)
        self.coalescer = RequestCoalescer()
        self._semaphore = asyncio.Semaphore(max(1, int(os.getenv("ACADEMIC_SEARCH_CONCURRENCY", "3"))))
        self.cache: SemanticTTLCache[AcademicSearchResult] = SemanticTTLCache(max_size=192, threshold=0.94)
        self.planner = AcademicQueryPlanner()
        self.connectors = [
            SemanticScholarConnector(timeout=11),
            CrossrefConnector(timeout=10),
            ArxivConnector(timeout=11),
            EuropePMCConnector(timeout=11),
            PubMedConnector(timeout=11),
            OpenAlexConnector(timeout=11),
        ]
        disabled = {x.strip().lower() for x in os.getenv("ACADEMIC_DISABLED_SOURCES", "").split(",") if x.strip()}
        self.connectors = [c for c in self.connectors if c.name.lower() not in disabled]

    @staticmethod
    def _select_connectors(domains: list[str], depth: str) -> set[str]:
        domain_set = set(domains)
        selected = {"Semantic Scholar", "Crossref", "OpenAlex"}
        if "biomedical" in domain_set:
            selected.update({"PubMed", "Europe PMC"})
        if domain_set & {"computing", "formal"}:
            selected.add("arXiv")
        if "humanities" in domain_set and depth in {"deep", "research"}:
            selected.add("arXiv")
        if domain_set == {"general"} and depth in {"deep", "research"}:
            selected.add("arXiv")
        return selected

    async def close(self) -> None:
        await self.http.close()

    async def search(self, question: str, analysis: QueryAnalysis) -> AcademicSearchResult:
        namespace = f"academic:{analysis.depth.value}"
        cached, _ = self.cache.get(question, namespace=namespace)
        if cached:
            result = copy.deepcopy(cached)
            result.diagnostics.cache_hit = True
            return result

        async def run() -> AcademicSearchResult:
            async with self._semaphore:
                return await self._search_uncached(question, analysis)

        result = await self.coalescer.run(f"{namespace}:{question.lower().strip()[:500]}", run)
        ttl = 1800 if analysis.is_current else 21600
        self.cache.set(question, copy.deepcopy(result), ttl=ttl, namespace=namespace)
        return result

    async def _search_uncached(self, question: str, analysis: QueryAnalysis) -> AcademicSearchResult:
        started_dt = datetime.now(timezone.utc)
        started = time.monotonic()
        plan = self.planner.build(question, analysis)
        diagnostics = SearchDiagnostics(started_at=started_dt)
        session = await self.http.get()
        per_connector = 5 if plan.depth in {"deep", "research"} else 3

        selected_names = self._select_connectors(plan.domains, plan.depth)
        tasks: list[tuple[str, asyncio.Task[list[AcademicWork]]]] = []
        for connector in self.connectors:
            if connector.name not in selected_names:
                continue
            diagnostics.providers_attempted.append(connector.name)
            query = plan.queries[0]
            if connector.name in {"PubMed", "Europe PMC"}:
                review_query = next((q for q in plan.queries if "review" in q.lower()), None)
                query = review_query or query
            elif connector.name == "arXiv" and len(plan.queries) > 1:
                query = plan.queries[1]
            tasks.append((connector.name, asyncio.create_task(connector.search(session, query, per_connector))))

        raw: list[AcademicWork] = []
        for name, task in tasks:
            try:
                works = await task
                raw.extend(works)
                diagnostics.providers_succeeded.append(name)
            except Exception as exc:
                diagnostics.provider_errors[name] = str(exc)[:240]
                logger.debug("Fonte %s falhou: %s", name, exc)

        diagnostics.raw_results = len(raw)
        merged = merge_works(raw)
        ranked = rank_works(question, merged, limit=plan.max_results)
        diagnostics.deduplicated_results = len(ranked)
        diagnostics.elapsed_ms = int((time.monotonic() - started) * 1000)

        context_parts = [evidence_instructions(ranked)]
        for index, work in enumerate(ranked, start=1):
            context_parts.append(work.compact(index, abstract_chars=850 if plan.depth in {"deep", "research"} else 520))
        context = "\n\n".join(part for part in context_parts if part)
        claims = extract_claims(question, limit=5)
        graph = build_evidence_graph(claims, ranked)
        coverage = source_coverage(ranked)
        landscape = assess_landscape(ranked)
        reference_audit = audit_references(ranked)
        evidence_summary = (
            "Mapa preliminar de evidência (correspondência textual, não veredito final):\n"
            + graph.compact()
            + "\nCobertura por base: "
            + ", ".join(f"{name}={count}" for name, count in coverage.items())
            + "\nPaisagem documental: " + landscape.compact()
            + "\n" + reference_audit.compact()
        )
        sources = [f"[S{i}] {work.title} | {work.source} | {work.url or work.doi}" for i, work in enumerate(ranked, start=1)]
        return AcademicSearchResult(question, ranked, context, sources, diagnostics, evidence_summary)
