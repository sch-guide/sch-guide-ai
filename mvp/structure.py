"""제목/문단/표를 먼저 구분하고, 큰 의미 단위만 검색 모델 길이로 나눕니다."""

import hashlib
import re
from uuid import UUID


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
            # 완결된 지시문/수치 단계를 제목으로 추측하지 않습니다.
            heading = (len(value) <= 60 and not re.search(r'(?:다[.!?]?|[。.!?])$', value)
                       and (re.match(r'^(?:\d+(?:\.\d+)*[.)]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+[. ]|[가-하]\))\s*\S', value)
                            or value in {'목적', '준비물', '주의사항', '적응증', '금기', '시행 방법', '절차'}))
            if heading:
                if buffer:
                    yield block(buffer, section)
                section = ' > '.join(x for x in (page.section or fallback_section, value) if x)
                buffer = [line]
            else:
                buffer.append(line)
        if buffer:
            yield block(buffer, section)
            buffer = []
