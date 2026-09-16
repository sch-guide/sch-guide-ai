"""실제 LLM 없이 evidence group, budget gate와 mock generation을 평가합니다."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from html import escape

import httpx

from mvp.ai import (
    GROQ_REQUEST_TOKEN_BUDGET,
    answer_text,
    estimated_tokens,
    generate,
    prompt_messages,
)
from mvp.evidence import assess_evidence, required_coverage_loss
from mvp.library import NO_GUIDELINE, Chunk, Embedder, Hit
from mvp.query import plan_query
from mvp.settings import ROOT, Settings
from tools.bm25_evaluate import stable_evaluation_chunks
from tools.rag_phase1_evaluate import DEFAULT_GOLD, DEFAULT_SOURCE, stage_recall

PHASE1_REPORT = ROOT / "artifacts" / "2026-09-13_rag-phase1-retrieval" / "q002_retrieval_funnel.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "2026-09-13_rag-phase2-evidence-generation"


def _mock_groq_settings():
    return Settings(llm_provider="groq_free", llm_key="fixture",
                    llm_model="openai/gpt-oss-20b", llm_approved=True,
                    groq_free_confirmed=True)


class _MockQuota:
    def __init__(self):
        self.reservations = []
        self.settlements = []

    def reserve(self, settings, user_id, tokens):
        self.reservations.append((user_id, tokens))
        return "mock-reservation"

    def settle(self, identifier, usage):
        self.settlements.append((identifier, usage))

    def cancel(self, identifier):
        pass


def _coverage(coverage):
    return coverage.__dict__ if coverage else None


def _group_rows(groups):
    return [{
        "group_key": group.key,
        "required": group.required,
        "requirement_reason": group.requirement_reason,
        "branch": group.branch,
        "complete": group.complete,
        "source_start": group.source_start,
        "source_end": group.source_end,
        "chunk_ids": [hit.chunk.id for hit in group.hits],
    } for group in groups]


def _load_q002():
    phase1 = json.loads(PHASE1_REPORT.read_text(encoding="utf-8"))
    gold = json.loads(DEFAULT_GOLD.read_text(encoding="utf-8"))
    model = Embedder()
    metadata, chunks, _ = stable_evaluation_chunks(DEFAULT_SOURCE, model)
    by_id = {chunk.id: chunk for chunk in chunks}
    hits = [Hit(
        by_id[row["chunk_id"]], row["semantic_score"], bm25_score=row["bm25_score"],
        fusion_score=row["rrf_score"], rerank_score=row["rerank_score"],
        context_only=row["context_only"], context_complete=row["context_complete"],
    ) for row in phase1["funnel"]["improved_evidence"]]
    return metadata, gold, hits


def _mock_success():
    source = Chunk("mock-source", "mock-doc", "가상지침.pdf", 1, "가상지침", "PCN 교육",
                   None, "PCN 교육 절차를 확인한다.", 0)
    hit = Hit(source, .8, bm25_score=.5)
    plan = plan_query("PCN 교육 절차는?")
    calls = []

    def handle(request):
        calls.append(request)
        statement = {"text": source.text, "evidence": [{"chunk_id": source.id, "quote": source.text}]}
        content = json.dumps({"answerable": True, "statements": [statement]}, ensure_ascii=False)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}], "usage": {"total_tokens": 20}
        })

    settings = _mock_groq_settings()
    trace = {}
    answer, selected = generate(
        settings, plan.query, [hit], "fixture", _MockQuota(),
        httpx.MockTransport(handle), plan=plan, trace=trace,
    )
    return {
        "http_calls": len(calls), "answerable": answer.answerable,
        "statement_count": len(answer.statements),
        "selected_chunk_ids": [item.chunk.id for item in selected], "trace": trace,
    }


def build_report():
    metadata, gold, hits = _load_q002()
    plan = plan_query(gold["question"], documents=[metadata])
    before = assess_evidence(plan, hits)
    prompt_trace = {}
    messages, selected = prompt_messages(
        plan.query, before.hits, 14000, token_budget=GROQ_REQUEST_TOKEN_BUDGET,
        plan=plan, groups=before.groups, trace=prompt_trace,
    )
    after = assess_evidence(plan, selected)
    loss = required_coverage_loss(before.procedure_coverage, after.procedure_coverage)
    full_messages, _ = prompt_messages(
        plan.query, before.hits, 14000, plan=plan, groups=before.groups,
    )

    q002_calls = []
    q002_trace = {}
    q002_answer, _ = generate(
        _mock_groq_settings(),
        plan.query, hits, "fixture", plan=plan, trace=q002_trace,
        transport=httpx.MockTransport(lambda request: q002_calls.append(request)),
    )

    q006_plan = plan_query("화성 우주선의 궤도 계산 공식은?", documents=[metadata])
    q006_calls = []
    q006_trace = {}
    q006_answer, _ = generate(
        Settings(), q006_plan.query, [], "fixture", plan=q006_plan, trace=q006_trace,
        transport=httpx.MockTransport(lambda request: q006_calls.append(request)),
    )

    pre_ids = [hit.chunk.id for hit in before.hits]
    post_ids = [hit.chunk.id for hit in after.hits]
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "evidence/group budget and MockTransport only; no external LLM call",
        "q002": {
            "question": gold["question"],
            "pre_assessment": before.reason,
            "pre_groups": _group_rows(before.groups),
            "pre_coverage": _coverage(before.procedure_coverage),
            "pre_gold_recall": stage_recall(gold, pre_ids),
            "full_prompt_estimated_tokens": estimated_tokens(full_messages, "groq_free"),
            "token_budget": GROQ_REQUEST_TOKEN_BUDGET,
            "selected_chunk_ids": [hit.chunk.id for hit in selected],
            "selected_prompt_estimated_tokens": estimated_tokens(messages, "groq_free") if messages else 0,
            "prompt_trace": prompt_trace,
            "post_assessment": after.reason,
            "post_coverage": _coverage(after.procedure_coverage),
            "post_gold_recall": stage_recall(gold, post_ids),
            "coverage_loss": loss,
            "generate_result": answer_text(q002_answer),
            "mock_http_calls": len(q002_calls),
            "generate_trace": q002_trace,
        },
        "mock_sufficient_case": _mock_success(),
        "q006": {
            "question": q006_plan.original,
            "domain": q006_plan.domain,
            "result": answer_text(q006_answer),
            "mock_http_calls": len(q006_calls),
            "trace": q006_trace,
        },
        "no_guideline_message": NO_GUIDELINE,
    }


def _write_csv(path, report):
    fields = ("group_key", "required", "requirement_reason", "branch", "complete",
              "source_start", "source_end", "chunk_ids", "selected", "exclusion_reason")
    selected = set(report["q002"]["selected_chunk_ids"])
    exclusions = {row["group_key"]: row["reason"]
                  for row in report["q002"]["prompt_trace"]["prompt_excluded_groups"]}
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in report["q002"]["pre_groups"]:
            writer.writerow({
                **row, "chunk_ids": "|".join(row["chunk_ids"]),
                "selected": all(identifier in selected for identifier in row["chunk_ids"]),
                "exclusion_reason": exclusions.get(row["group_key"], ""),
            })


def _write_html(path, report):
    q002 = report["q002"]
    selected = set(q002["selected_chunk_ids"])
    exclusions = {row["group_key"]: row["reason"] for row in q002["prompt_trace"]["prompt_excluded_groups"]}
    groups = "".join(
        f"<tr><td>{'required' if group['required'] else 'optional'}</td>"
        f"<td>{escape(group['requirement_reason'])}</td><td>{escape(group['branch'])}</td>"
        f"<td>{escape(', '.join(item.rsplit('-', 1)[-1] for item in group['chunk_ids']))}</td>"
        f"<td>{'포함' if all(item in selected for item in group['chunk_ids']) else '제외'}</td>"
        f"<td>{escape(exclusions.get(group['group_key'], ''))}</td></tr>"
        for group in q002["pre_groups"]
    )
    details = "".join(
        f"<tr><td>{stage['source_order']}</td><td>{escape(stage['importance'])}</td>"
        f"<td>{escape(stage['branch'])}</td><td>{escape(stage['label'])}</td>"
        f"<td>{'예' if stage['recalled'] else '아니오'}</td></tr>"
        for stage in q002["post_gold_recall"]["details"]
    )
    path.write_text(f"""<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">
