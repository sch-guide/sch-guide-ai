"""SCHAT BM25와 rank-bm25를 동일 질문·chunk로 비교하는 오프라인 평가 도구."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from dataclasses import asdict, replace
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from rank_bm25 import BM25Okapi

from mvp.diagnostics import scan_summary
from mvp.documents import read_document
from mvp.library import CHUNK_VERSION, Chunk, Embedder, clean, make_chunks
from mvp.query import plan_query
from mvp.retrieval import BM25Index, lexical_tokens, rank_bm25_candidates
from mvp.settings import ROOT

DEFAULT_QUESTIONS = (
    "진정간호 목적은?",
    "진정간호 절차는?",
    "진정 전 준비사항은?",
    "진정 간호의 목적은 무엇인가요?",
    "진정간호 주의사항은?",
    "화성 우주선의 궤도 계산 공식은?",
)
SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx", ".xlsx"})
RESULT_FIELDS = (
    "engine",
    "query_id",
    "original_query",
    "normalized_query",
    "expanded_query",
    "rank",
    "bm25_score",
    "requested_temporal_phase",
    "temporal_tier",
    "document_id",
    "document_name",
    "page_number",
    "location",
    "section_title",
    "chunk_id",
    "chunk_text",
    "source_type",
    "relevance_label",
    "failure_type",
    "review_note",
)


def default_input_paths(data_dir: Path) -> list[Path]:
    """평가 범위를 넓히지 않도록 data 바로 아래의 지원 문서만 선택한다."""
    if not data_dir.is_dir():
        return []
    return sorted(
        (path for path in data_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES),
        key=lambda path: path.name.casefold(),
    )


def stable_evaluation_chunks(path: Path, counter: object) -> tuple[dict, list[Chunk], dict]:
    """운영 추출·청킹을 재사용하되 평가 ID만 파일 해시 기반으로 안정화한다."""
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    document = read_document(path.name, content, ocr=False)
    metadata, generated = make_chunks(document, counter, file_hash=digest)
    identity = hashlib.sha256(f"{path.name.casefold()}:{digest}".encode()).hexdigest()
    document_id = f"eval-{identity[:20]}"
    ids = [f"{document_id}-chunk-{index + 1:05d}" for index in range(len(generated))]
    parent_map = {
        parent: f"{document_id}-parent-{index + 1:05d}"
        for index, parent in enumerate(dict.fromkeys(chunk.parent_id for chunk in generated))
    }
    chunks = [
        replace(
            chunk,
            id=ids[index],
            document_id=document_id,
            parent_id=parent_map[chunk.parent_id],
            previous_chunk_id=ids[index - 1] if index else None,
            next_chunk_id=ids[index + 1] if index + 1 < len(ids) else None,
        )
        for index, chunk in enumerate(generated)
    ]
    metadata = {**metadata, "id": document_id, "chunk_count": len(chunks)}
    extraction = {
        "document_id": document_id,
        "document_name": document.document_name,
        "source_type": document.source_type,
        "sha256": digest,
        "page_or_unit_count": len(document.pages),
        "text_page_or_unit_count": document.text_page_count,
        "total_characters": document.character_count,
        "empty_units": [page.number if page.number is not None else page.location for page in document.pages if not page.text],
        "extraction_errors": [
            {"page_number": page.number, "location": page.location, "error": bool(page.error)}
            for page in document.pages
            if page.error
        ],
        "warnings": list(document.warnings),
    }
    if document.source_type == "pdf":
        extraction["scan_assessment"] = scan_summary(content, document)
    return metadata, chunks, extraction


def corpus_statistics(chunks: Sequence[Chunk], counter: object) -> dict:
    normalized = [clean(chunk.text) for chunk in chunks]
    duplicates = {text: count for text, count in Counter(normalized).items() if text and count > 1}
    lengths = [counter.count(chunk.text) for chunk in chunks]
    return {
        "chunk_count": len(chunks),
        "empty_chunk_count": sum(not text for text in normalized),
        "duplicate_text_group_count": len(duplicates),
        "duplicate_chunk_count": sum(duplicates.values()),
        "short_body_chunk_count": sum(len(text) < 8 for text in normalized),
        "minimum_tokens": min(lengths) if lengths else None,
        "maximum_tokens": max(lengths) if lengths else None,
        "average_tokens": round(sum(lengths) / len(lengths), 3) if lengths else None,
    }


def _ranked_rows(
    engine: str,
    scores: Sequence[float],
    chunks: Sequence[Chunk],
    query_id: str,
    original: str,
    normalized: str,
    expanded: str,
    top_k: int,
    ranking=None,
) -> list[dict]:
    positions = (list(ranking.positions)[:top_k] if ranking is not None else
                 sorted(range(len(chunks)), key=lambda index: (-float(scores[index]), index))[:top_k])
    tier_by_position = ({position: tier for position, tier in zip(ranking.positions, ranking.tiers)}
                        if ranking is not None else {})
    rows = []
    for rank, position in enumerate(positions, start=1):
        chunk = chunks[position]
        rows.append(
            {
                "engine": engine,
                "query_id": query_id,
                "original_query": original,
                "normalized_query": normalized,
                "expanded_query": expanded,
                "rank": rank,
                "bm25_score": float(scores[position]),
                "requested_temporal_phase": (ranking.requested_phase if ranking is not None else None),
                "temporal_tier": tier_by_position.get(position, "baseline"),
                "zero_score": bool(float(scores[position]) == 0),
                "document_id": chunk.document_id,
                "document_name": chunk.document_name,
                "page_number": chunk.page,
                "location": chunk.location,
                "section_title": chunk.section,
                "chunk_id": chunk.id,
                "chunk_text": chunk.text,
                "source_type": chunk.source_type,
                "relevance_label": "unreviewed",
                "failure_type": "",
                "review_note": "",
            }
        )
    return rows


def evaluate_chunks(chunks: Sequence[Chunk], documents: Sequence[dict], questions: Sequence[str], top_k: int = 10) -> list[dict]:
    """동일 corpus와 질의를 두 구현에 넣고 엔진별 결과를 분리한다."""
    if not chunks:
        raise ValueError("평가할 chunk가 없습니다.")
    if not 1 <= top_k <= 100:
        raise ValueError("top_k는 1~100이어야 합니다.")

    schat = BM25Index(chunks)
    tokenized_corpus = [lexical_tokens(chunk.text + " " + chunk.section) for chunk in chunks]
    baseline = BM25Okapi(tokenized_corpus, k1=1.5, b=0.75)
    evaluations = []
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
            raise ValueError("평가 질문은 1~500자여야 합니다.")
        plan = plan_query(question, documents=documents)
        schat_scores = schat.scores(plan.expanded)
        schat_ranking = rank_bm25_candidates(
            plan.original, chunks, schat_scores, limit=max(40, top_k)
        )
        query_tokens = lexical_tokens(plan.expanded)
        baseline_scores = np.asarray(baseline.get_scores(query_tokens), dtype=np.float32)
        query_id = f"Q{index:03d}"
        schat_rows = _ranked_rows(
            "schat_bm25", schat_scores, chunks, query_id, question, plan.query, plan.expanded,
            top_k, ranking=schat_ranking
        )
        baseline_rows = _ranked_rows(
            "rank_bm25", baseline_scores, chunks, query_id, question, plan.query, plan.expanded, top_k
        )
        evaluations.append(
            {
                "query_id": query_id,
                "original_query": question,
                "normalized_query": plan.query,
                "expanded_query": plan.expanded,
                "query_tokens": query_tokens,
                "query_plan": asdict(plan),
                "schat_bm25": schat_rows,
                "rank_bm25": baseline_rows,
                "top10_overlap_chunk_ids": sorted(
                    {row["chunk_id"] for row in schat_rows} & {row["chunk_id"] for row in baseline_rows}
                ),
            }
        )
    return evaluations


def build_report(input_paths: Sequence[Path], questions: Sequence[str], counter: object, top_k: int = 10) -> dict:
    if not input_paths:
        raise ValueError("평가할 PDF·DOCX·XLSX 파일을 찾지 못했습니다.")
    documents, chunks, extraction = [], [], []
    for path in input_paths:
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError(f"지원하지 않는 파일 형식입니다: {path.name}")
        metadata, document_chunks, document_extraction = stable_evaluation_chunks(path, counter)
        documents.append(metadata)
        chunks.extend(document_chunks)
        extraction.append(document_extraction)
    evaluations = evaluate_chunks(chunks, documents, questions, top_k=top_k)
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "offline BM25 retrieval with deterministic temporal tiers; no vector search, RAG, or LLM",
        "comparison_contract": {
            "same_questions": True,
            "same_chunks": True,
            "same_corpus_tokenizer": "mvp.retrieval.lexical_tokens",
            "same_query_text": "QueryPlan.expanded",
            "mixed_ranking_created": False,
            "schat_engine": "mvp.retrieval.BM25Index",
            "schat_ranking": "match > neutral > mismatch stable temporal tiers within raw Top 40",
            "schat_ranking_query": "QueryPlan.original",
            "baseline_engine": "rank_bm25.BM25Okapi(k1=1.5, b=0.75)",
        },
        "top_k": top_k,
        "chunk_version": CHUNK_VERSION,
        "documents": extraction,
        "corpus": corpus_statistics(chunks, counter),
        "queries": evaluations,
        "manual_review": {
            "status": "pending",
            "labels": ["relevant", "partially_relevant", "irrelevant", "uncertain"],
            "note": "review.html에서 원문 근거를 사람이 판정한 뒤 지표를 확정합니다.",
        },
    }


def flatten_rows(report: dict, engine: str | None = None) -> Iterable[dict]:
    for query in report["queries"]:
        if engine in (None, "schat_bm25"):
            yield from query["schat_bm25"]
        if engine in (None, "rank_bm25"):
            yield from query["rank_bm25"]


def write_csv(report: dict, destination: Path, engine: str | None = None) -> None:
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flatten_rows(report, engine=engine))


def safe_json_for_script(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_review_html(report: dict) -> str:
    payload = safe_json_for_script(report)
    title = "SCHAT BM25 비교 검수"
    top_k = int(report.get("top_k", 10))
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme: light; --ink:#172033; --muted:#667085; --line:#d8dee9; --panel:#f7f9fc; --accent:#2357d9; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:system-ui,-apple-system,"Segoe UI",sans-serif; color:var(--ink); background:#fff; }}
    header {{ position:sticky; top:0; z-index:5; padding:18px 24px; border-bottom:1px solid var(--line); background:rgba(255,255,255,.96); }}
    h1 {{ margin:0 0 6px; font-size:22px; }}
    .warning {{ color:#8a4b08; font-size:13px; }}
    .controls {{ display:grid; grid-template-columns:minmax(240px,2fr) minmax(180px,1fr) auto auto; gap:10px; margin-top:14px; }}
    select,input,button {{ min-height:40px; border:1px solid var(--line); border-radius:8px; padding:8px 10px; background:#fff; color:var(--ink); }}
    button {{ cursor:pointer; }}
    button.primary {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
    main {{ padding:22px 24px 48px; }}
    .summary {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:18px; }}
    .pill {{ padding:7px 10px; border-radius:999px; background:#eef3ff; font-size:13px; }}
    .comparison {{ display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:18px; align-items:start; }}
    .engine {{ border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
    .engine h2 {{ margin:0; padding:14px 16px; background:var(--panel); font-size:17px; border-bottom:1px solid var(--line); }}
    .results {{ padding:12px; display:grid; gap:12px; }}
    article {{ border:1px solid var(--line); border-radius:10px; padding:12px; }}
    .meta {{ display:flex; flex-wrap:wrap; gap:7px; color:var(--muted); font-size:12px; margin-bottom:8px; }}
    .rank {{ color:var(--accent); font-weight:700; }}
    pre {{ white-space:pre-wrap; word-break:break-word; margin:10px 0; padding:10px; background:var(--panel); border-radius:7px; font:13px/1.5 ui-monospace,Consolas,monospace; }}
    .labels {{ display:flex; flex-wrap:wrap; gap:6px; }}
    .labels button {{ min-height:32px; padding:5px 8px; font-size:12px; }}
    .labels button.selected {{ outline:2px solid var(--accent); background:#eef3ff; }}
    textarea {{ width:100%; min-height:54px; margin-top:8px; border:1px solid var(--line); border-radius:7px; padding:8px; resize:vertical; }}
    .empty {{ padding:28px; color:var(--muted); text-align:center; }}
    @media (max-width:900px) {{ .comparison {{ grid-template-columns:1fr; }} .controls {{ grid-template-columns:1fr; }} header {{ position:static; }} }}
  </style>
</head>
<body>
<header>
  <h1>{escape(title)}</h1>
  <div class="warning">병원 지침 원문이 포함된 로컬 검수 파일입니다. 공개 저장소나 승인되지 않은 서비스에 업로드하지 마세요.</div>
  <div class="controls">
    <select id="query"></select>
    <input id="filter" type="search" placeholder="문서명·섹션·원문 필터">
    <button id="clear">현재 질문 판정 초기화</button>
    <button class="primary" id="export">판정 JSON 내려받기</button>
  </div>
</header>
<main>
  <div id="summary" class="summary"></div>
  <div class="comparison">
    <section class="engine"><h2>SCHAT BM25Index Top {top_k}</h2><div id="schat" class="results"></div></section>
    <section class="engine"><h2>rank-bm25 Top {top_k}</h2><div id="baseline" class="results"></div></section>
  </div>
</main>
<script id="report-data" type="application/json">{payload}</script>
<script>
(() => {{
  const report = JSON.parse(document.getElementById('report-data').textContent);
  const querySelect = document.getElementById('query');
  const filter = document.getElementById('filter');
  let labels = {{}};
  try {{ labels = JSON.parse(localStorage.getItem('schat-bm25-review-v1') || '{{}}'); }} catch (_) {{ labels = {{}}; }}
  const labelNames = {{relevant:'적절함', partially_relevant:'부분 적절함', irrelevant:'부적절함', uncertain:'판단 보류'}};
  for (const query of report.queries) {{
    const option = document.createElement('option');
    option.value = query.query_id;
    option.textContent = `${{query.query_id}} · ${{query.original_query}}`;
    querySelect.append(option);
  }}
  const keyFor = (queryId, chunkId) => `${{queryId}}::${{chunkId}}`;
  const save = () => {{ try {{ localStorage.setItem('schat-bm25-review-v1', JSON.stringify(labels)); }} catch (_) {{}} }};
  function metric(rows, k) {{
    return rows.slice(0, k).some(row => ['relevant','partially_relevant'].includes((labels[keyFor(row.query_id,row.chunk_id)] || {{}}).label));
  }}
  function renderSummary(query) {{
    const target = document.getElementById('summary'); target.replaceChildren();
    const overlap = query.top10_overlap_chunk_ids.length;
    const reviewed = new Set([...query.schat_bm25, ...query.rank_bm25]
      .filter(row => labels[keyFor(row.query_id,row.chunk_id)]).map(row => row.chunk_id)).size;
    const values = [`동일 Top 10 chunk: ${{overlap}}개`, `판정한 고유 chunk: ${{reviewed}}개`];
    for (const [name, rows] of [['SCHAT',query.schat_bm25],['rank-bm25',query.rank_bm25]]) {{
      values.push(`${{name}} Hit@1/3/5/10: ${{[1,3,5,10].map(k => metric(rows,k) ? 'Y' : '-').join(' / ')}}`);
    }}
    for (const value of values) {{ const node=document.createElement('span'); node.className='pill'; node.textContent=value; target.append(node); }}
  }}
  function renderRows(targetId, rows, query) {{
    const target = document.getElementById(targetId); target.replaceChildren();
    const term = filter.value.trim().toLocaleLowerCase('ko');
    const visible = rows.filter(row => !term || [row.document_name,row.section_title,row.chunk_text].join(' ').toLocaleLowerCase('ko').includes(term));
    if (!visible.length) {{ const node=document.createElement('div'); node.className='empty'; node.textContent='표시할 결과가 없습니다.'; target.append(node); return; }}
    for (const row of visible) {{
      const article=document.createElement('article');
      const meta=document.createElement('div'); meta.className='meta';
      const where=row.page_number ? `PDF ${{row.page_number}}페이지` : row.location || '위치 미확인';
      const phase=row.requested_temporal_phase ? `phase ${{row.requested_temporal_phase}} / ${{row.temporal_tier}}` : row.temporal_tier;
      const parts=[`#${{row.rank}}`, `score ${{row.bm25_score.toFixed(6)}}`, phase, row.document_name, where, row.section_title || '섹션 미입력', row.chunk_id].filter(Boolean);
      parts.forEach((value,index) => {{ const span=document.createElement('span'); span.textContent=value; if(index===0)span.className='rank'; meta.append(span); }});
      const pre=document.createElement('pre'); pre.textContent=row.chunk_text;
      const controls=document.createElement('div'); controls.className='labels';
      const key=keyFor(row.query_id,row.chunk_id);
      for (const [value,text] of Object.entries(labelNames)) {{
        const button=document.createElement('button'); button.type='button'; button.textContent=text;
        if ((labels[key] || {{}}).label===value) button.className='selected';
        button.addEventListener('click', () => {{ labels[key]={{...(labels[key]||{{}}),label:value}}; save(); render(); }});
        controls.append(button);
      }}
      const note=document.createElement('textarea'); note.placeholder='검수 메모 또는 실패 유형'; note.value=(labels[key]||{{}}).note||'';
      note.addEventListener('change', () => {{ labels[key]={{...(labels[key]||{{}}),note:note.value}}; save(); render(); }});
      article.append(meta,pre,controls,note); target.append(article);
    }}
  }}
  function render() {{
    const query=report.queries.find(item => item.query_id===querySelect.value) || report.queries[0];
    renderSummary(query); renderRows('schat',query.schat_bm25,query); renderRows('baseline',query.rank_bm25,query);
  }}
  querySelect.addEventListener('change', render); filter.addEventListener('input', render);
  document.getElementById('clear').addEventListener('click', () => {{
    const id=querySelect.value; for (const key of Object.keys(labels)) if(key.startsWith(id+'::')) delete labels[key]; save(); render();
  }});
  document.getElementById('export').addEventListener('click', () => {{
    const output={{schema_version:1,exported_at:new Date().toISOString(),source_generated_at:report.generated_at,labels}};
    const blob=new Blob([JSON.stringify(output,null,2)],{{type:'application/json'}}); const link=document.createElement('a');
    link.href=URL.createObjectURL(blob); link.download='relevance_labels.json'; link.click(); setTimeout(()=>URL.revokeObjectURL(link.href),1000);
  }});
  render();
}})();
</script>
</body>
</html>
"""


