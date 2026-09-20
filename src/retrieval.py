"""로컬 BM25 + FAISS 후보 RRF + 설명 가능한 재정렬. 추가 LLM 호출 없음."""

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Literal, Sequence

import numpy as np

from src.library import Hit, anchors, clean, compatible, has_substantive_body, lexical_evidence, term_matches
from src.query import plan_query, topic_words
from src.search_trace import begin_trace, candidate_trace, finish_trace


def lexical_tokens(text):
    tokens = re.findall(r'[a-z][a-z0-9-]*|[가-힣]{2,}', clean(text).lower())
    result = []
    for token in tokens:
        if re.fullmatch('[가-힣]+', token):
            base = re.sub(r'(?:에서는|으로는|이란|에는|에서|이랑|란|은|는|을|를|의)$', '', token)
            result.append(base or token)
            # 띄어쓰기/조사 차이를 보완합니다. 한 글자만 겹치는 것은 검색어로 쓰지 않습니다.
            result.extend('ko:' + token[i:i+2] for i in range(len(token)-1))
        else:
            result.append(token)
    result.extend('entity:' + entity for entity in anchors(text))
    return result


@dataclass(frozen=True)
class BM25CorpusPolicy:
    """BM25 검색 가능 여부와 본문에 상속할 제목 문맥."""

    searchable: tuple[bool, ...]
    inherited_titles: tuple[str, ...]


TemporalPhase = Literal['before', 'during', 'after']
TemporalTier = Literal['match', 'neutral', 'mismatch', 'non_positive', 'inactive']


@dataclass(frozen=True)
class BM25CandidateRanking:
    positions: tuple[int, ...]
    requested_phase: TemporalPhase | None
    tiers: tuple[TemporalTier, ...]


def _document_title_values(chunk):
    values = {clean(chunk.document_name), clean(chunk.title)}
    if '.' in chunk.document_name:
        values.add(clean(chunk.document_name.rsplit('.', 1)[0]))
    return {value.casefold() for value in values if value}


def _section_title_values(chunk):
    return {
        value.casefold()
        for value in (clean(part) for part in chunk.section.split('>'))
        if value
    }


def _looks_like_running_header(text):
    if not text or len(text) > 60 or len(text.splitlines()) > 2:
        return False
    return re.search(r'(?:다[.!?]?|[.!?])$', text) is None


def bm25_corpus_policy(chunks):
    """제목 chunk는 보존하되 독립 BM25 문서에서는 제외합니다."""

    chunks = list(chunks)
    cleaned_texts = [clean(chunk.text) for chunk in chunks]
    normalized_texts = [text.casefold() for text in cleaned_texts]
    section_values = [_section_title_values(chunk) for chunk in chunks]

    explicit_section = [
        bool(text) and text in values
        for text, values in zip(normalized_texts, section_values)
    ]
    explicit_title = [
        bool(text) and (text in _document_title_values(chunk) or text in sections)
        for text, chunk, sections in zip(normalized_texts, chunks, section_values)
    ]

    page_first = {}
    for position, chunk in enumerate(chunks):
        if chunk.source_type != 'pdf' or chunk.page is None:
            continue
        key = (chunk.document_id, chunk.page)
        previous = page_first.get(key)
        if previous is None or (chunk.index, position) < (chunks[previous].index, previous):
            page_first[key] = position

    repeated_page_headers = Counter()
    for position in page_first.values():
        text = cleaned_texts[position]
        if _looks_like_running_header(text):
            repeated_page_headers[(chunks[position].document_id, text.casefold())] += 1

    running_header = [False] * len(chunks)
    for position in page_first.values():
        chunk = chunks[position]
        text = cleaned_texts[position]
        if (_looks_like_running_header(text)
                and repeated_page_headers[(chunk.document_id, text.casefold())] >= 2):
            running_header[position] = True

    searchable = [
        not (is_explicit or is_running)
        for is_explicit, is_running in zip(explicit_title, running_header)
    ]
    inherited_titles = [''] * len(chunks)
    positions_by_document = defaultdict(list)
    for position, chunk in enumerate(chunks):
        positions_by_document[chunk.document_id].append(position)

    for positions in positions_by_document.values():
        positions.sort(key=lambda position: (chunks[position].index, position))
        pending_titles = []
        for position in positions:
            chunk = chunks[position]
            if not searchable[position]:
                title = cleaned_texts[position]
                known = {pending.casefold() for pending in pending_titles}
                if (explicit_section[position] and not running_header[position]
                        and title and title.casefold() not in known):
                    pending_titles.append(title)
                continue
            if pending_titles and has_substantive_body(chunk):
                inherited = [
                    title for title in pending_titles
                    if title.casefold() not in section_values[position]
                ]
                inherited_titles[position] = ' '.join(inherited)
                pending_titles.clear()

    return BM25CorpusPolicy(tuple(searchable), tuple(inherited_titles))


