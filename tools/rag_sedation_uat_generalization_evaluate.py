"""진정간호 실사용 UAT 질문을 provider 호출 없이 retrieval/evidence까지 평가합니다."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from html import escape
from pathlib import Path

import httpx

from src.ai import generate
from src.library import SEARCH_VERSION
from src.query import plan_query
from src.settings import ROOT
from tools.rag_facet_slot_evaluate import MockQuota, settings
from tools.rag_pilot_query_generalization_evaluate import retrieve_funnel
from tools.rag_procedure_live_evaluate import pilot_corpus

DEFAULT_FIXTURE = ROOT / 'tests' / 'fixtures' / 'sedation_uat_queries.json'
DEFAULT_OUTPUT = ROOT / 'workspace' / 'UAT' / '2026-09-16_rag-sedation-uat-generalization'
BASELINE_REPORT = (
    ROOT / 'workspace' / 'UAT' / '2026-09-15_rag-sedation-uat-generalization-baseline-02'
    / 'uat_report.json'
)


def load_cases(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    cases = payload.get('cases')
    if payload.get('schema_version') != 2 or not isinstance(cases, list):
        raise ValueError('unsupported sedation UAT fixture')
    return cases


def _branch_set(funnel: dict) -> set[str]:
    assessment = funnel.get('post_budget') or funnel.get('pre_budget') or {}
    return {
        group['branch']
        for group in assessment.get('groups', ())
        if group.get('branch') and group['branch'] != 'common'
    }


def _branch_matches(expected: str, actual: set[str]) -> bool:
    if expected in {'any', 'none'}:
        return True
    if expected == 'both':
        return {'adult', 'pediatric'}.issubset(actual)
    return expected in actual


def _failure_reasons(case: dict, funnel: dict) -> list[str]:
    mode = case['expected_mode']
    plan = funnel['plan']
    pre = funnel['pre_budget']
    post = funnel.get('post_budget')
    selected_count = len(funnel.get('selected_evidence', ()))
    reasons = []
    if mode == 'independent_abstain':
        if plan['domain'] != 'out_of_scope':
            reasons.append('negative_domain_not_out_of_scope')
        if pre['sufficient'] or selected_count:
            reasons.append('unsafe_negative_admission')
        return reasons
    if mode == 'context_follow_up':
        if pre['sufficient'] or selected_count:
            reasons.append('unsafe_standalone_follow_up_admission')
        return reasons
    if mode == 'clarification':
        if not plan['clarification']:
            reasons.append('missing_clarification')
        if pre['sufficient'] or selected_count:
            reasons.append('unsafe_clarification_admission')
        return reasons
    if plan['domain'] != 'hospital':
        reasons.append('domain_not_hospital')
    if plan['kind'] != case['expected_intent']:
        reasons.append('intent_mismatch')
    if funnel['query_analysis']['topic_words'] != [case['expected_topic']]:
        reasons.append('topic_mismatch')
    if not pre['sufficient']:
        reasons.append(f'pre_budget:{pre["reason"]}')
    if pre['sufficient'] and (post is None or not post['sufficient']):
        reason = post['reason'] if post else 'not_evaluated'
        reasons.append(f'post_budget:{reason}')
    if not _branch_matches(case['expected_branch'], _branch_set(funnel)):
        reasons.append('branch_coverage_mismatch')
    expected_phase = case.get('expected_phase')
    if (expected_phase is not None
            and funnel['query_analysis']['requested_temporal_phase'] != expected_phase):
        reasons.append('temporal_qualifier_mismatch')
    return reasons


def _ranking(rows: list[dict], limit: int = 5) -> list[dict]:
    return [
        {
            'rank': row['rank'],
            'chunk_id': row['chunk_id'],
            'score': row['score'],
            **(
                {'temporal_tier': row['temporal_tier']}
                if 'temporal_tier' in row else {}
            ),
        }
        for row in rows[:limit]
    ]


def evaluate_case(case: dict, runtime) -> dict:
    funnel = retrieve_funnel(case['question'], *runtime)
    reasons = _failure_reasons(case, funnel)
    branches = sorted(_branch_set(funnel))
    result = {
        **case,
        'actual_kind': funnel['plan']['kind'],
        'actual_domain': funnel['plan']['domain'],
        'actual_topic': funnel['query_analysis']['topic_words'],
        'actual_branches': branches,
        'actual_requested_phase': funnel['query_analysis']['requested_temporal_phase'],
        'pre_budget_reason': funnel['pre_budget']['reason'],
        'pre_budget_sufficient': funnel['pre_budget']['sufficient'],
        'post_budget_reason': (
            funnel['post_budget']['reason'] if funnel['post_budget'] else None
        ),
        'post_budget_sufficient': (
            funnel['post_budget']['sufficient'] if funnel['post_budget'] else False
        ),
        'selected_evidence_count': len(funnel['selected_evidence']),
        'pass': not reasons,
        'failure_category': reasons[0] if reasons else None,
        'failure_reasons': reasons,
        'diagnostics': {
            'normalized_query': funnel['plan']['query'],
            'expanded_query': funnel['plan']['expanded'],
            'bm25_top': _ranking(funnel['bm25_top']),
            'semantic_top': _ranking(funnel['semantic_top']),
            'rrf_top': _ranking(funnel['rrf_top']),
            'rerank_chunk_ids': [row['chunk_id'] for row in funnel['rerank_seeds']],
            'context_chunk_ids': [row['chunk_id'] for row in funnel['context_hits']],
            'selected_evidence_ids': list(funnel['selected_evidence']),
            'pre_group_keys': [
                group['key'] for group in funnel['pre_budget']['groups']
            ],
            'pre_groups': [
                {
                    'key': group['key'],
                    'branch': group['branch'],
                    'complete': group['complete'],
                    'required': group['required'],
                    'chunk_ids': group['chunk_ids'],
                }
                for group in funnel['pre_budget']['groups']
            ],
            'post_group_keys': [
                group['key'] for group in (funnel['post_budget'] or {}).get('groups', ())
            ],
        },
    }
    return result


def q006_zero_call(runtime) -> dict:
    calls = []
    metadata = runtime[1]
    plan = plan_query('화성 우주선의 궤도 계산 공식은?', documents=[metadata])

    def reject(request):
        calls.append(request)
        raise RuntimeError('Q006 attempted provider transport')

    answer, used = generate(
        settings(),
        plan.query,
        [],
        'sedation-uat-q006-zero-call',
        quota=MockQuota(),
        transport=httpx.MockTransport(reject),
        plan=plan,
    )
    return {
        'domain': plan.domain,
        'transport_calls': len(calls),
        'answerable': answer.answerable,
        'selected_evidence_count': len(used),
        'pass': plan.domain == 'out_of_scope' and not calls and not answer.answerable,
    }


FAILURE_GROUPS = (
    'intent classification',
    'topic/canonical topic',
    'summary',
    'comparison',
    'fact-specific',
    'branch qualifier',
    'temporal qualifier',
    'follow-up/context',
    'semantic block / parent selection',
    'out-of-scope',
    'other',
)


def _failure_group(case: dict) -> str:
    reasons = set(case.get('failure_reasons', ()))
    category = case.get('category')
    if case.get('expected_mode') == 'context_follow_up':
        return 'follow-up/context'
    if category == 'negative_out_of_scope':
        return 'out-of-scope'
    if any('incomplete_semantic_block' in reason for reason in reasons):
        return 'semantic block / parent selection'
    if category == 'summary_broad':
        return 'summary'
    if category == 'comparison':
        return 'comparison'
    if category == 'fact_specific':
        return 'fact-specific'
    if category == 'branch_specific':
        return 'branch qualifier'
    if 'temporal_qualifier_mismatch' in reasons:
        return 'temporal qualifier'
    if 'intent_mismatch' in reasons:
        return 'intent classification'
    if ('topic_mismatch' in reasons
            or any('no_topic_evidence' in reason for reason in reasons)):
        return 'topic/canonical topic'
    return 'other'


def _failure_record(case: dict) -> dict:
    diagnostics = case.get('diagnostics', {})
    return {
        'id': case['id'],
        'question': case['question'],
        'expected_mode': case.get('expected_mode'),
        'actual_query_plan': {
            'kind': case.get('actual_kind'),
            'domain': case.get('actual_domain'),
            'topic_words': case.get('actual_topic', []),
            'normalized_query': diagnostics.get('normalized_query'),
            'expanded_query': diagnostics.get('expanded_query'),
        },
        'pre_budget_reason': case.get('pre_budget_reason'),
        'post_budget_reason': case.get('post_budget_reason'),
        'retrieval_evidence_present': bool(diagnostics.get('context_chunk_ids')),
        'selected_evidence_present': bool(diagnostics.get('selected_evidence_ids')),
        'failure_reasons': case.get('failure_reasons', []),
    }


def _group_failures(cases: list[dict]) -> list[dict]:
    grouped = {name: [] for name in FAILURE_GROUPS}
    for case in cases:
        if case.get('pass'):
            continue
        grouped[_failure_group(case)].append(_failure_record(case))
    return [
        {
            'category': name,
            'count': len(grouped[name]),
            'representative_question': (
                grouped[name][0]['question'] if grouped[name] else None
            ),
            'cases': grouped[name],
        }
        for name in FAILURE_GROUPS
    ]


def write_failure_categories(output: Path, report: dict, fixture: Path) -> None:
    current_cases = {case['id']: case for case in load_cases(fixture)}
    baseline_payload = json.loads(BASELINE_REPORT.read_text(encoding='utf-8'))
    baseline_cases = []
    for case in baseline_payload['cases']:
        merged = {**case, **{
            'expected_mode': current_cases[case['id']]['expected_mode'],
            'expected_behavior': current_cases[case['id']]['expected_behavior'],
        }}
        baseline_cases.append(merged)
    payload = {
        'baseline_artifact': str(BASELINE_REPORT.relative_to(ROOT)),
        'before_total_failed': sum(not case.get('pass') for case in baseline_cases),
        'after_total_failed': report['failed'],
        'before': _group_failures(baseline_cases),
        'after': _group_failures(report['cases']),
        'contains_full_source_text': False,
    }
    (output / 'failure_categories.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8',
    )


def build_report(fixture: Path = DEFAULT_FIXTURE, *, chunk_version=4) -> dict:
    cases = load_cases(fixture)
    runtime = pilot_corpus(chunk_version=chunk_version)
    results = [evaluate_case(case, runtime) for case in cases]
    category_summary = []
    for category in dict.fromkeys(case['category'] for case in results):
        members = [case for case in results if case['category'] == category]
        category_summary.append({
            'category': category,
            'total': len(members),
            'passed': sum(case['pass'] for case in members),
            'failed': sum(not case['pass'] for case in members),
        })
    failures = Counter(
        case['failure_category'] for case in results if case['failure_category']
    )
    all_reasons = Counter(
        reason for case in results for reason in case['failure_reasons']
    )
    return {
        'evaluated_on': str(date.today()),
        'fixture': str(fixture.relative_to(ROOT)),
        'search_version': SEARCH_VERSION,
        'actual_groq_calls': 0,
        'question_count': len(results),
        'expected_mode_summary': [
            {
                'mode': mode,
                'count': sum(case['expected_mode'] == mode for case in results),
            }
            for mode in (
                'independent_answer', 'independent_abstain',
                'context_follow_up', 'clarification',
            )
        ],
        'passed': sum(case['pass'] for case in results),
        'failed': sum(not case['pass'] for case in results),
        'category_summary': category_summary,
        'failure_categories': [
            {'category': category, 'count': count}
            for category, count in failures.most_common()
        ],
        'all_failure_reasons': [
            {'reason': reason, 'count': count}
            for reason, count in all_reasons.most_common()
        ],
        'q006_zero_call': q006_zero_call(runtime),
        'contains_full_source_text': False,
        'cases': results,
    }


def write_csv(output: Path, report: dict) -> None:
    fields = (
        'id', 'category', 'question', 'expected_mode', 'expected_behavior', 'expected_intent',
        'expected_topic', 'expected_branch', 'expected_phase', 'expected_qualifier',
        'actual_kind', 'actual_domain', 'actual_topic', 'actual_branches',
        'actual_requested_phase', 'pre_budget_reason', 'post_budget_reason',
        'selected_evidence_count', 'pass', 'failure_category',
    )
    with (output / 'uat_results.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in report['cases']:
            row = {key: case.get(key) for key in fields}
            row['actual_topic'] = ','.join(case['actual_topic'])
            row['actual_branches'] = ','.join(case['actual_branches'])
            writer.writerow(row)


def _badge(passed: bool) -> str:
    return '<span class="pass">PASS</span>' if passed else '<span class="fail">FAIL</span>'


def write_review(output: Path, report: dict) -> None:
    category_rows = ''.join(
        '<tr>'
        f'<td>{escape(row["category"])}</td><td>{row["total"]}</td>'
        f'<td>{row["passed"]}</td><td>{row["failed"]}</td>'
        '</tr>'
        for row in report['category_summary']
    )
    failure_rows = ''.join(
        f'<li><code>{escape(row["category"])}</code>: {row["count"]}건</li>'
        for row in report['failure_categories']
    ) or '<li>실패 없음</li>'
    all_reason_rows = ''.join(
        f'<li><code>{escape(row["reason"])}</code>: {row["count"]}건</li>'
        for row in report['all_failure_reasons']
    ) or '<li>실패 없음</li>'
    rows = []
    details = []
    for case in report['cases']:
        status = 'pass' if case['pass'] else 'fail'
        rows.append(
            f'<tr data-category="{escape(case["category"])}" data-status="{status}">'
            f'<td>{escape(case["id"])}</td><td>{escape(case["category"])}</td>'
            f'<td>{escape(case["question"])}</td>'
            f'<td>{escape(case["expected_mode"])}</td>'
            f'<td>{escape(case["expected_intent"])}</td>'
            f'<td>{escape(str(case["expected_branch"]))} / '
            f'{escape(str(case["expected_qualifier"]))}</td>'
            f'<td>{escape(case["actual_kind"])}</td>'
            f'<td>{escape(case["actual_domain"])}</td>'
            f'<td>{escape(", ".join(case["actual_topic"]) or "-")}</td>'
            f'<td>{escape(case["pre_budget_reason"])}</td>'
            f'<td>{escape(case["post_budget_reason"] or "-")}</td>'
            f'<td>{case["selected_evidence_count"]}</td>'
            f'<td>{_badge(case["pass"])}</td>'
            f'<td>{escape(case["failure_category"] or "-")}</td></tr>'
        )
        if not case['pass']:
            diag = case['diagnostics']
            bm25 = ', '.join(row['chunk_id'].rsplit('-', 1)[-1] for row in diag['bm25_top'])
            semantic = ', '.join(
                row['chunk_id'].rsplit('-', 1)[-1] for row in diag['semantic_top']
            )
            details.append(
                '<details><summary>'
                f'{escape(case["id"])} · {escape(case["question"])} · '
                f'{escape(case["failure_category"] or "-")}</summary>'
                f'<p>모든 원인: {escape(", ".join(case["failure_reasons"]))}</p>'
                f'<p>정규 query: {escape(diag["normalized_query"])}</p>'
                f'<p>확장 query: {escape(diag["expanded_query"])}</p>'
                f'<p>BM25 Top-5: {escape(bm25)}</p>'
                f'<p>Semantic Top-5: {escape(semantic)}</p>'
                f'<p>선택 evidence: '
                f'{escape(", ".join(identifier.rsplit("-", 1)[-1] for identifier in diag["selected_evidence_ids"]) or "-")}</p>'
                '</details>'
            )
    categories = ''.join(
        f'<option value="{escape(row["category"])}">{escape(row["category"])}</option>'
        for row in report['category_summary']
    )
    html = f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>진정간호 UAT Generalization 결과</title>
<style>
body{{font-family:system-ui,sans-serif;margin:28px;color:#17324d;background:#f5f8fb}}
h1,h2{{color:#0b466b}} .card{{background:white;border:1px solid #ccd7e1;border-radius:12px;padding:16px;margin:16px 0}}
table{{border-collapse:collapse;width:100%;background:white;font-size:13px}} th,td{{border:1px solid #ccd7e1;padding:7px;vertical-align:top}}
th{{background:#e8f1f7;position:sticky;top:0}} .pass{{color:#087443;font-weight:700}} .fail{{color:#b42318;font-weight:700}}
select{{padding:6px;margin-right:8px}} details{{background:white;border:1px solid #d8e1e8;border-radius:8px;padding:10px;margin:8px 0}}
code{{background:#eef3f7;padding:2px 5px;border-radius:4px}}
</style></head><body>
<h1>진정간호 실사용 UAT 일반화 결과</h1>
<div class="card">질문 {report['question_count']}개 · PASS {report['passed']} · FAIL {report['failed']} · 실제 Groq 호출 0회<br>
Q006 zero-call: {_badge(report['q006_zero_call']['pass'])}</div>
<h2>유형별 요약</h2><table><tr><th>유형</th><th>전체</th><th>PASS</th><th>FAIL</th></tr>{category_rows}</table>
<h2>대표 실패 유형</h2><div class="card"><ol>{failure_rows}</ol></div>
<h2>동반 원인 전체</h2><div class="card"><ol>{all_reason_rows}</ol></div>
<h2>질문별 결과</h2>
<div class="card"><label>유형 <select id="category"><option value="all">전체</option>{categories}</select></label>
<label>판정 <select id="status"><option value="all">전체</option><option value="pass">PASS</option><option value="fail">FAIL</option></select></label></div>
<table id="results"><thead><tr><th>ID</th><th>유형</th><th>질문</th><th>기대 모드</th><th>기대 intent</th><th>기대 branch/qualifier</th><th>실제 kind</th><th>domain</th><th>topic</th><th>Pre</th><th>Post</th><th>Evidence</th><th>판정</th><th>실패 유형</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>FAIL 상세</h2>{''.join(details) or '<div class="card">실패 없음</div>'}
<script>
function filterRows(){{const c=document.getElementById('category').value;const s=document.getElementById('status').value;
document.querySelectorAll('#results tbody tr').forEach(r=>r.style.display=(c==='all'||r.dataset.category===c)&&(s==='all'||r.dataset.status===s)?'':'none');}}
document.getElementById('category').addEventListener('change',filterRows);document.getElementById('status').addEventListener('change',filterRows);
</script></body></html>'''
    (output / 'review.html').write_text(html, encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f'기존 artifacts를 덮어쓰지 않습니다: {args.output}')
    args.output.mkdir(parents=True)
    report = build_report(args.fixture)
    (args.output / 'uat_report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    write_csv(args.output, report)
    write_failure_categories(args.output, report, args.fixture)
    write_review(args.output, report)
    print(json.dumps({
        'output': str(args.output),
        'questions': report['question_count'],
        'passed': report['passed'],
        'failed': report['failed'],
        'failure_categories': report['failure_categories'],
        'q006_zero_call': report['q006_zero_call'],
        'actual_groq_calls': report['actual_groq_calls'],
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
