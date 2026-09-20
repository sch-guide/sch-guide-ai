from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date
from html import escape
from pathlib import Path

from src.library import SEARCH_VERSION
from src.query import MONITORING_ITEM_PATTERNS
from src.settings import ROOT
from tools.rag_pilot_query_generalization_evaluate import retrieve_funnel
from tools.rag_procedure_live_evaluate import pilot_corpus

DEFAULT_FIXTURE = ROOT / 'tests' / 'fixtures' / 'monitoring_qualifier_queries.json'
DEFAULT_OUTPUT = ROOT / 'workspace' / 'RAG_실험' / '2026-09-16_rag-monitoring-qualifier-generalization'


def load_cases(path: Path = DEFAULT_FIXTURE) -> list[dict]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if payload.get('schema_version') != 1 or not isinstance(payload.get('cases'), list):
        raise ValueError('unsupported monitoring qualifier fixture')
    return payload['cases']


def _branches(assessment: dict | None) -> set[str]:
    return {
        group['branch'] for group in (assessment or {}).get('groups', ())
        if group.get('branch') and group['branch'] != 'common'
    }


def evaluate_case(case: dict, runtime) -> dict:
    chunks = runtime[2]
    by_id = {chunk.id: chunk for chunk in chunks}
    funnel = retrieve_funnel(case['question'], *runtime)
    plan = funnel['plan']
    pre = funnel['pre_budget']
    post = funnel.get('post_budget')
    selected_ids = list(funnel.get('selected_evidence', ()))
    item_pattern = MONITORING_ITEM_PATTERNS[case['expected_item']]
    direct_ids = [
        chunk_id for chunk_id in selected_ids
        if re.search(item_pattern, by_id[chunk_id].text, re.I)
    ]
    actual_answerable = bool(pre['sufficient'] and post and post['sufficient'])
    reasons = []
    expected_fields = {
        'monitoring_branches': [case['expected_branch']],
        'monitoring_phase': case['expected_phase'],
        'monitoring_item': case['expected_item'],
        'monitoring_action': case['expected_action'],
    }
    for field, expected in expected_fields.items():
        actual = list(plan[field]) if field == 'monitoring_branches' else plan[field]
        if actual != expected:
            reasons.append(f'{field}_mismatch')
    if funnel['query_analysis']['topic_words'] != ['진정간호']:
        reasons.append('topic_mismatch')
    if actual_answerable != case['expected_answerable']:
        reasons.append('answerability_mismatch')
    if case['expected_answerable'] and not direct_ids:
        reasons.append('missing_direct_item_support')
    if not case['expected_answerable'] and direct_ids:
        reasons.append('unexpected_direct_item_support')
    if case['expected_answerable'] and case['expected_branch'] not in _branches(post):
        reasons.append('missing_requested_branch')
    return {
        **case,
        'actual_kind': plan['kind'],
        'actual_topic': funnel['query_analysis']['topic_words'],
        'actual_branch': list(plan['monitoring_branches']),
        'actual_phase': plan['monitoring_phase'],
        'actual_item': plan['monitoring_item'],
        'actual_action': plan['monitoring_action'],
        'pre_budget_reason': pre['reason'],
        'post_budget_reason': post['reason'] if post else None,
        'selected_evidence_ids': selected_ids,
        'direct_item_support_ids': direct_ids,
        'actual_answerable': actual_answerable,
        'pass': not reasons,
        'failure_reasons': reasons,
    }


def build_report(fixture: Path = DEFAULT_FIXTURE) -> dict:
    runtime = pilot_corpus()
    cases = [evaluate_case(case, runtime) for case in load_cases(fixture)]
    return {
        'evaluated_on': str(date.today()),
        'actual_groq_calls': 0,
        'search_version': SEARCH_VERSION,
        'question_count': len(cases),
        'passed': sum(case['pass'] for case in cases),
        'failed': sum(not case['pass'] for case in cases),
        'cases': cases,
    }


def write_artifacts(report: dict, output: Path = DEFAULT_OUTPUT) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / 'report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
    )
    fields = (
        'id', 'question', 'expected_answerable', 'actual_answerable', 'actual_kind',
        'actual_topic', 'actual_branch', 'actual_phase', 'actual_item', 'actual_action',
        'pre_budget_reason', 'post_budget_reason', 'selected_evidence_ids',
        'direct_item_support_ids', 'pass', 'failure_reasons',
    )
    with (output / 'results.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in report['cases']:
            row = {field: case.get(field) for field in fields}
            for field in ('actual_topic', 'actual_branch', 'selected_evidence_ids',
                          'direct_item_support_ids', 'failure_reasons'):
                row[field] = ','.join(row[field])
            writer.writerow(row)
    rows = ''.join(
        '<tr>' + ''.join(f'<td>{escape(str(value))}</td>' for value in (
            case['id'], case['question'], case['actual_branch'], case['actual_phase'],
            case['actual_item'], case['actual_action'], case['pre_budget_reason'],
            case['post_budget_reason'], len(case['selected_evidence_ids']),
            ', '.join(case['direct_item_support_ids']), 'PASS' if case['pass'] else 'FAIL',
        )) + '</tr>' for case in report['cases']
    )
    (output / 'review.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>Monitoring qualifier review</title>'
        '<style>body{font-family:sans-serif;margin:2rem}table{border-collapse:collapse}'
        'th,td{border:1px solid #ccd6df;padding:.5rem;vertical-align:top}</style>'
        f'<h1>Monitoring qualifier offline review</h1><p>{report["passed"]}/{report["question_count"]} PASS · Groq 0회</p>'
        '<table><thead><tr><th>ID</th><th>질문</th><th>branch</th><th>phase</th>'
        '<th>item</th><th>action</th><th>pre</th><th>post</th><th>selected</th>'
        f'<th>direct item support</th><th>result</th></tr></thead><tbody>{rows}</tbody></table>',
        encoding='utf-8',
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report()
    write_artifacts(report, args.output)
    print(json.dumps({key: report[key] for key in ('question_count', 'passed', 'failed', 'actual_groq_calls')}, ensure_ascii=False))
    return 0 if report['failed'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
