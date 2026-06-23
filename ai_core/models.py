from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Depth(str, Enum):
    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"
    RESEARCH = "research"


@dataclass(slots=True)
class QueryAnalysis:
    mode: str
    depth: Depth
    needs_search: bool = False
    needs_memory: bool = True
    needs_audit: bool = False
    needs_sources: bool = False
    is_current: bool = False
    is_high_stakes: bool = False
    is_comparison: bool = False
    is_claim_check: bool = False
    is_long_form: bool = False
    complexity: float = 0.0
    confidence: float = 1.0
    reasons: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RoutingDecision:
    primary_provider: str
    fallback_provider: str | None
    max_tokens: int
    temperature: float
    thinking_level: str
    use_memory: bool
    use_research: bool
    audit_level: str
    speculative_fallback_after: float | None = None


@dataclass(slots=True)
class AuditIssue:
    code: str
    severity: str
    message: str
    span: str = ""


@dataclass(slots=True)
class AuditReport:
    score: float
    issues: list[AuditIssue] = field(default_factory=list)
    repaired_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def should_warn(self) -> bool:
        return any(issue.severity in {"high", "critical"} for issue in self.issues)
