"""Q006을 먼저 차단한 뒤 Q002만 Groq로 최대 한 번 평가합니다."""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime
from html import escape

import httpx

from mvp.ai import (
    GROQ_REQUEST_TOKEN_BUDGET,
    PROMPT_EVIDENCE_SCHEMA_VERSION,
    answer_text,
    generate,
    prompt_messages,
)
from mvp.evidence import (
    assess_evidence,
    build_source_unit_catalog,
    procedure_citations_ordered,
    required_coverage_loss,
)
from mvp.library import NO_GUIDELINE
from mvp.query import plan_query
from mvp.settings import ROOT, GuideError, load_settings
from tools.rag_phase1_evaluate import stage_recall
from tools.rag_phase2_evaluate import _load_q002

DEFAULT_OUTPUT = ROOT / 'artifacts' / '2026-09-13_rag-groq-evaluation'


class EvaluationQuota:
    def __init__(self):
        self.reserved_tokens = None
        self.usage = None

    def reserve(self, settings, user_id, tokens):
        self.reserved_tokens = tokens
        return 'evaluation-only'

    def settle(self, identifier, usage):
        self.usage = usage

    def cancel(self, identifier):
        pass


class RejectTransport(httpx.BaseTransport):
    def __init__(self):
        self.calls = 0

    def handle_request(self, request):
        self.calls += 1
        raise RuntimeError('Q006 attempted an external request')


class SingleCallTransport(httpx.BaseTransport):
    """실제 HTTP는 한 번만 허용하고 비밀 header와 원문 response는 저장하지 않습니다."""

    def __init__(self, model, selected_ids, *, inner=None):
        self.model = model
        self.selected_ids = set(selected_ids)
        self.inner = inner or httpx.HTTPTransport(retries=0)
        self.calls = 0
        self.status_code = None

    def handle_request(self, request):
        if self.calls:
            raise RuntimeError('evaluation permits exactly one Groq request')
        self.calls += 1
        payload = json.loads(request.content)
        if payload.get('model') != self.model:
            raise RuntimeError('fallback or model change rejected')
        if 'tools' in payload or 'tool_choice' in payload:
            raise RuntimeError('tool or web-search request rejected')
        if len(payload.get('messages', ())) != 2:
            raise RuntimeError('unexpected prompt message count')
        content = payload['messages'][1]['content']
        envelope = json.loads(content.split('Evidence groups (JSON):\n', 1)[1])
        sent_ids = {
            source['chunk_id']
            for group in envelope['groups']
            for source in group['sources']
        }
        if sent_ids != self.selected_ids:
            raise RuntimeError('prompt evidence differs from selected evidence')
        response = self.inner.handle_request(request)
        self.status_code = response.status_code
        return response

    def close(self):
        self.inner.close()


def mock_response(selected):
    def handle(request):
        payload = json.loads(request.content)
        envelope = json.loads(
            payload['messages'][1]['content'].split('Evidence groups (JSON):\n', 1)[1]
        )
        group_selections = {}
        for group in envelope['groups']:
            unit = next(
                unit
                for source in group['sources']
                for unit in source['units']
                if unit['selectable']
            )
            group_selections[group['group_id']] = [unit['id']]
        content = json.dumps({
            'group_selections': group_selections,
        }, ensure_ascii=False)
        return httpx.Response(200, json={
            'model': 'openai/gpt-oss-20b',
            'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120},
        })

    return httpx.MockTransport(handle)


def evaluation_inputs():
    metadata, gold, hits = _load_q002()
    plan = plan_query(gold['question'], documents=[metadata])
    before = assess_evidence(plan, hits)
    prompt_trace = {}
    _, selected = prompt_messages(
        plan.query, before.hits, 14000, token_budget=GROQ_REQUEST_TOKEN_BUDGET,
        plan=plan, groups=before.groups, trace=prompt_trace,
    )
    after = assess_evidence(plan, selected)
    if not before.sufficient or not after.sufficient:
        raise RuntimeError('Q002 evidence gate is not ready')
    if required_coverage_loss(before.procedure_coverage, after.procedure_coverage):
        raise RuntimeError('Q002 required evidence changed before evaluation')
    return metadata, gold, hits, plan, before, selected, after, prompt_trace


