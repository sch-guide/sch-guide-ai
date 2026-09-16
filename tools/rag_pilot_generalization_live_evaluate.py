"""Q006 zero-call 뒤 Q001~Q005를 각각 최대 한 번 Live 평가합니다.

API key, Authorization, 전체 prompt, raw response 및 source-unit 원문은 저장하지 않습니다.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from html import escape
from pathlib import Path

from mvp.ai import answer_text, generate
from mvp.evidence import assess_evidence, relevant_body
from mvp.library import NO_GUIDELINE
from mvp.query import plan_query, topic_words
from mvp.retrieval import BM25Index, rank_bm25_candidates
from mvp.settings import ROOT, load_settings
from tools.bm25_evaluate import DEFAULT_QUESTIONS
from tools.rag_procedure_live_evaluate import (
    RejectTransport,
    pilot_corpus,
    prepare_q002,
    q002_gate,
    retrieve,
    run_case,
    write_json,
)

DEFAULT_OUTPUT = ROOT / "artifacts" / "2026-09-15_rag-pilot-generalization-live"
CASE_IDS = ("Q001", "Q002", "Q003", "Q004", "Q005")


def _blocked_case(case_id, question, plan, before, after, selected, reason):
    return {
        "case_id": case_id,
        "question": question,
        "query": {
            "kind": plan.kind,
            "topic": topic_words(plan),
            "aspect": list(plan.focus),
            "requested_phase": None,
        },
        "pre_budget_assessment": before.reason,
        "post_budget_assessment": after.reason if after else None,
        "selected_evidence_chunks": len(selected),
        "selected_source_unit_count": 0,
        "actual_groq_calls": 0,
        "http_status": None,
        "model_returned": None,
        "finish_reason": None,
        "latency_ms": 0,
        "tokens": {},
        "response_parse_stage": "pre_llm",
        "failure_code": None,
        "validation_reason": None,
        "block_reason": f"pre_llm:{reason}",
        "server_answerable": False,
        "verified_statement_count": 0,
        "reconstruction_success": False,
        "citation_coverage": 0.0,
        "citation_assessment": None,
        "duplicate_evidence": False,
        "source_order_reversal": None,
        "unsupported": {
            key: None for key in ("number", "unit", "condition", "negation", "action")
        },
        "pass": False,
    }


def _common_live_pass(case):
    return all((
        case.get("actual_groq_calls") == 1,
        case.get("http_status") == 200,
        case.get("model_returned") == "openai/gpt-oss-20b",
        case.get("finish_reason") == "stop",
        case.get("response_parse_stage") == "complete",
        case.get("failure_code") is None,
        case.get("validation_reason") is None,
        case.get("server_answerable") is True,
        case.get("reconstruction_success") is True,
        1 <= (case.get("verified_statement_count") or 0) <= 16,
        case.get("citation_coverage") == 1,
        case.get("citation_assessment") == "supported",
        case.get("duplicate_evidence") is False,
        case.get("source_order_reversal") == 0,
        all(value == 0 for value in case.get("unsupported", {}).values()),
    ))


def _requested_phase(plan, chunks):
    scores = BM25Index(chunks).scores(plan.expanded)
    return rank_bm25_candidates(plan.original, chunks, scores, limit=40).requested_phase


def _enrich_case(case, plan, before, after, selected, requested_phase, direct_body_count):
    case["query"] = {
        "kind": plan.kind,
        "topic": topic_words(plan),
        "aspect": list(plan.focus),
        "requested_phase": requested_phase,
    }
    case["pre_budget_assessment"] = before.reason
    case["post_budget_assessment"] = after.reason if after else None
    case["selected_evidence_chunks"] = len(selected)
    case["direct_body_supported_evidence_count"] = direct_body_count
    case["title_only_evidence"] = direct_body_count == 0
    case["reconstruction_success"] = bool(
        case.get("server_answerable") and case.get("verified_statement_count")
    )
    case["pass"] = _common_live_pass(case)
    return case


def _review_html(report):
    rows = []
    for case in report["cases"]:
        failure = (
            case.get("failure_code")
            or case.get("validation_reason")
            or case.get("block_reason")
            or "-"
        )
        tokens = case.get("tokens") or {}
        rows.append(
            "<tr>"
            f"<td>{escape(case['case_id'])}</td>"
            f"<td>{escape(case['query']['kind'])}</td>"
            f"<td>{escape('/'.join(case['query']['topic']))}</td>"
            f"<td>{escape(str(case['pre_budget_assessment']))}</td>"
            f"<td>{escape(str(case['post_budget_assessment']))}</td>"
            f"<td>{case['actual_groq_calls']}</td>"
            f"<td>{case.get('http_status')}</td>"
            f"<td>{escape(str(case.get('finish_reason')))}</td>"
            f"<td>{case.get('selected_source_unit_count')}</td>"
            f"<td>{case.get('verified_statement_count')}</td>"
            f"<td>{float(case.get('citation_coverage') or 0) * 100:.1f}%</td>"
            f"<td>{escape(str(failure))}</td>"
            f"<td class={'pass' if case.get('pass') else 'fail'}>"
            f"{'PASS' if case.get('pass') else 'FAIL'}</td>"
            f"<td>{escape(json.dumps(tokens, ensure_ascii=False))}</td>"
            "</tr>"
        )
    q006 = report["q006"]
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>SCHAT Pilot Query Generalization Live</title><style>
body{{font-family:system-ui,sans-serif;max-width:1500px;margin:24px auto;padding:0 18px;color:#17202a}}
table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{border:1px solid #ccd1d1;padding:8px;text-align:left}}
th{{background:#eef3f6}}.pass{{color:#196f3d;font-weight:700}}.fail{{color:#a93226;font-weight:700}}
</style></head><body><h1>진정간호 Q001~Q006 제한 Live 재평가</h1>
<p>Q006 zero-call: {q006['pass']} · actual calls: {q006['actual_groq_calls']} ·
전체 actual calls: {report['actual_groq_calls']}</p>
<table><thead><tr><th>Case</th><th>kind</th><th>topic</th><th>pre</th><th>post</th>
<th>calls</th><th>HTTP</th><th>finish</th><th>units</th><th>statements</th>
<th>citation</th><th>failure</th><th>result</th><th>tokens</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p>API key, Authorization, 전체 prompt, raw response/content 및 exact source text는 저장하지 않았습니다.</p>
</body></html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "Q006 zero-call then one live call per Q001-Q005",
        "constraints": {
            "maximum_actual_groq_calls": 5,
            "maximum_calls_per_answerable_case": 1,
            "q006_maximum_calls": 0,
            "retry": 0,
            "fallback": 0,
            "web_search": 0,
            "prompt_stored": False,
            "raw_response_stored": False,
            "exact_source_text_stored": False,
        },
        "q006": {
            "actual_groq_calls": 0,
            "pass": False,
            "fixed_abstention": None,
        },
        "cases": [],
        "actual_groq_calls": 0,
        "all_live_passed": False,
        "verdict": "LIVE_NOT_STARTED",
    }
    try:
        settings = load_settings(use_streamlit=False)
        settings.llm_endpoint()

        reject = RejectTransport()
        q006_trace = {}
        q006_plan = plan_query(DEFAULT_QUESTIONS[5])
        q006_answer, q006_hits = generate(
            settings,
            q006_plan.query,
            [],
            "evaluation-q006-final",
            plan=q006_plan,
            trace=q006_trace,
            transport=reject,
        )
        report["q006"] = {
            "query_kind": q006_plan.kind,
            "domain": q006_plan.domain,
            "actual_groq_calls": reject.calls,
            "llm_called": q006_trace.get("llm_called", False),
            "retrieval_hits": len(q006_hits),
            "fixed_abstention": answer_text(q006_answer),
            "pass": (
                q006_plan.domain == "out_of_scope"
                and reject.calls == 0
                and q006_hits == []
                and answer_text(q006_answer) == NO_GUIDELINE
            ),
        }
        if not report["q006"]["pass"]:
            report["verdict"] = "Q006_ZERO_CALL_SAFETY_FAILURE"
            raise RuntimeError("Q006 zero-call gate failed")

        model, metadata, chunks, vectors = pilot_corpus()
        q001_query = None
        for index, case_id in enumerate(CASE_IDS):
            question = DEFAULT_QUESTIONS[index]
            fingerprints = None
            if case_id == "Q002":
                (
                    _,
                    _,
                    hits,
                    plan,
                    selected,
                    _,
                    fingerprints,
                ) = prepare_q002()
                before = assess_evidence(plan, hits)
                after = assess_evidence(plan, selected)
                requested_phase = None
                direct_body_count = sum(relevant_body(plan, hit) for hit in selected)
            else:
                plan, hits, selected, before, _ = retrieve(
                    question, model, metadata, chunks, vectors
                )
                after = assess_evidence(plan, selected) if selected else None
                requested_phase = _requested_phase(plan, chunks)
                direct_body_count = sum(relevant_body(plan, hit) for hit in selected)

            if not before.sufficient or not after or not after.sufficient or not selected:
                reason = before.reason if not before.sufficient else (
                    after.reason if after else "no_selected_evidence"
                )
                case = _blocked_case(case_id, question, plan, before, after, selected, reason)
                case["query"]["requested_phase"] = requested_phase
            else:
                case = run_case(
                    case_id,
                    question,
                    hits,
                    plan,
                    selected,
                    settings,
                    expected_source_unit_fingerprints=fingerprints,
                )
                _enrich_case(
                    case, plan, before, after, selected, requested_phase, direct_body_count
                )

            if case_id == "Q001":
                q001_query = case["query"]
            elif case_id == "Q002":
                case["facet_slot_pass"] = q002_gate(case)
                case["pass"] = case["pass"] and case["facet_slot_pass"]
            elif case_id == "Q003":
                case["pass"] = case["pass"] and all((
                    case["query"]["kind"] == "preparation",
                    case["query"]["requested_phase"] == "before",
                    case["pre_budget_assessment"] == "supported",
                    case["post_budget_assessment"] == "supported",
                ))
            elif case_id == "Q004":
                case["canonical_purpose_matches_q001"] = bool(
                    q001_query
                    and case["query"]["kind"] == q001_query["kind"] == "purpose"
                    and case["query"]["topic"] == q001_query["topic"]
                    and case["query"]["aspect"] == q001_query["aspect"]
                )
                case["pass"] = case["pass"] and case["canonical_purpose_matches_q001"]
            elif case_id == "Q005":
                case["pass"] = case["pass"] and all((
                    case["query"]["kind"] == "cautions",
                    case["direct_body_supported_evidence_count"] > 0,
                    not case["title_only_evidence"],
                ))
            report["cases"].append(case)

        report["actual_groq_calls"] = sum(
            case["actual_groq_calls"] for case in report["cases"]
        )
        if report["actual_groq_calls"] > 5:
            raise RuntimeError("live call ceiling exceeded")
        report["all_live_passed"] = (
            len(report["cases"]) == len(CASE_IDS)
            and all(case["pass"] for case in report["cases"])
        )
        report["verdict"] = (
            "진정간호 1개 지침 기준 RAG MVP 검증 완료"
            if report["all_live_passed"]
            else "진정간호 제한 Live 일부 또는 전체 실패"
        )
    except Exception as error:  # noqa: BLE001 - raw-free report must survive any failure.
        if report["verdict"] == "LIVE_NOT_STARTED":
            report["verdict"] = "LIVE_PRECONDITION_OR_RUNTIME_FAILURE"
        report["runtime_failure_type"] = type(error).__name__
    finally:
        report["actual_groq_calls"] = sum(
            case.get("actual_groq_calls", 0) for case in report["cases"]
        )
        write_json(args.output / "live_report.json", report)
        (args.output / "review.html").write_text(_review_html(report), encoding="utf-8")
        print(json.dumps({
            "output": str(args.output),
            "q006_calls": report["q006"].get("actual_groq_calls"),
            "case_calls": {
                case["case_id"]: case["actual_groq_calls"] for case in report["cases"]
            },
            "total_actual_groq_calls": report["actual_groq_calls"],
            "all_live_passed": report["all_live_passed"],
            "verdict": report["verdict"],
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
