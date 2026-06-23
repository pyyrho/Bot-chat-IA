from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Awaitable, Callable


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class LocalHashEmbedder:
    """Embedding local leve por feature hashing. Não exige modelo nem biblioteca externa."""

    name = "local-hash-v1"

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = max(128, dimensions)

    async def embed(self, text: str, *, task: str = "document") -> list[float]:
        folded = unicodedata.normalize("NFKD", (text or "").lower())
        folded = "".join(c for c in folded if not unicodedata.combining(c))
        tokens = re.findall(r"[a-z0-9]{2,}", folded)
        vector = [0.0] * self.dimensions
        features: list[tuple[str, float]] = [(f"w:{token}", 1.0) for token in tokens]
        for first, second in zip(tokens, tokens[1:]):
            features.append((f"b:{first}_{second}", 0.65))
        compact = " ".join(tokens)
        for index in range(max(0, len(compact) - 3)):
            gram = compact[index:index + 4]
            if " " not in gram:
                features.append((f"c:{gram}", 0.12))
        for feature, weight in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            slot = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[slot] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector))
        return [round(value / norm, 8) for value in vector] if norm else vector


class HybridEmbedder:
    def __init__(
        self,
        remote: Callable[[str, str], Awaitable[list[float] | None]] | None = None,
        *,
        remote_name: str = "gemini-embedding-001",
        remote_enabled: bool = False,
    ) -> None:
        self.local = LocalHashEmbedder()
        self.remote = remote
        self.remote_name = remote_name
        self.remote_enabled = remote_enabled and remote is not None

    async def embed(self, text: str, *, task: str) -> tuple[list[float], str]:
        if self.remote_enabled and self.remote:
            try:
                result = await self.remote(text, task)
                if result:
                    return result, self.remote_name
            except Exception:
                pass
        return await self.local.embed(text, task=task), self.local.name
