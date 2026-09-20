"""붙임형 topic+aspect query 일반화를 외부 API 호출 없이 검수합니다."""

from __future__ import annotations

import json
from datetime import date
from html import escape

import httpx

from src.ai import generate
from src.evidence import assess_evidence
from src.library import SEARCH_VERSION
from src.query import plan_query, topic_words
from src.settings import ROOT
from tools.rag_facet_slot_evaluate import MockQuota, settings
from tools.rag_procedure_live_evaluate import pilot_corpus, retrieve

OUTPUT = ROOT / 'workspace' / 'RAG_실험' / '2026-09-15_rag-topic-aspect-generalization-02'
CASES = (
    ('Q001', 'purpose', '진정간호 목적은?', True),
    ('Q002', 'procedure', '진정간호 절차는?', True),
    ('Q003', 'preparation', '진정 전 준비사항은?', True),
    ('Q004', 'purpose', '진정 간호의 목적은 무엇인가요?', True),
    ('Q005', 'cautions', '진정간호 주의사항은?', True),
    ('Q006', 'fact', '화성 우주선의 궤도 계산 공식은?', False),
    ('P01', 'purpose', '진정간호 목적 알려줘', True),
    ('P02', 'purpose', '진정 간호는 왜 시행하나요?', True),
    ('P03', 'purpose', '진정의 목적이 뭐야?', True),
    ('P04', 'preparation', '진정 전에 뭘 준비해야 해?', True),
    ('P05', 'preparation', '진정 시행 전 확인할 것은?', True),
    ('P06', 'preparation', '진정 전 체크사항 알려줘', True),
    ('P07', 'cautions', '진정 시 주의할 점은?', True),
    ('P08', 'cautions', '진정간호에서 조심해야 할 것은?', True),
    ('P09', 'cautions', '진정 중 안전하게 봐야 할 항목은?', True),
    ('N01', 'procedure', '진정 절차는?', True),
    ('N02', 'procedure', '진정절차 알려줘', True),
    ('N03', 'procedure', '진정절차에 대해 알려줘', True),
    ('N04', 'procedure', '진정간호 방법 알려줘', True),
    ('N05', 'procedure', '진정은 어떻게 진행해?', True),
    ('N06', 'procedure', '진정 시행 순서는?', True),
    ('N07', 'purpose', '진정목적 알려줘', True),
    ('N08', 'purpose', '진정 목적은?', True),
    ('N09', 'purpose', '진정은 왜 시행해?', True),
    ('N10', 'preparation', '진정준비사항 알려줘', True),
    ('N11', 'preparation', '진정 전에 뭘 확인해?', True),
    ('N12', 'cautions', '진정주의사항 알려줘', True),
    ('N13', 'cautions', '진정 주의사항은?', True),
    ('N14', 'cautions', '진정할 때 조심할 점은?', True),
)


def _record(code, expected_kind, question, expected_answerable, runtime):
    model, metadata, chunks, vectors = runtime
    if not expected_answerable:
        plan = plan_query(question, documents=[metadata])
        assessment = assess_evidence(plan, [])
        return {
            'code': code,
            'question': question,
            'expected_kind': expected_kind,
            'expected_answerable': False,
            'normalized_query': plan.query,
            'expanded_query': plan.expanded,
            'kind': plan.kind,
            'domain': plan.domain,
            'canonical_topics': list(plan.canonical_topics),
            'topic_words': topic_words(plan),
            'pre_budget': assessment.reason,
            'post_budget': None,
            'selected_evidence_count': 0,
            'selected_evidence_ids': [],
            'group_keys': [],
            'pass': plan.domain == 'out_of_scope' and not assessment.sufficient,
        }
    plan, _, selected, before, _ = retrieve(question, model, metadata, chunks, vectors)
    after = assess_evidence(plan, selected) if selected else None
    passed = all((
        plan.kind == expected_kind,
        plan.domain == 'hospital',
        topic_words(plan) == ['진정간호'],
        before.sufficient,
        after is not None and after.sufficient,
    ))
    return {
        'code': code,
        'question': question,
        'expected_kind': expected_kind,
        'expected_answerable': True,
        'normalized_query': plan.query,
        'expanded_query': plan.expanded,
        'kind': plan.kind,
        'domain': plan.domain,
        'canonical_topics': list(plan.canonical_topics),
        'topic_words': topic_words(plan),
        'pre_budget': before.reason,
        'post_budget': after.reason if after else None,
        'selected_evidence_count': len(selected),
        'selected_evidence_ids': [hit.chunk.id for hit in selected],
        'group_keys': [group.key for group in before.groups],
        'pass': passed,
    }


