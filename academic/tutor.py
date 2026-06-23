from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class TutorAssessment:
    level: str
    confidence: float
    reasons: list[str]


class AdaptiveTutor:
    LEVELS = {"iniciante", "intermediario", "avancado", "graduacao", "pos-graduacao"}

    def assess(self, text: str, profile: dict, history: list[dict[str, str]]) -> TutorAssessment:
        configured = str(profile.get("academic_level") or "auto").lower()
        if configured in self.LEVELS:
            return TutorAssessment(configured, 0.98, ["nível definido pelo usuário"])
        words = re.findall(r"[\wÀ-ÿ-]+", text)
        technical = re.findall(
            r"\b(?:epistemolog|ontolog|metodolog|inferência|inferencia|causal|axiom|teorem|"
            r"dialétic|dialetic|fenomenolog|hermenêut|estatíst|estatist|regress|bayes|correlaç|correlac)\w*",
            text,
            re.I,
        )
        prior = sum(len(item.get("content", "").split()) for item in history[-6:])
        score = min(1.0, len(words) / 180 + len(technical) * 0.12 + min(0.25, prior / 1800))
        if score < 0.18:
            level = "iniciante"
        elif score < 0.38:
            level = "intermediario"
        elif score < 0.62:
            level = "graduacao"
        elif score < 0.82:
            level = "avancado"
        else:
            level = "pos-graduacao"
        return TutorAssessment(level, 0.62, ["vocabulário", "complexidade da pergunta", "histórico recente"])

    def instruction(self, assessment: TutorAssessment) -> str:
        mapping = {
            "iniciante": "Comece pela intuição, defina cada termo e use um exemplo concreto antes da formalização.",
            "intermediario": "Conecte conceitos básicos, mostre o raciocínio e inclua um exercício curto de aplicação.",
            "graduacao": "Use precisão conceitual de graduação, apresente método, objeções e uma questão de revisão.",
            "avancado": "Assuma domínio básico; discuta nuances, pressupostos, literatura e limites metodológicos.",
            "pos-graduacao": "Trate o tema em nível de pós-graduação, com controvérsias, metodologia, lacunas e agenda de pesquisa.",
        }
        return (
            f"TUTOR ADAPTATIVO: nível estimado={assessment.level}, confiança={assessment.confidence:.0%}. "
            + mapping.get(assessment.level, mapping["intermediario"])
            + " Não infantilize e permita que a resposta do usuário reveja essa estimativa."
        )
