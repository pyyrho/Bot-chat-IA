from __future__ import annotations

import re
import json
import unicodedata
from collections import Counter
from dataclasses import replace

from .models import Depth, QueryAnalysis, RoutingDecision
from academic.domains import topic_domains

_ACADEMIC = {
    "artigo", "paper", "estudo", "pesquisa", "tese", "dissertação", "metodologia",
    "evidência", "evidencias", "fonte", "fontes", "científico", "cientifica",
    "filosofia", "epistemologia", "ontologia", "metafísica", "ética", "lógica",
    "sociologia", "psicologia", "história", "economia", "física", "química",
    "biologia", "medicina", "neurociência", "matemática", "teorema", "prova",
    "conjectura", "álgebra", "calculo", "cálculo", "geometria", "topologia",
    "integral", "derivada", "poincaré", "poincare", "denef", "lipshitz",
    "programação", "programacao", "programar", "algoritmo", "estrutura de dados",
    "compilador", "computação", "computacao", "software", "código", "codigo",
    "banco de dados", "framework", "desenvolvimento web", "api rest", "rest api",
    "discord.py", "sql", "python", "javascript", "typescript", "java", "kotlin",
    "golang", "haskell", "prolog", "turing", "church", "aristóteles",
    "aristoteles", "platão", "platao", "sócrates", "socrates", "kant", "hegel",
    "nietzsche", "descartes", "hume", "locke", "rousseau", "marx", "leibniz",
    "spinoza", "frege", "gödel",
    "godel", "hilbert", "russell", "wittgenstein", "quine", "kripke", "euclides",
    "euler", "gauss", "riemann", "cantor", "noether",
}
_CURRENT = {
    "hoje", "agora", "atual", "atuais", "recente", "recentes", "último", "ultima",
    "2025", "2026", "notícia", "noticias", "preço", "cotação", "presidente atual",
    "versão atual", "lançamento", "resultado", "placar", "ranking",
}
_SEARCH = {
    "pesquise", "pesquisar", "procure", "buscar", "busque", "fontes", "referências",
    "artigos", "papers", "doi", "pubmed", "crossref", "openalex", "semantic scholar",
}
_COMPARE = {"compare", "comparar", "diferença", "diferenca", "versus", " vs ", "contraste"}
_VERIFY = {"verifique", "verificar", "é verdade", "e verdade", "confira", "procede", "falso"}
_HIGH_STAKES = {
    "diagnóstico", "diagnostico", "tratamento", "dose", "remédio", "medicamento",
    "jurídico", "juridico", "lei", "investimento", "financiamento", "imposto",
}
_LONG_FORM = {
    "detalhadamente", "profundo", "aprofundado", "completo", "relatório", "relatorio",
    "ensaio", "fichamento", "revisão de literatura", "revisao de literatura",
}
_REASONING = {
    "analise", "análise", "demonstre", "prove", "argumento", "premissa", "conclusão",
    "causa", "por que", "explique", "avalie", "critique", "objeção", "objecao",
}


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in value if not unicodedata.combining(c))


def _contains(text: str, terms: set[str]) -> bool:
    folded = _fold(text)
    return any(re.search(r"(?<!\w)" + re.escape(_fold(term).strip()) + r"(?!\w)", folded) for term in terms)


def conversational_intent(text: str) -> str:
    """Triagem conservadora: brincadeira não pode suprimir um pedido substantivo."""
    value = _fold(text).strip()
    if len(value.split()) > 45 or re.search(r'https?://|```', value):
        return 'question'
    substantive = _contains(value, _SEARCH | _VERIFY | _REASONING | _COMPARE | _LONG_FORM | _HIGH_STAKES)
    substantive = substantive or bool(re.search(
        r'\b(?:o que|oq|qual|quais|quem|como|quando|onde|quanto|defina|significa|diferenca|resuma|calcule|resolva|demonstre|cite)\b', value))
    if substantive:
        return 'question'
    if re.search(r'\b(?:bait|baitando|larp|larpando|troll|viajou|viajando|burro|burra|doida|doido)\b|k{3,}|(?:ha){3,}|😂|🤣', value):
        return 'banter'
    if re.fullmatch(r'(?:oi|ola|opa|e ai|bom dia|boa tarde|boa noite|obrigad[oa]|valeu|vlw|blz|beleza|ok|sim|nao|entendi)[!.? ]*', value):
        return 'social'
    if re.fullmatch(r'(?:continua|continue|e isso|por que|pq|e ai|explica melhor)[?.! ]*', value):
        return 'continuation'
    return 'question'


def research_declined(text: str) -> bool:
    return bool(re.search(
        r'\b(?:nao|sem)\s+(?:(?:precisa|preciso|quero|precisar|precisamos|faca)\s+(?:de\s+)?)?(?:pesquis\w*|busc\w*|fontes?|links?|consult\w*)', _fold(text)))


