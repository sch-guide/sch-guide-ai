"""검색한 문단의 앞뒤 문맥. 원본 파일을 열거나 별도 AI를 호출하지 않습니다."""

import re
from dataclasses import replace

from mvp.library import anchors, clean, has_substantive_body


def neighbors(seed, chunks, radius=2):
    """같은 문서·항목에서 연결된 문단만 최대 5개. 각 문단의 쪽수는 그대로 둡니다."""
    ordered = sorted((c for c in chunks if c.document_id == seed.document_id), key=lambda c: c.index)
    position = next((i for i, c in enumerate(ordered) if c.id == seed.id), None)
    if position is None:
        return []
    result = [seed]
    for direction in (-1, 1):
        prior = seed
        for distance in range(1, radius + 1):
            index = position + direction * distance
            if not 0 <= index < len(ordered):
                break
            candidate = ordered[index]
            if abs(candidate.index - prior.index) != 1:
                break
            linked = prior.previous_chunk_id if direction < 0 else prior.next_chunk_id
            if linked and candidate.id != linked:
                break
            if candidate.section != seed.section:
                break
            if seed.source_type == "pdf":
                if candidate.page is None or seed.page is None or abs(candidate.page - seed.page) > 1:
                    break
            elif not seed.section and candidate.location != seed.location:
                # 분류가 없는 별도 Word 문단·Excel 행은 인접했다는 이유로 채택하지 않습니다.
                break
            seed_topics = set(anchors(seed.text + " " + seed.section))
            candidate_topics = set(anchors(candidate.text))
            if seed_topics and candidate_topics and not seed_topics.intersection(candidate_topics):
                break
            result.append(candidate)
            prior = candidate
    return sorted(result, key=lambda c: c.index)


def _procedure_question(question):
    current = question.split(' / 추가 질문: ')[-1]
    return bool(re.search(r'어떻게|방법|절차|순서', current))


def _exact_signature(chunk):
    return chunk.document_id, clean(chunk.text).casefold()


def _branch_markers(text):
    """원문에 명시된 성인/소아 분기만 찾고 임상 단계를 추론하지 않습니다."""
    return frozenset(
        re.findall(
            r'\[(성인|소아)\]|^\s*(성인|소아)\s*(?:\||$)',
            text,
            re.MULTILINE,
        )
    )


def _flat_markers(text):
    return frozenset(marker for pair in _branch_markers(text) for marker in pair if marker)


def _branch_stems(text):
    stems = []
    for line in text.splitlines():
        if not _flat_markers(line):
            continue
        stem = clean(re.sub(r'\[(?:성인|소아)\]|(?:^|\|)\s*(?:성인|소아)\s*(?=\||$)', ' ', line))
        if len(stem) >= 4:
            stems.append(stem)
    return tuple(dict.fromkeys(stems))


def _parent_group(chunk, chunks):
    if not chunk.parent_id:
        return [chunk]
    return sorted(
        (candidate for candidate in chunks
         if candidate.document_id == chunk.document_id and candidate.parent_id == chunk.parent_id),
        key=lambda candidate: candidate.index,
    )


def _procedure_signature(chunk, chunks):
    branch = frozenset(
        marker
        for related in _parent_group(chunk, chunks)
        for marker in _flat_markers(related.text)
    )
    return chunk.document_id, tuple(sorted(branch)), clean(chunk.text).casefold()


def _procedure_expansion(seed, chunks):
    """seed의 의미 단위·이웃과 원문에 명시된 병렬 분기를 함께 보존합니다."""
    expanded = {chunk.id: chunk for chunk in _parent_group(seed, chunks)}
    expanded.update({chunk.id: chunk for chunk in neighbors(seed, chunks)})

    markers, stems = _flat_markers(seed.text), _branch_stems(seed.text)
    if markers and stems:
        for candidate in chunks:
            if candidate.document_id != seed.document_id:
                continue
            candidate_markers = _flat_markers(candidate.text)
            if not candidate_markers or candidate_markers == markers:
                continue
            if not any(stem in clean(candidate.text) for stem in stems):
                continue
            for related in _parent_group(candidate, chunks):
                expanded[related.id] = related
            for related in neighbors(candidate, chunks):
                expanded[related.id] = related
    return sorted(expanded.values(), key=lambda chunk: chunk.index)


def _complete_context(selected, chunks, *, preserve_branches=False):
    ids = {hit.chunk.id for hit in selected}
    parents = {(hit.chunk.document_id, hit.chunk.parent_id)
               for hit in selected if hit.chunk.parent_id}
    if preserve_branches:
        signatures = {_procedure_signature(hit.chunk, chunks) for hit in selected}
        incomplete = {
            (chunk.document_id, chunk.parent_id)
            for chunk in chunks
            if (chunk.document_id, chunk.parent_id) in parents
            and chunk.id not in ids
            and _procedure_signature(chunk, chunks) not in signatures
        }
    else:
        incomplete = {
            (chunk.document_id, chunk.parent_id)
            for chunk in chunks
            if (chunk.document_id, chunk.parent_id) in parents and chunk.id not in ids
        }
    return [replace(hit, context_complete=(hit.chunk.document_id, hit.chunk.parent_id) not in incomplete)
            for hit in selected]


def _expand_procedure_context(seeds, chunks, limit):
    """관련 seed 여러 개를 구조적으로 확장한 뒤 문서 원문 순서로 반환합니다."""
    seed_by_id = {hit.chunk.id: hit for hit in seeds}
    document_order = {document_id: position for position, document_id in enumerate(
        dict.fromkeys(hit.chunk.document_id for hit in seeds))}
    selected_by_signature = {}

    for seed in seeds:
        for chunk in _procedure_expansion(seed.chunk, chunks):
            if not has_substantive_body(chunk):
                continue
            hit = seed_by_id.get(chunk.id) or replace(
                seed, chunk=chunk, lexical=0, bm25_score=0, context_only=True
            )
            signature = _procedure_signature(chunk, chunks)
            previous = selected_by_signature.get(signature)
            if previous is None or (previous.context_only and not hit.context_only):
                selected_by_signature[signature] = hit

    ordered = sorted(
        selected_by_signature.values(),
        key=lambda hit: (document_order.get(hit.chunk.document_id, len(document_order)), hit.chunk.index),
    )[:limit]
    return _complete_context(ordered, chunks, preserve_branches=True)


def expand_context(question, seeds, chunks, limit=12):
    # 같은 항목의 문맥만 유지하고 다른 문서로 확장하지 않습니다.
    if limit < 1:
        raise ValueError('limit은 1 이상이어야 합니다.')
    if _procedure_question(question):
        return _expand_procedure_context(seeds, chunks, limit)

    selected, seen = [], set()
    groups = [[s.chunk] + [c for c in neighbors(s.chunk, chunks) if c.id != s.chunk.id] for s in seeds]
    # 검색 후보를 먼저 확보한 뒤 앞뒤를 추가하여 여러 문서의 비교 근거를 남깁니다.
    for depth in range(max((len(g) for g in groups), default=0)):
        for seed, group in zip(seeds, groups, strict=True):
            if depth >= len(group):
                continue
            chunk = group[depth]
            signature = (chunk.document_id, chunk.page, chunk.location, clean(chunk.text))
            if signature not in seen:
                selected.append(seed if depth == 0 else replace(seed, chunk=chunk, lexical=0,
                                                               bm25_score=0, context_only=True))
                seen.add(signature)
            if len(selected) >= limit:
                return _complete_context(selected, chunks)
    return _complete_context(selected, chunks)
