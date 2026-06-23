from __future__ import annotations

import math
import re
import time
import unicodedata
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in value if not unicodedata.combining(c))


def feature_vector(text: str) -> Counter[str]:
    folded = _fold(text)
    words = re.findall(r"[a-z0-9]{2,}", folded)
    features: Counter[str] = Counter(words)
    compact = " ".join(words)
    for index in range(max(0, len(compact) - 2)):
        tri = compact[index:index + 3]
        if " " not in tri:
            features[f"#c:{tri}"] += 0.18
    return features


def cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


_STOP = {"quem", "foi", "era", "pode", "explicar", "explique", "sobre", "como", "qual", "que", "uma", "um", "de", "do", "da", "o", "a"}


def semantic_similarity(text_a: str, vec_a: Counter[str], text_b: str, vec_b: Counter[str]) -> float:
    base = cosine(vec_a, vec_b)
    terms_a = {t for t in re.findall(r"[a-z0-9]{3,}", _fold(text_a)) if t not in _STOP}
    terms_b = {t for t in re.findall(r"[a-z0-9]{3,}", _fold(text_b)) if t not in _STOP}
    overlap = len(terms_a & terms_b) / max(1, min(len(terms_a), len(terms_b))) if terms_a and terms_b else 0.0
    entity_bonus = 0.12 if any(len(term) >= 7 for term in terms_a & terms_b) else 0.0
    return min(1.0, base * 0.72 + overlap * 0.28 + entity_bonus)


@dataclass(slots=True)
class CacheEntry(Generic[T]):
    key: str
    vector: Counter[str]
    value: T
    expires_at: float
    namespace: str


class SemanticTTLCache(Generic[T]):
    def __init__(self, max_size: int = 256, threshold: float = 0.90) -> None:
        self.max_size = max(8, max_size)
        self.threshold = threshold
        self._items: OrderedDict[str, CacheEntry[T]] = OrderedDict()

    def set(self, key: str, value: T, *, ttl: int, namespace: str = "default") -> None:
        cache_key = f"{namespace}:{_fold(key)[:500]}"
        self._items[cache_key] = CacheEntry(key, feature_vector(key), value, time.monotonic() + ttl, namespace)
        self._items.move_to_end(cache_key)
        self._purge()
        while len(self._items) > self.max_size:
            self._items.popitem(last=False)

    def get(self, key: str, *, namespace: str = "default", threshold: float | None = None) -> tuple[T | None, float]:
        self._purge()
        target = feature_vector(key)
        best: CacheEntry[T] | None = None
        best_score = 0.0
        for entry in self._items.values():
            if entry.namespace != namespace:
                continue
            score = semantic_similarity(key, target, entry.key, entry.vector)
            if score > best_score:
                best, best_score = entry, score
        required = self.threshold if threshold is None else threshold
        if best is not None and best_score >= required:
            return best.value, best_score
        return None, best_score

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        self._purge()
        return len(self._items)

    def _purge(self) -> None:
        now = time.monotonic()
        for key in list(self._items):
            if self._items[key].expires_at <= now:
                self._items.pop(key, None)