def research_requested(text: str) -> bool:
    """Intenção de consultar fontes; citar a palavra 'fonte' não basta."""
    value = _fold(text)
    if research_declined(text):
        return False
    return bool(re.search(
        r'\b(?:pesquise|pesquisa sobre|pesquisar|busque|procure|verifique|cheque|cite|consulte)\b'
        r'|^pesquisa (?:isso|esse|essa|ai|pra|para|se|como)\b'
        r'|\b(?:quero|mande|mostre|traga|com|tem|quais|qual a|qual e a)\s+(?:as?\s+)?(?:fontes?|referencias?|artigos?|evidencias?)\b', value))


def _context_text(serialized: str | None) -> str:
    texts = []
    for line in (serialized or '').splitlines():
        try:
            record = json.loads(line)
            if isinstance(record, dict) and isinstance(record.get('content'), str):
                texts.append(record['content'])
        except (ValueError, TypeError):
            texts.append(line)
    return ' '.join(texts[-4:])[-2400:]


def conversational_frame(text: str, history=None, reply_context=None, conversation_context=None) -> str:
    """O pedido atual e o reply determinam o enquadramento, antes das palavras técnicas."""
    value = _fold(text)
    if re.search(r'\b(?:na vida real|no mundo real|na realidade|fisicamente possivel|cientificamente|segundo a fisica|fisica real|ciencia real)\b', value):
        return 'real'
    fiction = re.compile(r'\b(?:ficcao|fictici[oa]s?|serie|series|filme|filmes|anime|manga|personage(?:m|ns)|enredo|roteiro|canon|canone|lore|temporada|episodio|superpoder\w*|super.heroi\w*|vilao|marvel|dragon ball|doctor who|rick and morty)\b')
    if re.search(r'\bseri(?:e|es) (?:de (?:fourier|taylor|potencias?)|temporais|numericas?|historicas?)\b', value):
        return 'real'
    if fiction.search(value):
        return 'fiction'
    # Uma pergunta nova e explicitamente real não herda o tema da conversa anterior.
    concrete_question = bool(re.search(r'^(?:explique|defina|calcule|resolva|prove|demonstre|pesquise|qual|como|por que|o que)\b', value))
    if concrete_question and (topic_domains(value) or _contains(value, _ACADEMIC)) and not re.search(r'\b(?:isso|disso|nesse|nessa|desse|dessa|dele|dela|essa|esse)\b', value):
        return 'real' if re.search(r'^(?:explique|defina|calcule|resolva|prove|demonstre|pesquise)\b', value) else 'technical'
    reference = _context_text(reply_context)
    if not reference:
        reference = next((str(item.get('content', '')) for item in reversed(history or []) if item.get('role') == 'user'), '')
    if not reference:
        reference = _context_text(conversation_context)
    if fiction.search(_fold(reference)):
        return 'fiction'
    return 'unspecified'


def contextual_query(text: str, history: list[dict[str, str]], reply_context: str | None = None) -> str:
    """Resolve seguimentos curtos, sem reabrir o tema anterior em uma nova pergunta."""
    value = _fold(text).strip()
    if conversational_intent(text) in {'banter', 'social'} or len(value.split()) > 18:
        return text
    followup = bool(re.search(r'^(?:e (?:nas?|nos?|quanto|sobre|se)|como assim|por que|pq)\b|\b(?:isso|disso|nesse|nessa|desse|dessa|dele|dela)\b', value))
    if not followup:
        return text
    base = ''
    for line in (reply_context or '').splitlines():
        try:
            item = json.loads(line)
            if isinstance(item, dict) and item.get('content'):
                base = str(item['content'])[:800]
        except (ValueError, TypeError):
            continue
    if not base:
        base = next((item['content'][:800] for item in reversed(history) if item.get('role') == 'user' and item.get('content')), '')
    return f'{base} — Pergunta de continuidade: {text}' if base else text


