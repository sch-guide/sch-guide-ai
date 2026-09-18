"""Read-only preflight by default; --run explicitly executes the real baseline."""
from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AREA = ROOT / 'evaluation'
TARGET = '실무지침서_수혈간호.pdf'
OUTPUT = AREA / 'results' / 'bm25_baseline_v1.json'
sys.path.insert(0, str(ROOT))

from mvp import ai, library, query, retrieval
from mvp.evaluate import load_local_index
from mvp.settings import MODEL, GuideError, load_settings


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_value(*args):
    result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True,
                            text=True, encoding='utf-8', check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def scope_library(original):
    """Select existing chunks/vectors, never ingest, re-chunk, or write a catalog."""
    docs = [d for d in original.docs if d['document_name'] == TARGET]
    if len(docs) != 1:
        raise ValueError('Exactly one ready target document is required.')
    indices = [i for i, c in enumerate(original.chunks) if c.document_id == docs[0]['id']]
    if not indices:
        raise ValueError('Target document has no indexed chunks.')
    scoped = library.LocalLibrary()
    scoped.docs = docs
    scoped.chunks = [original.chunks[i] for i in indices]
    scoped.vectors = original.vectors[indices].copy()
    return scoped


def load_cases():
    cases = json.loads((AREA / 'label_set.json').read_text(encoding='utf-8-sig'))
    if not isinstance(cases, list) or len(cases) != 10:
        raise ValueError('Label Set must contain exactly 10 cases.')
    ids = set()
    for case in cases:
        for key in ('id', 'category', 'question', 'reference_answer'):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError('Missing or invalid label field: ' + key)
        if case['id'] in ids or case.get('source_document') != TARGET:
            raise ValueError('Duplicate ID or unexpected source document.')
        ids.add(case['id'])
        if not 1 <= len(case['question']) <= 500:
            raise ValueError('Question length must be 1..500 characters.')
        for key in ('must_include', 'critical_error'):
            if not isinstance(case.get(key), list) or not all(isinstance(s, str) for s in case[key]):
                raise ValueError('Invalid list field: ' + key)
    return cases


def code_settings():
    """Read literal settings from existing source, without duplicating algorithms."""
    chunk_tree = ast.parse(inspect.getsource(library.make_chunks))
    standard = next((n for n in ast.walk(chunk_tree) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name) and n.func.id == 'RecursiveCharacterTextSplitter'
                     and any(k.arg == 'chunk_size' and isinstance(k.value, ast.Constant)
                             for k in n.keywords)), None)
    chunk = {k.arg: k.value.value for k in standard.keywords if isinstance(k.value, ast.Constant)} if standard else {}
    ai_tree = ast.parse(inspect.getsource(ai.generate))
    temperature = next((k.value.value for n in ast.walk(ai_tree) if isinstance(n, ast.Call)
                        for k in n.keywords if k.arg == 'temperature' and isinstance(k.value, ast.Constant)), None)
    search_tree = ast.parse(inspect.getsource(retrieval.search))
    dense = next((n.args[0].value for n in ast.walk(search_tree) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name) and n.func.id == 'min' and n.args
                  and isinstance(n.args[0], ast.Constant)), None)
    lexical = next((n.upper.value for n in ast.walk(search_tree) if isinstance(n, ast.Slice)
                    and isinstance(n.upper, ast.Constant)), None)
    plan_tree = ast.parse(inspect.getsource(query.plan_query))
    plan_call = next((n for n in ast.walk(plan_tree) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name) and n.func.id == 'QueryPlan'), None)
    limits = {}
    for name in ('max_seeds', 'max_hits'):
        position = [f.name for f in fields(query.QueryPlan)].index(name)
        node = plan_call.args[position] if plan_call and len(plan_call.args) > position else None
        limits[name] = ([node.body.value, node.orelse.value] if isinstance(node, ast.IfExp)
                        and isinstance(node.body, ast.Constant) and isinstance(node.orelse, ast.Constant) else None)
    return dict(chunk_size=chunk.get('chunk_size'), chunk_overlap=chunk.get('chunk_overlap'),
                chunk_unit='embedding tokenizer tokens',
                chunking_note='General splitter values; table blocks use a dynamic budget/overlap. Existing stored chunks are reused.',
                top_k=dict(bm25_candidates=lexical, vector_candidates=dense,
                           seed_limits_broad_normal=limits['max_seeds'],
                           context_limits_broad_normal=limits['max_hits']), temperature=temperature)


