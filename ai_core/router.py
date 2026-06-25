from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .models import Depth, QueryAnalysis, RoutingDecision

_ACADEMIC = {
    "artigo", "paper", "estudo", "pesquisa", "tese", "dissertação", "metodologia",
    "evidência", "evidencias", "fonte", "fontes", "científico", "cientifica",
    "filosofia", "epistemologia", "ontologia", "metafísica", "ética", "lógica",
    "sociologia", "psicologia", "história", "economia", "física", "química",
    "biologia", "medicina", "neurociência", "matemática", "teorema", "prova",
}
_CURRENT = {
    "hoje", "agora", "atual", "atuais", "recente", "recentes", "último", "ultima",
    "2025", "2026", "notícia", "noticias", "preço", "cotação", "presidente atual",
    "versão atual", "lançamento", "resultado", "placar", "ranking",
}
_SEARCH = {
    "pesquise", "pesquisar", "procure", "buscar", "busque", "fontes", "referências",
    "artigos", "papers", "doi", "pubmed", "crossref", "openalex", "semantic scholar",
}
_COMPARE = {"compare", "comparar", "diferença", "diferenca", "versus", " vs ", "contraste"}
_VERIFY = {"verifique", "verificar", "é verdade", "e verdade", "confira", "procede", "falso"}
_HIGH_STAKES = {
    "diagnóstico", "diagnostico", "tratamento", "dose", "remédio", "medicamento",
    "jurídico", "juridico", "lei", "investimento", "financiamento", "imposto",
}
_LONG_FORM = {
    "detalhadamente", "profundo", "aprofundado", "completo", "relatório", "relatorio",
    "ensaio", "fichamento", "revisão de literatura", "revisao de literatura",
}
_REASONING = {
    "analise", "análise", "demonstre", "prove", "argumento", "premissa", "conclusão",
    "causa", "por que", "explique", "avalie", "critique", "objeção", "objecao",
}


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in value if not unicodedata.combining(c))


def _contains(text: str, terms: set[str]) -> bool:
    folded = _fold(text)
    return any(_fold(term) in folded for term in terms)