def _keywords(text: str, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[a-zA-ZÀ-ÿ][\wÀ-ÿ-]{2,}", text.lower())
    stop = {
        "para", "como", "isso", "essa", "esse", "uma", "uns", "das", "dos", "que",
        "com", "por", "mais", "sobre", "pode", "ser", "qual", "quais", "onde", "quando",
        "porque", "meu", "minha", "seus", "suas", "também", "então", "fazer", "faça",
    }
    counts = Counter(t for t in tokens if _fold(t) not in {_fold(s) for s in stop})
    return [term for term, _ in counts.most_common(limit)]


class QueryRouter:
    """Classificador local barato. Não consome cota e deixa decisões explicáveis."""

    def analyze(self, text: str, forced_mode: str | None = None) -> QueryAnalysis:
        raw = (text or "").strip()
        words = re.findall(r"\S+", raw)
        intent = conversational_intent(raw)
        if intent in {'banter', 'social'} and forced_mode not in {'search', 'code'}:
            return QueryAnalysis(
                mode='chat', depth=Depth.FAST, needs_memory=False,
                conversation_intent=intent, response_style='casual_short',
                reasons=['interação social curta; sem pedido de pesquisa'],
            )
        forced_academic = forced_mode in {"academic", "argument", "study", "code"}
        academic = _contains(raw, _ACADEMIC) or bool(topic_domains(raw)) or forced_academic
        current = _contains(raw, _CURRENT) or bool(re.search(r"\b20(?:2[5-9]|[3-9]\d)\b", raw))
        explicit_search = _contains(raw, _SEARCH)
        comparison = _contains(raw, _COMPARE)
        claim_check = _contains(raw, _VERIFY)
        high_stakes = _contains(raw, _HIGH_STAKES)
        long_form = len(words) > 90 or _contains(raw, _LONG_FORM)
        reasoning = _contains(raw, _REASONING)
        question_count = raw.count("?")

        if forced_mode and forced_mode != "auto":
            mode = forced_mode
        elif claim_check:
            mode = "academic" if academic else "search"
        elif academic:
            mode = "academic"
        elif current or explicit_search:
            mode = "search"
        elif reasoning and any(k in _fold(raw) for k in ("argument", "premiss", "falaci")):
            mode = "argument"
        else:
            mode = "chat"

        complexity = 0.08
        complexity += min(0.28, len(words) / 450)
        complexity += 0.18 if academic else 0
        complexity += 0.14 if reasoning else 0
        complexity += 0.12 if comparison else 0
        complexity += 0.14 if claim_check else 0
        complexity += 0.12 if long_form else 0
        complexity += min(0.08, question_count * 0.03)
        complexity = min(1.0, complexity)

        if explicit_search or current or claim_check:
            depth = Depth.RESEARCH
        elif complexity >= 0.62 or long_form:
            depth = Depth.DEEP
        elif complexity >= 0.28:
            depth = Depth.STANDARD
        else:
            depth = Depth.FAST

        needs_sources = academic or explicit_search or claim_check or high_stakes
        needs_search = (
            explicit_search
            or current
            or claim_check
            or forced_academic
            or (academic and depth in {Depth.DEEP, Depth.RESEARCH})
        )
        needs_audit = depth in {Depth.DEEP, Depth.RESEARCH} or high_stakes or claim_check

        reasons: list[str] = []
        for flag, label in (
            (academic, "domínio acadêmico"),
            (forced_academic, "modo acadêmico solicitado"),
            (current, "informação temporal"),
            (explicit_search, "busca solicitada"), (comparison, "comparação"),
            (claim_check, "checagem de afirmação"), (high_stakes, "alto impacto"),
            (long_form, "resposta longa"), (reasoning, "raciocínio estruturado"),
        ):
            if flag:
                reasons.append(label)

        queries = self._plan_queries(raw, comparison=comparison, claim_check=claim_check)
        return QueryAnalysis(
            mode=mode,
            depth=depth,
            conversation_intent=intent,
            needs_search=needs_search,
            needs_memory=True,
            needs_audit=needs_audit,
            needs_sources=needs_sources,
            is_current=current,
            is_high_stakes=high_stakes,
            is_comparison=comparison,
            is_claim_check=claim_check,
            is_long_form=long_form,
            complexity=round(complexity, 3),
            confidence=0.88 if reasons else 0.72,
            reasons=reasons,
            search_queries=queries,
        )

    def analyze_conversation(self, text: str, forced_mode: str | None = None, *, history=None,
                             reply_context=None, conversation_context=None) -> QueryAnalysis:
        analysis = self.analyze(text, forced_mode)
        frame = conversational_frame(text, history, reply_context, conversation_context)
        wants_research = research_requested(text) or forced_mode == 'search'
        if research_declined(text):
            return replace(analysis, mode='chat', conversation_intent='conversation', needs_sources=False, needs_search=False, needs_audit=False)
        if forced_mode == 'code':
            return analysis
        if frame == 'fiction':
            # Fontes de uma obra são pesquisadas na web; não em bases de física.
            release_question = bool(re.search(r'\b(?:data (?:de|da) estreia|data (?:de|do) lancamento|quando (?:estreia|lanca|sai|vai sair|chega))\b', _fold(text)))
            return replace(analysis, mode='search' if wants_research or release_question else 'chat',
                conversation_intent='fiction', needs_sources=wants_research or release_question,
                needs_search=wants_research or release_question, needs_audit=False,
                is_high_stakes=False, is_claim_check=False, is_current=release_question,
                depth=Depth.RESEARCH if wants_research or release_question else Depth.STANDARD,
                reasons=['conversa sobre uma obra; preservar regras e acontecimentos da ficcao'])
        if analysis.conversation_intent in {'banter', 'social'}:
            return analysis
        value = _fold(text)
        joking = bool(re.search(r'\b(?:meme|piada|zoeira|zoando|bait|larp)\b|k{3,}|😂|🤣', value))
        request = bool(re.search(r'\b(?:explique|explica|defina|calcule|resolva|prove|demonstre|compare|analise|resuma|qual|quais|quem|como|por que|quando|quanto|o que)\b', value))
        explicit_tease = bool(re.search(r'\b(?:meme|piada|zoeira|zoando|bait|larp)\b', value))
        if joking and not wants_research and not (request and frame == 'real') and (not request or explicit_tease):
            return replace(analysis, mode='chat', depth=Depth.FAST, conversation_intent='banter',
                needs_sources=False, needs_search=False, needs_audit=False, is_claim_check=False, is_current=False)
        if wants_research or analysis.is_high_stakes or forced_mode in {'academic', 'study', 'argument'}:
            return analysis
        if not request:
            resolved = contextual_query(text, history or [], reply_context)
            prior_user = next((item.get('content', '') for item in reversed(history or []) if item.get('role') == 'user'), '')
            if (resolved != text and conversational_frame(resolved) != 'fiction'
                    and re.search(r'\b(?:explique|defina|calcule|resolva|prove|demonstre|pesquise|como|por que)\b', _fold(prior_user))
                    and self.analyze(prior_user).mode in {'academic', 'study', 'argument', 'code'}):
                inherited = self.analyze(resolved)
                return replace(inherited, reasons=inherited.reasons + ["continuidade academica do autor"])
            # Uma observação com 'hoje', 'física' ou 'teologia' não é uma solicitação de pesquisa.
            return replace(analysis, mode='chat', depth=Depth.FAST, conversation_intent='conversation',
                needs_sources=False, needs_search=False, needs_audit=False, is_current=False)
        return analysis

    def _plan_queries(self, text: str, *, comparison: bool, claim_check: bool) -> list[str]:
        clean = re.sub(r"\s+", " ", text).strip()[:500]
        terms = _keywords(clean)
        queries = [clean]
        if terms:
            core = " ".join(terms[:6])
            queries.append(core)
            queries.append(core + " review evidence")
        if comparison:
            queries.append(clean + " comparison review")
        if claim_check:
            queries.append(clean + " evidence systematic review")
        output: list[str] = []
        seen: set[str] = set()
        for q in queries:
            key = _fold(q)
            if q and key not in seen:
                seen.add(key)
                output.append(q[:500])
        return output[:4]

    def decide(
        self,
        analysis: QueryAnalysis,
        *,
        has_gemini: bool,
        has_groq: bool,
        normal_max_tokens: int,
        academic_max_tokens: int,
        deep_max_tokens: int,
    ) -> RoutingDecision:
        academic = analysis.mode in {"academic", "argument", "study", "code"}
        if academic and has_groq:
            primary, fallback = "groq", "gemini" if has_gemini else None
        else:
            primary = "gemini" if has_gemini else "groq"
            fallback = "groq" if primary == "gemini" and has_groq else ("gemini" if has_gemini else None)

        if analysis.depth == Depth.FAST:
            # O Gemini 2.5 contabiliza o raciocínio interno no orçamento de saída.
            # Mantemos um piso para uma variável de ambiente baixa demais não
            # produzir respostas cortadas; o limite final em caracteres é aplicado
            # antes do envio ao Discord.
            max_tokens = max(640, min(normal_max_tokens, 900))
            thinking = "low"
        elif analysis.depth == Depth.STANDARD:
            max_tokens = academic_max_tokens if academic else normal_max_tokens
            thinking = "medium" if academic else "low"
        elif analysis.depth == Depth.DEEP:
            max_tokens = max(academic_max_tokens, min(deep_max_tokens, 2200))
            thinking = "high" if academic else "medium"
        else:
            max_tokens = max(academic_max_tokens, min(deep_max_tokens, 2600))
            thinking = "high"

        temperature = 0.25 if academic else (0.42 if analysis.needs_search else 0.68)
        audit_level = "full" if analysis.is_claim_check or analysis.is_high_stakes else ("standard" if analysis.needs_audit else "light")
        return RoutingDecision(
            primary_provider=primary,
            fallback_provider=fallback,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking_level=thinking,
            use_memory=analysis.needs_memory,
            use_research=analysis.needs_search,
            audit_level=audit_level,
            speculative_fallback_after=None,
        )
