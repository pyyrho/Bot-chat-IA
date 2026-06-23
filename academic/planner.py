from __future__ import annotations

import re
import unicodedata

from ai_core.models import QueryAnalysis
from .models import SearchPlan


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in value if not unicodedata.combining(char))


_DOMAIN_MARKERS = {
    "biomedical": {
        "medicina", "saude", "doenca", "tratamento", "clinico", "clinica", "paciente",
        "farmaco", "medicamento", "terapia", "epidemiologia", "neurociencia", "biomedicina",
        "cancer", "virus", "bacteria", "genetica", "diagnostico",
    },
    "computing": {
        "computacao", "algoritmo", "software", "machine learning", "inteligencia artificial",
        "programacao", "rede neural", "llm", "linguagem natural", "banco de dados",
    },
    "formal": {
        "matematica", "teorema", "demonstracao", "logica", "algebra", "calculo", "geometria",
        "estatistica", "probabilidade", "fisica", "quantica",
    },
    "humanities": {
        "filosofia", "epistemologia", "ontologia", "metafisica", "etica", "historia",
        "sociologia", "antropologia", "hermeneutica", "fenomenologia", "kant", "platao",
        "aristoteles", "hegel", "nietzsche", "foucault",
    },
}


class AcademicQueryPlanner:
    def build(self, question: str, analysis: QueryAnalysis) -> SearchPlan:
        queries = list(analysis.search_queries or [question])
        folded = _fold(question)
        domains = [
            domain
            for domain, markers in _DOMAIN_MARKERS.items()
            if any(marker in folded for marker in markers)
        ]
        if "humanities" in domains:
            queries.append(question + " philosophy encyclopedia scholarship")
        if "biomedical" in domains:
            queries.append(question + " systematic review clinical evidence")
        if "computing" in domains:
            queries.append(question + " survey benchmark evaluation")
        if "formal" in domains:
            queries.append(question + " theorem proof formal analysis")
        if analysis.is_comparison:
            queries.append(question + " comparative analysis")
        if analysis.is_claim_check:
            queries.append(question + " replication evidence")

        clean: list[str] = []
        seen: set[str] = set()
        for query in queries:
            value = re.sub(r"\s+", " ", query).strip()[:500]
            key = _fold(value)
            if value and key not in seen:
                seen.add(key)
                clean.append(value)
        max_results = 18 if analysis.depth.value in {"deep", "research"} else 10
        return SearchPlan(
            original_query=question,
            queries=clean[:5],
            depth=analysis.depth.value,
            max_results=max_results,
            recent_only=analysis.is_current,
            include_reviews=True,
            domains=domains or ["general"],
        )