def _keywords(text: str, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[a-zA-ZÀ-ÿ][\wÀ-ÿ-]{2,}", text.lower())
    stop = {
        "para", "como", "isso", "essa", "esse", "uma", "uns", "das", "dos", "que",
        "com", "por", "mais", "sobre", "pode", "ser", "qual", "quais", "onde", "quando",
        "porque", "meu", "minha", "seus", "suas", "também", "então", "fazer", "faça",
    }
    counts = Counter(t for t in tokens if _fold(t) not in {_fold(s) for s in stop})
    return [term for term, _ in counts.most_common(limit)]


class QueryRouter:
    """Classificador local barato. Não consome cota e deixa decisões explicáveis."""

    def analyze(self, text: str, forced_mode: str | None = None) -> QueryAnalysis:
        raw = (text or "").strip()
        words = re.findall(r"\S+", raw)
        academic = _contains(raw, _ACADEMIC)
        current = _contains(raw, _CURRENT) or bool(re.search(r"\b20(?:2[5-9]|[3-9]\d)\b", raw))
        explicit_search = _contains(raw, _SEARCH)
        comparison = _contains(raw, _COMPARE)
        claim_check = _contains(raw, _VERIFY)
        high_stakes = _contains(raw, _HIGH_STAKES)
        long_form = len(words) > 90 or _contains(raw, _LONG_FORM)
        reasoning = _contains(raw, _REASONING)
        question_count = raw.count("?")

        if forced_mode and forced_mode != "auto":
            mode = forced_mode
        elif claim_check:
            mode = "academic" if academic else "search"
        elif academic:
            mode = "academic"
        elif current or explicit_search:
            mode = "search"
        elif reasoning and any(k in _fold(raw) for k in ("argument", "premiss", "falaci")):
            mode = "argument"
        else:
            mode = "chat"

        complexity = 0.08
        complexity += min(0.28, len(words) / 450)
        complexity += 0.18 if academic else 0
        complexity += 0.14 if reasoning else 0
        complexity += 0.12 if comparison else 0
        complexity += 0.14 if claim_check else 0
        complexity += 0.12 if long_form else 0
        complexity += min(0.08, question_count * 0.03)
        complexity = min(1.0, complexity)

        if explicit_search or current or claim_check:
            depth = Depth.RESEARCH
        elif complexity >= 0.62 or long_form:
            depth = Depth.DEEP
        elif complexity >= 0.28:
            depth = Depth.STANDARD
        else:
            depth = Depth.FAST

        needs_sources = academic or explicit_search or claim_check or high_stakes
        needs_search = explicit_search or current or claim_check or (academic and depth in {Depth.DEEP, Depth.RESEARCH})
        needs_audit = depth in {Depth.DEEP, Depth.RESEARCH} or high_stakes or claim_check

        reasons: list[str] = []
        for flag, label in (
            (academic, "domínio acadêmico"), (current, "informação temporal"),
            (explicit_search, "busca solicitada"), (comparison, "comparação"),
            (claim_check, "checagem de afirmação"), (high_stakes, "alto impacto"),
            (long_form, "resposta longa"), (reasoning, "raciocínio estruturado"),
        ):
            if flag:
                reasons.append(label)

        queries = self._plan_queries(raw, comparison=comparison, claim_check=claim_check)
        return QueryAnalysis(
            mode=mode,
            depth=depth,
            needs_search=needs_search,
            needs_memory=True,
            needs_audit=needs_audit,
            needs_sources=needs_sources,
            is_current=current,
            is_high_stakes=high_stakes,
            is_comparison=comparison,
            is_claim_check=claim_check,
            is_long_form=long_form,
            complexity=round(complexity, 3),
            confidence=0.88 if reasons else 0.72,
            reasons=reasons,
            search_queries=queries,
        )

    def _plan_queries(self, text: str, *, comparison: bool, claim_check: bool) -> list[str]:
        clean = re.sub(r"\s+", " ", text).strip()[:500]
        terms = _keywords(clean)
        queries = [clean]
        if terms:
            core = " ".join(terms[:6])
            queries.append(core)
            queries.append(core + " review evidence")
        if comparison:
            queries.append(clean + " comparison review")
        if claim_check:
            queries.append(clean + " evidence systematic review")
        output: list[str] = []
        seen: set[str] = set()
        for q in queries:
            key = _fold(q)
            if q and key not in seen:
                seen.add(key)
                output.append(q[:500])
        return output[:4]

    def decide(
        self,
        analysis: QueryAnalysis,
        *,
        has_gemini: bool,
        has_groq: bool,
        normal_max_tokens: int,
        academic_max_tokens: int,
        deep_max_tokens: int,
    ) -> RoutingDecision:
        academic = analysis.mode in {"academic", "argument", "study", "code"}
        if academic and has_groq:
            primary, fallback = "groq", "gemini" if has_gemini else None
        else:
            primary = "gemini" if has_gemini else "groq"
            fallback = "groq" if primary == "gemini" and has_groq else ("gemini" if has_gemini else None)

        if analysis.depth == Depth.FAST:
            max_tokens = min(normal_max_tokens, 900)
            thinking = "low"
        elif analysis.depth == Depth.STANDARD:
            max_tokens = academic_max_tokens if academic else normal_max_tokens
            thinking = "medium" if academic else "low"
        elif analysis.depth == Depth.DEEP:
            max_tokens = max(academic_max_tokens, min(deep_max_tokens, 2200))
            thinking = "high" if academic else "medium"
        else:
            max_tokens = max(academic_max_tokens, min(deep_max_tokens, 2600))
            thinking = "high"

        temperature = 0.25 if academic else (0.42 if analysis.needs_search else 0.68)
        audit_level = "full" if analysis.is_claim_check or analysis.is_high_stakes else ("standard" if analysis.needs_audit else "light")
        return RoutingDecision(
            primary_provider=primary,
            fallback_provider=fallback,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking_level=thinking,
            use_memory=analysis.needs_memory,
            use_research=analysis.needs_search,
            audit_level=audit_level,
            speculative_fallback_after=None,
        )
