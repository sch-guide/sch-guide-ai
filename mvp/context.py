"""검색한 문단의 앞뒤 문맥. 원본 파일을 열거나 별도 AI를 호출하지 않습니다."""

from dataclasses import replace

from mvp.library import anchors, clean


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


def expand_context(question, seeds, chunks, limit=12, *, plan=None, trace=None):
    # 같은 항목의 문맥만 유지하고 다른 문서로 확장하지 않습니다.
    selected, seen = [], set()
    def complete_context():
        # 128토큰 제한으로 나뉜 의미 단위의 뒷부분이 빠지면 이를 생성 단계에 알립니다.
        ids = {h.chunk.id for h in selected}
        parents = {(h.chunk.document_id, h.chunk.parent_id) for h in selected if h.chunk.parent_id}
        incomplete = {(c.document_id, c.parent_id) for c in chunks
                      if (c.document_id, c.parent_id) in parents and c.id not in ids}
        return [replace(h, context_complete=(h.chunk.document_id, h.chunk.parent_id) not in incomplete)
                for h in selected]
    def finish(reason, result):
        if trace is not None:
            trace.setdefault('context_selection', {}).update(reason=reason, limit=limit,
                seed_count=len(seeds), selected_chunk_count=len(result))
        return result
    if not seeds:
        return finish('no_candidates_after_rerank', [])
    if limit <= 0:
        return finish('context_budget_exceeded', [])
    # Relevance supplies parent candidates, not an unconditional requirement
    # to retain every candidate. Coverage and evidence checks constrain selection.
    from mvp.evidence import relevant_body, assess_evidence, GENERIC
    from mvp.library import terms, term_matches
    from mvp.grounding import explicit_conflicts
    from itertools import combinations
    from mvp.query import plan_query
    plan = plan or plan_query(question)
    required = [seed for seed in seeds if relevant_body(plan, seed)]
    if required and any(seed.chunk.parent_id for seed in required):
        def parent_key(chunk):
            return chunk.document_id, chunk.parent_id or chunk.id
        groups = {}
        for seed in required:
            key = parent_key(seed.chunk)
            if key not in groups:
                siblings = [c for c in chunks if parent_key(c) == key]
                # A missing seed in an inconsistent catalog must not disappear.
                members = {c.id: c for c in siblings}
                members[seed.chunk.id] = seed.chunk
                groups[key] = (seed, sorted(members.values(), key=lambda c: c.index))
        ordered = list(groups)
        def build(keys):
            wanted = set(keys)
            result, ids = [], set()
            for seed in required:
                key = (seed.chunk.document_id, seed.chunk.id)
                if parent_key(seed.chunk) in wanted and key not in ids:
                    result.append(seed)
                    ids.add(key)
            for key in keys:
                seed, members = groups[key]
                for chunk in members:
                    identifier = (chunk.document_id, chunk.id)
                    if identifier not in ids:
                        result.append(replace(seed, chunk=chunk, lexical=0, bm25_score=0, context_only=True))
                        ids.add(identifier)
            return result
        all_hits = build(ordered)
        current = plan.query.split(' / 추가 질문: ')[-1]
        words = [word for word in terms(current) if word not in GENERIC]
        supported = {word for word in words if any(term_matches(word, h.chunk.text) for h in all_hits)}
        # Existing explicit conflict evidence must not be hidden by selection.
        conflict_ids = {identifier for pair in explicit_conflicts(all_hits) for identifier in pair}
        if trace is not None:
            trace['context_selection'] = dict(parent_candidates=[
                dict(document_id=key[0], parent_id=key[1], seed_rank=i+1,
                     chunk_count=len(groups[key][1])) for i, key in enumerate(ordered)],
                all_parent_chunk_count=len(all_hits))
        feasible = []
        sufficient_over_budget = False
        # At most max_seeds parents (normally six): enumerate bounded combinations
        # without changing retrieval scores or calling search/LLM.
        for count in range(1, len(ordered)+1):
            for tail in combinations(range(1, len(ordered)), count-1):
                ranks = (0,) + tail
                keys = [ordered[i] for i in ranks]
                selected = build(keys)
                completed = complete_context()
                if not all(h.context_complete for h in completed):
                    continue
                if not conflict_ids.issubset({h.chunk.id for h in completed}):
                    continue
                retained = {word for word in words if any(term_matches(word, h.chunk.text) for h in completed)}
                if not supported.issubset(retained):
                    continue
                if not assess_evidence(plan, completed).sufficient:
                    continue
                if len(completed) > limit:
                    sufficient_over_budget = True
                    continue
                feasible.append((ranks, keys, completed))
        if not feasible:
            return finish('context_budget_exceeded' if sufficient_over_budget else 'no_complete_required_parent', [])
        _, chosen, result = min(feasible, key=lambda item: item[0])
        if trace is not None:
            trace['context_selection']['selected_parents'] = [dict(document_id=k[0], parent_id=k[1]) for k in chosen]
        return finish('context_ready', result)
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
                return finish('context_ready', complete_context())
    return finish('context_ready', complete_context())
