"""로컬 BM25 + FAISS 후보 RRF + 설명 가능한 재정렬. 추가 LLM 호출 없음."""

import math
import re
from collections import Counter, defaultdict
from dataclasses import replace

import numpy as np

from mvp.library import Hit, anchors, clean, compatible, lexical_evidence, term_matches
from mvp.query import plan_query, topic_words
from mvp.search_trace import begin_trace, candidate_trace, finish_trace


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


class BM25Index:
    def __init__(self, chunks):
        self.lengths, self.postings = [], defaultdict(dict)
        for index, chunk in enumerate(chunks):
            counts = Counter(lexical_tokens(chunk.text + ' ' + chunk.section))
            self.lengths.append(sum(counts.values()))
            for word, count in counts.items():
                self.postings[word][index] = count
        self.average = max(1, float(np.mean(self.lengths))) if self.lengths else 1
        self.size = len(chunks)

    def scores(self, query):
        scores = np.zeros(self.size, dtype=np.float32)
        for word in set(lexical_tokens(query)):
            posting = self.postings.get(word, {})
            idf = math.log(1 + (self.size - len(posting) + .5) / (len(posting) + .5))
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
    from mvp.evidence import explicit_heading_context

    selected, ranked = [], []
    words = topic_words(plan)
    for hit in hits:
        text, section = hit.chunk.text, hit.chunk.section
        decision = dict(chunk_id=hit.chunk.id, similarity=hit.similarity)
        if trace is not None:
            trace['rerank_decisions'].append(decision)
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

    from mvp.context import expand_context
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
    lexical = [int(i) for i in np.argsort(-bm25, kind='stable')[:40] if bm25[i] > 0]
    fused = rrf(dense, lexical)
    candidates = []
    for position in sorted(set(dense) | set(lexical)):
        index = indices[position]
        candidates.append(Hit(library.chunks[index], float(np.dot(library.vectors[index], vector)),
                              bm25_score=float(bm25[position]), fusion_score=fused[position]))
    by_id = {h.chunk.id: h for h in candidates}
    candidate_trace(trace, [library.chunks[i] for i in indices], bm25,
                    [by_id[library.chunks[indices[p]].id] for p in dense], candidates)
    seeds = rerank(plan, candidates, minimum, trace=trace)
    hits = expand_context(question, seeds, [library.chunks[i] for i in indices], limit=plan.max_hits)
    finish_trace(trace, seeds, hits)
    return hits
