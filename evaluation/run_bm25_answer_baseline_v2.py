"""Context-fix v2: configuration preflight by default, explicit --run for evaluation."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation import run_bm25_baseline as base  # noqa: E402

OUTPUT = base.AREA / 'results' / 'bm25_answer_baseline_v2.json'


def prepare():
    settings, cases, scoped, metadata = base.prepare(
        output=OUTPUT, expected_branch='lagom-bm25-context-fix')
    ids = [f'TRF-{i:03}' for i in range(1, 11)]
    by_id = {case['id']: case for case in cases}
    if len(cases) != 10 or set(by_id) != set(ids):
        raise ValueError('Expected exactly TRF-001 through TRF-010.')
    if settings.llm_provider != 'gemini' or settings.llm_model != 'gemini-3.1-flash-lite':
        metadata['blockers'].append('Use gemini provider and exactly gemini-3.1-flash-lite.')
    metadata.update(
        baseline_name='BM25 + Gemini Answer Baseline v2',
        ready=not metadata['blockers'], output_file=str(OUTPUT),
        runner_sha256=base.sha256(Path(__file__).resolve()),
        shared_runner_sha256=base.sha256(Path(base.__file__).resolve()),
        change_scope='Complete evidence subset selection; retrieval and PDF parsing unchanged.',
        scoring_performed=False,
        answer_representation='Final validated service answer_text, not unvalidated API text.')
    return settings, [by_id[i] for i in ids], scoped, metadata


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--run', action='store_true')
    group.add_argument('--preflight', action='store_true')
    args = parser.parse_args(argv)
    try:
        prepared = prepare()
        print(json.dumps(prepared[3], ensure_ascii=False, indent=2))
        if not args.run:
            return 0
        if not prepared[3]['ready']:
            return 2
        return base.run_baseline(*prepared, output=OUTPUT)
    except (ValueError, OSError, base.GuideError, sqlite3.Error) as exc:
        print('Baseline v2 preparation failed: ' + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
