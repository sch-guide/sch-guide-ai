"""실제 Groq 없이 compact evidence prompt와 Q002 budget gate를 평가합니다."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from datetime import datetime
from functools import lru_cache
from html import escape

import httpx

from src.ai import (
    GROQ_REQUEST_TOKEN_BUDGET,
    OUTPUT_LIMIT,
    PROMPT_EVIDENCE_SCHEMA_VERSION,
    SYSTEM,
    FacetSlotSelectionContract,
    answer_text,
    estimated_request_tokens,
    evidence_group_token_rows,
    generate,
    prompt_messages,
)
from src.evidence import (
    PROCEDURE_ACTION,
    assess_evidence,
    build_source_unit_catalog,
    required_coverage_loss,
    source_sentences,
)
from src.library import NO_GUIDELINE
from src.query import plan_query
from src.settings import ROOT, Settings
from tools.rag_phase1_evaluate import stage_recall
from tools.rag_phase2_evaluate import _load_q002

DEFAULT_OUTPUT = ROOT / 'workspace' / 'RAG_실험' / '2026-09-13_rag-output-limit-mock'


class MockQuota:
    def __init__(self):
        self.reservations = []
        self.settlements = []

    def reserve(self, settings, user_id, tokens):
        self.reservations.append((user_id, tokens))
        return 'mock-reservation'

    def settle(self, identifier, usage):
        self.settlements.append((identifier, usage))

    def cancel(self, identifier):
        pass


def mock_settings():
    return Settings(
        llm_provider='groq_free', llm_key='fixture', llm_model='openai/gpt-oss-20b',
        llm_approved=True, groq_free_confirmed=True,
    )


def exact_group_statements(groups):
    statements = []
    for group in groups:
        found = None
        for hit in group.hits:
            for sentence in source_sentences(hit.chunk.text):
                if re.search(PROCEDURE_ACTION, sentence):
                    found = {
                        'text': sentence,
                        'evidence': [{'chunk_id': hit.chunk.id, 'quote': sentence}],
                    }
                    break
            if found:
                break
        if found:
            found['label'] = ''
            statements.append(found)
    return statements


def parent_partial_count(groups, selected_ids):
    count = 0
    for group in groups:
        identifiers = {hit.chunk.id for hit in group.hits}
        included = identifiers & selected_ids
        if included and included != identifiers:
            count += 1
    return count


def compact_sources(messages):
    content = messages[1]['content'].split('Evidence groups (JSON):\n', 1)[1]
    envelope = json.loads(content)
    if 'source_units' in envelope:
        return envelope, {
            unit['id']: unit['text'] for unit in envelope['source_units']
        }
    return envelope, {
        source['chunk_id']: [unit['text'] for unit in source['units']]
        for group in envelope['groups']
        for source in group['sources']
    }


def facet_response(contract, identifiers):
    target = tuple(identifiers)

    @lru_cache(maxsize=None)
    def visit(slot_index, introduced):
        if slot_index == len(contract.slots):
            return () if introduced == len(target) else None
        allowed = set(contract.slots[slot_index].eligible_source_unit_ids)
        choices = []
        if introduced < len(target) and target[introduced] in allowed:
            choices.append(target[introduced])
        choices.extend(identifier for identifier in target[:introduced] if identifier in allowed)
        for identifier in choices:
            next_introduced = introduced + int(
                introduced < len(target) and identifier == target[introduced]
            )
            tail = visit(slot_index + 1, next_introduced)
            if tail is not None:
                return (identifier,) + tail
        return None

    chosen = visit(0, 0)
    if chosen is None:
        raise RuntimeError('Q002 gold IDs cannot satisfy facet contract in source order')
    return json.dumps({
        'facet_selections': {
            slot.prompt_facet_id: identifier
            for slot, identifier in zip(contract.slots, chosen)
        },
    }, ensure_ascii=False)


def build_report():
    metadata, gold, hits = _load_q002()
    plan = plan_query(gold['question'], documents=[metadata])
    before = assess_evidence(plan, hits)
    trace = {}
    messages, selected, prompt_catalog, selection_contract = prompt_messages(
        plan.query, before.hits, 14000, token_budget=GROQ_REQUEST_TOKEN_BUDGET,
        plan=plan, groups=before.groups, trace=trace,
        return_catalog=True, return_contract=True,
    )
    after = assess_evidence(plan, selected)
    catalog = build_source_unit_catalog(after.groups)
    by_source = {(unit.chunk_id, unit.source_order[1]): unit for unit in catalog}
    gold_ids = {
        by_source[(item['chunk_id'], item['source_unit_position'])].source_unit_id
        for stage in gold['stages'] if stage['importance'] == 'required'
        for requirement in stage['source_unit_requirements']
        for item in requirement['all_of']
    }
    selected_ids = {hit.chunk.id for hit in selected}
    envelope, serialized = compact_sources(messages)
    if isinstance(selection_contract, FacetSlotSelectionContract):
        original = {
            unit.source_unit_id: unit.exact_text
            for unit in prompt_catalog
            if unit.source_unit_id in serialized
        }
    else:
        original = {hit.chunk.id: source_sentences(hit.chunk.text) for hit in before.hits}
    reserved = estimated_request_tokens(messages, 'groq_free', selection_contract)
    required_headroom = max(256, math.ceil(reserved * .08))

    calls = []
    def handle(request):
        calls.append(request)
        request_payload = json.loads(request.content)
        request_messages = request_payload['messages']
        assert request_payload['max_completion_tokens'] == OUTPUT_LIMIT
        assert request_payload['response_format']['type'] == 'json_schema'
        assert request_payload['response_format']['json_schema']['strict'] is True
        request_envelope, request_sources = compact_sources(request_messages)
        assert request_envelope['schema_version'] == PROMPT_EVIDENCE_SCHEMA_VERSION
        assert request_sources == original
        if isinstance(selection_contract, FacetSlotSelectionContract):
            content = facet_response(selection_contract, sorted(
                gold_ids,
                key=lambda identifier: next(
                    unit.source_order for unit in prompt_catalog
                    if unit.source_unit_id == identifier
                ),
            ))
        else:
            group_selections = {}
            for group in request_envelope['groups']:
                group_selections[group['group_id']] = [
                    unit['id']
                    for source in group['sources']
                    for unit in source['units']
                    if unit['id'] in gold_ids
                ]
            content = json.dumps({
                'group_selections': group_selections,
            }, ensure_ascii=False)
        return httpx.Response(200, json={
            'model': 'openai/gpt-oss-20b',
            'choices': [{
                'finish_reason': 'stop',
                'message': {'content': content, 'refusal': None},
            }],
            'usage': {
                'prompt_tokens': reserved - 128,
                'completion_tokens': 128,
                'total_tokens': reserved,
            },
        })

    generate_trace = {}
    quota = MockQuota()
    answer, generated_hits = generate(
        mock_settings(), plan.query, hits, 'fixture', quota=quota,
        transport=httpx.MockTransport(handle), plan=plan, trace=generate_trace,
    )

    q006_plan = plan_query('화성 우주선의 궤도 계산 공식은?', documents=[metadata])
    q006_calls = []
    q006_trace = {}
    q006_answer, _ = generate(
        Settings(), q006_plan.query, [], 'fixture', plan=q006_plan, trace=q006_trace,
        transport=httpx.MockTransport(lambda request: q006_calls.append(request)),
    )

    pre_ids = [hit.chunk.id for hit in before.hits]
    post_ids = [hit.chunk.id for hit in after.hits]
    before_coverage = before.procedure_coverage
    after_coverage = after.procedure_coverage
    required_groups = set(before_coverage.required_group_keys)
    post_required_groups = set(after_coverage.required_group_keys)
    selected_branches = sorted({
        group.branch
        for group in before.groups
        if any(hit.chunk.id in selected_ids for hit in group.hits)
    })
    token_rows = evidence_group_token_rows(before.groups)
    group_by_key = {group.key: group for group in before.groups}
    for row in token_rows:
        group = group_by_key[row['group_key']]
        row.update(
            required=group.required,
            requirement_reason=group.requirement_reason,
            branch=group.branch,
            chunk_ids=[hit.chunk.id for hit in group.hits],
        )

    return {
        'schema_version': 1,
        'prompt_schema_version': PROMPT_EVIDENCE_SCHEMA_VERSION,
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'scope': 'compact evidence serialization and MockTransport only; no external LLM call',
        'system_prompt_sha256': hashlib.sha256(SYSTEM.encode()).hexdigest(),
        'budget': {
            'output_limit': OUTPUT_LIMIT,
            'request_token_budget': GROQ_REQUEST_TOKEN_BUDGET,
            'estimated_request_tokens': reserved,
            'headroom': GROQ_REQUEST_TOKEN_BUDGET - reserved,
            'required_headroom': required_headroom,
            'headroom_passed': GROQ_REQUEST_TOKEN_BUDGET - reserved >= required_headroom,
        },
        'q002': {
            'question': gold['question'],
            'pre_assessment': before.reason,
            'post_assessment': after.reason,
            'pre_gold_recall': stage_recall(gold, pre_ids),
            'post_gold_recall': stage_recall(gold, post_ids),
            'required_group_count': len(required_groups),
            'retained_required_group_count': len(required_groups & post_required_groups),
            'required_groups_retained': required_groups.issubset(post_required_groups),
            'coverage_loss': required_coverage_loss(before_coverage, after_coverage),
            'required_branches_before': list(before_coverage.required_branches),
            'required_branches_after': list(after_coverage.required_branches),
            'selected_branches': selected_branches,
            'parent_partial_inclusion_count': parent_partial_count(before.groups, selected_ids),
            'source_ordered': after_coverage.source_ordered,
            'duplicate_count': after_coverage.duplicate_count,
            'selected_chunk_ids': [hit.chunk.id for hit in selected],
            'serialized_source_count': len(serialized),
            'source_text_exactly_preserved': serialized == original,
            'quota_reserved_tokens': quota.reservations[0][1],
            'mock_http_calls': len(calls),
            'answerable': answer.answerable,
            'statement_count': len(answer.statements),
            'exact_citation_passed': answer.answerable and generate_trace.get('citation_assessment') == 'supported',
            'generated_hit_ids': [hit.chunk.id for hit in generated_hits],
            'trace': generate_trace,
        },
        'group_tokens': token_rows,
        'prompt_envelope': envelope,
        'q006': {
            'question': q006_plan.original,
            'result': answer_text(q006_answer),
            'mock_http_calls': len(q006_calls),
            'trace': q006_trace,
        },
        'no_guideline_message': NO_GUIDELINE,
    }


def write_csv(path, report):
    fields = (
        'group_key', 'required', 'requirement_reason', 'branch', 'chunk_ids',
        'source_tokens', 'metadata_tokens', 'serialized_tokens',
    )
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report['group_tokens']:
            writer.writerow({**row, 'chunk_ids': '|'.join(row['chunk_ids'])})


def write_html(path, report):
    budget = report['budget']
    q002 = report['q002']
    rows = ''.join(
        '<tr>'
        f"<td>{escape(row['branch'])}</td>"
        f"<td>{escape(', '.join(item.rsplit('-', 1)[-1] for item in row['chunk_ids']))}</td>"
        f"<td>{row['source_tokens']}</td><td>{row['metadata_tokens']}</td>"
        f"<td>{row['serialized_tokens']}</td>"
        '</tr>'
        for row in report['group_tokens']
    )
    pre = q002['pre_gold_recall']['required']
    post = q002['post_gold_recall']['required']
    path.write_text(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>SCHAT Q002 Prompt Budget 검수</title><style>
body{{font-family:system-ui,sans-serif;max-width:1150px;margin:24px auto;padding:0 16px;color:#17202a}}
table{{border-collapse:collapse;width:100%;margin:18px 0 34px}}th,td{{border:1px solid #ccd1d1;padding:8px;text-align:left}}
th{{background:#eef3f6}}.pass{{color:#196f3d;font-weight:700}}code{{white-space:nowrap}}
</style></head><body><h1>Q002 compact evidence / budget 검수</h1>
<p>실제 Groq 호출 없음 · MockTransport만 사용 · 생성 {escape(report['generated_at'])}</p>
<p class="pass">예약 {budget['estimated_request_tokens']} / budget {budget['request_token_budget']} · headroom {budget['headroom']} · 요구 {budget['required_headroom']} · 통과 {budget['headroom_passed']}</p>
<h2>필수 근거 보존</h2>
<ul><li>pre-budget gold: {pre['recalled']}/{pre['total']}</li>
<li>post-budget gold: {post['recalled']}/{post['total']}</li>
<li>required groups: {q002['retained_required_group_count']}/{q002['required_group_count']}</li>
<li>parent partial inclusion: {q002['parent_partial_inclusion_count']}</li>
<li>source order: {q002['source_ordered']}</li>
<li>원문 정확 보존: {q002['source_text_exactly_preserved']}</li></ul>
<h2>Group별 token</h2><table><thead><tr><th>branch</th><th>chunks</th><th>원문</th><th>metadata</th><th>전체</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Mock 결과</h2><ul>
<li>Q002 HTTP 호출: {q002['mock_http_calls']}회</li>
<li>Q002 answerable: {q002['answerable']}</li>
<li>exact citation: {q002['exact_citation_passed']}</li>
<li>Q006 HTTP 호출: {report['q006']['mock_http_calls']}회</li>
<li>Q006 결과: {escape(report['q006']['result'])}</li></ul>
</body></html>""", encoding='utf-8')


