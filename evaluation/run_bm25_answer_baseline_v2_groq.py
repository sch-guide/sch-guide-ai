"""Existing Groq path comparison; defaults to preflight, never overwrites results."""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation import run_bm25_baseline as base

OUTPUT = base.AREA / 'results' / 'bm25_answer_baseline_v2_groq.json'
GEMINI_RESULT = base.AREA / 'results' / 'bm25_answer_baseline_v2.json'
MODEL = 'openai/gpt-oss-20b'  # Existing README and mvp/.env.example configuration.


def prepare():
    reference = json.loads(GEMINI_RESULT.read_text(encoding='utf-8'))
    # Process-local provider selection; never write .env or change approval/key settings.
    overrides = {'GUIDE_LLM_PROVIDER': 'groq_free', 'GUIDE_LLM_MODEL': MODEL}
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        os.environ.update(overrides)
        settings, cases, scoped, metadata = base.prepare(
            output=OUTPUT, expected_branch='lagom-bm25-context-fix')
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    ids = [f'TRF-{i:03}' for i in range(1, 11)]
    by_id = {case['id']: case for case in cases}
    if len(cases) != 10 or set(by_id) != set(ids):
        raise ValueError('Expected exactly TRF-001 through TRF-010.')
    if not reference.get('run_complete') or reference.get('llm_provider') != 'gemini':
        metadata['blockers'].append('A completed Gemini v2 reference is required.')
    for key in ('service_source_sha256', 'label_set_sha256', 'corpus_sha256',
                'prompt_version', 'embedding_model', 'min_similarity', 'top_k',
                'chunk_size', 'chunk_overlap', 'temperature', 'search_version',
                'chunk_version', 'source_documents'):
        if metadata.get(key) != reference.get(key):
            metadata['blockers'].append('Gemini v2 reference mismatch: ' + key)
    metadata.update(
        baseline_name='BM25 + Groq Answer Baseline v2',
        ready=not metadata['blockers'], output_file=str(OUTPUT),
        runner_sha256=base.sha256(Path(__file__).resolve()),
        shared_runner_sha256=base.sha256(Path(base.__file__).resolve()),
        comparison_reference=str(GEMINI_RESULT),
        comparison_reference_sha256=base.sha256(GEMINI_RESULT),
        historical_model_evidence='README.md and mvp/.env.example; not a historical API execution log',
        provider_model_only_comparison=False,
        existing_provider_differences={
            'groq_request_token_budget': base.ai.GROQ_REQUEST_TOKEN_BUDGET,
            'gemini_request_token_budget': None,
            'groq_reasoning_effort': 'low',
            'groq_minute_token_budget': base.ai.GROQ_MINUTE_TOKEN_BUDGET,
            'groq_day_token_budget': base.ai.GROQ_DAY_TOKEN_BUDGET,
            'note': 'Existing provider branches retained. Prompt context selection may differ due to token budget; transport and quota handling also differ.'},
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
        if not prepared[3]['ready']:
            return 2
        if not args.run:
            return 0
        return base.run_baseline(*prepared, output=OUTPUT)
    except (ValueError, OSError, base.GuideError, sqlite3.Error):
        print('Groq comparison preparation failed; check reference files and configuration.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
