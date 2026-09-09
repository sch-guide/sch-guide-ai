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


def expand_context(question, seeds, chunks, limit=12):
    # 같은 항목의 문맥만 유지하고 다른 문서로 확장하지 않습니다.
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
                return selected
    return selected
