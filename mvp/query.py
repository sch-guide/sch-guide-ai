"""비용이 들지 않는 질문 계획. 검색 표현만 확장하며 임상 답변을 만들지 않습니다."""

import re
import unicodedata
from dataclasses import dataclass
from difflib import get_close_matches

from mvp.library import ALIASES, INTENT_TERMS, anchors, clean, retrieval_question, terms

FOCUSES = {
    'materials': ('준비물', '물품', '준비'),
    'preparation': ('준비', '확인', '평가', '설명', '동의', '계획'),
    'cautions': ('주의', '주의사항', '금기', '관찰', '보고', '안전', '증상', '합병증'),
    'purpose': ('목적', '정의', '예방', '위해', '이란'),
    'procedure': ('방법', '절차', '순서', '시행'),
    'release': ('해제', '종료', '중단', '기준'),
}
STYLE = {'materials': 'bullets', 'cautions': 'bullets', 'procedure': 'steps',
         'comparison': 'comparison', 'summary': 'summary', 'synthesis': 'summary',
         'fact': 'paragraph', 'purpose': 'paragraph', 'preparation': 'bullets',
         'release': 'bullets'}
EXPANSIONS = {
    '세척': ('irrigation', '세척'), 'irrigation': ('irrigation', '세척'),
    '격리': ('격리', 'isolation'), 'isolation': ('격리', 'isolation'),
    '해제': ('해제', '종료', '중단', '기준'),
}

_ASPECT_SUFFIX_KIND = {
    '준비사항': 'preparation',
    '체크사항': 'preparation',
    '주의사항': 'cautions',
    '절차': 'procedure',
    '방법': 'procedure',
    '순서': 'procedure',
    '시행': 'procedure',
    '목적': 'purpose',
    '이유': 'purpose',
    '준비': 'preparation',
    '확인': 'preparation',
    '주의': 'cautions',
    '조심': 'cautions',
    '안전': 'cautions',
}
_ASPECT_SUFFIXES = tuple(sorted(_ASPECT_SUFFIX_KIND, key=len, reverse=True))
_CANONICAL_ASPECT = {
    'procedure': '절차',
    'purpose': '목적',
    'preparation': '준비',
    'cautions': '주의',
}
_QUERY_NOISE = {
    '알려줘', '알려주세요', '해주세요', '해줘', '무엇인가요', '뭔가요', '뭐야',
    '대해서', '대해', '대한', '관한', '어떻게', '진행해', '진행하나요', '시행해',
    '시행하나요', '수행해', '수행하나요', '확인해', '준비해', '조심할', '주의할',
    '안전하게', '봐야', '항목', '점은', '사항은', '것은', '하는', '방법',
    '어떤',
    '전체적으로', '전반적으로', '간단히', '설명해줘', '설명해주세요',
    '정리해줘', '정리해주세요',
}
_DOCUMENT_LABELS = {
    '지침', '지침서', '실무지침', '실무지침서', '간호지침', '간호실무지침', '간호실무지침서',
}
_TOPIC_ROLE_SUFFIXES = ('간호', '관리', '치료', '교육')


@dataclass(frozen=True)
class QueryPlan:
    original: str
    query: str
    expanded: str
    kind: str = 'fact'
    format: str = 'paragraph'
    focus: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    min_documents: int = 1
    entities: tuple[str, ...] = ()
    clarification: str = ''
    corrections: tuple[tuple[str, str], ...] = ()
    max_seeds: int = 6
    max_hits: int = 12
    domain: str = 'unknown'
    canonical_topics: tuple[str, ...] = ()
    monitoring_branches: tuple[str, ...] = ()
    monitoring_phase: str = ''
    monitoring_item: str = ''
    monitoring_action: str = ''


