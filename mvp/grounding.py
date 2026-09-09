"""의미 검증을 대체하지 않는 보수적인 충돌 단서 검사."""

import re

from mvp.library import clean


def explicit_conflicts(hits):
    seen, pairs = {}, []
    for hit in hits:
        for sentence in re.split(r'\n|(?<=[.!?])\s+', hit.chunk.text):
            sentence = clean(sentence).lower()
            if not 15 <= len(sentence) <= 350:
                continue
            quantities = tuple(re.findall(r'\d+(?:\.\d+)?\s*(?:mg|ml|분|초|시간|회|개|일)', sentence))
            if not quantities:
                continue
            # 수치 이외의 조건·문구가 완전히 같을 때만 확정적 검토 단서로 사용합니다.
            key = re.sub(r'\d+(?:\.\d+)?(?=\s*(?:mg|ml|분|초|시간|회|개|일))', '#', sentence)
            if key in seen:
                previous, values = seen[key]
                if previous.chunk.document_id != hit.chunk.document_id and quantities != values:
                    pairs.append((previous.chunk.id, hit.chunk.id))
            else:
                seen[key] = (hit, quantities)
    return list(dict.fromkeys(pairs))[:3]


ACTION_GROUPS = (
    r'준비|구비|챙[기겨]',
    r'투여|투약',
    r'복용',
    r'주입',
    r'주사',
    r'삽입|삽관',
    r'제거|발관',
    r'소독|살균',
    r'세척|irrigation',
    r'중단|종료|해제|중지',
    r'기록|기재|작성',
    r'의뢰',
    r'(?<![가-힣])보고|보고(?:하|해)',
    r'연락',
)


def unsupported_action(text, source_text):
    """알려진 동작을 새로 추가하는 출력을 차단하는 보조 검사. 의미 전체 판정은 아닙니다."""
    return any(re.search(pattern, text, re.I) and not re.search(pattern, source_text, re.I)
               for pattern in ACTION_GROUPS)