_PHASE_WORDS = {
    'before': ('사전', '이전'),
    'during': ('동안',),
    'after': ('이후',),
}
_SHORT_PHASES = {'전': 'before', '중': 'during', '후': 'after'}
_PHASE_BOUNDARY = r'(?=$|[\s·‧/(),.\[\]]|으로|인|의)'
_EVENT_PHASE = re.compile(
    r'[가-힣a-zA-Z0-9()/-]{2,}(?:\s+[가-힣a-zA-Z0-9()/-]{2,}){0,3}\s+(전|중|후)'
    + _PHASE_BOUNDARY
)
_CONTINUED_PHASE = re.compile(r'[·‧/]\s*(전|중|후)' + _PHASE_BOUNDARY)
_EVENT_DURING = re.compile(
    r'(?<![가-힣a-zA-Z0-9])([가-힣a-zA-Z0-9()/-]{2,})\s*(시|할\s*때|하는\s*동안)'
    r'(?=$|[\s·‧/(),.?!\[\]])'
)
_NON_PROCEDURE_TIME_SUBJECTS = frozenset({'필요', '회복', '동의'})
_BOUNDED_PHASE_PATTERNS = {
    'before': (
        r'(?<![가-힣])전(?:에|에는)(?=$|[\s·‧/(),.?!\[\]])',
        r'(?:하|되|시행|수행)기\s*전(?=$|[\s·‧/(),.?!\[\]])',
    ),
    'during': (
        r'(?<![가-힣])중(?:에)?(?=$|[\s·‧/(),.?!\[\]])',
        r'(?:하는|시행하는|수행하는)\s*동안(?=$|[\s·‧/(),.?!\[\]])',
    ),
    'after': (
        r'(?<![가-힣])후(?:에)?(?=$|[\s·‧/(),.?!\[\]])',
        r'(?:하고|한|시행하고|수행하고)\s*나서(?=$|[\s·‧/(),.?!\[\]])',
    ),
}


def _temporal_phases(text: str) -> frozenset[TemporalPhase]:
    normalized = clean(unicodedata.normalize('NFKC', text)).casefold()
    phases = set()
    for phase, words in _PHASE_WORDS.items():
        if any(re.search(rf'(?<![가-힣a-z0-9]){word}(?![가-힣a-z0-9])', normalized)
               for word in words):
            phases.add(phase)
    for phase, patterns in _BOUNDED_PHASE_PATTERNS.items():
        if any(re.search(pattern, normalized) for pattern in patterns):
            phases.add(phase)
    phases.update(_SHORT_PHASES[marker] for marker in _EVENT_PHASE.findall(normalized))
    phases.update(_SHORT_PHASES[marker] for marker in _CONTINUED_PHASE.findall(normalized))
    if any(subject not in _NON_PROCEDURE_TIME_SUBJECTS
           for subject, _ in _EVENT_DURING.findall(normalized)):
        phases.add('during')
    return frozenset(phases)


