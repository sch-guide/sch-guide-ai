"""Gemini answer baseline. Default: read-only preflight. Use --run explicitly."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation import run_bm25_baseline as base  # noqa: E402

OUTPUT = base.AREA / 'results' / 'bm25_answer_baseline_v1.json'
MODEL = 'gemini-3.1-flash-lite'
QUESTION_IDS = [f'TRF-{i:03}' for i in range(1, 11)]


def prepare():
    settings, cases, scoped, metadata = base.prepare(output=OUTPUT)
    by_id = {case['id']: case for case in cases}
    if len(cases) != 10 or set(by_id) != set(QUESTION_IDS):
        raise ValueError('Expected exactly TRF-001 through TRF-010.')
    cases = [by_id[identifier] for identifier in QUESTION_IDS]
    if settings.llm_provider != 'gemini' or settings.llm_model != MODEL:
        metadata['blockers'].append('Set GUIDE_LLM_PROVIDER=gemini and GUIDE_LLM_MODEL=gemini-3.1-flash-lite.')
    metadata.update(
        baseline_name='BM25 + Gemini Answer Baseline v1',
        ready=not metadata['blockers'],
        output_file=str(OUTPUT),
        runner_sha256=base.sha256(Path(__file__).resolve()),
        shared_runner_sha256=base.sha256(Path(base.__file__).resolve()),
        answer_representation='Final service answer_text after existing validation, with structured_answer; not unvalidated API text.',
        scoring_performed=False)
    return settings, cases, scoped, metadata


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--run', action='store_true', help='Run 10 questions and save actual final service answers.')
    group.add_argument('--preflight', action='store_true', help='Check configuration only (default).')
    args = parser.parse_args(argv)
    try:
        prepared = prepare()
        metadata = prepared[3]
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        if not args.run:
            return 0
        if not metadata['ready']:
            return 2
        return base.run_baseline(*prepared, output=OUTPUT)
    except (ValueError, OSError, base.GuideError, sqlite3.Error) as exc:
        # Never emit arbitrary exception bodies or configuration/credential objects.
        print('Answer baseline preparation failed: ' + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
