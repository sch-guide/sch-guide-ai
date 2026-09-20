"""제목/문단/표를 먼저 구분하고, 큰 의미 단위만 검색 모델 길이로 나눕니다."""

import hashlib
import re
from uuid import UUID


def looks_like_heading(value):
    """완결된 지시문과 수치 단계는 제외하고 짧은 항목 제목만 찾는다."""
    value = value.strip()
    return (len(value) <= 60 and not re.search(r'(?:다[.!?]?|[。.!?])$', value)
            and (re.match(r'^(?:\d+(?:\.\d+)*[.)]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[. ]|[가-하]\))\s*\S', value)
                 or value in {'목적', '준비물', '주의사항', '적응증', '금기', '시행 방법', '절차'}))


def semantic_blocks(page, document_id, fallback_section=''):
    section = page.section or fallback_section
    buffer = []
    ordinal = 0
    def block(lines, current):
        nonlocal ordinal
        ordinal += 1
        # Supabase의 parent_id는 uuid입니다. 24자리 해시는 PostgreSQL에서 거절됩니다.
        parent = str(UUID(hashlib.sha256(
            f'{document_id}:{page.number}:{page.location}:{ordinal}'.encode()).hexdigest()[:32]))
        return '\n'.join(lines).strip(), current, parent
    for paragraph in page.text.split('\n\n'):
        if ' | ' in paragraph or page.header:
            if buffer:
                yield block(buffer, section)
                buffer = []
            yield block(paragraph.splitlines(), section)
            continue
        for line in paragraph.splitlines():
            value = line.strip()
            if looks_like_heading(value):
                if buffer:
                    yield block(buffer, section)
                section = ' > '.join(x for x in (page.section or fallback_section, value) if x)
                buffer = [line]
            else:
                buffer.append(line)
        if buffer:
            yield block(buffer, section)
            buffer = []
