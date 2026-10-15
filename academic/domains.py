"""Políticas por disciplina; prioridade de fonte não é certificado de verdade."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit


def fold(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))


# Marcadores com limites de palavra evitam 'lei' em palavras como 'leitura'.
MARKERS = {
    'history': ('historia', 'historico', 'historiografia', 'revolucao francesa', 'guerra mundial', 'imperio romano', 'idade media', 'colonialismo', 'escravidao', 'ditadura', 'reforma protestante'),
    'politics': ('politica', 'politico', 'democracia', 'eleicao', 'eleicoes', 'partido', 'parlamento', 'congresso', 'geopolitica', 'governo', 'presidente', 'autoritarismo', 'liberalismo', 'socialismo'),
    'theology': ('teologia', 'teologico', 'biblia', 'biblico', 'exegese', 'hermeneutica biblica', 'cristianismo', 'judaismo', 'isla', 'religiao', 'patristica', 'trindade', 'soteriologia', 'escatologia', 'agostinho', 'tomas de aquino'),
    'biology': ('biologia', 'biologico', 'ecologia', 'evolucao', 'genetica', 'celula', 'celular', 'dna', 'rna', 'biodiversidade', 'botanica', 'zoologia', 'fotossintese'),
    'physics': ('fisica', 'fisico', 'quantica', 'quantico', 'relatividade', 'termodinamica', 'eletromagnetismo', 'mecanica', 'particulas', 'cosmologia', 'gravitacao'),
    'chemistry': ('quimica', 'quimico', 'molecula', 'molecular', 'reacao quimica', 'estequiometria', 'catalise', 'ligacao covalente', 'tabela periodica', 'entalpia', 'acido', 'oxidacao'),
}

SOURCES = {
    'history': ('cpdoc.fgv.br', 'bndigital.bn.gov.br', 'bn.gov.br', 'loc.gov', 'archives.gov', 'scielo.br'),
    'politics': ('tse.jus.br', 'camara.leg.br', 'senado.leg.br', 'ibge.gov.br', 'ipea.gov.br', 'data.worldbank.org', 'scielo.br'),
    'theology': ('bibleodyssey.org', 'thetorah.com', 'vatican.va', 'plato.stanford.edu', 'scielo.br'),
    'biology': ('pubmed.ncbi.nlm.nih.gov', 'europepmc.org', 'scielo.br', 'ncbi.nlm.nih.gov', 'nature.com'),
    'physics': ('cern.ch', 'home.cern', 'inspirehep.net', 'arxiv.org', 'aps.org', 'nist.gov'),
    'chemistry': ('goldbook.iupac.org', 'iupac.org', 'pubchem.ncbi.nlm.nih.gov', 'webbook.nist.gov', 'rsc.org', 'acs.org'),
}

GUIDANCE = {
    'history': 'Diferencie documento primário, reconstrução historiográfica e interpretação; informe período e lugar e apresente divergências relevantes.',
    'politics': 'Diferencie dado oficial, legislação, análise e opinião partidária. Para cargos, eleições e estatísticas atuais, verifique data e jurisdição. Fonte governamental não é interpretação neutra por definição.',
    'theology': 'Identifique tradição, texto e contexto; separe doutrina confessional, exegese e pesquisa histórico-crítica. Vaticano é fonte primária católica, não consenso de todas as religiões. Não apresente uma crença como resultado empírico.',
    'biology': 'Diferencie revisão, estudo primário, modelo animal e resultado em humanos; não transforme associação em causalidade.',
    'physics': 'Separe teoria, observação e preprint; declare hipóteses, unidades e regime de validade. arXiv não garante revisão por pares.',
    'chemistry': 'Priorize nomenclatura IUPAC e dados NIST/PubChem; confira unidade, temperatura, pressão e condições experimentais; diferencie cálculo teórico e medida.',
}


def topic_domains(text: str) -> list[str]:
    value = fold(text)
    return [domain for domain, terms in MARKERS.items() if any(re.search(r'(?<!\w)' + re.escape(term) + r'(?:s|a|as|os)?(?!\w)', value) for term in terms)]


def preferred_domains(text: str) -> list[str]:
    return list(dict.fromkeys(host for topic in topic_domains(text) for host in SOURCES[topic]))[:24]


def domain_guidance(text: str) -> str:
    topics = topic_domains(text)
    if not topics:
        return ''
    return 'CRITÉRIOS DA DISCIPLINA:\n' + '\n'.join(GUIDANCE[t] for t in topics)


def matches_host(url: str, domain: str) -> bool:
    try:
        host = (urlsplit(url).hostname or '').lower()
    except ValueError:
        return False
    return host == domain or host.endswith('.' + domain)