def evaluate(*, dry_run=False):
    settings = load_settings(use_streamlit=False)
    if not dry_run:
        settings.llm_endpoint()
    metadata, gold, hits, plan, before, selected, after, prompt_trace = evaluation_inputs()

    q006_plan = plan_query('화성 우주선의 궤도 계산 공식은?', documents=[metadata])
    q006_transport = RejectTransport()
    q006_trace = {}
    q006_answer, _ = generate(
        settings, q006_plan.query, [], 'evaluation', plan=q006_plan, trace=q006_trace,
        transport=q006_transport,
    )
    if q006_transport.calls or answer_text(q006_answer) != NO_GUIDELINE:
        raise RuntimeError('Q006 zero-call gate failed')

    inner = mock_response(selected) if dry_run else None
    transport = SingleCallTransport(settings.llm_model, [hit.chunk.id for hit in selected], inner=inner)
    trace = {}
    quota = EvaluationQuota()
    request_error = None
    started = time.perf_counter()
    try:
        answer, used_hits = generate(
            settings, plan.query, hits, 'evaluation', quota=quota, transport=transport,
            plan=plan, trace=trace,
        )
    except GuideError as exc:
        request_error = trace.get('llm_error_code') or (
            'AI_AUTH' if 'AI_AUTH' in str(exc) else 'AI_REQUEST_FAILED'
        )
        answer, used_hits = None, selected
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    if transport.calls != 1:
        raise RuntimeError('Q002 did not make exactly one request')

    source_map = {hit.chunk.id: hit.chunk for hit in used_hits}
    cited_ids = [
        evidence.chunk_id
        for statement in (answer.statements if answer else [])
        for evidence in statement.evidence
    ]
    statement_count = len(answer.statements) if answer is not None else 0
    cited_statement_count = sum(bool(statement.evidence) for statement in answer.statements) if answer else 0
    citation_coverage = cited_statement_count / statement_count if statement_count else 0.0
    source_ordered = procedure_citations_ordered(plan, answer.statements, source_map) if answer else None
    source_unit_catalog = build_source_unit_catalog(after.groups)
    source_unit_pairs = {
        (unit.chunk_id, unit.exact_text)
        for unit in source_unit_catalog
        if unit.selectable
    }
    reconstruction_text_exact = bool(answer and answer.statements) and all(
        len(statement.evidence) == 1
        and (statement.evidence[0].chunk_id, statement.text) in source_unit_pairs
        for statement in answer.statements
    )
    reconstruction_quote_exact = bool(answer and answer.statements) and all(
        len(statement.evidence) == 1
        and statement.evidence[0].quote == statement.text
        and (statement.evidence[0].chunk_id, statement.evidence[0].quote) in source_unit_pairs
        for statement in answer.statements
    )
    validation_passed = bool(
        answer is not None
        and answer.answerable
        and citation_coverage == 1
        and source_ordered
        and trace.get('citation_assessment') == 'supported'
    )
    statements = (
        [statement.model_dump() for statement in answer.statements]
        if validation_passed and answer else []
    )
    validated_sources = []
    if validation_passed:
        for chunk_id in dict.fromkeys(cited_ids):
            chunk = source_map[chunk_id]
            validated_sources.append({
                'chunk_id': chunk.id,
                'document_name': chunk.document_name,
                'page': chunk.page,
                'location': chunk.location,
                'section': chunk.section,
            })
    pre_ids = [hit.chunk.id for hit in before.hits]
    post_ids = [hit.chunk.id for hit in after.hits]
    usage = trace.get('response_usage') or {}

    return {
        'schema_version': 1,
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'mode': 'mock-dry-run' if dry_run else 'live-groq-single-call',
        'actual_groq_calls': 0 if dry_run else transport.calls,
        'constraints': {
            'automatic_retry': False,
            'fallback_model': False,
            'web_search': False,
            'request_token_budget': GROQ_REQUEST_TOKEN_BUDGET,
            'prompt_schema_version': PROMPT_EVIDENCE_SCHEMA_VERSION,
        },
        'q006': {
            'question': q006_plan.original,
            'llm_called': q006_trace.get('llm_called', False),
            'transport_calls': q006_transport.calls,
            'result': answer_text(q006_answer),
            'abstention_reason': q006_trace.get('block_reason'),
        },
        'q002': {
            'question': gold['question'],
            'model_requested': settings.llm_model,
            'model_returned': trace.get('response_model'),
            'http_status': trace.get('response_http_status', transport.status_code),
            'finish_reason': trace.get('finish_reason'),
            'llm_called': trace.get('llm_called', False),
            'transport_calls': transport.calls,
            'latency_ms': latency_ms,
            'input_tokens': usage.get('prompt_tokens'),
            'output_tokens': usage.get('completion_tokens'),
            'total_tokens': usage.get('total_tokens'),
            'reserved_tokens': quota.reserved_tokens,
            'prompt_schema_version': prompt_trace.get('prompt_schema_version'),
            'selected_evidence_chunk_ids': [hit.chunk.id for hit in used_hits],
            'cited_chunk_ids': list(dict.fromkeys(cited_ids)),
            'pre_gold_recall': stage_recall(gold, pre_ids),
            'post_gold_recall': stage_recall(gold, post_ids),
            'statement_count': statement_count,
            'statements': statements,
            'validated_sources': validated_sources,
            'citation_coverage': citation_coverage,
            'exact_citation_validation': trace.get('citation_assessment'),
            'source_ordered': source_ordered,
            'source_order_reversal_count': 0 if source_ordered else None,
            'reconstruction_text_exact': reconstruction_text_exact,
            'reconstruction_quote_exact': reconstruction_quote_exact,
            'validation_passed': validation_passed,
            'request_error': request_error,
            'failure_code': trace.get('llm_error_code'),
            'validation_reason': trace.get('validation_reason'),
            'abstention_reason': trace.get('block_reason'),
            'answerable': answer.answerable if answer else False,
            'trace_summary': {
                'stage': trace.get('stage'),
                'budget_assessment': trace.get('budget_assessment'),
                'estimated_request_tokens': trace.get('estimated_request_tokens'),
                'request_token_headroom': trace.get('request_token_headroom'),
                'required_group_keys': trace.get('post_budget_required_group_keys'),
                'response_http_status': trace.get('response_http_status'),
                'response_model': trace.get('response_model'),
                'response_usage': trace.get('response_usage'),
                'finish_reason': trace.get('finish_reason'),
                'llm_error_code': trace.get('llm_error_code'),
                'response_parse_stage': trace.get('response_parse_stage'),
                'response_failure_detail': trace.get('response_failure_detail'),
                'response_json_succeeded': trace.get('response_json_succeeded'),
                'response_top_level_type': trace.get('response_top_level_type'),
                'response_choices_present': trace.get('response_choices_present'),
                'response_choices_count': trace.get('response_choices_count'),
                'response_choice0_type': trace.get('response_choice0_type'),
                'response_finish_reason_present': trace.get('response_finish_reason_present'),
                'response_message_present': trace.get('response_message_present'),
                'response_message_type': trace.get('response_message_type'),
                'response_content_present': trace.get('response_content_present'),
                'response_content_type': trace.get('response_content_type'),
                'response_content_char_count': trace.get('response_content_char_count'),
                'response_refusal_present': trace.get('response_refusal_present'),
                'response_refusal_non_null': trace.get('response_refusal_non_null'),
                'response_selection_schema_version': trace.get(
                    'response_selection_schema_version'
                ),
                'response_selection_group_count': trace.get(
                    'response_selection_group_count'
                ),
                'selected_source_unit_count': trace.get('selected_source_unit_count'),
                'answer_coverage': trace.get('answer_coverage'),
            },
        },
    }


