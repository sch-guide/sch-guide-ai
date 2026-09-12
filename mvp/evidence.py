"""LLM 호출 전의 보수적 근거 검사. 유사도 점수는 정답 확률이 아닙니다."""

import re
from dataclasses import dataclass

from mvp.library import INTENT_TERMS, anchors, clean, compatible, term_matches, terms

# 같은 주제의 목적 문단만으로 용량·주기·해제 기준 등을 답하지 못하게 합니다.
ASPECTS = (
    (r'용량|투여량|세척량|몇\s*(?:mg|ml)|얼마나\s*(?:투여|주입|세척)',
     r'용량|투여량|세척량|\d+(?:\.\d+)?\s*(?:mg|mcg|μg|µg|ml|mL|cc|단위)'),
    (r'속도|몇\s*(?:방울|gtt)', r'속도|\d+(?:\.\d+)?\s*(?:ml/h|mL/h|gtt|방울)'),
    (r'주기|간격|몇\s*회|몇\s*번|얼마나\s*자주', r'주기|간격|매일|매주|\d+\s*(?:시간|분|일|회|번)'),
    (r'해제|종료\s*기준|중단\s*기준', r'해제|종료|중단|중지'),
    (r'준비물|준비할\s*물품', r'준비|물품'),
    (r'주의사항|금기', r'주의|금기|금지|않|말아|해서는\s*안'),
    (r'목적|정의', r'목적|정의|이란|란\s'),
    (r'방법|절차|순서|어떻게',
     r'(?:확인|작성|시행|세척|교체|준비|연결|제거|소독|측정|주입|투여|기록|보고|표시|사용|중단)(?:하|합|해)|'
     r'읽(?:고|습|는|어)|끄(?:고|는)|끕니다|누르|눌러'),
)
GENERIC = INTENT_TERMS | {'교육', '안내', '지침서', '문서', '등록된', '병원', '환자', '직원',
                         '요약', '정리', '쉽게', '종합', '여러', '자료', '어떤', '뭐야', '무엇',
                         '전', '후', '교육은', '쓰는', '알려', '사용해', '확인해', '사용법'}


@dataclass(frozen=True)
class EvidenceAssessment:
    sufficient: bool
    hits: tuple = ()
    reason: str = ''


def relevant_body(plan, hit):
    """제목이나 높은 벡터 점수만으로 근거를 인정하지 않습니다."""
    text = hit.chunk.text
    context = text + ' ' + hit.chunk.section
    if not compatible(plan.query, context) or len(clean(text)) < 8:
        return False
    if plan.entities:
        return bool(set(plan.entities) & set(anchors(context)))
    words = [w for w in terms(plan.query) if w not in GENERIC]
    matched = [w for w in words if term_matches(w, text)]
    # 등록 문서명만 같거나 '방법/교육' 같은 공통어만 같은 것은 제외합니다.
    return bool(matched)


def assess_evidence(plan, hits):
    if plan.domain == 'out_of_scope' or plan.clarification:
        return EvidenceAssessment(False, reason='domain_or_clarification')
    seeds = [h for h in hits if not h.context_only and relevant_body(plan, h)]
    scopes = {(h.chunk.document_id, h.chunk.section) for h in seeds}
    relevant = [h for h in hits if h in seeds or (
        h.context_only and (h.chunk.document_id, h.chunk.section) in scopes
        and compatible(plan.query, h.chunk.text + ' ' + h.chunk.section))]
    if not seeds:
        return EvidenceAssessment(False, reason='no_topic_evidence')
    if any(not h.context_complete for h in relevant):
        return EvidenceAssessment(False, tuple(relevant), 'incomplete_semantic_block')
    bodies = '\n'.join(h.chunk.text for h in relevant)
    current = plan.query.split(' / 추가 질문: ')[-1]
    # 검색어 확장에 사용한 동의어는 원문 존재 여부를 판단할 때 재사용하지 않습니다.
    for request, support in ASPECTS:
        if re.search(request, current, re.I) and not re.search(support, bodies, re.I):
            return EvidenceAssessment(False, tuple(relevant), 'missing_requested_aspect')
    if plan.entities:
        present = set().union(*(anchors(h.chunk.text + ' ' + h.chunk.section) for h in relevant))
        if not set(plan.entities).issubset(present):
            return EvidenceAssessment(False, tuple(relevant), 'missing_entity')
        # PCN이라는 이름만 있고 질문의 구체적 동작(예: 세척)이 없는 경우를 차단합니다.
        for action in ('세척', 'irrigation', '소독', '교체'):
            if term_matches(action, current):
                aliases = ('세척', 'irrigation') if action in {'세척', 'irrigation'} else (action,)
                if not any(term_matches(word, bodies) for word in aliases):
                    return EvidenceAssessment(False, tuple(relevant), 'missing_action')
    docs = {h.chunk.document_id for h in relevant}
    if len(docs) < plan.min_documents or not set(plan.document_ids).issubset(docs):
        return EvidenceAssessment(False, tuple(relevant), 'missing_document')
    return EvidenceAssessment(True, tuple(relevant), 'supported')


def source_sentences(text):
    """수치의 소수점은 유지하며 문장/표 행을 출처 단위로 나눕니다."""
    return [clean(s) for s in re.split(r'(?<=[.!?。！？])(?<!\d\.)\s+|\n+', text) if clean(s)]


def sentence_evidence(text, evidence, sources):
    """문장 전체가 원문과 일치해야 합니다. 조건/부정을 삭제한 부분 인용은 실패합니다."""
    result = []
    for sentence in source_sentences(text):
        matching = [e for e in evidence if sentence in source_sentences(sources[e.chunk_id].text)
                    and sentence in clean(e.quote)]
        if not matching:
            return []
        result.append((sentence, matching))
    return result
