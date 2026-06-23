from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class MemoryItem:
    id: int | None
    user_id: int
    guild_id: int | None
    kind: str
    content: str
    importance: float = 0.5
    confidence: float = 0.8
    source: str = "conversation"
    embedding: list[float] = field(default_factory=list)
    embedding_model: str = "local-hash-v1"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    use_count: int = 0
    score: float = 0.0


@dataclass(slots=True)
class MemoryCandidate:
    kind: str
    content: str
    importance: float
    confidence: float
    ttl_days: int | None = None


@dataclass(slots=True)
class MemoryContext:
    items: list[MemoryItem]
    session_summary: str = ""

    def as_prompt(self, max_chars: int = 2400) -> str:
        lines: list[str] = []
        if self.session_summary:
            lines.append("Resumo de contexto anterior: " + self.session_summary)
        for item in self.items:
            lines.append(f"- [{item.kind}; confiança {item.confidence:.0%}] {item.content}")
        text = "\n".join(lines)
        return text[:max_chars]