def write_artifacts(report, output):
    output.mkdir(parents=True, exist_ok=False)
    (output / 'groq_evaluation_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    with (output / 'q002_statements.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=('statement', 'chunk_ids', 'quotes'))
        writer.writeheader()
        for statement in report['q002']['statements']:
            writer.writerow({
                'statement': statement['text'],
                'chunk_ids': '|'.join(item['chunk_id'] for item in statement['evidence']),
                'quotes': '|'.join(item['quote'] for item in statement['evidence']),
            })
    statement_rows = ''.join(
        '<tr>'
        f"<td>{index}</td><td>{escape(statement['text'])}</td>"
        f"<td>{'<br>'.join(escape(item['chunk_id']) for item in statement['evidence'])}</td>"
        f"<td>{'<br>'.join(escape(item['quote']) for item in statement['evidence'])}</td>"
        '</tr>'
        for index, statement in enumerate(report['q002']['statements'], start=1)
    ) or '<tr><td colspan="4">검증된 statement 없음</td></tr>'
    q002 = report['q002']
    q006 = report['q006']
    (output / 'review.html').write_text(f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>SCHAT Groq 실제 평가</title><style>
body{{font-family:system-ui,sans-serif;max-width:1200px;margin:24px auto;padding:0 16px;color:#17202a}}
table{{border-collapse:collapse;width:100%;margin:18px 0 32px}}th,td{{border:1px solid #ccd1d1;padding:8px;text-align:left;vertical-align:top}}
th{{background:#eef3f6}}code{{white-space:nowrap}}.pass{{color:#196f3d;font-weight:700}}
</style></head><body><h1>Q002 실제 Groq 단일 호출 평가</h1>
<p>생성 {escape(report['generated_at'])} · 모델 {escape(str(q002['model_returned'] or q002['model_requested']))}</p>
<ul><li>실제 Groq 호출: {report['actual_groq_calls']}회</li>
<li>latency: {q002['latency_ms']} ms</li><li>input/output/total: {q002['input_tokens']} / {q002['output_tokens']} / {q002['total_tokens']}</li>
<li>finish reason: {escape(str(q002['finish_reason']))}</li><li>failure code: {escape(str(q002['failure_code']))}</li>
<li>statement: {q002['statement_count']}개</li><li>citation coverage: {q002['citation_coverage'] * 100:.1f}%</li>
<li>exact validation: {escape(str(q002['exact_citation_validation']))}</li>
<li>source order: {q002['source_ordered']}</li><li>validation passed: {q002['validation_passed']}</li></ul>
<h2>검증된 답변과 원문</h2><table><thead><tr><th>#</th><th>statement</th><th>chunk</th><th>exact quote</th></tr></thead><tbody>{statement_rows}</tbody></table>
<h2>Q006 zero-call</h2><ul><li>transport 호출: {q006['transport_calls']}회</li>
<li>llm_called: {q006['llm_called']}</li><li>결과: {escape(q006['result'])}</li>
<li>reason: {escape(str(q006['abstention_reason']))}</li></ul>
</body></html>""", encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--output', type=str, default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    report = evaluate(dry_run=args.dry_run)
    if not args.dry_run:
        write_artifacts(report, ROOT / args.output if not args.output.startswith(str(ROOT)) else DEFAULT_OUTPUT)
    print(json.dumps({
        'mode': report['mode'],
        'actual_groq_calls': report['actual_groq_calls'],
        'q006_calls': report['q006']['transport_calls'],
        'q002_calls': report['q002']['transport_calls'],
        'q002_answerable': report['q002']['answerable'],
        'q002_validation_passed': report['q002']['validation_passed'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
