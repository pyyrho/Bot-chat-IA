from __future__ import annotations

import html
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from typing import Any


def clean_text(value: Any, limit: int | None = None) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if limit else text


def normalize_doi(value: str | None) -> str:
    doi = clean_text(value).lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi.rstrip(".,; )]")


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKD", clean_text(value).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def token_set(value: str) -> set[str]:
    return {token for token in normalize_title(value).split() if len(token) > 1}


def title_similarity(a: str, b: str) -> float:
    aa, bb = token_set(a), token_set(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


def reconstruct_openalex_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, offsets in index.items():
        for offset in offsets or []:
            positions.append((int(offset), word))
    return clean_text(" ".join(word for _, word in sorted(positions)))


def query_terms(query: str) -> Counter[str]:
    terms = [t for t in normalize_title(query).split() if len(t) > 2]
    stop = {
        "como", "qual", "quais", "para", "sobre", "uma", "das", "dos", "que",
        "com", "por", "mais", "estudo", "pesquisa", "explique", "explica", "fale",
        "alternativa", "alternativas", "tentarem", "resolver", "resolucionarem",
        "review", "evidence", "analysis", "approaches", "methods", "what", "how",
        "the", "for", "and", "with",
    }
    return Counter(t for t in terms if t not in stop)


def relevance_score(
    query: str,
    title: str,
    abstract: str,
    *,
    year: int | None = None,
    citations: int | None = None,
    source_weight: float = 0.0,
    is_retracted: bool = False,
    prefer_recent: bool = False,
) -> float:
    terms = query_terms(query)
    title_terms = token_set(title)
    abstract_terms = token_set(abstract)
    lexical = sum(3.0 * weight for term, weight in terms.items() if term in title_terms)
    lexical += sum(0.7 * weight for term, weight in terms.items() if term in abstract_terms)
    normalized_query = " ".join(terms)
    normalized_title = normalize_title(title)
    phrase_bonus = 0.0
    if len(normalized_query) >= 8 and normalized_query in normalized_title:
        phrase_bonus = 3.0
    recency = 0.0
    if year and prefer_recent:
        current_year = datetime.now(timezone.utc).year
        age = max(0, current_year - year)
        recency = max(-0.8, 1.4 - age * 0.12)
    citation = math.log1p(max(0, citations or 0)) * 0.22
    penalty = 8.0 if is_retracted else 0.0
    return round(lexical + phrase_bonus + recency + citation + source_weight - penalty, 4)