MONITORING_ITEM_PATTERNS = {
    'respiratory_rate': r'호흡\s*수|호흡수|(?<![a-z])RR(?![a-z])',
    'pulse': r'맥박|(?<![a-z])pulse(?![a-z])|(?<![a-z])HR(?![a-z])',
    'oxygen_saturation': r'산소\s*포화도|산소포화도|SpO2|Oxymetry',
    'consciousness': r'의식\s*(?:상태|수준)|의식상태|의식수준|MOAA/S',
}

_MONITORING_ACTION_PATTERNS = (
    ('assess', r'평가(?:해|하나요|하니|하는지|해요)?'),
    ('measure', r'(?:측정(?:해|하나요|하니|하는지|해요)?|(?<![가-힣])재(?:나요|니|는지|요)?(?=$|[\s?!,.]))'),
    ('check', r'(?:확인(?:해|하나요|하니|하는지|해요)?|체크(?:해|하나요|하니|하는지|해요)?)'),
    ('observe', r'(?:(?<![가-힣])봐(?:요)?(?=$|[\s?!,.])|(?<![가-힣])보(?:나요|니|는지)(?=$|[\s?!,.]))'),
)


def _monitoring_qualifiers(question):
    """질문의 monitoring 축만 구조화하며 임상 지식은 추론하지 않습니다."""
    current = question.split(' / 추가 질문: ')[-1]
    branches = []
    if re.search(r'(?<![가-힣])성인(?:과|와|은|는|이|가|의|에게|[·‧/]|\s|[?!,.]|$)', current):
        branches.append('adult')
    if re.search(r'(?<![가-힣])소아(?:과|와|은|는|이|가|의|에게|[·‧/]|\s|[?!,.]|$)', current):
        branches.append('pediatric')

    item = next(
        (name for name, pattern in MONITORING_ITEM_PATTERNS.items()
         if re.search(pattern, current, re.I)),
        '',
    )
    action = next(
        (name for name, pattern in _MONITORING_ACTION_PATTERNS
         if re.search(pattern, current, re.I)),
        '',
    )
    from mvp.retrieval import _temporal_phases

    phases = _temporal_phases(current)
    phase = next(iter(phases)) if len(phases) == 1 else ''
    return tuple(branches), phase, item, action


def normalize_attached_aspects(question):
    """붙여 쓴 한국어 주제+요청 관점을 검색용 경계로만 분리합니다."""
    suffixes = '|'.join(map(re.escape, _ASPECT_SUFFIXES))
    particles = r'(?:에서는|으로는|에는|에서|은|는|을|를|이|가|에|의)?'
    pattern = rf'(?<![가-힣])([가-힣]{{2,}}?)({suffixes})(?={particles}(?:\s|[?!,.]|$))'
    return clean(re.sub(pattern, r'\1 \2', question))


def _document_topics(documents):
    topics = []
    for doc in documents:
        for value in (doc.get('title', ''), doc.get('document_name', '')):
            stem = re.sub(r'\.[a-z0-9]{1,8}$', '', clean(value), flags=re.I)
            parts = re.split(r'[_/\\>|·]+', stem)
            for part in parts:
                compact = re.sub(r'[^가-힣a-z0-9-]', '', part.casefold())
                for label in sorted(_DOCUMENT_LABELS, key=len, reverse=True):
                    if compact.startswith(label) and len(compact) - len(label) >= 2:
                        compact = compact[len(label):]
                        break
                if len(compact) >= 2 and compact not in _DOCUMENT_LABELS:
                    topics.append(compact)
    return tuple(dict.fromkeys(topics))


def _topic_aliases(topic):
    aliases = {topic}
    for suffix in _TOPIC_ROLE_SUFFIXES:
        if topic.endswith(suffix) and len(topic) - len(suffix) >= 2:
            aliases.add(topic[:-len(suffix)])
    return aliases


def _subject_matches_topic(subject, topic):
    aliases = _topic_aliases(topic)
    stems = {subject}
    for ending in ('하는', '하다', '해', '할', '하'):
        if subject.endswith(ending) and len(subject) > len(ending):
            stems.add(subject[:-len(ending)])
    return bool(stems & aliases)


