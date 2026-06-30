"""
mention_gate.py — Revolux · Coordenação entre utility.py e ai_chat.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

discord.Message usa __slots__, então não é possível anexar atributos
arbitrários a uma instância (message.__dict__ não existe). Para evitar
que o ai_chat responda a uma menção que o utility.py já tratou (modo
"Jarvis"), usamos aqui um registro em memória por ID de mensagem com
expiração automática.

Uso:
    from utils.mention_gate import mark_handled, was_handled

    # No cog que trata a intenção primeiro:
    mark_handled(message.id)

    # No cog que decide se deve responder com IA:
    if was_handled(message.id):
        return
"""

from __future__ import annotations

import time

_TTL_SECONDS = 30.0
_handled: dict[int, float] = {}


def _purge_expired(now: float) -> None:
    expired = [mid for mid, expires_at in _handled.items() if expires_at < now]
    for mid in expired:
        _handled.pop(mid, None)


def mark_handled(message_id: int) -> None:
    """Marca uma mensagem como já tratada por um cog de intenção."""
    now = time.monotonic()
    _purge_expired(now)
    _handled[message_id] = now + _TTL_SECONDS


def was_handled(message_id: int) -> bool:
    """Verifica se uma mensagem já foi tratada por outro cog."""
    now = time.monotonic()
    _purge_expired(now)
    return message_id in _handled