def assert_acceptance(report):
    q002 = report['q002']
    budget = report['budget']
    if not (
        q002['pre_gold_recall']['required']['recall'] == 1
        and q002['post_gold_recall']['required']['recall'] == 1
        and q002['required_groups_retained']
        and len(q002['selected_chunk_ids']) == 12
        and q002['parent_partial_inclusion_count'] == 0
        and {'adult', 'pediatric'}.issubset(q002['required_branches_after'])
        and {'common', 'adult', 'pediatric'}.issubset(q002['selected_branches'])
        and q002['source_ordered']
        and q002['source_text_exactly_preserved']
        and q002['mock_http_calls'] == 1
        and q002['answerable']
        and q002['exact_citation_passed']
        and q002['quota_reserved_tokens'] == budget['estimated_request_tokens']
        and q002['quota_reserved_tokens'] != budget['request_token_budget']
        and report['q006']['mock_http_calls'] == 0
        and budget['headroom_passed']
    ):
        raise RuntimeError('Q002 compact prompt acceptance failed')


def main():
    report = build_report()
    assert_acceptance(report)
    DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=False)
    (DEFAULT_OUTPUT / 'prompt_budget_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    write_csv(DEFAULT_OUTPUT / 'q002_group_tokens.csv', report)
    write_html(DEFAULT_OUTPUT / 'review.html', report)
    print(json.dumps({
        'output': str(DEFAULT_OUTPUT),
        'budget': report['budget'],
        'q002_pre_required': report['q002']['pre_gold_recall']['required'],
        'q002_post_required': report['q002']['post_gold_recall']['required'],
        'required_groups_retained': report['q002']['required_groups_retained'],
        'parent_partial_inclusion_count': report['q002']['parent_partial_inclusion_count'],
        'q002_mock_http_calls': report['q002']['mock_http_calls'],
        'q002_exact_citation_passed': report['q002']['exact_citation_passed'],
        'q006_mock_http_calls': report['q006']['mock_http_calls'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
