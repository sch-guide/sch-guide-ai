"""Run one approved Q002 Groq call and persist only raw-free evaluation metadata."""

from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path

from mvp.ai import AI_VERSION, GROQ_REQUEST_TOKEN_BUDGET, OUTPUT_LIMIT
from mvp.settings import ROOT
from tools.rag_groq_evaluate import evaluate

DEFAULT_OUTPUT = ROOT / "artifacts" / "2026-09-13_rag-groq-schema-description-live"


def safe_report(report):
    q002 = report["q002"]
    validation_passed = q002["validation_passed"]
    reason = q002["validation_reason"]
    unsupported = {
        "number": False if validation_passed else (True if reason == "number" else None),
        "unit": False if validation_passed else (True if reason == "unit" else None),
        "condition": False if validation_passed else None,
        "negation": False if validation_passed else None,
        "action": False if validation_passed else (True if reason == "unsupported action" else None),
    }
    return {
        "schema_version": 1,
        "generated_at": report["generated_at"],
        "mode": report["mode"],
        "actual_groq_calls": report["actual_groq_calls"],
        "configuration": {
            "ai_version": AI_VERSION,
            "output_limit": OUTPUT_LIMIT,
            "request_token_budget": GROQ_REQUEST_TOKEN_BUDGET,
            "prompt_schema_version": q002["prompt_schema_version"],
            "model_requested": q002["model_requested"],
            "response_format": "json_schema",
            "strict": True,
            "selected_evidence_only": True,
            "automatic_retry": False,
            "fallback_model": False,
            "web_search": False,
        },
        "q006": {
            "llm_called": report["q006"]["llm_called"],
            "transport_calls": report["q006"]["transport_calls"],
            "abstention_reason": report["q006"]["abstention_reason"],
        },
        "q002": {
            "http_status": q002["http_status"],
            "model_returned": q002["model_returned"],
            "finish_reason": q002["finish_reason"],
            "prompt_tokens": q002["input_tokens"],
            "completion_tokens": q002["output_tokens"],
            "total_tokens": q002["total_tokens"],
            "latency_ms": q002["latency_ms"],
            "llm_called": q002["llm_called"],
            "transport_calls": q002["transport_calls"],
            "response_parse_stage": q002["trace_summary"]["response_parse_stage"],
            "failure_code": q002["failure_code"],
            "validation_reason": q002["validation_reason"],
            "answerable": q002["answerable"],
            "verified_statement_count": q002["statement_count"],
            "all_statements_complete_source_units": True if validation_passed else None,
            "exact_quote_chunk_citation_validation": q002["exact_citation_validation"],
            "citation_coverage": q002["citation_coverage"],
            "source_ordered": q002["source_ordered"],
            "source_order_reversal_count": 0 if q002["source_ordered"] else None,
            "unsupported_content_detected": unsupported,
            "validation_passed": validation_passed,
            "selected_evidence_chunk_ids": q002["selected_evidence_chunk_ids"],
            "pre_required_gold_recall": q002["pre_gold_recall"]["required"],
            "post_required_gold_recall": q002["post_gold_recall"]["required"],
            "reserved_tokens": q002["reserved_tokens"],
            "request_token_headroom": q002["trace_summary"]["request_token_headroom"],
            "response_shape": {
                key: q002["trace_summary"].get(key)
                for key in (
                    "response_json_succeeded",
                    "response_top_level_type",
                    "response_choices_present",
                    "response_choices_count",
                    "response_choice0_type",
                    "response_finish_reason_present",
                    "response_message_present",
                    "response_message_type",
                    "response_content_present",
                    "response_content_type",
                    "response_content_char_count",
                    "response_refusal_present",
                    "response_refusal_non_null",
                )
            },
        },
        "security": {
            "api_key_saved": False,
            "authorization_header_saved": False,
            "full_prompt_saved": False,
            "raw_response_saved": False,
            "response_content_saved": False,
            "refusal_content_saved": False,
            "verified_statement_text_saved": False,
            "evidence_quote_saved": False,
        },
    }


def write_artifacts(report, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    (output / "groq_evaluation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    q002 = report["q002"]
    q006 = report["q006"]
    chunk_rows = "".join(
        f"<li><code>{escape(chunk_id)}</code></li>"
        for chunk_id in q002["selected_evidence_chunk_ids"]
    )
    (output / "review.html").write_text(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>SCHAT Q002 schema description live 평가</title><style>
body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#17202a}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd1d1;padding:8px;text-align:left}}
th{{background:#eef3f6}}code{{white-space:nowrap}}
</style></head><body><h1>Q002 strict schema description 단일 live 평가</h1>
<p>생성 {escape(report['generated_at'])} · 실제 Groq 호출 {report['actual_groq_calls']}회</p>
<table><tbody>
<tr><th>HTTP status</th><td>{q002['http_status']}</td></tr>
<tr><th>model</th><td>{escape(str(q002['model_returned']))}</td></tr>
<tr><th>finish reason</th><td>{escape(str(q002['finish_reason']))}</td></tr>
<tr><th>prompt/completion/total</th><td>{q002['prompt_tokens']} / {q002['completion_tokens']} / {q002['total_tokens']}</td></tr>
<tr><th>latency</th><td>{q002['latency_ms']} ms</td></tr>
<tr><th>parse stage</th><td>{escape(str(q002['response_parse_stage']))}</td></tr>
<tr><th>failure / validation</th><td>{escape(str(q002['failure_code']))} / {escape(str(q002['validation_reason']))}</td></tr>
<tr><th>answerable / statements</th><td>{q002['answerable']} / {q002['verified_statement_count']}</td></tr>
<tr><th>citation coverage</th><td>{q002['citation_coverage'] * 100:.1f}%</td></tr>
<tr><th>exact citation</th><td>{escape(str(q002['exact_quote_chunk_citation_validation']))}</td></tr>
<tr><th>source order reversals</th><td>{escape(str(q002['source_order_reversal_count']))}</td></tr>
</tbody></table>
<h2>Q006</h2><p>transport {q006['transport_calls']}회 · llm_called {q006['llm_called']}</p>
<h2>Selected evidence chunk IDs</h2><ol>{chunk_rows}</ol>
<p>전체 prompt, raw response/content, verified statement text와 evidence quote는 저장하지 않았습니다.</p>
</body></html>""",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = safe_report(evaluate(dry_run=False))
    write_artifacts(report, args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "actual_groq_calls": report["actual_groq_calls"],
                "q006_calls": report["q006"]["transport_calls"],
                "q002_calls": report["q002"]["transport_calls"],
                "http_status": report["q002"]["http_status"],
                "finish_reason": report["q002"]["finish_reason"],
                "failure_code": report["q002"]["failure_code"],
                "validation_reason": report["q002"]["validation_reason"],
                "answerable": report["q002"]["answerable"],
                "verified_statement_count": report["q002"]["verified_statement_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