def rank_bm25_candidates(
    question: str,
    chunks: Sequence,
    scores: Sequence[float],
    *,
    limit: int = 40,
) -> BM25CandidateRanking:
    """기존 BM25 후보 안에서 명시적 시점 tier만 안정적으로 재정렬합니다."""

    if len(chunks) != len(scores):
        raise ValueError('chunks와 scores 길이가 같아야 합니다.')
    if limit < 1:
        raise ValueError('limit은 1 이상이어야 합니다.')
    numeric_scores = [float(score) for score in scores]
    if any(not math.isfinite(score) for score in numeric_scores):
        raise ValueError('BM25 score는 유한한 숫자여야 합니다.')

    raw_positions = sorted(
        range(len(chunks)), key=lambda position: (-numeric_scores[position], position)
    )[:limit]
    requested = _temporal_phases(question)
    if len(requested) != 1:
        return BM25CandidateRanking(
            tuple(raw_positions),
            None,
            tuple('inactive' for _ in raw_positions),
        )

    requested_phase = next(iter(requested))

    def tier(position):
        if numeric_scores[position] <= 0:
            return 'non_positive'
        candidate_phases = _temporal_phases(
            f'{chunks[position].text} {chunks[position].section}'
        )
        if requested_phase in candidate_phases:
            return 'match'
        if not candidate_phases:
            return 'neutral'
        return 'mismatch'

    tier_order = {'match': 0, 'neutral': 1, 'mismatch': 2, 'non_positive': 3}
    raw_order = {position: order for order, position in enumerate(raw_positions)}
    tier_by_position = {position: tier(position) for position in raw_positions}
    positions = tuple(sorted(
        raw_positions,
        key=lambda position: (tier_order[tier_by_position[position]], raw_order[position]),
    ))
    tiers = tuple(tier_by_position[position] for position in positions)
    return BM25CandidateRanking(positions, requested_phase, tiers)


class BM25Index:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.policy = bm25_corpus_policy(self.chunks)
        self.lengths = [0] * len(self.chunks)
        self.postings = defaultdict(dict)
        for index, chunk in enumerate(self.chunks):
            if not self.policy.searchable[index]:
                continue
            # 자체 section이 있으면 상위 제목을 검색어로 다시 가중하지 않습니다.
            # inherited_titles에는 문맥을 남겨 두되 lexical 보강은 section이 없는 본문에만 적용합니다.
            context = self.policy.inherited_titles[index] if not clean(chunk.section) else ''
            counts = Counter(lexical_tokens(f'{chunk.text} {chunk.section} {context}'))
            self.lengths[index] = sum(counts.values())
            for word, count in counts.items():
                self.postings[word][index] = count
        indexed_lengths = [
            length for length, searchable in zip(self.lengths, self.policy.searchable)
            if searchable
        ]
        self.average = max(1, float(np.mean(indexed_lengths))) if indexed_lengths else 1
        self.size = len(self.chunks)
        self.indexed_size = sum(self.policy.searchable)

    def scores(self, query):
        scores = np.zeros(self.size, dtype=np.float32)
        for word in set(lexical_tokens(query)):
            posting = self.postings.get(word, {})
            idf = math.log(1 + (self.indexed_size - len(posting) + .5) / (len(posting) + .5))
            # 한국어 n-gram은 보조 수단이며 약어/전체 단어보다 낮게 반영합니다.
            weight = .25 if word.startswith('ko:') else 1
            for index, count in posting.items():
                denominator = count + 1.5 * (1 - .75 + .75 * self.lengths[index] / self.average)
                scores[index] += weight * idf * count * 2.5 / denominator
        return scores


def rrf(*rankings):
    scores = defaultdict(float)
    for ranking in rankings:
        for position, identifier in enumerate(ranking):
            scores[identifier] += 1 / (60 + position + 1)
    return scores