def _subject_candidates(question):
    current = question.split(' / 추가 질문: ')[-1]
    candidates = list(re.findall(r'(?<![가-힣])([가-힣]{2,})할\s*(?:때|경우)', current))
    ignored = INTENT_TERMS | set(_ASPECT_SUFFIX_KIND) | _QUERY_NOISE | {
        '사전', '이전', '전에', '진행', '단계', '항목', '사항', '필요',
    }
    candidates.extend(word for word in terms(current) if word not in ignored)
    return tuple(dict.fromkeys(candidates))


def _canonical_registered_topics(question, documents):
    """질문 subject가 등록 문서 topic 하나에만 대응할 때 canonical topic을 반환합니다."""
    subjects = _subject_candidates(question)
    topics = _document_topics(documents)
    matched = []
    for subject in subjects:
        candidates = [topic for topic in topics if _subject_matches_topic(subject, topic)]
        if len(candidates) == 1:
            matched.append(candidates[0])
    unique = tuple(dict.fromkeys(matched))
    return unique if len(unique) == 1 else ()


def _canonical_expanded_query(query, kind, canonical_topics, extra):
    if not canonical_topics:
        return clean(query + ' ' + ' '.join(dict.fromkeys(extra)))
    subjects = set(_subject_candidates(query))
    matched_subjects = {
        subject for subject in subjects
        if any(_subject_matches_topic(subject, topic) for topic in canonical_topics)
    }
    topic_roles = {
        suffix for topic in canonical_topics for suffix in _TOPIC_ROLE_SUFFIXES
        if topic.endswith(suffix)
    }
    aspect_words = set(_ASPECT_SUFFIX_KIND)
    def aspect_form(word):
        return any(re.fullmatch(
            re.escape(aspect) + r'(?:으로|로|은|는|을|를|이|가|에|의)?', word,
        ) for aspect in aspect_words)

    remaining = [
        word for word in terms(query)
        if word not in matched_subjects and word not in topic_roles
        and not aspect_form(word) and word not in _QUERY_NOISE
    ]
    canonical_aspect = _CANONICAL_ASPECT.get(kind)
    parts = [*canonical_topics]
    if canonical_aspect:
        parts.append(canonical_aspect)
    if kind == 'summary':
        parts.extend(('목적', '절차', '주의'))
    if kind == 'cautions' and re.search(
        r'안전|확인|관찰|모니터링|이상\s*증상|문제가\s*생기면|회복', query,
    ):
        parts.extend(('관찰', '모니터링'))
    if re.search(r'(?<![가-힣])성인(?:과|와|은|는|이|가|의|에게|[·‧/]|\s|[?!,.]|$)', query):
        parts.append('성인')
    if re.search(r'(?<![가-힣])소아(?:과|와|은|는|이|가|의|에게|[·‧/]|\s|[?!,.]|$)', query):
        parts.append('소아')
    parts.extend(remaining)
    parts.extend(extra)
    return clean(' '.join(dict.fromkeys(parts)))


def question_domain(question):
    """명백한 외부 주제는 검색 전 차단; 미등록 용어는 근거 검사에서 판단합니다."""
    current = question.split(' / 추가 질문: ')[-1]
    if re.search(r'날씨|주식|코인|비트코인|로또|운세|연애|맛집|여행\s*(?:추천|일정)|'
                 r'우주선|축구\s*(?:경기\s*)?결과|파이썬\s*코드|영화\s*추천', current, re.I):
        return 'out_of_scope'
    if anchors(question) or re.search(
        r'간호|병원|환자|진료|투약|투여|수혈|수술|검사|감염|격리|소독|세척|도뇨|'
        r'카테터|배액|활력|혈압|혈당|산소|심폐|응급|낙상|욕창|처방|병동|직원|교육실|인계|병실', question):
        return 'hospital'
    if re.search(
        r'진정.{0,20}(?:치료|시행|수행|전|중|후|목적|준비|확인|체크|주의|조심|안전|'
        r'평가|동의|투약|모니터링)',
        current,
    ):
        return 'hospital'
    if re.search(
        r'(?<![가-힣a-zA-Z0-9-])[가-힣a-zA-Z0-9-]{2,}교육\s*'
        r'(?:목적|준비|주의|방법|절차)',
        current,
        re.I,
    ):
        return 'hospital'
    return 'unknown'


