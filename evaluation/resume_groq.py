"""Resume only recorded pre-API local minute-quota failures. No automatic API retries."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from evaluation import run_bm25_baseline as base
from evaluation import evaluation_quota

SOURCE = base.AREA / 'results' / 'bm25_answer_diagnostics_groq_v5_final.json'
OUTPUT = base.AREA / 'results' / 'current_hybrid_groq_v5_completed.json'


def target_ids(source):
    rows = source['results']
    ids = [r['question_id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate question IDs.')
    return [r['question_id'] for r in rows
            if r.get('status') == 'error'
            and r.get('generation_trace', {}).get('llm_called') is False
            and (r.get('error') or {}).get('type') == 'RateLimitError'
            and '(AI_LIMIT_MINUTE)' in (r.get('error') or {}).get('message', '')]


class PacedQuota:
    """Wait exclusively inside reserve, before generate can issue its HTTP request."""
    def __init__(self, quota, *, sleep=time.sleep, emit=print):
        self.quota, self.sleep, self.emit = quota, sleep, emit

    def reserve(self, settings, user_id, tokens):
        while True:
            try:
                return self.quota.reserve(settings, user_id, tokens)
            except base.ai.RateLimitError as exc:
                if '(AI_LIMIT_MINUTE)' not in str(exc):
                    raise
                # Service reserve computes retry_after from actual usage timestamps.
                remaining = exc.retry_after
                while remaining > 0:
                    self.emit(f'waiting for evaluation quota {remaining}')
                    delay = min(remaining, 60)
                    self.sleep(delay)
                    remaining -= delay

    def cancel(self, identifier):
        return self.quota.cancel(identifier)

    def settle(self, identifier, usage):
        return self.quota.settle(identifier, usage)


def execute_resume(source, output, execute):
    targets = target_ids(source)
    report = deepcopy(source)
    report.update(original_results=deepcopy(source['results']), resume_target_ids=targets,
                  resume_date=datetime.now(timezone.utc).isoformat(),
                  run_complete=False, resumed_question_ids=[], in_progress_question_id=None)
    for row in report['results']:
        row['result_origin'] = 'original'
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also prevents replay after a crash with an ambiguous API outcome.
    with output.open('x', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2)

    def checkpoint():
        temporary = output.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(output)

    for index, row in enumerate(report['results']):
        identifier = row['question_id']
        if identifier not in targets:
            continue
        report['in_progress_question_id'] = identifier
        checkpoint()
        result = execute(identifier)  # Exactly one run_case attempt; never retry API errors.
        report['results'][index] = {**result, 'result_origin': 'resumed'}
        report['resumed_question_ids'].append(identifier)
        report['in_progress_question_id'] = None
        checkpoint()
    report['run_complete'] = True
    report['has_execution_errors'] = any(r['status'] == 'error' for r in report['results'])
    checkpoint()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Otherwise read-only preflight.')
    args = parser.parse_args(argv)
    source = json.loads(SOURCE.read_text(encoding='utf-8'))
    if not source.get('run_complete'):
        raise ValueError('A complete source record is required.')
    targets = target_ids(source)
    previous = {k: os.environ.get(k) for k in ('GUIDE_LLM_PROVIDER', 'GUIDE_LLM_MODEL')}
    try:
        os.environ.update(GUIDE_LLM_PROVIDER='groq_free', GUIDE_LLM_MODEL='openai/gpt-oss-20b')
        settings, cases, scoped, metadata = base.prepare(output=OUTPUT, expected_branch='lagom-bm25-context-fix')
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    for key in ('service_source_sha256', 'label_set_sha256', 'corpus_sha256',
                'prompt_version', 'embedding_model', 'min_similarity', 'top_k',
                'chunk_size', 'chunk_overlap', 'temperature', 'search_version',
                'chunk_version', 'source_documents', 'llm_provider', 'llm_model'):
        if metadata.get(key) != source.get(key):
            raise ValueError('Source environment mismatch: ' + key)
    if not metadata['ready']:
        raise ValueError('Evaluation preflight blocked.')
    by_id = {case['id']: case for case in cases}
    if not set(targets).issubset(by_id):
        raise ValueError('Source IDs do not match Label Set.')
    usage_db = evaluation_quota.resolve_path(source['quota_scope'])
    if not usage_db.exists():
        raise ValueError('Original evaluation quota DB is required; no reset or fresh scope.')
    preflight = evaluation_quota.preflight(settings, len(targets), usage_db)
    print(json.dumps({'resume_target_ids': targets, 'quota_preflight': preflight}, indent=2))
    if not args.run:
        return 0
    if not preflight['ten_question_call_capacity']:
        raise ValueError('Insufficient local daily call capacity.')
    source.update(resume_source_file=str(SOURCE), resume_source_sha256=base.sha256(SOURCE),
                  resume_quota_preflight=preflight)
    # Initialization is lazy: execute_resume claims the output before model/DB work.
    resources = {}

    def execute(identifier):
        if not resources:
            resources.update(model=base.library.Embedder(), quota=PacedQuota(base.ai.Quota(usage_db)))
        return base.run_case(by_id[identifier], settings, scoped, resources['model'], resources['quota'])

    report = execute_resume(source, OUTPUT, execute)
    return 1 if report['has_execution_errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