def prepare(*, output=OUTPUT, expected_branch='lagom-bm25'):
    cases = load_cases()
    settings = load_settings(use_streamlit=False)
    catalog = (settings.library_dir / 'catalog.sqlite3').resolve()
    original = load_local_index(catalog)  # Existing loader opens SQLite with mode=ro.
    scoped = scope_library(original)
    with sqlite3.connect(catalog.as_uri() + '?mode=ro', uri=True) as db:
        inventory = [dict(document=json.loads(meta)['document_name'], status=status)
                     for meta, status in db.execute('select metadata,status from documents')]
    blockers = []
    try:
        settings.llm_endpoint()  # Configuration validation only; no network call.
    except GuideError:
        blockers.append('Current LLM configuration is not ready; no model/provider override is made.')
    branch = git_value('branch', '--show-current')
    if branch != expected_branch:
        blockers.append(f'Current branch must be {expected_branch}; branch is not changed automatically.')
    secrets_paths = [ROOT / '.streamlit' / 'secrets.toml', Path.home() / '.streamlit' / 'secrets.toml']
    if any(p.exists() for p in secrets_paths):
        blockers.append('Streamlit secrets exist: verify CLI/server configuration parity before this CLI baseline.')
    if output.exists():
        blockers.append('Baseline output already exists; this runner never overwrites an existing run.')
    fingerprint = hashlib.sha256()
    fingerprint.update(json.dumps(scoped.docs, sort_keys=True, ensure_ascii=False).encode())
    fingerprint.update(json.dumps([asdict(c) for c in scoped.chunks], sort_keys=True, ensure_ascii=False).encode())
    fingerprint.update(scoped.vectors.tobytes())
    metadata = dict(
        baseline_name='BM25 Baseline v1', retrieval_engine='BM25',
        retrieval_implementation='BM25 + FAISS dense + RRF + rule rerank + context expansion',
        is_pure_bm25=False,
        retrieval_engine_note='BM25 is the requested baseline label; the unchanged service retrieval is hybrid.',
        evaluation_date=None, source_documents=[TARGET], number_of_questions=len(cases),
        llm_provider=settings.llm_provider, llm_model=settings.llm_model or None,
        embedding_model=MODEL, min_similarity=settings.min_similarity,
        prompt_version=dict(ai_version=ai.AI_VERSION, module='mvp/ai.py',
                            system_sha256=hashlib.sha256(ai.SYSTEM.encode()).hexdigest(),
                            module_sha256=sha256(ROOT / 'mvp' / 'ai.py')),
        search_version=library.SEARCH_VERSION, chunk_version=library.CHUNK_VERSION,
        git_branch=branch, git_commit=git_value('rev-parse', 'HEAD'),
        tracked_worktree_dirty=bool(git_value('status', '--porcelain', '--untracked-files=no')),
        service_source_sha256={str(p.relative_to(ROOT)): sha256(p) for p in sorted((ROOT / 'mvp').glob('*.py'))},
        label_set_sha256=sha256(AREA / 'label_set.json'),
        runner_sha256=sha256(Path(__file__).resolve()),
        catalog=str(catalog), catalog_access='read-only; target-only in-memory index',
        corpus_sha256=fingerprint.hexdigest(), corpus_metadata=scoped.docs,
        indexed_chunk_count=len(scoped.chunks), registered_documents=inventory,
        searchable_documents=[d['document_name'] for d in original.docs],
        other_searchable_documents=[d['document_name'] for d in original.docs if d['document_name'] != TARGET],
        settings_source='mvp/.env + process environment; no Streamlit session',
        conversation_mode='Each case is a fresh conversation; no history or answer cache.',
        quota_scope='evaluation/.runtime/usage.sqlite3; existing quota rules, independent counters',
        response_time_scope='query planning + embedding + retrieval + generation; excludes model initialization',
        ready=not blockers, blockers=blockers, **code_settings())
    return settings, cases, scoped, metadata


def context_rows(hits):
    return [dict(rank=i, document=h.chunk.document_name, page=h.chunk.page,
                 bm25_score=None if h.context_only else getattr(h, 'bm25_score', None),
                 content=h.chunk.text, chunk_id=h.chunk.id, document_id=h.chunk.document_id,
                 context_only=h.context_only, similarity=h.similarity,
                 fusion_score=h.fusion_score, rerank_score=h.rerank_score)
            for i, h in enumerate(hits, 1)]