def correct_spelling(question):
    # 짧은 약어(CRE/CPE/VRE 등)는 오타라고 추측해서 서로 바꾸지 않습니다.
    vocabulary = {word for group in ALIASES.values() for alias in group
                  for word in re.findall(r'[a-z]{6,}', alias)} | {'irrigation', 'isolation', 'thoracentesis'}
    changes = []
    def correct(match):
        word = match.group(0)
        if word.lower() in vocabulary:
            return word
        close = get_close_matches(word.lower(), sorted(vocabulary), n=2, cutoff=.88)
        if len(close) == 1:
            changes.append((word, close[0]))
            return close[0]
        return word
    return re.sub(r'[a-zA-Z]{6,}', correct, question), tuple(changes)


def classify(question):
    question = question.split(' / 추가 질문: ')[-1]
    if re.search(r'비교|차이|다른 점|다른점|어떻게\s*달라|뭐가\s*달라', question):
        return 'comparison'
    if re.search(r'종합|여러 문서|여러 지침|함께 정리|문서 간|문서간', question):
        return 'synthesis'
    if re.search(r'준비물|물품', question):
        return 'materials'
    phase = bool(re.search(r'사전|이전|전(?:에|에는|의|\s|$)', question))
    if (re.search(r'준비사항|체크\s*사항|확인\s*사항|뭘\s*준비|준비할\s*(?:것|항목)|'
                  r'(?:^|\s)(?:준비|확인)(?:\s|[?!,.]|$)', question)
            or (phase and re.search(r'준비(?:할|해야|해)|확인(?:할|해야|해)', question))):
        return 'preparation'
    if re.search(r'목적|정의|이유|왜\s*(?:시행|수행|하|해|하는|필요)', question):
        return 'purpose'
    if re.search(r'해제|종료 기준|중단 기준', question):
        return 'release'
    if re.search(r'방법|절차|순서|(?:^|\s)(?:시행|진행)(?:\s|[?!,.]|$)', question):
        return 'procedure'
    if re.search(r'주의|금기|관찰|모니터링|보고|조심|이상\s*증상|문제가\s*생기면|'
                 r'(?:^|\s)안전(?:\s|[?!,.]|$)|안전하게.{0,12}(?:봐야|확인|관찰)', question):
        return 'cautions'
    if re.search(r'요약|정리|쉽게|신규간호사|전체적|전반적|간단히|'
                 r'(?:에\s*대해|에\s*관해).{0,20}(?:알려|설명)|'
                 r'(?:^|\s)[가-힣a-zA-Z0-9-]{2,}(?:은|는|이|가)?\s+(?:알려줘|알려주세요)$', question):
        return 'summary'
    if re.search(r'어떻게|방법|절차|순서|(?:^|\s)(?:시행|진행)(?:\s|[?!,.]|$)', question):
        return 'procedure'
    return 'fact'


