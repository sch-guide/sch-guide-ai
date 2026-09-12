"""비용이 들지 않는 질문 계획. 검색 표현만 확장하며 임상 답변을 만들지 않습니다."""

import re
import unicodedata
from dataclasses import dataclass
from difflib import get_close_matches

from mvp.library import ALIASES, INTENT_TERMS, anchors, clean, retrieval_question, terms

FOCUSES = {
    'materials': ('준비물', '물품', '준비'),
    'cautions': ('주의', '주의사항', '금기', '관찰', '보고'),
    'procedure': ('방법', '절차', '순서', '시행'),
    'release': ('해제', '종료', '중단', '기준'),
}
STYLE = {'materials': 'bullets', 'cautions': 'bullets', 'procedure': 'steps',
         'comparison': 'comparison', 'summary': 'summary', 'synthesis': 'summary',
         'fact': 'paragraph', 'release': 'bullets'}
EXPANSIONS = {
    '세척': ('irrigation', '세척'), 'irrigation': ('irrigation', '세척'),
    '격리': ('격리', 'isolation'), 'isolation': ('격리', 'isolation'),
    '해제': ('해제', '종료', '중단', '기준'),
}


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


def question_domain(question):
    """명백한 외부 주제는 검색 전 차단; 미등록 용어는 근거 검사에서 판단합니다."""
    current = question.split(' / 추가 질문: ')[-1]
    if re.search(r'날씨|주식|코인|비트코인|로또|운세|연애|맛집|여행\s*(?:추천|일정)|'
                 r'우주선|축구\s*결과|파이썬\s*코드|영화\s*추천', current, re.I):
        return 'out_of_scope'
    if anchors(question) or re.search(
        r'간호|병원|환자|진료|투약|투여|수혈|수술|검사|감염|격리|소독|세척|도뇨|'
        r'카테터|배액|활력|혈압|혈당|산소|심폐|응급|낙상|욕창|처방|병동|직원|교육실|인계|병실', question):
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
    if re.search(r'비교|차이|다른 점|다른점', question):
        return 'comparison'
    if re.search(r'종합|여러 문서|여러 지침|함께 정리|문서 간|문서간', question):
        return 'synthesis'
    if re.search(r'준비물|물품|준비할|준비해야', question):
        return 'materials'
    if re.search(r'주의|금기|관찰|보고', question):
        return 'cautions'
    if re.search(r'해제|종료 기준|중단 기준', question):
        return 'release'
    if re.search(r'요약|정리|쉽게|신규간호사', question):
        return 'summary'
    if re.search(r'어떻게|방법|절차|순서', question):
        return 'procedure'
    return 'fact'


def plan_query(question, previous='', follow_up=False, documents=(), previous_sources=()):
    # 개인정보 검사와 대화 길이 제한은 기존 공통 함수에서 수행합니다.
    corrected, corrections = correct_spelling(clean(unicodedata.normalize('NFKC', question)))
    auto = bool(re.match(r'^(그럼|그때|그것|이어서|추가로|아까|주의사항은|준비물은|해제 기준)', corrected))
    if re.search(r'이 두 (?:지침|문서)|이 문서|이 지침|쉽게 정리|보기 쉽게', corrected):
        auto = True
    query = retrieval_question(corrected, previous, follow_up or auto)
    kind = classify(corrected)
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
    expanded = clean(query + ' ' + ' '.join(dict.fromkeys(extra)))
    focus = FOCUSES.get(kind, ())
    if not focus:
        focus = tuple(w for w in ('목적', '정의', '대상', '적응증', '기준') if w in corrected)
    broad = kind in {'comparison', 'synthesis', 'summary'}
    return QueryPlan(question, query, expanded, kind, STYLE[kind], focus, tuple(chosen),
                     2 if reference_two or len(chosen) >= 2 else 1, tuple(anchors(query)), clarification,
                     corrections, 8 if broad else 6, 14 if broad else 12, question_domain(query))


def topic_words(plan):
    ignored = INTENT_TERMS | {'어떻게', '환자', '시행하는', '투여할', '준비물', '해제', '기준', '정리',
                             '요약', '쉽게', '신규간호사', '알려', '지침서', '문서', '등록된', '충분히'}
    return [word for word in terms(plan.query) if word not in ignored]
