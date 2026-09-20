"""Q001~Q006와 표현 변형의 retrieval/evidence 일반화를 외부 호출 없이 평가합니다."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from datetime import datetime
from html import escape
from pathlib import Path

import httpx
import numpy as np

from src.ai import (
    GROQ_REQUEST_TOKEN_BUDGET,
    FacetSlotSelectionContract,
    generate,
    prompt_messages,
)
from src.context import expand_context
from src.evidence import assess_evidence
from src.library import Hit, bounded_embedding_question, embedding_question, terms
from src.query import plan_query, topic_words
from src.retrieval import (
    BM25Index,
    lexical_tokens,
    rank_bm25_candidates,
    rerank,
    rrf,
)
from src.settings import ROOT
from tools.bm25_evaluate import DEFAULT_QUESTIONS
from tools.rag_facet_slot_evaluate import (
    MockQuota,
    facet_response,
    q002_inputs,
    settings,
)
from tools.rag_phase1_evaluate import _stable_positions
from tools.rag_procedure_live_evaluate import pilot_corpus

DEFAULT_OUTPUT = ROOT / 'workspace' / 'RAG_실험' / '2026-09-14_rag-pilot-query-generalization'
PARAPHRASES = (
    ('P01', 'purpose', '진정간호 목적 알려줘'),
    ('P02', 'purpose', '진정 간호는 왜 시행하나요?'),
    ('P03', 'purpose', '진정의 목적이 뭐야?'),
    ('P04', 'preparation', '진정 전에 뭘 준비해야 해?'),
    ('P05', 'preparation', '진정 시행 전 확인할 것은?'),
    ('P06', 'preparation', '진정 전 체크사항 알려줘'),
    ('P07', 'cautions', '진정 시 주의할 점은?'),
    ('P08', 'cautions', '진정간호에서 조심해야 할 것은?'),
    ('P09', 'cautions', '진정 중 안전하게 봐야 할 항목은?'),
)
EXPECTED = {
    'Q001': True,
    'Q002': True,
    'Q003': True,
    'Q004': True,
    'Q005': True,
    'Q006': False,
}
LEGACY = {
    'Q001': {'answerable': True, 'reason': 'supported'},
    'Q002': {'answerable': True, 'reason': 'supported'},
    'Q003': {'answerable': False, 'reason': 'incomplete_semantic_block'},
    'Q004': {'answerable': False, 'reason': 'incomplete_semantic_block'},
    'Q005': {'answerable': False, 'reason': 'no_topic_evidence'},
    'Q006': {'answerable': False, 'reason': 'domain_or_clarification'},
}


def _top_rows(positions, chunks, scores, *, tiers=None, limit=10):
    tiers = tiers or ('inactive',) * len(positions)
    return [
        {
            'rank': rank,
            'chunk_id': chunks[position].id,
            'chunk_index': chunks[position].index,
            'score': round(float(scores[position]), 6),
            'temporal_tier': tier,
            'section': chunks[position].section,
            'text': chunks[position].text,
        }
        for rank, (position, tier) in enumerate(
            zip(positions[:limit], tiers[:limit]), start=1,
        )
    ]


def retrieve_funnel(question, model, metadata, chunks, vectors):
    plan = plan_query(question, documents=[metadata])
    query_text = bounded_embedding_question(plan.expanded, model)
    query_vector = model.encode([query_text])[0]
    dense_scores = np.dot(vectors, query_vector)
    dense_positions = _stable_positions(dense_scores)
    bm25_scores = BM25Index(chunks).scores(plan.expanded)
    bm25_ranking = rank_bm25_candidates(plan.original, chunks, bm25_scores, limit=40)
    bm25_positions = [
        position for position in bm25_ranking.positions if bm25_scores[position] > 0
    ]
    fusion_scores = rrf(dense_positions, bm25_positions)
    candidate_positions = sorted(set(dense_positions) | set(bm25_positions))
    candidates = [
        Hit(
            chunks[position],
            float(dense_scores[position]),
            bm25_score=float(bm25_scores[position]),
            fusion_score=float(fusion_scores[position]),
        )
        for position in candidate_positions
    ]
    seeds = rerank(plan, candidates, .38)
    hits = expand_context(question, seeds, chunks, plan.max_hits, plan=plan)
    before = assess_evidence(plan, hits)
    prompt_trace = {}
    selected, catalog, contract = [], (), None
    after = None
    if before.sufficient:
        _, selected, catalog, contract = prompt_messages(
            plan.query,
            before.hits,
            14000,
            token_budget=GROQ_REQUEST_TOKEN_BUDGET,
            plan=plan,
            groups=before.groups,
            trace=prompt_trace,
            return_catalog=True,
            return_contract=True,
        )
        branch_by_chunk = {
            hit.chunk.id: group.branch
            for group in before.groups
            for hit in group.hits
        }
        after = assess_evidence(
            plan, selected, branch_by_chunk=branch_by_chunk,
        )
    fused_positions = sorted(candidate_positions, key=lambda position: -fusion_scores[position])
    return {
        'question': question,
        'plan': asdict(plan),
        'query_analysis': {
            'terms': terms(plan.query),
            'topic_words': topic_words(plan),
            'embedding_question': embedding_question(plan.expanded),
            'lexical_tokens': lexical_tokens(plan.expanded),
            'requested_temporal_phase': bm25_ranking.requested_phase,
        },
        'bm25_top': _top_rows(
            list(bm25_ranking.positions), chunks, bm25_scores,
            tiers=bm25_ranking.tiers,
        ),
        'semantic_top': _top_rows(dense_positions, chunks, dense_scores),
        'rrf_top': _top_rows(
            fused_positions,
            chunks,
            [fusion_scores[position] for position in range(len(chunks))],
        ),
        'rerank_seeds': [
            {
                'rank': rank,
                'chunk_id': hit.chunk.id,
                'chunk_index': hit.chunk.index,
                'score': round(hit.rerank_score, 6),
                'section': hit.chunk.section,
                'text': hit.chunk.text,
            }
            for rank, hit in enumerate(seeds, start=1)
        ],
        'context_hits': [
            {
                'chunk_id': hit.chunk.id,
                'chunk_index': hit.chunk.index,
                'parent_id': hit.chunk.parent_id,
                'context_only': hit.context_only,
                'context_complete': hit.context_complete,
                'section': hit.chunk.section,
                'text': hit.chunk.text,
            }
            for hit in hits
        ],
        'pre_budget': _assessment(before),
        'selected_evidence': [hit.chunk.id for hit in selected],
        'post_budget': _assessment(after) if after else None,
        'selectable_source_units': sum(unit.selectable for unit in catalog),
        'selection_slots': len(contract.slots) if contract else 0,
        'budget': {
            'reservation': prompt_trace.get('estimated_request_tokens'),
            'cap': prompt_trace.get('request_token_budget'),
            'headroom': prompt_trace.get('request_token_headroom'),
            'minimum_headroom': prompt_trace.get('minimum_request_token_headroom'),
        },
        '_runtime': {
            'plan': plan,
            'hits': hits,
            'before': before,
            'catalog': catalog,
            'contract': contract,
        },
    }


def _assessment(value):
    return {
        'sufficient': value.sufficient,
        'reason': value.reason,
        'hit_count': len(value.hits),
        'groups': [
            {
                'key': group.key,
                'branch': group.branch,
                'source_start': group.source_start,
                'source_end': group.source_end,
                'complete': group.complete,
                'required': group.required,
                'chunk_ids': [hit.chunk.id for hit in group.hits],
            }
            for group in value.groups
        ],
    }


def _group_response(contract):
    return json.dumps({
        'group_selections': {
            slot.prompt_group_id: (
                [slot.selectable_source_unit_ids[0]] if slot.required else []
            )
            for slot in contract.slots
        },
    }, ensure_ascii=False)


def _mock_generate(question, runtime, *, content=None):
    plan = runtime['plan']
    contract = runtime['contract']
    if content is None:
        if contract is None:
            # Evidence gate에서 차단되는 baseline도 동일 generate() 경로로 zero-call 검증한다.
            content = '{}'
        elif isinstance(contract, FacetSlotSelectionContract):
            raise RuntimeError('facet response is required')
        else:
            content = _group_response(contract)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={
            'model': 'openai/gpt-oss-20b',
            'choices': [{
                'finish_reason': 'stop',
                'message': {'content': content, 'refusal': None},
            }],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120},
        })

    trace = {}
    answer, used = generate(
        settings(),
        question,
        runtime['hits'],
        'pilot-query-generalization',
        quota=MockQuota(),
        transport=httpx.MockTransport(handle),
        plan=plan,
        trace=trace,
    )
    statement_count = len(answer.statements)
    return {
        'transport_calls': len(calls),
        'answerable': answer.answerable,
        'selected_evidence_count': len(used),
        'verified_statement_count': statement_count,
        'citation_coverage': (
            sum(bool(statement.evidence) for statement in answer.statements) / statement_count
            if statement_count else 0.0
        ),
        'citation_assessment': trace.get('citation_assessment'),
        'response_parse_stage': trace.get('response_parse_stage'),
        'validation_reason': trace.get('validation_reason'),
        'block_reason': trace.get('block_reason'),
    }


def build_report():
    model, metadata, chunks, vectors = pilot_corpus()
    funnels = {}
    for index, question in enumerate(DEFAULT_QUESTIONS, start=1):
        case_id = f'Q{index:03d}'
        funnels[case_id] = retrieve_funnel(question, model, metadata, chunks, vectors)

    q002 = q002_inputs()
    q002_content = facet_response(q002[7], q002[9])
    mocks = {}
    for case_id in ('Q001', 'Q003', 'Q004', 'Q005'):
        mocks[case_id] = _mock_generate(
            funnels[case_id]['question'], funnels[case_id]['_runtime'],
        )
    q002_runtime = {
        'plan': q002[2],
        'hits': q002[1],
        'before': q002[3],
        'catalog': q002[6],
        'contract': q002[7],
    }
    mocks['Q002'] = _mock_generate(q002[2].query, q002_runtime, content=q002_content)
    q006_calls = []
    q006_runtime = funnels['Q006']['_runtime']
    q006_answer, _ = generate(
        settings(),
        funnels['Q006']['question'],
        q006_runtime['hits'],
        'pilot-q006',
        quota=MockQuota(),
        transport=httpx.MockTransport(lambda request: q006_calls.append(request)),
        plan=q006_runtime['plan'],
    )
    mocks['Q006'] = {
        'transport_calls': len(q006_calls),
        'answerable': q006_answer.answerable,
        'verified_statement_count': len(q006_answer.statements),
        'citation_coverage': 0.0,
    }

    paraphrases = []
    for case_id, expected_kind, question in PARAPHRASES:
        funnel = retrieve_funnel(question, model, metadata, chunks, vectors)
        funnel.pop('_runtime')
        paraphrases.append({
            'case_id': case_id,
            'question': question,
            'expected_kind': expected_kind,
            'actual_kind': funnel['plan']['kind'],
            'topic_words': funnel['query_analysis']['topic_words'],
            'pre_budget': funnel['pre_budget']['reason'],
            'post_budget': (funnel['post_budget'] or {}).get('reason'),
            'reservation': funnel['budget']['reservation'],
            'headroom': funnel['budget']['headroom'],
            'pass': (
                funnel['plan']['kind'] == expected_kind
                and funnel['pre_budget']['sufficient']
                and bool(funnel['post_budget'] and funnel['post_budget']['sufficient'])
                and funnel['budget']['headroom'] >= funnel['budget']['minimum_headroom']
            ),
        })

    for funnel in funnels.values():
        funnel.pop('_runtime')
    cases = []
    for case_id, funnel in funnels.items():
        actual = mocks[case_id]['answerable']
        cases.append({
            'case_id': case_id,
            'question': funnel['question'],
            'expected_answerable': EXPECTED[case_id],
            'legacy': LEGACY[case_id],
            'current_pre_budget': funnel['pre_budget']['reason'],
            'current_post_budget': (funnel['post_budget'] or {}).get('reason'),
            'mock': mocks[case_id],
            'pass': actual is EXPECTED[case_id]
                and (mocks[case_id]['transport_calls'] == 0 if case_id == 'Q006' else True),
        })
    return {
        'schema_version': 1,
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'scope': 'offline local retrieval and MockTransport only; actual Groq calls 0',
        'actual_groq_calls': 0,
        'expected_cases': EXPECTED,
        'cases': cases,
        'funnels': funnels,
        'paraphrases': paraphrases,
        'all_expected_passed': all(case['pass'] for case in cases),
        'all_paraphrases_passed': all(case['pass'] for case in paraphrases),
        'q006_zero_call': mocks['Q006']['transport_calls'] == 0,
        'minimum_headroom_passed': all(
            not funnel['budget']['reservation']
            or funnel['budget']['headroom'] >= max(
                256, math.ceil(funnel['budget']['reservation'] * .08),
            )
            for funnel in funnels.values()
        ),
    }


def _rows(items, score='score'):
    return ''.join(
        '<tr>'
        f'<td>{item["rank"]}</td><td>{item["chunk_index"]}</td>'
        f'<td>{escape(item["chunk_id"])}</td><td>{item.get(score, "-")}</td>'
        f'<td>{escape(str(item.get("temporal_tier", "-")))}</td>'
        f'<td>{escape(item.get("section") or "-")}</td>'
        f'<td><pre>{escape(item.get("text") or "")}</pre></td>'
        '</tr>'
        for item in items
    )


def write_review(output, report):
    case_rows = ''.join(
        '<tr>'
        f'<td>{case["case_id"]}</td><td>{escape(case["question"])}</td>'
        f'<td>{case["expected_answerable"]}</td><td>{case["legacy"]["reason"]}</td>'
        f'<td>{case["current_pre_budget"]}</td><td>{case["current_post_budget"] or "-"}</td>'
        f'<td>{case["mock"]["answerable"]}</td><td>{case["mock"]["transport_calls"]}</td>'
        f'<td class="{"pass" if case["pass"] else "fail"}">{"PASS" if case["pass"] else "FAIL"}</td>'
        '</tr>'
        for case in report['cases']
    )
    cards = []
    for case_id in ('Q003', 'Q004', 'Q005'):
        funnel = report['funnels'][case_id]
        plan = funnel['plan']
        groups = ''.join(
            '<li>'
            f'{escape(group["key"])} · {group["branch"]} · '
            f'{group["source_start"]}~{group["source_end"]} · complete={group["complete"]}'
            '</li>'
            for group in funnel['pre_budget']['groups']
        ) or '<li>없음</li>'
        cards.append(f'''
<section><h2>{case_id} · {escape(funnel['question'])}</h2>
<p>kind={plan['kind']} · domain={plan['domain']} · focus={escape(str(plan['focus']))}</p>
<p>topic={escape(str(funnel['query_analysis']['topic_words']))} · temporal={funnel['query_analysis']['requested_temporal_phase']}</p>
<p>pre={funnel['pre_budget']['reason']} · post={(funnel['post_budget'] or {}).get('reason', '-')} · selected chunks={len(funnel['selected_evidence'])}</p>
<h3>Evidence groups</h3><ul>{groups}</ul>
<details><summary>BM25 Top 10</summary><table><tr><th>rank</th><th>index</th><th>chunk</th><th>score</th><th>tier</th><th>section</th><th>text</th></tr>{_rows(funnel['bm25_top'])}</table></details>
<details><summary>Semantic Top 10</summary><table><tr><th>rank</th><th>index</th><th>chunk</th><th>score</th><th>tier</th><th>section</th><th>text</th></tr>{_rows(funnel['semantic_top'])}</table></details>
<details><summary>RRF Top 10</summary><table><tr><th>rank</th><th>index</th><th>chunk</th><th>score</th><th>tier</th><th>section</th><th>text</th></tr>{_rows(funnel['rrf_top'])}</table></details>
<details open><summary>Rerank seeds</summary><table><tr><th>rank</th><th>index</th><th>chunk</th><th>score</th><th>tier</th><th>section</th><th>text</th></tr>{_rows(funnel['rerank_seeds'])}</table></details>
</section>''')
    variation_rows = ''.join(
        '<tr>'
        f'<td>{case["case_id"]}</td><td>{escape(case["question"])}</td>'
        f'<td>{case["actual_kind"]}</td><td>{escape(str(case["topic_words"]))}</td>'
        f'<td>{case["pre_budget"]}</td><td>{case["post_budget"]}</td>'
        f'<td>{case["reservation"]}</td><td>{case["headroom"]}</td>'
        f'<td class="{"pass" if case["pass"] else "fail"}">{"PASS" if case["pass"] else "FAIL"}</td>'
        '</tr>'
        for case in report['paraphrases']
    )
    html = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>RAG Pilot Query Generalization</title>
<style>body{{font:14px system-ui;margin:24px;line-height:1.5}}table{{border-collapse:collapse;width:100%;margin:12px 0}}th,td{{border:1px solid #ccc;padding:6px;vertical-align:top}}pre{{white-space:pre-wrap;margin:0;max-width:760px}}section{{border-top:3px solid #333;margin-top:30px;padding-top:12px}}.pass{{color:#08752f;font-weight:700}}.fail{{color:#b00020;font-weight:700}}details{{margin:12px 0}}</style></head><body>
<h1>Q001~Q006 Pilot Query Generalization 무호출 검수</h1>
<p>실제 Groq 호출 0회. 로컬 승인 corpus와 MockTransport만 사용했다.</p>
<table><tr><th>Case</th><th>질문</th><th>기대</th><th>기존</th><th>현재 pre</th><th>현재 post</th><th>Mock answerable</th><th>Mock calls</th><th>판정</th></tr>{case_rows}</table>
<h2>표현 변형</h2><table><tr><th>ID</th><th>질문</th><th>intent</th><th>topic</th><th>pre</th><th>post</th><th>reservation</th><th>headroom</th><th>판정</th></tr>{variation_rows}</table>
{''.join(cards)}
</body></html>'''
    (output / 'review.html').write_text(html, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f'기존 artifacts를 덮어쓰지 않습니다: {args.output}')
    args.output.mkdir(parents=True)
    report = build_report()
    (args.output / 'pilot_query_generalization_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    write_review(args.output, report)
    print(json.dumps({
        'output': str(args.output),
        'actual_groq_calls': report['actual_groq_calls'],
        'all_expected_passed': report['all_expected_passed'],
        'all_paraphrases_passed': report['all_paraphrases_passed'],
        'q006_zero_call': report['q006_zero_call'],
    }, ensure_ascii=True))


if __name__ == '__main__':
    main()
