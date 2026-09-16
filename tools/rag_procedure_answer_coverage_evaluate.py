"""Broad procedure AnswerCoverage를 외부 LLM 호출 없이 검증하고 안전한 보고서를 생성합니다."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from collections import Counter
from dataclasses import replace
from datetime import datetime
from html import escape
from pathlib import Path

import httpx

from mvp.ai import (
    AI_VERSION,
    GROQ_REQUEST_TOKEN_BUDGET,
    PROMPT_EVIDENCE_SCHEMA_VERSION,
    RESPONSE_SELECTION_SCHEMA_VERSION,
    answer_text,
    build_selection_contract,
    generate,
    prompt_messages,
    validate_source_unit_selection,
)
from mvp.evidence import (
    answer_coverage,
    assess_evidence,
    build_source_unit_catalog,
    evidence_groups,
    is_broad_procedure,
    procedure_answer_requirement,
    procedure_coverage,
)
from mvp.library import NO_GUIDELINE, Chunk, Hit
from mvp.query import plan_query
from mvp.settings import ROOT, GuideError, Settings
from tools.rag_phase1_evaluate import stage_recall
from tools.rag_phase2_evaluate import _load_q002

DEFAULT_OUTPUT = ROOT / 'artifacts' / '2026-09-14_rag-procedure-answer-coverage'


class MockQuota:
    def __init__(self):
        self.reservations = []

    def reserve(self, settings, user_id, tokens):
        self.reservations.append(tokens)
        return 'procedure-coverage-mock'

    def settle(self, identifier, usage):
        pass

    def cancel(self, identifier):
        pass


class RejectTransport(httpx.BaseTransport):
    def __init__(self):
        self.calls = 0

    def handle_request(self, request):
        self.calls += 1
        raise RuntimeError('Q006 transport must remain unused')


def settings():
    return Settings(
        llm_provider='groq_free',
        llm_key='fixture',
        llm_model='openai/gpt-oss-20b',
        llm_approved=True,
        groq_free_confirmed=True,
    )


def response(contract, identifiers):
    selections = {slot.prompt_group_id: [] for slot in contract.slots}
    slot_by_id = {
        identifier: slot.prompt_group_id
        for slot in contract.slots
        for identifier in slot.selectable_source_unit_ids
    }
    for identifier in identifiers:
        selections[slot_by_id.get(identifier, contract.slots[0].prompt_group_id)].append(
            identifier
        )
    return json.dumps({'group_selections': selections}, ensure_ascii=False)


def q002_inputs():
    metadata, gold, hits = _load_q002()
    plan = plan_query(gold['question'], documents=[metadata])
    before = assess_evidence(plan, hits)
    prompt_trace = {}
    _, selected, catalog = prompt_messages(
        plan.query,
        before.hits,
        14000,
        token_budget=GROQ_REQUEST_TOKEN_BUDGET,
        plan=plan,
        groups=before.groups,
        trace=prompt_trace,
        return_catalog=True,
    )
    after = assess_evidence(plan, selected)
    catalog = build_source_unit_catalog(after.groups)
    contract = build_selection_contract(after.groups, catalog)
    by_source = {(unit.chunk_id, unit.source_order[1]): unit for unit in catalog}
    required_stages = [stage for stage in gold['stages'] if stage['importance'] == 'required']
    gold_ids = []
    for stage in required_stages:
        for item in stage['source_unit_requirements'][0]['all_of']:
            unit = by_source[(item['chunk_id'], item['source_unit_position'])]
            if hashlib.sha256(unit.exact_text.encode()).hexdigest() != item['exact_text_sha256']:
                raise RuntimeError('Q002 gold source-unit fingerprint drift')
            if unit.source_unit_id not in gold_ids:
                gold_ids.append(unit.source_unit_id)
    return metadata, gold, hits, plan, before, selected, after, catalog, contract, required_stages, gold_ids, prompt_trace


def selection_reason(raw, catalog, contract, coverage, plan):
    trace = {}
    try:
        validate_source_unit_selection(raw, catalog, contract, coverage, plan, trace=trace)
    except GuideError:
        return trace.get('validation_reason')
    return 'PASS'


def build_offline_report(inputs):
    _, gold, _, plan, before, selected, after, catalog, contract, stages, gold_ids, prompt_trace = inputs
    requirement = procedure_answer_requirement(plan, after.procedure_coverage, catalog)
    by_id = {unit.source_unit_id: unit for unit in catalog}
    gold_units = tuple(by_id[identifier] for identifier in gold_ids)
    gold_coverage = answer_coverage(plan, after.procedure_coverage, catalog, gold_units)
    required_groups = [slot for slot in contract.slots if slot.required]
    selectable_by_group = {
        slot.group_key: tuple(
            unit for unit in catalog if unit.group_key == slot.group_key and unit.selectable
        )
        for slot in required_groups
    }
    reason_counts = Counter()
    combination_count = 0
    for units in itertools.product(*(
        selectable_by_group[slot.group_key] for slot in required_groups
    )):
        combination_count += 1
        reason_counts[selection_reason(
            response(contract, [unit.source_unit_id for unit in units]),
            catalog, contract, after.procedure_coverage, plan,
        )] += 1
    over_reason = selection_reason(
        response(contract, [unit.source_unit_id for unit in catalog if unit.selectable]),
        catalog, contract, after.procedure_coverage, plan,
    )
    gold_set = set(gold_ids)
    reservation = prompt_trace['estimated_request_tokens']
    minimum_headroom = max(256, math.ceil(reservation * .08))
    return {
        'schema_version': 1,
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'scope': 'offline only; no Groq or external LLM call',
        'query': gold['question'],
        'broad_procedure': is_broad_procedure(plan),
        'versions': {
            'ai': AI_VERSION,
            'prompt_evidence': PROMPT_EVIDENCE_SCHEMA_VERSION,
            'response_selection': RESPONSE_SELECTION_SCHEMA_VERSION,
        },
        'counts': {
            'selected_evidence_chunks': len(selected),
            'catalog_units': len(catalog),
            'selectable_units': sum(unit.selectable for unit in catalog),
            'gold_units': len(gold_ids),
            'required_groups': len(required_groups),
        },
        'retrieval': {
            'pre_gold_recall': stage_recall(gold, [hit.chunk.id for hit in before.hits]),
            'post_gold_recall': stage_recall(gold, [hit.chunk.id for hit in after.hits]),
        },
        'requirement': requirement.__dict__,
        'gold_answer_coverage': gold_coverage.__dict__,
        'facet_rows': [
            {
                'source_unit_id': unit.source_unit_id,
                'group_key': unit.group_key,
                'branch': unit.branch,
                'phase': unit.phase,
                'action_family': list(unit.action_families),
                'source_order': list(unit.source_order),
                'gold_evaluation_unit': unit.source_unit_id in gold_set,
            }
            for unit in catalog if unit.selectable
        ],
        'under_selection': {
            'strategy': 'complete Cartesian product of one selectable unit per required group',
            'combination_count': combination_count,
            'reason_counts': dict(reason_counts),
            'all_blocked': reason_counts.get('PASS', 0) == 0,
        },
        'over_selection': {
            'selected_count': sum(unit.selectable for unit in catalog),
            'reason': over_reason,
        },
        'budget': {
            'reservation': reservation,
            'limit': GROQ_REQUEST_TOKEN_BUDGET,
            'headroom': prompt_trace['request_token_headroom'],
            'minimum_headroom': minimum_headroom,
            'passed': (
                reservation <= GROQ_REQUEST_TOKEN_BUDGET
                and prompt_trace['request_token_headroom'] >= minimum_headroom
            ),
        },
        'gold_stage_recall': {
            'required_stage_count': len(stages),
            'recalled_stage_count': sum(
                any(all(by_source_key(item, catalog) in gold_set
                        for item in alternative['all_of'])
                    for alternative in stage['source_unit_requirements'])
                for stage in stages
            ),
        },
    }


def by_source_key(item, catalog):
    return next(
        unit.source_unit_id for unit in catalog
        if unit.chunk_id == item['chunk_id']
        and unit.source_order[1] == item['source_unit_position']
    )


def duplicate_chunk(identifier, document_id, text, index, parent):
    return Chunk(
        identifier, document_id, f'{document_id}.pdf', 1, '진정 지침', '진정 절차',
        None, text, index, parent_id=parent,
    )


def build_duplicate_report(inputs):
    _, _, _, plan, _, _, after, catalog, contract, _, gold_ids, _ = inputs
    exact = '진정 전 상태를 확인한다.'
    first = Hit(duplicate_chunk('a', 'doc', exact, 1, 'p1'), .9, bm25_score=1)
    same = Hit(duplicate_chunk('b', 'doc', exact, 2, 'p2'), .8, bm25_score=1)
    other_doc = Hit(duplicate_chunk('c', 'other', exact, 3, 'p3'), .8, bm25_score=1)
    semantic = Hit(duplicate_chunk('d', 'doc', '진정 전에 상태를 평가한다.', 4, 'p4'),
                   .8, bm25_score=1)
    adult = Hit(duplicate_chunk('adult', 'doc', f'작성 방법 [성인]\n{exact}', 5, 'pa'),
                .9, bm25_score=1)
    pediatric = Hit(duplicate_chunk('pediatric', 'doc', f'작성 방법 [소아]\n{exact}', 6, 'pp'),
                    .8, bm25_score=1)
    original = evidence_groups(plan, [adult, pediatric])
    branch_by_chunk = {
        hit.chunk.id: group.branch for group in original for hit in group.hits
    }
    cited = [
        Hit(replace(hit.chunk, text=exact, section=''), hit.similarity,
            bm25_score=hit.bm25_score)
        for hit in (adult, pediatric)
    ]
    cases = {
        'same_source_unit_id': selection_reason(
            response(contract, gold_ids + [gold_ids[0]]),
            catalog, contract, after.procedure_coverage, plan,
        ),
        'different_group_exact_same_document_branch': procedure_coverage(
            evidence_groups(plan, [first, same])
        ).duplicate_count,
        'different_document': procedure_coverage(
            evidence_groups(plan, [first, other_doc])
        ).duplicate_count,
        'different_branch': procedure_coverage(evidence_groups(
            plan, [first, same], branch_by_chunk={'a': 'adult', 'b': 'pediatric'}
        )).duplicate_count,
        'semantic_only_different_text': procedure_coverage(
            evidence_groups(plan, [first, semantic])
        ).duplicate_count,
        'heading_loss_without_metadata': procedure_coverage(
            evidence_groups(plan, cited)
        ).duplicate_count,
        'heading_loss_with_server_metadata': procedure_coverage(
            evidence_groups(plan, cited, branch_by_chunk=branch_by_chunk)
        ).duplicate_count,
    }
    return {
        'schema_version': 1,
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'scope': 'synthetic exact-signature fixtures; no external call',
        'signature': ['document_id', 'server_branch', 'clean(cited_text).casefold()'],
        'cases': cases,
        'passed': cases == {
            'same_source_unit_id': 'selection_duplicate_id',
            'different_group_exact_same_document_branch': 1,
            'different_document': 0,
            'different_branch': 0,
            'semantic_only_different_text': 0,
            'heading_loss_without_metadata': 1,
            'heading_loss_with_server_metadata': 0,
        },
    }


def build_mock_report(inputs):
    metadata, gold, hits, plan, before, selected, after, catalog, contract, stages, gold_ids, prompt_trace = inputs
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={
            'model': 'openai/gpt-oss-20b',
            'choices': [{
                'finish_reason': 'stop',
                'message': {'content': response(contract, gold_ids)},
            }],
            'usage': {'prompt_tokens': 1000, 'completion_tokens': 100, 'total_tokens': 1100},
        })

    trace = {}
    quota = MockQuota()
    answer, used_hits = generate(
        settings(), plan.query, hits, 'fixture', quota=quota,
        transport=httpx.MockTransport(handle), plan=plan, trace=trace,
    )
    reject = RejectTransport()
    q006_trace = {}
    q006, q006_hits = generate(
        settings(), '화성 우주선의 궤도 계산 공식은?', [], 'fixture',
        transport=reject, trace=q006_trace,
    )
    citations = [
        evidence for statement in answer.statements for evidence in statement.evidence
    ]
    exact = all(
        statement.text == statement.evidence[0].quote
        for statement in answer.statements
    )
    recalled = sum(
        any(all(by_source_key(item, catalog) in set(gold_ids)
                for item in alternative['all_of'])
            for alternative in stage['source_unit_requirements'])
        for stage in stages
    )
    unsupported = {
        name: 0 if answer.answerable else None
        for name in ('number', 'unit', 'condition', 'negation', 'action')
    }
    minimum_headroom = max(
        256, math.ceil(prompt_trace['estimated_request_tokens'] * .08)
    )
    passed = all((
        len(calls) == 1,
        len(used_hits) == len(selected) == 12,
        stage_recall(gold, [hit.chunk.id for hit in before.hits])['required']['recalled'] == 10,
        stage_recall(gold, [hit.chunk.id for hit in after.hits])['required']['recalled'] == 10,
        recalled == 10,
        len(gold_ids) == len(answer.statements) == 14,
        answer.answerable,
        exact,
        len(citations) == len(answer.statements),
        trace.get('citation_assessment') == 'supported',
        trace.get('answer_coverage', {}).get('complete') is True,
        trace.get('citation_branch_metadata') == 'server_evidence_group',
        reject.calls == 0,
        q006_hits == [],
        answer_text(q006) == NO_GUIDELINE,
        prompt_trace['request_token_headroom'] >= minimum_headroom,
    ))
    return {
        'schema_version': 1,
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'scope': 'MockTransport only; actual Groq calls 0',
        'q002': {
            'transport_calls': len(calls),
            'selected_evidence_chunks': len(used_hits),
            'pre_gold_recall': stage_recall(gold, [hit.chunk.id for hit in before.hits]),
            'post_gold_recall': stage_recall(gold, [hit.chunk.id for hit in after.hits]),
            'source_unit_gold_recall': recalled,
            'source_unit_gold_total': len(stages),
            'selected_source_units': trace.get('selected_source_unit_count'),
            'group_counts': trace.get('selected_group_counts'),
            'answer_coverage': trace.get('answer_coverage'),
            'statements': len(answer.statements),
            'exact_text_quote': exact,
            'citation_coverage': len(citations) / len(answer.statements),
            'citation_assessment': trace.get('citation_assessment'),
            'duplicate_evidence': trace.get('citation_assessment') == 'duplicate_evidence',
            'citation_branch_metadata': trace.get('citation_branch_metadata'),
            'source_order_reversal': 0 if answer.answerable else None,
            'unsupported': unsupported,
            'server_answerable': answer.answerable,
            'reservation': quota.reservations[0] if quota.reservations else None,
            'headroom': prompt_trace['request_token_headroom'],
            'minimum_headroom': minimum_headroom,
        },
        'q006': {
            'catalog_units': 0,
            'transport_calls': reject.calls,
            'llm_called': q006_trace.get('llm_called'),
            'fixed_abstention': answer_text(q006),
        },
        'passed': passed,
    }


def write_reports(output, offline, duplicate, mock):
    output.mkdir(parents=True, exist_ok=False)
    for name, report in (
        ('offline_facet_report.json', offline),
        ('duplicate_contract_report.json', duplicate),
        ('mock_report.json', mock),
    ):
        (output / name).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
        )
    reason_rows = ''.join(
        f'<tr><td>{escape(reason)}</td><td>{count}</td></tr>'
        for reason, count in offline['under_selection']['reason_counts'].items()
    )
    phase_rows = ''.join(
        f'<tr><td>{escape(branch)}</td><td>{escape(phase)}</td></tr>'
        for branch, phase in offline['requirement']['required_phase_slots']
    )
    duplicate_rows = ''.join(
        f'<tr><td>{escape(name)}</td><td>{escape(str(value))}</td></tr>'
        for name, value in duplicate['cases'].items()
    )
    (output / 'review.html').write_text(f'''<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><title>SCHAT Procedure AnswerCoverage 검수</title><style>
body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 18px;color:#17202a}}
table{{border-collapse:collapse;width:100%;margin:12px 0 28px}}th,td{{border:1px solid #ccd1d1;padding:8px;text-align:left}}
th{{background:#eef3f6}}.pass{{color:#196f3d;font-weight:700}}
</style></head><body><h1>Broad procedure AnswerCoverage 오프라인 검수</h1>
<p>생성: {escape(offline['generated_at'])}</p>
<ul><li>evidence chunk: {offline['counts']['selected_evidence_chunks']}</li>
<li>selectable/gold: {offline['counts']['selectable_units']} / {offline['counts']['gold_units']}</li>
<li>capacity witness: {offline['requirement']['capacity_witness_size']}</li>
<li>budget reservation/headroom: {offline['budget']['reservation']} / {offline['budget']['headroom']}</li></ul>
<h2>Required phase</h2><table><tr><th>branch</th><th>phase</th></tr>{phase_rows}</table>
<h2>1-per-group 72조합</h2><table><tr><th>reason</th><th>count</th></tr>{reason_rows}</table>
<h2>Duplicate contract</h2><table><tr><th>case</th><th>result</th></tr>{duplicate_rows}</table>
<h2>Mock</h2><p class="pass">PASS: {mock['passed']} · Q002 statements: {mock['q002']['statements']} · citation: {mock['q002']['citation_coverage'] * 100:.1f}% · Q006 calls: {mock['q006']['transport_calls']}</p>
</body></html>''', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    inputs = q002_inputs()
    offline = build_offline_report(inputs)
    duplicate = build_duplicate_report(inputs)
    mock = build_mock_report(inputs)
    if not (
        offline['gold_answer_coverage']['complete']
        and offline['under_selection']['all_blocked']
        and offline['over_selection']['reason'] == 'selection_limit'
        and offline['budget']['passed']
        and duplicate['passed']
        and mock['passed']
    ):
        raise RuntimeError('procedure AnswerCoverage offline acceptance failed')
    write_reports(args.output, offline, duplicate, mock)
    print(json.dumps({
        'output': str(args.output),
        'offline_passed': offline['gold_answer_coverage']['complete'],
        'under_selection': offline['under_selection'],
        'duplicate_passed': duplicate['passed'],
        'mock_passed': mock['passed'],
        'actual_groq_calls': 0,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