def _q006_zero_call(metadata):
    calls = []
    plan = plan_query(CASES[5][2], documents=[metadata])
    answer, _ = generate(
        settings(),
        plan.query,
        [],
        'topic-aspect-evaluation',
        quota=MockQuota(),
        transport=httpx.MockTransport(lambda request: calls.append(request)),
        plan=plan,
    )
    return {'transport_calls': len(calls), 'answerable': answer.answerable}


def _review(report):
    rows = ''.join(
        '<tr>'
        f'<td>{escape(case["code"])}</td>'
        f'<td>{escape(case["question"])}</td>'
        f'<td>{escape(case["kind"])}</td>'
        f'<td>{escape(case["domain"])}</td>'
        f'<td>{escape(", ".join(case["topic_words"]))}</td>'
        f'<td>{escape(case["pre_budget"])}</td>'
        f'<td>{escape(case["post_budget"] or "-")}</td>'
        f'<td>{case["selected_evidence_count"]}</td>'
        f'<td class="{("pass" if case["pass"] else "fail")}">'
        f'{"PASS" if case["pass"] else "FAIL"}</td>'
        '</tr>'
        for case in report['cases']
    )
    comparison = report['q002_uat_comparison']
    return f'''<!doctype html>
<html lang="ko"><meta charset="utf-8"><title>Topic+Aspect Generalization Review</title>
<style>
body{{font-family:system-ui,sans-serif;margin:32px;color:#17324d;background:#f6f9fc}}
h1,h2{{color:#0b466b}} table{{border-collapse:collapse;width:100%;background:white}}
th,td{{border:1px solid #ccd7e1;padding:8px;text-align:left;vertical-align:top}}
th{{background:#e9f2f8}} .pass{{color:#087443;font-weight:700}} .fail{{color:#b42318;font-weight:700}}
.card{{background:white;border:1px solid #ccd7e1;border-radius:10px;padding:16px;margin:18px 0}}
</style><body>
<h1>붙임형 Topic + Aspect 일반화 오프라인 검수</h1>
<div class="card">실제 Groq 호출: 0회 · SEARCH_VERSION={report['search_version']} ·
통과: {report['passed']}/{report['total']}</div>
<h2>기준 질문과 UAT 질문 비교</h2>
<div class="card">Canonical topic 동일: {comparison['canonical_topic_equal']} ·
Selected evidence 동일: {comparison['selected_evidence_equal']} ·
Evidence groups 동일: {comparison['group_keys_equal']}</div>
<h2>질문별 결과</h2>
<table><thead><tr><th>ID</th><th>질문</th><th>Intent</th><th>Domain</th><th>Topic</th>
<th>Pre</th><th>Post</th><th>Evidence</th><th>판정</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>'''


def main():
    runtime = pilot_corpus()
    cases = [_record(*case, runtime) for case in CASES]
    by_code = {case['code']: case for case in cases}
    q002, uat = by_code['Q002'], by_code['N03']
    report = {
        'evaluated_on': str(date.today()),
        'actual_groq_calls': 0,
        'search_version': SEARCH_VERSION,
        'total': len(cases),
        'passed': sum(case['pass'] for case in cases),
        'cases': cases,
        'q002_uat_comparison': {
            'canonical_topic_equal': q002['topic_words'] == uat['topic_words'],
            'selected_evidence_equal': (
                q002['selected_evidence_ids'] == uat['selected_evidence_ids']
            ),
            'group_keys_equal': q002['group_keys'] == uat['group_keys'],
        },
        'q006': _q006_zero_call(runtime[1]),
        'contains_source_text': False,
    }
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / 'generalization_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    (OUTPUT / 'review.html').write_text(_review(report), encoding='utf-8')
    print(json.dumps({
        'output': str(OUTPUT),
        'passed': report['passed'],
        'total': report['total'],
        'q002_uat_comparison': report['q002_uat_comparison'],
        'q006': report['q006'],
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