<title>SCHAT RAG 2차 Evidence 검수</title><style>
body{{font-family:system-ui,sans-serif;max-width:1200px;margin:24px auto;padding:0 16px;color:#17202a}}
table{{border-collapse:collapse;width:100%;margin:20px 0 36px}}th,td{{border:1px solid #ccd1d1;padding:8px;text-align:left}}
th{{background:#eef3f6}}.stop{{color:#a93226;font-weight:700}}code{{white-space:nowrap}}
</style></head><body><h1>Q002 Evidence group / budget 수동 검수</h1>
<p>실제 Groq 호출 없음 · MockTransport만 사용 · 생성 {escape(report['generated_at'])}</p>
<p>전체 prompt 예상 {q002['full_prompt_estimated_tokens']} tokens / 한도 {q002['token_budget']}; 선택 prompt {q002['selected_prompt_estimated_tokens']} tokens</p>
<p class=\"stop\">coverage loss: {escape(q002['coverage_loss'])} · Q002 mock HTTP 호출: {q002['mock_http_calls']}회</p>
<h2>Evidence groups</h2><table><thead><tr><th>구분</th><th>근거</th><th>분기</th><th>chunks</th><th>budget</th><th>제외 이유</th></tr></thead><tbody>{groups}</tbody></table>
<h2>Post-budget Q002 gold stage</h2><table><thead><tr><th>순서</th><th>gold 구분</th><th>분기</th><th>단위</th><th>유지</th></tr></thead><tbody>{details}</tbody></table>
<h2>Mock 경계</h2><p>충분한 synthetic case HTTP 호출: {report['mock_sufficient_case']['http_calls']}회 · answerable: {report['mock_sufficient_case']['answerable']}</p>
<p>Q006 HTTP 호출: {report['q006']['mock_http_calls']}회 · 결과: <strong>{escape(report['q006']['result'])}</strong></p>
</body></html>""", encoding="utf-8")


def main():
    report = build_report()
    DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=False)
    (DEFAULT_OUTPUT / "evidence_generation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_csv(DEFAULT_OUTPUT / "q002_evidence_groups.csv", report)
    _write_html(DEFAULT_OUTPUT / "review.html", report)
    print(json.dumps({
        "output": str(DEFAULT_OUTPUT),
        "q002_pre_required_recall": report["q002"]["pre_gold_recall"]["required"],
        "q002_post_required_recall": report["q002"]["post_gold_recall"]["required"],
        "q002_coverage_loss": report["q002"]["coverage_loss"],
        "q002_http_calls": report["q002"]["mock_http_calls"],
        "sufficient_mock_http_calls": report["mock_sufficient_case"]["http_calls"],
        "q006_http_calls": report["q006"]["mock_http_calls"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