def plan_query(question, previous='', follow_up=False, documents=(), previous_sources=()):
    # 개인정보 검사와 대화 길이 제한은 기존 공통 함수에서 수행합니다.
    corrected, corrections = correct_spelling(clean(unicodedata.normalize('NFKC', question)))
    auto = bool(re.match(r'^(그럼|그때|그것|이어서|추가로|아까|주의사항은|준비물은|해제 기준)', corrected))
    if re.search(r'이 두 (?:지침|문서)|이 문서|이 지침|쉽게 정리|보기 쉽게', corrected):
        auto = True
    query = normalize_attached_aspects(retrieval_question(
        corrected, previous, follow_up or auto,
    ))
    kind = classify(normalize_attached_aspects(corrected))
    canonical_topics = _canonical_registered_topics(query, documents)
    compact = re.sub(r'\s+', '', query.lower())
    chosen = []
    for doc in documents:
        names = [doc.get('document_name', '').rsplit('.', 1)[0], doc.get('title', '')]
        if any(len(re.sub(r'\s+', '', name)) >= 4 and re.sub(r'\s+', '', name.lower()) in compact for name in names):
            chosen.append(doc['id'])
    reference_two = bool(re.search(r'(?:두|여러)\s*(?:지침|문서)|문서\s*간|지침\s*간', corrected))
    if not chosen and reference_two:
        allowed = {doc['id'] for doc in documents}
        chosen = list(dict.fromkeys(doc_id for doc_id in previous_sources if doc_id in allowed))[:4]
    clarification = ''
    if reference_two and len(chosen) < 2 and re.search(r'두\s*(?:지침|문서)', corrected):
        clarification = '비교·종합할 지침서 두 개의 이름을 질문에 적어 주세요.'
    if not previous and ' / 추가 질문: ' not in corrected and re.search(r'아까|그 환자|그럼|이 두', corrected) and not anchors(corrected):
        clarification = '새 대화에서는 이전 내용을 알 수 없습니다. 환자 식별정보 없이 지침 주제와 확인할 조건을 적어 주세요.'
    if re.search(r'아까 질문한 환자|그 환자에서는|이 환자에서는', corrected):
        clarification = '환자 식별정보 없이, 이전 지침의 어떤 조건이나 항목을 확인하려는지 구체적으로 적어 주세요.'
    extra = []
    for key, synonyms in EXPANSIONS.items():
        if key in query.lower():
            extra.extend(synonyms)
    for entity in anchors(query):
        extra.extend(ALIASES[entity])
    expanded = _canonical_expanded_query(query, kind, canonical_topics, extra)
    focus = FOCUSES.get(kind, ())
    if not focus:
        focus = tuple(w for w in ('목적', '정의', '대상', '적응증', '기준') if w in corrected)
    broad = kind in {'comparison', 'synthesis', 'summary'}
    domain = question_domain(query)
    if domain != 'out_of_scope' and canonical_topics:
        domain = 'hospital'
    monitoring = _monitoring_qualifiers(query)
    return QueryPlan(question, query, expanded, kind, STYLE[kind], focus, tuple(chosen),
                     2 if reference_two or len(chosen) >= 2 else 1, tuple(anchors(query)), clarification,
                     corrections, 8 if broad else 6, 14 if broad else 12, domain, canonical_topics,
                     *monitoring)


def topic_words(plan):
    if plan.canonical_topics:
        return list(plan.canonical_topics)
    ignored = INTENT_TERMS | {'어떻게', '환자', '시행하는', '투여할', '준비물', '해제', '기준', '정리',
                             '요약', '쉽게', '신규간호사', '알려', '지침서', '문서', '등록된', '충분히',
                             '왜', '준비사항', '준비해야', '체크사항', '확인할', '주의할', '점은',
                             '조심해야', '안전하게', '봐야', '항목', '것은', '뭘', '전에', '뭐야',
                             '시행하나요', '수행하나요', '필요한가요'}
    current = plan.query.split(' / 추가 질문: ')[-1]
    nursing_phrases = tuple(dict.fromkeys(
        left + '간호'
        for left in re.findall(
            r'(?<![가-힣])([가-힣]{2,})\s+간호(?=(?:은|는|이|가|의|에서|를|을)?(?:\s|[?!,.]|$))',
            current,
        )
    ))
    consumed = {phrase[:-2] for phrase in nursing_phrases} | ({'간호'} if nursing_phrases else set())
    remaining = [word for word in terms(plan.query) if word not in ignored and word not in consumed]
    return list(dict.fromkeys((*nursing_phrases, *remaining)))
