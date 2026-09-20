"""운영 청크를 바꾸지 않고 객관적인 품질 수치를 계산한다."""

import re
from collections import Counter
from collections.abc import Callable, Iterable


def _lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def _page_edge_lines(page):
    lines = _lines(page.text)
    return set(lines[:3] + lines[-3:])


def _page_label_candidate(text):
    if not text or len(text) > 60 or " | " in text:
        return False
    if re.search(r"(?:다[.!?]?|[。.!?])$", text):
        return False
    if re.search(r"\d\s*(?:mg|ml|㎎|㎖|℃|분|시간|회|%)", text, re.I):
        return False
    return bool(re.search(r"[가-힣A-Za-z]", text))


def repeated_edge_labels(pages):
    """서로 다른 페이지 가장자리에 반복되는 짧은 검색 표지를 찾는다."""
    counts = Counter(
        line
        for page in pages
        for line in _page_edge_lines(page)
        if _page_label_candidate(line)
    )
    return frozenset(line for line, count in counts.items() if count >= 2)


def repeated_edge_label_count(pages, labels):
    """본문의 같은 문구는 제외하고 페이지 가장자리 표지 수만 센다."""
    return sum(len(_page_edge_lines(page).intersection(labels)) for page in pages)


def validate_chunk_texts(
    texts: Iterable[str],
    count_tokens: Callable[[str], int],
    max_tokens: int = 110,
) -> dict[str, int]:
    """운영 전에 확인할 객관적인 청크 품질 수치를 계산한다."""
    values = list(texts)
    normalized = [re.sub(r"\s+", " ", value).strip().casefold() for value in values]
    nonempty = [value for value in normalized if value]
    return {
        "empty": sum(not value for value in normalized),
        "too_long": sum(count_tokens(value) > max_tokens for value in values),
        "duplicates": len(nonempty) - len(set(nonempty)),
        "max_tokens": max((count_tokens(value) for value in values), default=0),
    }
