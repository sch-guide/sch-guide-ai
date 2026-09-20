"""Q006 zero-call 뒤 Q002 1회, 성공 시 Q001/Q003/Q004/Q005를 각 1회 평가합니다.

Raw response, prompt, API key, authorization header와 source-unit 원문은 저장하지 않습니다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from datetime import datetime
from html import escape
from pathlib import Path

import httpx
import numpy as np

from src.ai import (
    GROQ_REQUEST_TOKEN_BUDGET,
    answer_text,
    generate,
    prompt_messages,
)
from src.context import expand_context
from src.evidence import assess_evidence
from src.library import (
    NO_GUIDELINE,
    Embedder,
    Hit,
    bounded_embedding_question,
)
from src.query import plan_query
from src.retrieval import BM25Index, rank_bm25_candidates, rerank, rrf
from src.settings import ROOT, load_settings
from tools.bm25_evaluate import DEFAULT_QUESTIONS, stable_evaluation_chunks
from tools.rag_phase1_evaluate import DEFAULT_SOURCE, _stable_positions, stage_recall
from tools.rag_phase2_evaluate import _load_q002

DEFAULT_OUTPUT = ROOT / "workspace" / "RAG_실험" / "2026-09-14_rag-procedure-answer-coverage"
PILOT_IDS = ("Q001", "Q003", "Q004", "Q005")
COMMON_STOP_CODES = {"AI_AUTH", "AI_SERVER", "AI_RESPONSE", "AI_TIMEOUT", "AI_LIMIT"}


class EvaluationQuota:
    def __init__(self):
        self.reserved_tokens = None
        self.usage = None

    def reserve(self, settings, user_id, tokens):
        self.reserved_tokens = tokens
        return "live-evaluation-only"

    def settle(self, identifier, usage):
        self.usage = usage

    def cancel(self, identifier):
        pass


class RejectTransport(httpx.BaseTransport):
    def __init__(self):
        self.calls = 0

    def handle_request(self, request):
        self.calls += 1
        raise RuntimeError("Q006 attempted an external request")


class OneCallTransport(httpx.BaseTransport):
    """한 case의 실제 HTTP를 최대 한 번만 허용합니다."""

    def __init__(
        self,
        model,
        expected_chunk_ids,
        *,
        expected_source_unit_fingerprints=None,
        inner=None,
    ):
        self.model = model
        self.expected_chunk_ids = set(expected_chunk_ids)
        self.expected_source_unit_fingerprints = dict(
            expected_source_unit_fingerprints or {}
        )
        self.inner = inner or httpx.HTTPTransport(retries=0)
        self.calls = 0
        self.status_code = None

    def handle_request(self, request):
        if self.calls:
            raise RuntimeError("second request rejected")
        payload = json.loads(request.content)
        if payload.get("model") != self.model:
            raise RuntimeError("fallback model rejected")
        if any(key in payload for key in ("tools", "tool_choice")):
            raise RuntimeError("tool or web search rejected")
        if len(payload.get("messages", ())) != 2:
            raise RuntimeError("unexpected message count")
        envelope = json.loads(payload["messages"][1]["content"].split("Evidence groups (JSON):\n", 1)[1])
        if "source_units" in envelope:
            units = envelope.get("source_units")
            if not isinstance(units, list) or any(
                not isinstance(unit, dict)
                or set(unit) != {"id", "text"}
                or not isinstance(unit["id"], str)
                or not isinstance(unit["text"], str)
                for unit in units
            ):
                raise RuntimeError("unexpected source-unit catalog")
            actual = {
                unit["id"]: hashlib.sha256(unit["text"].encode()).hexdigest()
                for unit in units
            }
            if len(actual) != len(units) or actual != self.expected_source_unit_fingerprints:
                raise RuntimeError("prompt source units differ from admitted evidence")
        else:
            sent_ids = {
                source["chunk_id"]
                for group in envelope.get("groups", ())
                for source in group.get("sources", ())
            }
            if sent_ids != self.expected_chunk_ids:
                raise RuntimeError("prompt evidence differs from admitted evidence")
        self.calls += 1
        response = self.inner.handle_request(request)
        self.status_code = response.status_code
        return response

    def close(self):
        self.inner.close()


def safe_error_code(error, trace):
    if trace.get("llm_error_code"):
        return trace["llm_error_code"]
    match = re.search(r"\((AI_[A-Z_]+)\)", str(error))
    return match.group(1) if match else type(error).__name__


def safe_sources(answer, used_hits):
    if not answer or not answer.answerable:
        return []
    by_id = {hit.chunk.id: hit.chunk for hit in used_hits}
    cited = dict.fromkeys(
        evidence.chunk_id for statement in answer.statements for evidence in statement.evidence
    )
    return [
        {
            "document_name": by_id[chunk_id].document_name,
            "page": by_id[chunk_id].page,
            "section": by_id[chunk_id].section,
        }
        for chunk_id in cited
        if chunk_id in by_id
    ]


def coverage_summary(trace):
    coverage = trace.get("answer_coverage") or {}
    required_groups = {tuple_value(value) for value in coverage.get("required_group_keys", ())}
    selected_groups = {tuple_value(value) for value in coverage.get("selected_group_keys", ())}
    required_branches = set(coverage.get("required_branches", ()))
    selected_branches = set(coverage.get("selected_branches", ()))
    required_phases = {tuple(value) for value in coverage.get("required_phase_slots", ())}
    selected_phases = {tuple(value) for value in coverage.get("selected_phase_slots", ())}
    required_actions = {tuple(value) for value in coverage.get("required_action_slots", ())}
    selected_actions = {tuple(value) for value in coverage.get("selected_action_slots", ())}
    return {
        "required_group_count": len(required_groups),
        "covered_group_count": len(required_groups & selected_groups),
        "required_branches": sorted(required_branches),
        "covered_branches": sorted(required_branches & selected_branches),
        "required_phase_count": len(required_phases),
        "covered_phase_count": len(required_phases & selected_phases),
        "required_action_count": len(required_actions),
        "covered_action_count": len(required_actions & selected_actions),
        "required_action_families": list(coverage.get("required_action_families", ())),
        "selected_action_families": list(coverage.get("selected_action_families", ())),
        "complete": coverage.get("complete"),
        "reason": coverage.get("reason"),
    }


def tuple_value(value):
    return tuple(value) if isinstance(value, list) else value


def run_case(
    case_id,
    question,
    hits,
    plan,
    expected_selected,
    settings,
    *,
    expected_source_unit_fingerprints=None,
):
    transport = OneCallTransport(
        settings.llm_model,
        [hit.chunk.id for hit in expected_selected],
        expected_source_unit_fingerprints=expected_source_unit_fingerprints,
    )
    quota = EvaluationQuota()
    trace = {}
    answer = None
    used_hits = list(expected_selected)
    error_code = None
    started = time.perf_counter()
    try:
        answer, used_hits = generate(
            settings,
            question,
            hits,
            f"evaluation-{case_id.lower()}",
            quota=quota,
            transport=transport,
            plan=plan,
            trace=trace,
        )
    except Exception as error:  # noqa: BLE001 - persist a safe one-call result without retry.
        error_code = safe_error_code(error, trace)
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    statements = len(answer.statements) if answer else 0
    cited_statements = sum(bool(statement.evidence) for statement in answer.statements) if answer else 0
    citation_coverage = cited_statements / statements if statements else 0.0
    public_answer = answer_text(answer) if answer and answer.answerable else ""
    source_order = bool(answer and answer.answerable and trace.get("citation_assessment") == "supported")
    result = {
        "case_id": case_id,
        "question": question,
        "retrieved_hits": len(hits),
        "selected_evidence_chunks": len(used_hits),
        "selected_evidence_chunk_ids": [hit.chunk.id for hit in used_hits],
        "actual_groq_calls": transport.calls,
        "http_status": trace.get("response_http_status", transport.status_code),
        "model_returned": trace.get("response_model"),
        "finish_reason": trace.get("finish_reason"),
        "latency_ms": latency_ms,
        "tokens": trace.get("response_usage") or {},
        "reserved_tokens": quota.reserved_tokens,
        "response_parse_stage": trace.get("response_parse_stage"),
        "failure_code": error_code or trace.get("llm_error_code"),
        "validation_reason": trace.get("validation_reason"),
        "block_reason": trace.get("block_reason"),
        "selected_source_unit_count": trace.get("selected_source_unit_count"),
        "required_facet_count": trace.get("response_selection_facet_count"),
        "selected_facet_count": trace.get("selected_facet_count"),
        "group_counts": trace.get("selected_group_counts"),
        "coverage": coverage_summary(trace),
        "server_answerable": bool(answer and answer.answerable),
        "verified_statement_count": statements,
        "citation_coverage": citation_coverage,
        "citation_assessment": trace.get("citation_assessment"),
        "duplicate_evidence": trace.get("citation_assessment") == "duplicate_evidence",
        "citation_branch_metadata": trace.get("citation_branch_metadata"),
        "source_order_reversal": 0 if source_order else None,
        "unsupported": {
            key: 0 if answer and answer.answerable else None
            for key in ("number", "unit", "condition", "negation", "action")
        },
        "citations": safe_sources(answer, used_hits),
        "final_answer": {
            "stored": False,
            "answerable": bool(answer and answer.answerable),
            "statement_count": statements,
            "sha256": hashlib.sha256(public_answer.encode()).hexdigest() if public_answer else None,
        },
    }
    result["pass"] = (
        transport.calls == 0 and not result["server_answerable"] and not result["failure_code"]
    ) or (
        transport.calls == 1
        and result["http_status"] == 200
        and result["finish_reason"] == "stop"
        and result["response_parse_stage"] == "complete"
        and not result["failure_code"]
        and not result["validation_reason"]
        and result["server_answerable"]
        and 1 <= statements <= 16
        and citation_coverage == 1
        and result["citation_assessment"] == "supported"
        and not result["duplicate_evidence"]
        and result["source_order_reversal"] == 0
    )
    return result


def q002_gate(result):
    coverage = result["coverage"]
    selected_count = result["selected_source_unit_count"] or 0
    return all(
        (
            result["actual_groq_calls"] == 1,
            result["selected_evidence_chunks"] == 12,
            result["http_status"] == 200,
            result["finish_reason"] == "stop",
            result["response_parse_stage"] == "complete",
            result["failure_code"] is None,
            result["validation_reason"] is None,
            result["required_facet_count"] == result["selected_facet_count"] > 0,
            1 <= selected_count <= 16,
            coverage["required_group_count"] == coverage["covered_group_count"] == 5,
            set(coverage["required_branches"]) == set(coverage["covered_branches"]) == {"adult", "pediatric"},
            coverage["required_phase_count"] == coverage["covered_phase_count"] > 0,
            coverage["required_action_count"] == coverage["covered_action_count"] > 0,
            coverage["complete"] is True,
            result["server_answerable"],
            1 <= result["verified_statement_count"] <= 16,
            result["citation_coverage"] == 1,
            result["citation_assessment"] == "supported",
            not result["duplicate_evidence"],
            result["source_order_reversal"] == 0,
            all(value == 0 for value in result["unsupported"].values()),
        )
    )


def prepare_q002():
    metadata, gold, hits = _load_q002()
    plan = plan_query(gold["question"], documents=[metadata])
    assessment = assess_evidence(plan, hits)
    trace = {}
    _, selected, catalog, contract = prompt_messages(
        plan.query,
        assessment.hits,
        14000,
        token_budget=GROQ_REQUEST_TOKEN_BUDGET,
        plan=plan,
        groups=assessment.groups,
        trace=trace,
        return_catalog=True,
        return_contract=True,
    )
    after = assess_evidence(plan, selected)
    if not assessment.sufficient or not after.sufficient or len(selected) != 12:
        raise RuntimeError("Q002 evidence gate failed before live")
    headroom = trace["request_token_headroom"]
    required_headroom = max(256, math.ceil(trace["estimated_request_tokens"] * 0.08))
    if trace["estimated_request_tokens"] > GROQ_REQUEST_TOKEN_BUDGET or headroom < required_headroom:
        raise RuntimeError("Q002 budget gate failed before live")
    allowed_ids = {
        identifier
        for slot in contract.slots
        for identifier in getattr(slot, "eligible_source_unit_ids", ())
    }
    expected_source_unit_fingerprints = {
        unit.source_unit_id: hashlib.sha256(unit.exact_text.encode()).hexdigest()
        for unit in catalog
        if unit.source_unit_id in allowed_ids
    }
    if not expected_source_unit_fingerprints:
        raise RuntimeError("Q002 facet catalog is empty before live")
    return (
        metadata,
        gold,
        hits,
        plan,
        selected,
        trace,
        expected_source_unit_fingerprints,
    )


def pilot_corpus(*, chunk_version=4):
    model = Embedder()
    metadata, chunks, _ = stable_evaluation_chunks(
        DEFAULT_SOURCE, model, chunk_version=chunk_version
    )
    vectors = model.encode([chunk.text for chunk in chunks])
    return model, metadata, chunks, vectors


def retrieve(question, model, metadata, chunks, vectors):
    plan = plan_query(question, documents=[metadata])
    query_vector = model.encode([bounded_embedding_question(plan.expanded, model)])[0]
    dense_scores = np.dot(vectors, query_vector)
    dense_positions = _stable_positions(dense_scores)
    bm25_scores = BM25Index(chunks).scores(plan.expanded)
    bm25_ranking = rank_bm25_candidates(plan.original, chunks, bm25_scores, limit=40)
    bm25_positions = [position for position in bm25_ranking.positions if bm25_scores[position] > 0]
    fusion_scores = rrf(dense_positions, bm25_positions)
    candidates = [
        Hit(
            chunks[position],
            float(dense_scores[position]),
            bm25_score=float(bm25_scores[position]),
            fusion_score=float(fusion_scores[position]),
        )
        for position in sorted(set(dense_positions) | set(bm25_positions))
    ]
    seeds = rerank(plan, candidates, 0.38)
    hits = expand_context(question, seeds, chunks, plan.max_hits, plan=plan)
    assessment = assess_evidence(plan, hits)
    trace = {}
    if assessment.sufficient:
        _, selected = prompt_messages(
            plan.query,
            assessment.hits,
            14000,
            token_budget=GROQ_REQUEST_TOKEN_BUDGET,
            plan=plan,
            groups=assessment.groups,
            trace=trace,
        )
    else:
        selected = []
    return plan, hits, selected, assessment, trace


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _case_review_html(case):
    final_answer = case.get("final_answer") or {}
    answer = (
        "검증된 답변 본문은 보안 정책에 따라 저장하지 않음"
        if final_answer.get("answerable")
        else "검증된 최종 답변 없음"
    )
    citations = case.get("citations") or []
    citation_html = (
        "".join(
            f"<li>{escape(str(source.get('document_name') or '-'))} · "
            f"p.{escape(str(source.get('page') or '-'))} · "
            f"{escape(str(source.get('section') or '-'))}</li>"
            for source in citations
        )
        or "<li>표시할 검증 citation 없음</li>"
    )
    failure = next(
        (case.get(key) for key in ("failure_code", "validation_reason", "block_reason") if case.get(key)),
        "-",
    )
    coverage = case.get("coverage") or {}
    tokens = json.dumps(case.get("tokens") or {}, ensure_ascii=False)
    status = "PASS" if case.get("pass") else "FAIL"
    status_class = "pass" if case.get("pass") else "fail"
    return f'''<article class="case"><h2>{escape(str(case.get("case_id") or "-"))}</h2>
<p><strong>질문</strong>: {escape(str(case.get("question") or "-"))}</p>
<p class="{status_class}">{status}</p>
<div class="answer"><strong>검증된 최종 답변</strong><br>{escape(answer)}</div>
<h3>출처·citation</h3><ul>{citation_html}</ul>
<table><tr><th>retrieval / selected evidence</th><td>{case.get("retrieved_hits")} / {case.get("selected_evidence_chunks")}</td></tr>
<tr><th>selected source units</th><td>{case.get("selected_source_unit_count")}</td></tr>
<tr><th>phase coverage</th><td>{coverage.get("covered_phase_count", 0)}/{coverage.get("required_phase_count", 0)}</td></tr>
<tr><th>action coverage</th><td>{coverage.get("covered_action_count", 0)}/{coverage.get("required_action_count", 0)}</td></tr>
<tr><th>HTTP / finish</th><td>{case.get("http_status")} / {escape(str(case.get("finish_reason")))}</td></tr>
<tr><th>latency</th><td>{case.get("latency_ms")} ms</td></tr>
<tr><th>token usage</th><td>{escape(tokens)}</td></tr>
<tr><th>answerable / citation</th><td>{case.get("server_answerable")} / {float(case.get("citation_coverage") or 0) * 100:.1f}%</td></tr>
<tr><th>실패 사유</th><td>{escape(str(failure))}</td></tr></table></article>'''


def write_review(output, report, pilot):
    cases = [report["q002"], *((pilot or {}).get("cases") or [])]
    cards = "".join(_case_review_html(case) for case in cases)
    pilot_note = (
        "Q001/Q003/Q004/Q005 pilot 실행 완료"
        if pilot and len(pilot.get("cases") or []) == len(PILOT_IDS)
        else "Q002 미통과 또는 공통 오류로 일부/전체 pilot 미실행"
    )
    (output / "review.html").write_text(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>SCHAT Procedure AnswerCoverage Live</title><style>
body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 18px;color:#17202a;background:#f6f8fa}}
h1,h2,h3{{color:#123b55}}.case{{background:white;border:1px solid #ccd1d1;border-radius:14px;padding:20px;margin:18px 0}}
.answer{{white-space:pre-wrap;background:#eef6f7;border-radius:10px;padding:14px;line-height:1.65}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}th,td{{border:1px solid #ccd1d1;padding:8px;text-align:left}}
th{{background:#eef3f6;width:28%}}.pass{{color:#196f3d;font-weight:700}}.fail{{color:#a93226;font-weight:700}}
</style></head><body><h1>Broad procedure AnswerCoverage Live 검수</h1>
<p>생성: {escape(report["generated_at"])}</p><p>{escape(pilot_note)}</p>{cards}
<p>전체 prompt, raw provider response/content, API key와 Authorization header는 저장하지 않았습니다.</p>
</body></html>""",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    q006 = {
        "transport_calls": 0,
        "llm_called": False,
        "fixed_abstention": None,
        "pass": False,
    }
    q002 = {
        "case_id": "Q002",
        "question": DEFAULT_QUESTIONS[1],
        "actual_groq_calls": 0,
        "failure_code": "not_started",
        "pass": False,
        "http_status": None,
        "finish_reason": None,
        "selected_source_unit_count": 0,
        "verified_statement_count": 0,
        "citation_coverage": 0.0,
        "coverage": {
            "required_phase_count": 0,
            "covered_phase_count": 0,
            "required_action_count": 0,
            "covered_action_count": 0,
        },
    }
    report = {
        "schema_version": 1,
        "generated_at": generated_at,
        "constraints": {
            "retry": 0,
            "fallback": 0,
            "web_search": 0,
            "maximum_actual_groq_calls": 5,
            "raw_response_stored": False,
            "prompt_stored": False,
            "exact_source_unit_text_stored": False,
        },
        "q006": q006,
        "q002": q002,
        "q002_rag_complete": False,
        "verdict": "LIVE_NOT_STARTED",
    }
    pilot = None
    try:
        settings = load_settings(use_streamlit=False)
        settings.llm_endpoint()
        (
            metadata,
            gold,
            hits,
            plan,
            selected,
            prompt_trace,
            source_unit_fingerprints,
        ) = prepare_q002()
        reject = RejectTransport()
        q006_trace = {}
        q006_answer, q006_hits = generate(
            settings,
            DEFAULT_QUESTIONS[5],
            [],
            "evaluation-q006",
            plan=plan_query(DEFAULT_QUESTIONS[5], documents=[metadata]),
            trace=q006_trace,
            transport=reject,
        )
        q006 = {
            "transport_calls": reject.calls,
            "llm_called": q006_trace.get("llm_called", False),
            "catalog_units": 0,
            "fixed_abstention": answer_text(q006_answer),
            "pass": reject.calls == 0 and q006_hits == [] and answer_text(q006_answer) == NO_GUIDELINE,
        }
        report["q006"] = q006
        if not q006["pass"]:
            raise RuntimeError("Q006 zero-call gate failed")

        q002 = run_case(
            "Q002",
            gold["question"],
            hits,
            plan,
            selected,
            settings,
            expected_source_unit_fingerprints=source_unit_fingerprints,
        )
        q002["retrieval_gold"] = {
            "pre_required": stage_recall(gold, [hit.chunk.id for hit in hits])["required"],
            "post_required": stage_recall(gold, [hit.chunk.id for hit in selected])["required"],
        }
        q002["prompt_budget"] = {
            "reservation": prompt_trace["estimated_request_tokens"],
            "headroom": prompt_trace["request_token_headroom"],
        }
        report["q002"] = q002
        report["q002_rag_complete"] = q002_gate(q002)
        report["verdict"] = (
            "Q002 기준 RAG 엔진 Live 완주"
            if report["q002_rag_complete"]
            else "Q002 기준 RAG 엔진 Live 완주 실패"
        )

        if report["q002_rag_complete"]:
            model, pilot_metadata, chunks, vectors = pilot_corpus()
            pilot_cases = []
            stopped = False
            for case_id in PILOT_IDS:
                question = DEFAULT_QUESTIONS[int(case_id[1:]) - 1]
                plan, case_hits, case_selected, assessment, retrieval_trace = retrieve(
                    question, model, pilot_metadata, chunks, vectors
                )
                case = run_case(case_id, question, case_hits, plan, case_selected, settings)
                case["retrieval"] = {
                    "evidence_sufficient": assessment.sufficient,
                    "evidence_reason": assessment.reason,
                    "prompt_selected_chunks": len(case_selected),
                    "reservation": retrieval_trace.get("estimated_request_tokens"),
                    "headroom": retrieval_trace.get("request_token_headroom"),
                }
                pilot_cases.append(case)
                if (
                    case["failure_code"] in COMMON_STOP_CODES
                    or case["validation_reason"] == "selection_schema"
                ):
                    stopped = True
                    break
            pilot = {
                "schema_version": 1,
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "cases": pilot_cases,
                "stopped_on_common_error": stopped,
                "actual_groq_calls": sum(case["actual_groq_calls"] for case in pilot_cases),
                "all_executed_passed": (
                    len(pilot_cases) == len(PILOT_IDS) and all(case["pass"] for case in pilot_cases)
                ),
            }
    except Exception as error:  # noqa: BLE001 - always write the safe evaluation report.
        if report["verdict"] == "LIVE_NOT_STARTED":
            report["verdict"] = "LIVE_PRECONDITION_OR_RUNTIME_FAILURE"
            report["failure_code"] = type(error).__name__
    finally:
        pilot_calls = pilot["actual_groq_calls"] if pilot else 0
        report["actual_groq_calls"] = report["q002"].get("actual_groq_calls", 0) + pilot_calls
        write_json(args.output / "live_report.json", report)
        if pilot is not None:
            write_json(args.output / "pilot_report.json", pilot)
        write_review(args.output, report, pilot)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "q006_calls": report["q006"].get("transport_calls"),
                    "q002_calls": report["q002"].get("actual_groq_calls"),
                    "q002_rag_complete": report["q002_rag_complete"],
                    "pilot_calls": pilot_calls,
                    "total_actual_groq_calls": report["actual_groq_calls"],
                    "verdict": report["verdict"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
