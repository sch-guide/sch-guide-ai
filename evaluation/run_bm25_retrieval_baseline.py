"""Run the existing local retrieval pipeline only; never generate answers."""
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation import run_bm25_baseline as base

OUTPUT = base.AREA / 'results' / 'bm25_retrieval_baseline_v1.json'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    cases = base.load_cases()
    settings = base.load_settings(use_streamlit=False)
    catalog = (settings.library_dir / 'catalog.sqlite3').resolve()
    original = base.load_local_index(catalog)
    scoped = base.scope_library(original)
    if OUTPUT.exists():
        raise FileExistsError('Existing retrieval baseline will not be overwritten.')
    if base.git_value('branch', '--show-current') != 'lagom-bm25':
        raise ValueError('Expected lagom-bm25 branch; no automatic branch change.')
    if any(p.exists() for p in [base.ROOT / '.streamlit/secrets.toml',
                               Path.home() / '.streamlit/secrets.toml']):
        raise ValueError('Verify CLI/Streamlit configuration parity before running.')
    print(json.dumps({'searchable_documents': [d['document_name'] for d in original.docs],
                      'evaluation_documents': [base.TARGET], 'chunks': len(scoped.chunks)}, ensure_ascii=False))
    # Existing embedding model is required by the unchanged hybrid retrieval.
    # It is not an answer-generating LLM. No ai.generate or Quota call occurs.
    model = base.library.Embedder()
    report = dict(
        baseline_name='BM25 Retrieval Baseline v1', evaluation_date=datetime.now(timezone.utc).isoformat(),
        retrieval_engine='BM25', is_pure_bm25=False,
        retrieval_implementation='BM25 + FAISS dense + RRF + rule rerank + context expansion',
        rank_semantics='Final unchanged service context order, not raw BM25 rank.',
        source_documents=[base.TARGET], corpus_metadata=scoped.docs,
        indexed_chunk_count=len(scoped.chunks), number_of_questions=len(cases),
        embedding_model=base.MODEL, min_similarity=settings.min_similarity,
        llm_called=False, automatic_scoring=False,
        git_branch=base.git_value('branch', '--show-current'), git_commit=base.git_value('rev-parse', 'HEAD'),
        label_set_sha256=base.sha256(base.AREA / 'label_set.json'),
        catalog_sha256=base.sha256(catalog), runner_sha256=base.sha256(Path(__file__).resolve()),
        service_source_sha256={str(p.relative_to(base.ROOT)): base.sha256(p)
                               for p in sorted((base.ROOT / 'mvp').glob('*.py'))},
        retrieval_time_scope='query planning + question embedding + retrieval; excludes model initialization',
        conversation_mode='Independent questions with no previous conversation',
        run_complete=False, results=[], **base.code_settings())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create prevents accidental overwrite; checkpoints retain real attempts only.
    with OUTPUT.open('x', encoding='utf-8') as output:
        for case in cases:
            start = time.perf_counter()
            row = dict(question_id=case['id'], question=case['question'],
                       retrieved_contexts=[], status='error', error=None)
            trace = {}
            try:
                plan = base.query.plan_query(case['question'], documents=scoped.docs)
                row['query_plan'] = asdict(plan)
                if plan.clarification or plan.domain == 'out_of_scope':
                    row['status'] = 'clarification' if plan.clarification else 'out_of_scope'
                else:
                    vector = model.encode([base.library.bounded_embedding_question(plan.expanded, model)])[0]
                    hits = scoped.search(plan.query, vector, [d['id'] for d in scoped.docs],
                                         settings.min_similarity, plan=plan, trace=trace)
                    row['retrieved_contexts'] = base.context_rows(hits)
                    row['status'] = 'success'
            except Exception as exc:
                row['error'] = {'type': type(exc).__name__,
                                'message': str(exc) if isinstance(exc, base.GuideError) else 'Retrieval failed.'}
            row['retrieval_time_seconds'] = round(time.perf_counter() - start, 6)
            row['retrieval_trace'] = trace
            report['results'].append(row)
            report['successful_questions'] = sum(r['status'] == 'success' for r in report['results'])
            report['error_count'] = sum(r['status'] == 'error' for r in report['results'])
            report['run_complete'] = len(report['results']) == len(cases)
            output.seek(0)
            output.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
            output.truncate()
            output.flush()
            print(json.dumps({'question_id': row['question_id'], 'status': row['status'],
                              'pages': list(dict.fromkeys(c['page'] for c in row['retrieved_contexts'])),
                              'contexts': len(row['retrieved_contexts']),
                              'seconds': row['retrieval_time_seconds']}, ensure_ascii=False), flush=True)
    return 0 if report['successful_questions'] == len(cases) else 1


if __name__ == '__main__':
    raise SystemExit(main())
