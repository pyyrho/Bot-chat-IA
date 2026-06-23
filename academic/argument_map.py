from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class ArgumentUnit:
    text: str
    role: str
    marker: str = ""


@dataclass(slots=True)
class ArgumentMap:
    premises: list[ArgumentUnit] = field(default_factory=list)
    conclusions: list[ArgumentUnit] = field(default_factory=list)
    objections: list[ArgumentUnit] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def compact(self, max_chars: int = 2600) -> str:
        lines = ["MAPA ARGUMENTATIVO LOCAL (heurístico; confirme na análise):"]
        for index, unit in enumerate(self.premises, 1):
            lines.append(f"P{index}. {unit.text}")
        for index, unit in enumerate(self.conclusions, 1):
            lines.append(f"C{index}. {unit.text}")
        for index, unit in enumerate(self.objections, 1):
            lines.append(f"O{index}. {unit.text}")
        if self.assumptions:
            lines.append("Pressupostos candidatos: " + "; ".join(self.assumptions[:5]))
        if self.warnings:
            lines.append("Alertas: " + "; ".join(self.warnings[:4]))
        return "\n".join(lines)[:max_chars]


_PREMISE = re.compile(r"\b(porque|pois|dado que|visto que|uma vez que|considerando que|since|because|given that)\b", re.I)
_CONCLUSION = re.compile(r"\b(portanto|logo|assim|consequentemente|daí|então|conclui-se|therefore|thus|hence)\b", re.I)
_OBJECTION = re.compile(r"\b(porém|contudo|entretanto|mas|todavia|objeção|contra|however|but|nevertheless)\b", re.I)
_QUANTIFIER = re.compile(r"\b(todo|todos|sempre|nunca|nenhum|necessariamente|only|all|always|never|must)\b", re.I)


def map_argument(text: str, *, limit: int = 18) -> ArgumentMap:
    value = re.sub(r"\r\n?", "\n", text or "").strip()
    parts = [
        re.sub(r"\s+", " ", part).strip(" -*•\t")
        for part in re.split(r"(?<=[.!?;:])\s+|\n+", value)
    ]
    parts = [part for part in parts if 3 <= len(part.split()) <= 90][:limit]
    result = ArgumentMap()

    for part in parts:
        if _CONCLUSION.search(part):
            result.conclusions.append(ArgumentUnit(part, "conclusion", _CONCLUSION.search(part).group(0)))
        elif _OBJECTION.search(part):
            result.objections.append(ArgumentUnit(part, "objection", _OBJECTION.search(part).group(0)))
        elif _PREMISE.search(part):
            result.premises.append(ArgumentUnit(part, "premise", _PREMISE.search(part).group(0)))
        else:
            # Em argumentos curtos, sentenças anteriores à conclusão costumam ser
            # premissas candidatas; sem conclusão explícita, não fingimos certeza.
            result.premises.append(ArgumentUnit(part, "candidate-premise"))

        if _QUANTIFIER.search(part):
            result.assumptions.append(f"quantificador forte em: {part[:180]}")

    if not result.conclusions:
        result.warnings.append("nenhum marcador explícito de conclusão")
    if len(result.premises) < 2:
        result.warnings.append("poucas premissas identificáveis")
    if result.objections and not result.conclusions:
        result.warnings.append("há contraste, mas a tese central está implícita")
    return result
