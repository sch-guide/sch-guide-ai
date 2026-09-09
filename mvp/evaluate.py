"""로컬 검색 평가: python -m mvp.evaluate --cases docs/search-cases.example.jsonl

관리자가 정답 문서/쪽수를 작성하면 실제 임베딩으로 검색 누락과 지연을 측정합니다.
Groq를 호출하지 않으며 결과 파일에는 질문·원문·답변을 저장하지 않습니다.
"""

import argparse
import json
import sqlite3
import statistics
import time
from pathlib import Path

import numpy as np

from mvp.library import Chunk, Embedder, LocalLibrary, bounded_embedding_question, clean, protect_private
from mvp.query import plan_query
from mvp.settings import DIMENSIONS, MODEL, GuideError, load_settings


def load_local_index(path):
    if not path.is_file():
        raise GuideError("등록된 검색 색인이 없습니다.")
    library = LocalLibrary()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("begin")
        library.docs = [json.loads(row[0]) for row in db.execute("select metadata from documents where status='ready'")]
        if any(doc["model"] != MODEL for doc in library.docs):
            raise GuideError("현재 검색 모델로 문서를 재색인하세요.")
        rows = db.execute("select c.payload,c.vector from chunks c join documents d on c.document_id=d.id "
                          "where d.status='ready' order by d.id,c.position").fetchall()
    library.chunks = [Chunk.from_row(json.loads(row[0])) for row in rows]
    library.vectors = np.stack([np.frombuffer(row[1], dtype='<f4') for row in rows]) if rows else np.empty((0, DIMENSIONS))
    return library


def evaluate(library, model, cases, minimum=.38):
    rows, expected_count, matched_count = [], 0, 0
    for index, case in enumerate(cases):
        question = case['question']
        if not isinstance(question, str) or not 1 <= len(question) <= 500:
            raise GuideError("평가 질문은 1~500자여야 합니다.")
        protect_private(question)
        expected = case['expected_sources']
        start = time.perf_counter()
        plan = plan_query(question, previous=case.get('previous_question', ''), documents=library.docs)
        vector = model.encode([bounded_embedding_question(plan.expanded, model)])[0]
        hits = library.search(plan.query, vector, [d['id'] for d in library.docs], minimum, plan)
        elapsed = time.perf_counter() - start
        matches = sum(any(h.chunk.document_name == source['document_name']
                          and (source.get('page') is None or h.chunk.page == source['page'])
                          and (not source.get('quote') or clean(source['quote']) in clean(h.chunk.text))
                          for h in hits) for source in expected)
        expected_count += len(expected)
        matched_count += matches
        rows.append(dict(case_number=index + 1, expected=len(expected), matched=matches,
                         passed=matches == len(expected) if expected else not hits,
                         retrieved=len(hits), seconds=round(elapsed, 4)))
    timings = [row['seconds'] for row in rows]
    return dict(cases=len(rows), passed=sum(r['passed'] for r in rows),
                evidence_recall=matched_count / expected_count if expected_count else None,
                median_seconds=statistics.median(timings) if timings else None,
                p95_seconds=float(np.percentile(timings, 95)) if timings else None,
                scope="local retrieval only; not answer quality or concurrent-load validation", results=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=Path('data/evaluation/latest.json'))
    args = parser.parse_args()
    try:
        cases = [json.loads(line) for line in args.cases.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        if not 1 <= len(cases) <= 200:
            raise GuideError("평가 질문은 1~200개를 준비하세요.")
        settings = load_settings()
        report = evaluate(load_local_index(settings.library_dir / 'catalog.sqlite3'), Embedder(), cases,
                          settings.min_similarity)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: v for k, v in report.items() if k != 'results'}, ensure_ascii=False))
    except (GuideError, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        raise SystemExit('평가를 완료하지 못했습니다. 질문 파일 형식과 색인·모델 상태를 확인하세요.') from None


if __name__ == '__main__':
    main()
