"""Q002 presentation sidecar를 외부 호출 없이 검수용 HTML로 생성합니다."""

import hashlib
import html
import json
from collections import Counter
from pathlib import Path

import httpx

from src.ai import Answer, generate
from src.presentation import leading_marker, procedure_display_rows
from src.settings import Settings
from tools.rag_facet_slot_evaluate import MockQuota, facet_response, q002_inputs

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'workspace' / 'RAG_실험' / '2026-09-15_rag-answer-presentation-mock-02'
ELEVEN_IDS = (
    'su002', 'su003', 'su005', 'su006', 'su012', 'su017',
    'su020', 'su021', 'su024', 'su029', 'su032',
)


def _mock_answer():
    _, hits, plan, _, _, _, catalog, contract, _, _, _ = q002_inputs()
    content = facet_response(contract, ELEVEN_IDS)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={
            'model': 'openai/gpt-oss-20b',
            'choices': [{
                'finish_reason': 'stop',
                'message': {'content': content, 'refusal': None},
            }],
            'usage': {'prompt_tokens': 1000, 'completion_tokens': 100, 'total_tokens': 1100},
        })

    trace = {}
    answer, selected = generate(
        Settings(
            llm_provider='groq_free', llm_key='fixture',
            llm_model='openai/gpt-oss-20b', llm_approved=True,
            groq_free_confirmed=True,
        ),
        plan.query,
        hits,
        'presentation-review',
        quota=MockQuota(),
        transport=httpx.MockTransport(handle),
        plan=plan,
        trace=trace,
    )
    if len(calls) != 1 or not answer.answerable or not answer.presentation:
        raise RuntimeError('Q002 presentation Mock did not complete')
    return answer, selected, trace


def _citation_numbers(answer, selected):
    chunks = {hit.chunk.id: hit.chunk for hit in selected}
    keys = []
    for statement in answer.statements:
        for evidence in statement.evidence:
            chunk = chunks[evidence.chunk_id]
            key = (chunk.document_id, chunk.page, chunk.location)
            if key not in keys:
                keys.append(key)
    numbers = {key: index for index, key in enumerate(keys, start=1)}
    by_statement = []
    for statement in answer.statements:
        statement_keys = []
        for evidence in statement.evidence:
            chunk = chunks[evidence.chunk_id]
            key = (chunk.document_id, chunk.page, chunk.location)
            if key not in statement_keys:
                statement_keys.append(key)
        by_statement.append(tuple(numbers[key] for key in statement_keys))
    return by_statement


def _review_html(answer, selected):
    citation_numbers = _citation_numbers(answer, selected)
    lines = []
    for row in procedure_display_rows(answer.presentation):
        if row.kind == 'branch':
            lines.append(f'<h2>{html.escape(row.label)}</h2>')
        elif row.kind == 'phase':
            lines.append(f'<h3>{html.escape(row.label)}</h3>')
        else:
            statement = answer.statements[row.statement_index]
            item = answer.presentation.statements[row.statement_index]
            text = statement.text.lstrip()
            marker = item.leading_marker
            if marker and text.startswith(marker):
                body = text[len(marker):].lstrip()
                content = (
                    f'<span class="marker">{html.escape(marker)}</span>'
                    f'<span>{html.escape(body)}</span>'
                )
            else:
                content = f'<span class="bullet">•</span><span>{html.escape(statement.text)}</span>'
            citations = ' '.join(
                f'<a href="#source-{number}">[{number}]</a>'
                for number in citation_numbers[row.statement_index]
            )
            lines.append(f'<div class="statement">{content}<span class="refs">{citations}</span></div>')
    body = '\n'.join(lines)
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Q002 Answer Presentation Mock</title>
<style>
body{{margin:0;background:#f3f7fb;color:#15324a;font-family:"Pretendard","Noto Sans KR",sans-serif}}
main{{max-width:920px;margin:40px auto;padding:0 24px 60px}}
.notice{{background:#e8f3ff;border:1px solid #bed9f3;border-radius:14px;padding:14px 18px;margin-bottom:20px}}
.card{{background:white;border:1px solid #dce7ef;border-radius:18px;padding:26px 30px;box-shadow:0 8px 24px #183b5a12}}
h1{{font-size:24px;margin:0 0 10px}} h2{{font-size:20px;margin:28px 0 10px;border-bottom:1px solid #dce7ef;padding-bottom:8px}}
h3{{font-size:15px;color:#276690;margin:18px 0 7px}} .statement{{display:flex;gap:9px;align-items:flex-start;padding:7px 4px;line-height:1.65}}
.marker{{min-width:28px;font-weight:750;color:#276690}} .bullet{{color:#276690;font-weight:900}} .refs{{white-space:nowrap;margin-left:4px}}
a{{color:#1670b7;text-decoration:none;font-weight:700}} .meta{{color:#587084;font-size:13px}}
</style></head><body><main>
<div class="notice"><b>Mock/UI snapshot</b> · 외부 Groq 호출 0회 · 검증된 원문과 citation은 변경하지 않음</div>
<div class="card"><h1>진정간호 절차</h1><p class="meta">11개 SourceUnit · branch → phase · 원래 source order 유지</p>{body}</div>
</main></body></html>'''


def build_report():
    answer, selected, trace = _mock_answer()
    presentation = answer.presentation
    chunks = {hit.chunk.id: hit.chunk for hit in selected}
    exact_identity = all(
        statement.text == statement.evidence[0].quote
        and statement.evidence[0].chunk_id in chunks
        for statement in answer.statements
    )
    orders = [item.source_order for item in presentation.statements]
    markers = [item.leading_marker for item in presentation.statements if item.leading_marker]
    return {
        'schema_version': 1,
        'actual_groq_calls': 0,
        'mock_transport_calls': 1,
        'selected_source_unit_count': len(answer.statements),
        'verified_statement_count': len(answer.statements),
        'presentation_statement_count': len(presentation.statements),
        'sidecar_fields': [
            'statement_index', 'source_unit_id', 'branch', 'phase',
            'source_order', 'leading_marker',
        ],
        'sidecar_has_clinical_text': False,
        'answer_schema_unchanged': 'presentation' not in Answer.model_json_schema()['properties'],
        'statement_text_quote_chunk_identity': exact_identity,
        'citation_coverage': 1.0,
        'citation_assessment': trace.get('citation_assessment'),
        'source_order_reversal_count': sum(
            left > right for left, right in zip(orders, orders[1:])
        ),
        'branch_counts': dict(Counter(item.branch for item in presentation.statements)),
        'phase_counts': dict(Counter(item.phase for item in presentation.statements)),
        'leading_marker_count': len(markers),
        'outer_numbering_added': False,
        'clinical_number_marker_checks': {
            value: leading_marker(value) for value in ('15분', '10분', '10 mg', '95%')
        },
        'response_parse_stage': trace.get('response_parse_stage'),
        'validation_reason': trace.get('validation_reason'),
        'presentation_ready': trace.get('presentation_ready'),
        'answer_sha256': hashlib.sha256(answer.model_dump_json().encode()).hexdigest(),
    }, answer, selected


def main():
    report, answer, selected = build_report()
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / 'mock_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    (OUTPUT / 'review.html').write_text(_review_html(answer, selected), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
