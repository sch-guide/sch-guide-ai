"""LLM 호출 없이 Q002 Hybrid retrieval/context 1차 결과를 재현합니다."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, replace
from datetime import datetime
from html import escape
from pathlib import Path

import numpy as np

from src.context import (
    _procedure_expansion,
    _procedure_signature,
    expand_context,
    neighbors,
)
from src.evidence import assess_evidence
from src.library import NO_GUIDELINE, Embedder, Hit, bounded_embedding_question, clean, has_substantive_body
from src.query import plan_query
from src.retrieval import BM25Index, rank_bm25_candidates, rerank, rrf
from src.settings import ROOT
from tools.bm25_evaluate import stable_evaluation_chunks

QUESTION = "진정간호 절차는?"
NEGATIVE_QUESTION = "화성 우주선의 궤도 계산 공식은?"
DEFAULT_SOURCE = ROOT / "data" / "실무지침서_진정간호.pdf"
DEFAULT_GOLD = ROOT / "tests" / "fixtures" / "q002_gold_stages.json"
DEFAULT_OUTPUT = ROOT / "workspace" / "RAG_실험" / "2026-09-13_rag-phase1-retrieval"


def _stable_positions(scores, limit=40):
    return sorted(range(len(scores)), key=lambda position: (-float(scores[position]), position))[:limit]


def _legacy_expand_context(seeds, chunks, limit=12):
    """1차 변경 전 round-robin evidence를 비교용으로만 재현합니다."""
    selected, seen = [], set()
    groups = [[seed.chunk] + [chunk for chunk in neighbors(seed.chunk, chunks)
                             if chunk.id != seed.chunk.id] for seed in seeds]
    for depth in range(max((len(group) for group in groups), default=0)):
        for seed, group in zip(seeds, groups, strict=True):
            if depth >= len(group):
                continue
            chunk = group[depth]
            signature = (chunk.document_id, chunk.page, chunk.location, clean(chunk.text))
            if signature not in seen:
                selected.append(seed if depth == 0 else replace(
                    seed, chunk=chunk, lexical=0, bm25_score=0, context_only=True
                ))
                seen.add(signature)
            if len(selected) >= limit:
                return selected
    return selected


def _rows(name, positions, chunks, dense_scores, bm25_scores, fusion_scores, rank_limit=40):
    rows = []
    for rank, position in enumerate(positions[:rank_limit], start=1):
        chunk = chunks[position]
        rows.append({
            "funnel_stage": name,
            "rank": rank,
            "chunk_id": chunk.id,
            "chunk_index": chunk.index,
            "page": chunk.page,
            "section": chunk.section,
            "text": chunk.text,
            "semantic_score": float(dense_scores[position]),
            "bm25_score": float(bm25_scores[position]),
            "rrf_score": float(fusion_scores.get(position, 0)),
        })
    return rows


def _hit_rows(name, hits):
    return [{
        "funnel_stage": name,
        "rank": rank,
        "chunk_id": hit.chunk.id,
        "chunk_index": hit.chunk.index,
        "page": hit.chunk.page,
        "section": hit.chunk.section,
        "text": hit.chunk.text,
        "semantic_score": float(hit.similarity),
        "bm25_score": float(hit.bm25_score),
        "rrf_score": float(hit.fusion_score),
        "rerank_score": float(hit.rerank_score),
        "context_only": hit.context_only,
        "context_complete": hit.context_complete,
    } for rank, hit in enumerate(hits, start=1)]


def stage_recall(gold, chunk_ids):
    found = set(chunk_ids)
    details = []
    for stage in gold["stages"]:
        matched = sorted(found.intersection(stage["allowed_chunk_ids"]))
        details.append({**stage, "recalled": bool(matched), "matched_chunk_ids": matched})
    required = [row for row in details if row["importance"] == "required"]
    supplemental = [row for row in details if row["importance"] == "supplemental"]
    recalled_required = sum(row["recalled"] for row in required)
    recalled_supplemental = sum(row["recalled"] for row in supplemental)
    return {
        "required": {"recalled": recalled_required, "total": len(required),
                     "recall": recalled_required / len(required) if required else 1.0},
        "supplemental": {"recalled": recalled_supplemental, "total": len(supplemental),
                         "recall": recalled_supplemental / len(supplemental) if supplemental else 1.0},
        "details": details,
    }


def _duplicate_audit(seeds, chunks, final_hits):
    raw = [chunk for seed in seeds for chunk in _procedure_expansion(seed.chunk, chunks)
           if has_substantive_body(chunk)]
    seen, removed = {}, []
    for chunk in raw:
        signature = _procedure_signature(chunk, chunks)
        if signature in seen:
            removed.append({"kept_chunk_id": seen[signature], "removed_chunk_id": chunk.id})
        else:
            seen[signature] = chunk.id
    final_signatures = [_procedure_signature(hit.chunk, chunks) for hit in final_hits]
    return {
        "raw_expansion_occurrences": len(raw),
        "unique_branch_aware_exact_signatures": len(set(seen)),
        "removed_occurrences": removed,
        "final_duplicate_count": len(final_signatures) - len(set(final_signatures)),
    }


def _source_order_audit(hits):
    by_document = {}
    for hit in hits:
        by_document.setdefault(hit.chunk.document_id, []).append(hit.chunk.index)
    return {
        "valid": all(indexes == sorted(indexes) for indexes in by_document.values()),
        "document_indexes": by_document,
    }


def build_report(source, gold_path):
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    model = Embedder()
    metadata, chunks, extraction = stable_evaluation_chunks(source, model)
    if metadata["id"] != gold["document_id"] or extraction["sha256"] != gold["source_sha256"]:
        raise ValueError("Q002 gold fixture와 평가 원문 identity가 일치하지 않습니다.")
    chunk_ids = {chunk.id for chunk in chunks}
    missing = sorted({identifier for stage in gold["stages"]
                      for identifier in stage["allowed_chunk_ids"]} - chunk_ids)
    if missing:
        raise ValueError(f"gold fixture chunk가 현재 코퍼스에 없습니다: {missing}")

    documents = [metadata]
    plan = plan_query(QUESTION, documents=documents)
    vectors = model.encode([chunk.text for chunk in chunks])
    query_vector = model.encode([bounded_embedding_question(plan.expanded, model)])[0]
    dense_scores = np.dot(vectors, query_vector)
    dense_positions = _stable_positions(dense_scores)

    bm25_scores = BM25Index(chunks).scores(plan.expanded)
    bm25_ranking = rank_bm25_candidates(plan.original, chunks, bm25_scores, limit=40)
    bm25_positions = [position for position in bm25_ranking.positions if bm25_scores[position] > 0]
    fusion_scores = rrf(dense_positions, bm25_positions)
    rrf_positions = sorted(fusion_scores, key=lambda position: (-fusion_scores[position], position))[:40]

    candidates = [Hit(
        chunks[position], float(dense_scores[position]), bm25_score=float(bm25_scores[position]),
        fusion_score=float(fusion_scores[position]),
    ) for position in sorted(set(dense_positions) | set(bm25_positions))]
    seeds = rerank(plan, candidates, .38)
    baseline_hits = _legacy_expand_context(seeds, chunks, plan.max_hits)
    improved_hits = expand_context(QUESTION, seeds, chunks, plan.max_hits)

    funnel = {
        "bm25_top40": _rows("bm25_top40", bm25_positions, chunks, dense_scores,
                            bm25_scores, fusion_scores),
        "semantic_top40": _rows("semantic_top40", dense_positions, chunks, dense_scores,
                                bm25_scores, fusion_scores),
        "rrf_top40": _rows("rrf_top40", rrf_positions, chunks, dense_scores,
                           bm25_scores, fusion_scores),
        "rerank_seeds": _hit_rows("rerank_seeds", seeds),
        "baseline_context": _hit_rows("baseline_context", baseline_hits),
        "improved_evidence": _hit_rows("improved_evidence", improved_hits),
    }
    recalls = {name: stage_recall(gold, [row["chunk_id"] for row in rows])
               for name, rows in funnel.items()}

    negative_plan = plan_query(NEGATIVE_QUESTION, documents=documents)
    negative_vector = model.encode([bounded_embedding_question(negative_plan.expanded, model)])[0]
    negative_dense = np.dot(vectors, negative_vector)
    negative_bm25 = BM25Index(chunks).scores(negative_plan.expanded)
    negative_bm25_ranking = rank_bm25_candidates(
        negative_plan.original, chunks, negative_bm25, limit=40
    )
    negative_lexical = [position for position in negative_bm25_ranking.positions
                        if negative_bm25[position] > 0]
    negative_dense_positions = _stable_positions(negative_dense)
    negative_fusion = rrf(negative_dense_positions, negative_lexical)
    negative_candidates = [Hit(
        chunks[position], float(negative_dense[position]),
        bm25_score=float(negative_bm25[position]),
        fusion_score=float(negative_fusion[position]),
    ) for position in sorted(set(negative_dense_positions) | set(negative_lexical))]
    negative_seeds = rerank(negative_plan, negative_candidates, .38)
    negative_hits = expand_context(NEGATIVE_QUESTION, negative_seeds, chunks,
                                   negative_plan.max_hits)
    negative_assessment = assess_evidence(negative_plan, negative_hits)

    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "RAG phase 1 retrieval/context evaluation; no Groq or other LLM calls",
        "source": extraction,
        "embedding": {"model": metadata.get("model"), "dimensions": int(vectors.shape[1])},
        "query": {"id": "Q002", "question": QUESTION, "plan": asdict(plan)},
        "gold": gold,
        "funnel": funnel,
        "stage_recall": recalls,
        "comparison": {
            "baseline_evidence_ids": [hit.chunk.id for hit in baseline_hits],
            "improved_evidence_ids": [hit.chunk.id for hit in improved_hits],
            "added_ids": sorted({hit.chunk.id for hit in improved_hits} -
                                {hit.chunk.id for hit in baseline_hits}),
            "removed_ids": sorted({hit.chunk.id for hit in baseline_hits} -
                                  {hit.chunk.id for hit in improved_hits}),
        },
        "duplicate_audit": _duplicate_audit(seeds, chunks, improved_hits),
        "source_order_audit": _source_order_audit(improved_hits),
        "branch_audit": {
            "adult_present": any("[성인]" in hit.chunk.text for hit in improved_hits),
            "pediatric_present": any("[소아]" in hit.chunk.text for hit in improved_hits),
        },
        "q006": {
            "question": NEGATIVE_QUESTION,
            "domain": negative_plan.domain,
            "positive_bm25_candidates": len(negative_lexical),
            "retrieved_hit_ids": [hit.chunk.id for hit in negative_hits],
            "evidence_sufficient": negative_assessment.sufficient,
            "evidence_reason": negative_assessment.reason,
            "final_behavior": NO_GUIDELINE if not negative_assessment.sufficient else "unexpected_answerable",
            "llm_called": False,
        },
    }


def _write_csv(path, report):
    fields = ["funnel_stage", "rank", "chunk_id", "chunk_index", "page", "section", "text",
              "semantic_score", "bm25_score", "rrf_score", "rerank_score", "context_only",
              "context_complete", "required_recalled", "required_total", "required_recall"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for name, rows in report["funnel"].items():
            summary = report["stage_recall"][name]["required"]
            for row in rows:
                writer.writerow({**row, "required_recalled": summary["recalled"],
                                 "required_total": summary["total"],
                                 "required_recall": summary["recall"]})


def _write_html(path, report):
    cards = []
    for name in ("rerank_seeds", "baseline_context", "improved_evidence"):
        recall = report["stage_recall"][name]["required"]
        rows = "".join(
            f"<tr><td>{row['rank']}</td><td><code>{escape(row['chunk_id'].rsplit('-', 1)[-1])}</code></td>"
            f"<td>{row['chunk_index']}</td><td>{escape(str(row['section']))}</td>"
            f"<td><pre>{escape(row['text'])}</pre></td></tr>"
            for row in report["funnel"][name]
        )
        cards.append(
            f"<section><h2>{escape(name)}</h2><p>필수 단계 recall: "
            f"{recall['recalled']}/{recall['total']} ({recall['recall']:.1%})</p>"
            f"<table><thead><tr><th>순위</th><th>chunk</th><th>원문 순서</th><th>section</th>"
            f"<th>원문</th></tr></thead><tbody>{rows}</tbody></table></section>"
        )
    stages = "".join(
        f"<tr><td>{stage['source_order']}</td><td>{escape(stage['branch'])}</td>"
        f"<td>{escape(stage['importance'])}</td><td>{escape(stage['label'])}</td>"
        f"<td><code>{escape(', '.join(identifier.rsplit('-', 1)[-1] for identifier in stage['allowed_chunk_ids']))}</code></td></tr>"
        for stage in report["gold"]["stages"]
    )
    q006 = report["q006"]
    path.write_text(f"""<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">