def rerank(plan, hits, minimum=.38, trace=None):
    from src.evidence import explicit_heading_context

    selected, ranked = [], []
    words = topic_words(plan)
    for hit in hits:
        text, section = hit.chunk.text, hit.chunk.section
        decision = dict(chunk_id=hit.chunk.id, similarity=hit.similarity)
        if trace is not None:
            trace['rerank_decisions'].append(decision)
        if not has_substantive_body(hit.chunk):
            decision['reason'] = 'heading_only_or_short_body'
            continue
        if not compatible(plan.query, text + ' ' + section):
            decision['reason'] = 'incompatible_topic'
            continue
        lexical, supported = lexical_evidence(plan.query, hit.chunk)
        supported = supported or explicit_heading_context(plan, hit.chunk)
        decision.update(lexical_supported=supported, minimum_applied=None if supported else max(minimum, .55))
        if not supported and hit.similarity < max(minimum, .55):
            decision['reason'] = 'below_unsupported_dense_threshold'
            continue
        coverage = sum(term_matches(word, text + ' ' + section) for word in words) / max(1, len(words))
        focus = sum(term_matches(word, text) for word in plan.focus) / max(1, len(plan.focus))
        score = hit.fusion_score + .045 * coverage + .04 * focus + .008 * max(0, hit.similarity)
        decision.update(reason='scored', rerank_score=score)
        ranked.append(replace(hit, lexical=lexical, rerank_score=score))
    ranked.sort(key=lambda h: h.rerank_score, reverse=True)
    # 비교 대상 문서·약어를 먼저 확보한 뒤 나머지 상위 후보를 선택합니다.
    if plan.kind in {'comparison', 'synthesis'}:
        groups = ([('document', doc_id) for doc_id in plan.document_ids] if plan.document_ids else
                  [('entity', entity) for entity in plan.entities] if len(plan.entities) >= 2 else
                  [('document', doc_id) for doc_id in dict.fromkeys(h.chunk.document_id for h in ranked)][:4])
        for kind, value in groups:
            candidate = next((h for h in ranked if (h.chunk.document_id == value if kind == 'document' else
                             value in anchors(h.chunk.text + ' ' + h.chunk.section))), None)
            if candidate and candidate not in selected:
                selected.append(candidate)
    seen = {(h.chunk.document_id, h.chunk.page, h.chunk.location, clean(h.chunk.text)) for h in selected}
    for hit in ranked:
        signature = (hit.chunk.document_id, hit.chunk.page, hit.chunk.location, clean(hit.chunk.text))
        if signature not in seen:
            selected.append(hit)
            seen.add(signature)
        if len(selected) >= plan.max_seeds:
            break
    if trace is not None:
        selected_ids = {h.chunk.id for h in selected}
        for decision in trace['rerank_decisions']:
            if decision.get('reason') == 'scored':
                decision['reason'] = 'selected' if decision['chunk_id'] in selected_ids else 'rank_limit_or_duplicate'
    return selected


def search(library, question, vector, doc_ids, minimum, plan=None, trace=None):
    import faiss

    from src.context import expand_context
    plan = plan or plan_query(question)
    begin_trace(trace, plan, doc_ids, minimum)
    allowed = set(doc_ids) & (set(plan.document_ids) if plan.document_ids else set(doc_ids))
    indices = [i for i, chunk in enumerate(library.chunks) if chunk.document_id in allowed]
    if trace is not None:
        trace.update(backend='FAISS IndexFlatIP', allowed_document_ids=sorted(allowed),
                     reason='no_authorized_chunks' if not indices else 'domain_or_clarification')
    if not indices or plan.clarification or plan.domain == 'out_of_scope':
        return []
    key = (tuple(indices), id(library.chunks))
    if library._index_key != key:
        library._index = faiss.IndexFlatIP(library.vectors.shape[1])
        library._index.add(library.vectors[indices])
        library._bm25 = BM25Index([library.chunks[i] for i in indices])
        library._index_key = key
    dense_scores, dense_positions = library._index.search(np.asarray([vector], dtype='float32'), min(40, len(indices)))
    dense = [int(p) for p in dense_positions[0] if p >= 0]
    bm25 = library._bm25.scores(plan.expanded)
    bm25_ranking = rank_bm25_candidates(plan.original, [library.chunks[i] for i in indices], bm25)
    lexical = [position for position in bm25_ranking.positions if bm25[position] > 0]
    fused = rrf(dense, lexical)
    candidates = []
    for position in sorted(set(dense) | set(lexical)):
        index = indices[position]
        candidates.append(Hit(library.chunks[index], float(np.dot(library.vectors[index], vector)),
                              bm25_score=float(bm25[position]), fusion_score=fused[position]))
    by_id = {h.chunk.id: h for h in candidates}
    candidate_trace(trace, [library.chunks[i] for i in indices], bm25,
                    [by_id[library.chunks[indices[p]].id] for p in dense], candidates,
                    bm25_ranking=bm25_ranking)
    seeds = rerank(plan, candidates, minimum, trace=trace)
    hits = expand_context(
        question, seeds, [library.chunks[i] for i in indices],
        limit=plan.max_hits, plan=plan,
    )
    finish_trace(trace, seeds, hits)
    return hits
