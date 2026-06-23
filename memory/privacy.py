from __future__ import annotations

import re

_SECRET_PATTERNS = [
    re.compile(r"\b(?:sk-|AIza|gsk_)[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\b(?:senha|password|token|api[_ -]?key)\s*[:=]\s*\S+", re.I),
    re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
]
_SENSITIVE = re.compile(
    r"\b(diagnóstico|diagnostico|doença|doenca|transtorno|religião|religiao|partido político|partido politico|"
    r"orientação sexual|orientacao sexual|vida sexual|antecedente criminal|endereço completo|endereco completo)\b",
    re.I,
)


def safe_to_store(text: str, *, explicit: bool = False) -> bool:
    value = (text or "").strip()
    if not value or len(value) > 900:
        return False
    if any(pattern.search(value) for pattern in _SECRET_PATTERNS):
        return False
    if _SENSITIVE.search(value) and not explicit:
        return False
    return True


def redact(text: str) -> str:
    value = text
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("[DADO REMOVIDO]", value)
    return value