def write_outputs(report: dict, output_dir: Path) -> tuple[Path, ...]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "bm25_results.json"
    csv_path = output_dir / "bm25_results.csv"
    schat_csv_path = output_dir / "schat_bm25_top10.csv"
    baseline_csv_path = output_dir / "rank_bm25_top10.csv"
    html_path = output_dir / "review.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(report, csv_path)
    write_csv(report, schat_csv_path, engine="schat_bm25")
    write_csv(report, baseline_csv_path, engine="rank_bm25")
    html_path.write_text(render_review_html(report), encoding="utf-8")
    return csv_path, schat_csv_path, baseline_csv_path, json_path, html_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", dest="inputs", help="평가할 문서 경로(반복 가능)")
    parser.add_argument("--question", action="append", dest="questions", help="평가 질문(반복 가능)")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "artifacts" / f"{date.today().isoformat()}_bm25-evaluation"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    inputs = args.inputs or default_input_paths(ROOT / "data")
    questions = args.questions or list(DEFAULT_QUESTIONS)
    counter = Embedder()
    report = build_report(inputs, questions, counter, top_k=args.top_k)
    destinations = write_outputs(report, args.output_dir)
    print(
        json.dumps(
            {
                "documents": len(report["documents"]),
                "chunks": report["corpus"]["chunk_count"],
                "questions": len(report["queries"]),
                "outputs": [str(path) for path in destinations],
                "manual_review": "pending",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
