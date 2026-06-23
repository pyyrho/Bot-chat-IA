from __future__ import annotations

import re

from .models import MemoryCandidate
from .privacy import safe_to_store

_EXPLICIT = re.compile(r"\b(?:lembre(?:-se)?|memorize|guarde|anote|não esqueça|nao esqueca)\s+(?:que\s+)?(.+)", re.I)
_PREFERENCE = re.compile(r"\b(?:eu prefiro|prefiro|gosto mais de|responda sempre|quero que você|quero que voce)\s+(.+)", re.I)
_PROJECT = re.compile(r"\b(?:meu projeto|estou criando|estou desenvolvendo|estou trabalhando em)\s+(.+)", re.I)
_DECISION = re.compile(r"\b(?:decidimos|decidi|ficou definido|vamos usar|escolhi)\s+(.+)", re.I)


class MemoryExtractor:
    def extract(self, user_text: str) -> list[MemoryCandidate]:
        text = re.sub(r"\s+", " ", user_text or "").strip()
        candidates: list[MemoryCandidate] = []
        for regex, kind, importance, ttl in (
            (_EXPLICIT, "explicit", 0.95, None),
            (_PREFERENCE, "preference", 0.84, None),
            (_PROJECT, "project", 0.78, 365),
            (_DECISION, "decision", 0.80, 365),
        ):
            match = regex.search(text)
            if not match:
                continue
            content = match.group(1).strip(" .")[:700]
            explicit = kind == "explicit"
            if safe_to_store(content, explicit=explicit):
                candidates.append(MemoryCandidate(kind, content, importance, 0.92 if explicit else 0.82, ttl))
        return candidates[:3]

    def summarize_session(self, history: list[dict[str, str]], max_chars: int = 1200) -> str:
        pieces: list[str] = []
        for item in history[-12:]:
            role = item.get("role", "user")
            content = re.sub(r"\s+", " ", item.get("content", "")).strip()
            if not content:
                continue
            sentences = re.split(r"(?<=[.!?])\s+", content)
            selected = next((s for s in sentences if 5 <= len(s.split()) <= 35), sentences[0])
            prefix = "Usuário" if role == "user" else "Assistente"
            pieces.append(f"{prefix}: {selected[:240]}")
        summary = " | ".join(pieces)
        return summary[:max_chars]