<title>SCHAT RAG 1차 retrieval 검수</title><style>
body{{font-family:system-ui,sans-serif;max-width:1400px;margin:24px auto;padding:0 16px;color:#17202a}}
table{{border-collapse:collapse;width:100%;margin-bottom:32px}}th,td{{border:1px solid #ccd1d1;padding:8px;vertical-align:top;text-align:left}}
th{{background:#eef3f6}}pre{{white-space:pre-wrap;margin:0;font-family:inherit}}code{{white-space:nowrap}}
.ok{{color:#087830;font-weight:700}}section{{margin:36px 0}}
</style></head><body><h1>Q002 Hybrid retrieval / evidence bundle 검수</h1>
<p>생성: {escape(report['generated_at'])} · LLM 호출 없음</p>
<p class=\"ok\">source order: {report['source_order_audit']['valid']} · final duplicate: {report['duplicate_audit']['final_duplicate_count']} · 성인/소아 분기: {report['branch_audit']['adult_present']}/{report['branch_audit']['pediatric_present']}</p>
<h2>Q002 gold stage</h2><table><thead><tr><th>원문 순서</th><th>분기</th><th>구분</th><th>단위</th><th>허용 chunk</th></tr></thead><tbody>{stages}</tbody></table>
{''.join(cards)}
<section><h2>Q006 답 없음</h2><p>양수 BM25 후보: {q006['positive_bm25_candidates']} · evidence sufficient: {q006['evidence_sufficient']} · reason: {escape(q006['evidence_reason'])} · LLM 호출: {q006['llm_called']}</p><p><strong>{escape(q006['final_behavior'])}</strong></p></section>
</body></html>""", encoding="utf-8")


def write_report(report, output):
    output.mkdir(parents=True, exist_ok=False)
    (output / "q002_gold_stages.json").write_text(
        json.dumps(report["gold"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "q002_retrieval_funnel.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "q006_no_answer.json").write_text(
        json.dumps(report["q006"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_csv(output / "q002_retrieval_funnel.csv", report)
    _write_html(output / "review.html", report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report(args.source, args.gold)
    write_report(report, args.output)
    print(json.dumps({
        "output": str(args.output),
        "required_recall_before": report["stage_recall"]["baseline_context"]["required"],
        "required_recall_after": report["stage_recall"]["improved_evidence"]["required"],
        "q006": report["q006"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
