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


def _requested_branches(question):
    current = question.split(' / 추가 질문: ')[-1]
    branches = set()
    if re.search(r'(?<![가-힣])성인(?:과|와|은|는|이|가|의|에게|[·‧/]|\s|[?!,.]|$)', current):
        branches.add('adult')
    if re.search(r'(?<![가-힣])소아(?:과|와|은|는|이|가|의|에게|[·‧/]|\s|[?!,.]|$)', current):
        branches.add('pediatric')
    return frozenset(branches)


def _requested_branch(question):
    branches = _requested_branches(question)
    return next(iter(branches)) if len(branches) == 1 else ''


def _parent_branch(chunk, chunks):
    markers = {
        marker
        for related in _parent_group(chunk, chunks)
        for marker in _flat_markers(related.text)
    }
    if markers == {'성인'}:
        return 'adult'
    if markers == {'소아'}:
        return 'pediatric'
    return 'common' if not markers else 'explicit_other'


def _expand_procedure_context(question, seeds, chunks, limit):
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

    candidates = list(selected_by_signature.values())
    requested_branch = _requested_branch(question)
    if requested_branch:
        candidates = [
            hit for hit in candidates
            if _parent_branch(hit.chunk, chunks) in {'common', requested_branch}
        ]
    candidates.sort(
        key=lambda hit: (document_order.get(hit.chunk.document_id, len(document_order)), hit.chunk.index),
    )
    if not requested_branch:
        return _complete_context(candidates[:limit], chunks, preserve_branches=True)

    # 한 분기만 요청한 경우에는 parent를 통째로 담을 수 있을 때만 채택합니다.
    ordered, seen_groups = [], set()
    for candidate in candidates:
        key = (candidate.chunk.document_id, candidate.chunk.parent_id or candidate.chunk.id)
        if key in seen_groups:
            continue
        seen_groups.add(key)
        group = [hit for hit in candidates if (
            hit.chunk.document_id, hit.chunk.parent_id or hit.chunk.id
        ) == key]
        if len(ordered) + len(group) > limit:
            if not ordered:
                return []
            continue
        ordered.extend(group)
    return _complete_context(ordered, chunks, preserve_branches=True)


def _expand_non_procedure_context(
    question, seeds, chunks, limit, *, canonical_topics=(),
):
    """Parent metadata가 있으면 의미 block을 부분 포함하지 않고 원자적으로 선택합니다."""
    requested = _requested_branches(question)
    summary_like = bool(re.search(
        r'요약|정리|전체적|전반적|간단히|(?:에\s*대해|에\s*관해).{0,20}(?:알려|설명)',
        question,
    ))
    branch_sensitive = bool(re.search(
        r'몇\s*분|간격|얼마나\s*자주|활력\s*징후|활력징후|모니터링|회복|입원실|병실',
        question,
    ))
    seeds = list(seeds)
    if requested or summary_like or branch_sensitive:
        document_ids = {seed.chunk.document_id for seed in seeds}
        target_branches = requested or frozenset({'adult', 'pediatric'})
        existing_ids = {seed.chunk.id for seed in seeds}
        template_by_document = {
            seed.chunk.document_id: seed for seed in seeds
        }
        topic_stems = set(canonical_topics)
        for topic in canonical_topics:
            for suffix in ('간호', '관리', '치료', '교육'):
                if topic.endswith(suffix) and len(topic) - len(suffix) >= 2:
                    topic_stems.add(topic[:-len(suffix)])
        seen_groups = set()
        for chunk in chunks:
            key = (chunk.document_id, chunk.parent_id or chunk.id)
            if chunk.document_id not in document_ids or key in seen_groups:
                continue
            seen_groups.add(key)
            group = _parent_group(chunk, chunks) if chunk.parent_id else [chunk]
            branch = _parent_branch(chunk, chunks)
            group_context = clean(' '.join(
                value
                for item in group
                for value in (item.text, item.section, item.title)
                if value
            )).casefold()
            if (topic_stems
                    and not any(stem.casefold() in group_context for stem in topic_stems)):
                continue
            if (branch not in target_branches
                    or not any(has_substantive_body(item) for item in group)):
                continue
            template = template_by_document[chunk.document_id]
            for item in group:
                if item.id in existing_ids:
                    continue
                seeds.append(replace(
                    template, chunk=item, lexical=0, bm25_score=0, context_only=True,
                ))
                existing_ids.add(item.id)

        indexed = list(enumerate(seeds))

        def branch_priority(item):
            position, seed = item
            branch = _parent_branch(seed.chunk, chunks)
            if requested:
                if branch in requested:
                    return 0, position
                if branch == 'common':
                    return 1, position
                return 2, position
            return (0 if branch in {'adult', 'pediatric'} else 1), position

        seeds = [seed for _, seed in sorted(indexed, key=branch_priority)]
    if not any(seed.chunk.parent_id for seed in seeds):
        selected, seen = [], set()
        groups = [
            [seed.chunk] + [chunk for chunk in neighbors(seed.chunk, chunks)
                            if chunk.id != seed.chunk.id]
            for seed in seeds
        ]
        for depth in range(max((len(group) for group in groups), default=0)):
            for seed, group in zip(seeds, groups, strict=True):
                if depth >= len(group):
                    continue
                chunk = group[depth]
                signature = _exact_signature(chunk)
                if signature not in seen:
                    selected.append(seed if depth == 0 else replace(
                        seed, chunk=chunk, lexical=0, bm25_score=0, context_only=True,
                    ))
                    seen.add(signature)
                if len(selected) >= limit:
                    return _complete_context(selected, chunks)
        return _complete_context(selected, chunks)

    seed_by_id = {seed.chunk.id: seed for seed in seeds}
    document_order = {
        document_id: position for position, document_id in enumerate(
            dict.fromkeys(seed.chunk.document_id for seed in seeds)
        )
    }
    selected, selected_ids, seen_groups = [], set(), set()
    for seed in seeds:
        key = (seed.chunk.document_id, seed.chunk.parent_id or seed.chunk.id)
        if key in seen_groups:
            continue
        seen_groups.add(key)
        group = (_parent_group(seed.chunk, chunks)
                 if seed.chunk.parent_id else neighbors(seed.chunk, chunks))
        additions = [chunk for chunk in group if chunk.id not in selected_ids]
        if len(selected) + len(additions) > limit:
            if not selected:
                # 가장 관련도 높은 첫 parent조차 통째로 담을 수 없으면 부분
                # context를 만들지 않는다. 빈 결과는 기존 evidence gate에서
                # fail closed되며 어느 parent 조각도 prompt로 진행하지 않는다.
                return []
            continue
        for chunk in additions:
            selected.append(seed_by_id.get(chunk.id) or replace(
                seed, chunk=chunk, lexical=0, bm25_score=0, context_only=True,
            ))
            selected_ids.add(chunk.id)
    selected.sort(key=lambda hit: (
        document_order.get(hit.chunk.document_id, len(document_order)), hit.chunk.index
    ))
    return _complete_context(selected, chunks)


def expand_context(question, seeds, chunks, limit=12, *, kind=None, plan=None):
    # 같은 항목의 문맥만 유지하고 다른 문서로 확장하지 않습니다.
    if limit < 1:
        raise ValueError('limit은 1 이상이어야 합니다.')
    if plan is not None:
        kind = plan.kind
    procedure = kind == 'procedure' if kind is not None else _procedure_question(question)
    if procedure:
        return _expand_procedure_context(question, seeds, chunks, limit)
    return _expand_non_procedure_context(
        question, seeds, chunks, limit,
        canonical_topics=plan.canonical_topics if plan is not None else (),
    )