def run_case(case, settings, scoped, model, quota):
    start = time.perf_counter()
    row = dict(question_id=case['id'], **{k: case[k] for k in
               ('category', 'question', 'reference_answer', 'must_include', 'critical_error')},
               generated_answer=None, retrieved_contexts=[], status='error', error=None)
    search_trace, generation_trace = {}, {}
    try:
        # Only the question enters the pipeline; labels never reach search or the prompt.
        plan = query.plan_query(case['question'], documents=scoped.docs)
        row['query_plan'] = asdict(plan)
        if plan.domain == 'out_of_scope':
            row.update(status='out_of_scope', generated_answer=library.NO_GUIDELINE)
        elif plan.clarification:
            row.update(status='clarification', generated_answer=plan.clarification)
        else:
            vector = model.encode([library.bounded_embedding_question(plan.expanded, model)])[0]
            hits = scoped.search(plan.query, vector, [d['id'] for d in scoped.docs],
                                 settings.min_similarity, plan=plan, trace=search_trace)
            row['retrieved_contexts'] = context_rows(hits)
            if hits:
                answer, used = ai.generate(settings, plan.query, hits, 'bm25-baseline-v1',
                                           quota=quota, plan=plan, trace=generation_trace)
                row.update(generated_answer=ai.answer_text(answer),
                           structured_answer=answer.model_dump(mode='json'),
                           generation_selected_contexts=context_rows(used),
                           status='answered' if answer.answerable else 'abstained')
            else:
                row.update(generated_answer=library.NO_GUIDELINE, status='no_hits')
    except Exception as exc:
        # Preserve failed attempts without leaking arbitrary provider responses/credentials.
        row['error'] = dict(type=type(exc).__name__,
                            message=str(exc) if isinstance(exc, GuideError) else 'See local configuration and source; exception text omitted.',
                            retry_after_seconds=getattr(exc, 'retry_after', None))
    contexts = row['retrieved_contexts']
    row.update(retrieved_document=[c['document'] for c in contexts],
               retrieved_page=[c['page'] for c in contexts],
               retrieval_rank=[c['rank'] for c in contexts],
               bm25_score=[c['bm25_score'] for c in contexts],
               retrieval_rank_semantics='Final service context order, not raw BM25 rank; raw top 10 in retrieval_trace.',
               retrieval_trace=search_trace, generation_trace=generation_trace,
               response_time_seconds=round(time.perf_counter() - start, 6))
    return row


def run_baseline(settings, cases, scoped, metadata, *, output=OUTPUT):
    if not metadata['ready']:
        raise ValueError('Preflight is blocked.')
    output.parent.mkdir(parents=True, exist_ok=True)
    runtime = AREA / '.runtime'
    runtime.mkdir(exist_ok=True)
    # Exclusive run lock avoids two processes racing to create/replace the same artifact.
    lock = runtime / ('baseline.lock' if output == OUTPUT else output.stem + '.lock')
    with lock.open('x', encoding='utf-8') as file:
        file.write(datetime.now(timezone.utc).isoformat())
    try:
        if output.exists():
            raise ValueError('Existing baseline must be preserved.')
        model = library.Embedder()
        quota = ai.Quota(runtime / 'usage.sqlite3')
        report = {**metadata, 'evaluation_date': datetime.now(timezone.utc).isoformat(),
                  'run_complete': False, 'results': []}
        for case in cases:
            report['results'].append(run_case(case, settings, scoped, model, quota))
            report['completed_questions'] = len(report['results'])
            report['run_complete'] = len(report['results']) == len(cases)
            report['has_execution_errors'] = any(r['status'] == 'error' for r in report['results'])
            # Checkpoint only after a real attempt. No reference-based/generated fake answer.
            temporary = output.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            temporary.replace(output)
            print(f"{case['id']}: {report['results'][-1]['status']}")
        return 1 if report['has_execution_errors'] else 0
    finally:
        lock.unlink(missing_ok=True)


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--run', action='store_true', help='Explicitly run all 10 cases and preserve actual results.')
    group.add_argument('--preflight', action='store_true', help='Read-only readiness report (default).')
    args = parser.parse_args(argv)
    try:
        settings, cases, scoped, metadata = prepare()
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        if not args.run:
            return 0
        if not metadata['ready']:
            return 2
        return run_baseline(settings, cases, scoped, metadata)
    except (ValueError, OSError, GuideError, sqlite3.Error) as exc:
        print('Baseline preparation failed: ' + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
