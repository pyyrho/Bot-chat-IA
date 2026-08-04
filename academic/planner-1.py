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
        "estatistica", "probabilidade", "fisica", "quantica", "conjectura", "axioma", "lema",
        "corolario", "topologia", "poincare", "denef", "lipshitz", "teoria dos numeros",
    },
    "humanities": {
        "filosofia", "epistemologia", "ontologia", "metafisica", "etica", "historia",
        "sociologia", "antropologia", "hermeneutica", "fenomenologia", "kant", "platao",
        "aristoteles", "hegel", "nietzsche", "foucault",
    },
}


_QUERY_NOISE = {
    "a", "ao", "aos", "as", "da", "das", "de", "do", "dos", "e", "ela",
    "ele", "em", "essa", "esse", "esta", "este", "isso", "na", "nas", "no",
    "nos", "o", "os", "ou", "para", "por", "que", "se", "sobre", "um", "uma",
    "como", "qual", "quais", "quando", "onde", "porque", "sao", "seria", "seriam",
    "explique", "explica", "explicar", "fale", "resuma", "defina", "conceitue",
    "tentar", "tentarem", "resolver", "resolverem", "resolucionar", "resolucionarem",
    "possivel", "possiveis", "existem", "existe", "alternativa", "alternativas",
}


def _scholarly_core(question: str) -> str:
    """Remove a moldura conversacional e preserva os termos pesquisáveis."""
    tokens = re.findall(r"[A-Za-zÀ-ÿ0-9][\wÀ-ÿ-]*", question)
    kept = [token for token in tokens if _fold(token) not in _QUERY_NOISE]
    # Consultas com um único termo genérico perdem precisão; nesse caso a frase
    # original é mais segura para os conectores.
    if len(kept) < 2:
        return re.sub(r"\s+", " ", question).strip()[:500]
    return " ".join(kept)[:500]


def _intent_suffix(question: str) -> str:
    folded = _fold(question)
    if any(marker in folded for marker in ("alternativ", "abordag", "estrateg", "soluc")):
        return " approaches methods"
    if any(marker in folded for marker in ("critic", "objec", "limitac", "problema")):
        return " criticism limitations"
    if any(marker in folded for marker in ("compar", "diferenc", "versus")):
        return " comparative analysis"
    return ""


class AcademicQueryPlanner:
    def build(self, question: str, analysis: QueryAnalysis) -> SearchPlan:
        core = _scholarly_core(question)
        queries = [core]
        queries.extend(analysis.search_queries or [question])
        folded = _fold(question)
        domains = [
            domain
            for domain, markers in _DOMAIN_MARKERS.items()
            if any(marker in folded for marker in markers)
        ]
        intent_suffix = _intent_suffix(question)
        if intent_suffix:
            queries.append(core + intent_suffix)
        if "humanities" in domains:
            queries.append(core + " philosophy encyclopedia scholarship")
        if "biomedical" in domains:
            queries.append(core + " systematic review clinical evidence")
        if "computing" in domains:
            queries.append(core + " survey benchmark evaluation")
        if "formal" in domains:
            queries.append(core + " theorem proof formal analysis")
        if analysis.is_comparison:
            queries.append(core + " comparative analysis")
        if analysis.is_claim_check:
            queries.append(core + " replication evidence")

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
