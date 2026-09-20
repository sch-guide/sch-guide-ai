"""Evaluate the extractive Answer schema with local MockTransport responses only."""

from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime
from html import escape

import httpx

from src.ai import (
    AI_VERSION,
    GROQ_REQUEST_TOKEN_BUDGET,
    OUTPUT_LIMIT,
    PROMPT_EVIDENCE_SCHEMA_VERSION,
    generate,
    groq_answer_json_schema,
)
from src.evidence import assess_evidence, source_sentences
from src.query import plan_query
from src.settings import ROOT
from tools.rag_phase1_evaluate import stage_recall
from tools.rag_phase2_evaluate import _load_q002
from tools.rag_prompt_budget_evaluate import MockQuota, exact_group_statements, mock_settings

DEFAULT_OUTPUT = ROOT / "workspace" / "RAG_실험" / "2026-09-13_rag-unsupported-sentence-mock"


def _response(statements):
    return json.dumps(
        {
            "answerable": True,
            "statements": statements,
            "format": "steps",
            "conflict": False,
        },
        ensure_ascii=False,
    )


def _run_case(name, plan, hits, statements):
    calls = []

    def handle(request):
        calls.append(True)
        payload = json.loads(request.content)
        schema = payload["response_format"]["json_schema"]
        assert schema["strict"] is True
        assert payload["max_completion_tokens"] == OUTPUT_LIMIT
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-oss-20b",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": _response(statements), "refusal": None},
                    }
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
            },
        )

    trace = {}
    answer, selected = generate(
        mock_settings(),
        plan.query,
        hits,
        "fixture",
        quota=MockQuota(),
        transport=httpx.MockTransport(handle),
        plan=plan,
        trace=trace,
    )
    return {
        "name": name,
        "mock_http_calls": len(calls),
        "answerable": answer.answerable,
        "verified_statement_count": len(answer.statements),
        "selected_chunk_count": len(selected),
        "response_parse_stage": trace.get("response_parse_stage"),
        "failure_code": trace.get("llm_error_code"),
        "validation_reason": trace.get("validation_reason"),
    }


def _first_sentence(hits, chunk_suffix, contains=None):
    hit = next(hit for hit in hits if hit.chunk.id.endswith(chunk_suffix))
    sentence = next(
        sentence
        for sentence in source_sentences(hit.chunk.text)
        if len(sentence) >= 4 and (contains is None or contains in sentence)
    )
    return hit, sentence


def _case_statements(base, mutation):
    statements = copy.deepcopy(base)
    item = statements[0]
    text = item["text"]
    if mutation == "ending_changed":
        item["text"] = text + "요"
    elif mutation == "fragment":
        item["text"] = text[: max(4, len(text) // 2)]
    elif mutation == "punctuation_changed":
        item["text"] = text[:-1] + "!" if text[-1:] in ".。!?！？" else text + "!"
    elif mutation == "paraphrase":
        item["text"] = text + " 추가 설명입니다."
    elif mutation == "partial_quote":
        item["evidence"][0]["quote"] = text[: max(4, len(text) // 2)]
    elif mutation == "bad_chunk":
        item["evidence"][0]["chunk_id"] = "missing-selected-chunk"
    elif mutation == "bad_quote":
        item["evidence"][0]["quote"] = "not present in the selected chunk"
    elif mutation == "number":
        item["text"] = text + " 999"
    elif mutation == "action":
        item["text"] = text + " 제거한다."
    elif mutation == "source_order":
        statements.reverse()
    elif mutation != "exact":
        raise ValueError(f"unknown mutation: {mutation}")
    return statements


def build_report():
    metadata, gold, hits = _load_q002()
    plan = plan_query(gold["question"], documents=[metadata])
    assessment = assess_evidence(plan, hits)
    base = exact_group_statements(assessment.groups)
    schema = groq_answer_json_schema()
    statement_schema = schema["$defs"]["Statement"]["properties"]
    evidence_schema = schema["$defs"]["Evidence"]["properties"]

    cases = [
        _run_case(name, plan, hits, _case_statements(base, name))
        for name in (
            "exact",
            "ending_changed",
            "fragment",
            "punctuation_changed",
            "paraphrase",
            "partial_quote",
            "bad_chunk",
            "bad_quote",
            "number",
            "action",
            "source_order",
        )
    ]

    unit_hit, unit_sentence = _first_sentence(hits, "chunk-00021", "15분")
    unit_changed = re.sub(r"15분", "15초", unit_sentence, count=1)
    unit_statement = [
        {
            "text": unit_changed,
            "evidence": [{"chunk_id": unit_hit.chunk.id, "quote": unit_sentence}],
            "label": "",
        }
    ]
    cases.append(_run_case("unit", plan, hits, unit_statement))

    exact_trace = {}
    exact_quota = MockQuota()
    calls = []

    def exact_handler(request):
        calls.append(True)
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-oss-20b",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": _response(base), "refusal": None},
                    }
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
            },
        )

    exact_answer, selected = generate(
        mock_settings(),
        plan.query,
        hits,
        "fixture",
        quota=exact_quota,
        transport=httpx.MockTransport(exact_handler),
        plan=plan,
        trace=exact_trace,
    )
    selected_ids = [hit.chunk.id for hit in selected]
    pre_ids = [hit.chunk.id for hit in assessment.hits]
    post_assessment = assess_evidence(plan, selected)
    post_ids = [hit.chunk.id for hit in post_assessment.hits]
    reserved = exact_quota.reservations[0][1]
    headroom = GROQ_REQUEST_TOKEN_BUDGET - reserved
    required_headroom = max(256, math.ceil(reserved * 0.08))

    q006_calls = []
    q006_plan = plan_query("화성 우주선의 궤도 계산 공식은?", documents=[metadata])
    q006_answer, _ = generate(
        mock_settings(),
        q006_plan.query,
        [],
        "fixture",
        plan=q006_plan,
        trace={},
        transport=httpx.MockTransport(lambda request: q006_calls.append(request)),
    )

    coverage = post_assessment.procedure_coverage
    descriptions = {
        "statement_text": statement_schema["text"].get("description"),
        "statement_evidence": statement_schema["evidence"].get("description"),
        "evidence_chunk_id": evidence_schema["chunk_id"].get("description"),
        "evidence_quote": evidence_schema["quote"].get("description"),
    }
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "local fixtures and MockTransport only; no external LLM call",
        "versions": {
            "ai": AI_VERSION,
            "prompt_evidence_schema": PROMPT_EVIDENCE_SCHEMA_VERSION,
        },
        "limits": {
            "output": OUTPUT_LIMIT,
            "request_admission": GROQ_REQUEST_TOKEN_BUDGET,
            "reservation": reserved,
            "headroom": headroom,
            "required_headroom": required_headroom,
            "within_admission_cap": reserved <= GROQ_REQUEST_TOKEN_BUDGET,
            "headroom_passed": headroom >= required_headroom,
        },
        "strict_schema": {
            "descriptions_present": all(descriptions.values()),
            "descriptions": descriptions,
        },
        "mock_cases": cases,
        "q002": {
            "selected_chunk_count": len(selected_ids),
            "selected_chunk_ids": selected_ids,
            "pre_required_gold_recall": stage_recall(gold, pre_ids)["required"],
            "post_required_gold_recall": stage_recall(gold, post_ids)["required"],
            "required_group_count": len(coverage.required_group_keys),
            "parent_partial_inclusion_count": sum(
                1
                for group in assessment.groups
                if 0 < len({hit.chunk.id for hit in group.hits} & set(selected_ids)) < len(group.hits)
            ),
            "required_branches": list(coverage.required_branches),
            "source_ordered": coverage.source_ordered,
            "exact_answerable": exact_answer.answerable,
            "exact_statement_count": len(exact_answer.statements),
            "exact_citation_passed": exact_trace.get("citation_assessment") == "supported",
            "mock_http_calls": len(calls),
        },
        "q006": {
            "answerable": q006_answer.answerable,
            "mock_http_calls": len(q006_calls),
        },
        "expected": {
            "exact": [True, None],
            "ending_changed": [False, "unsupported sentence"],
            "fragment": [False, "unsupported sentence"],
            "punctuation_changed": [False, "unsupported sentence"],
            "paraphrase": [False, "unsupported sentence"],
            "partial_quote": [False, "unsupported sentence"],
            "bad_chunk": [False, "citation"],
            "bad_quote": [False, "citation"],
            "number": [False, "number"],
            "unit": [False, "unit"],
            "action": [False, "unsupported action"],
            "source_order": [False, "procedure source order"],
        },
        "raw_prompt_saved": False,
        "raw_response_saved": False,
    }


def assert_acceptance(report):
    observed = {
        case["name"]: [case["answerable"], case["validation_reason"]]
        for case in report["mock_cases"]
    }
    q002 = report["q002"]
    if not (
        report["strict_schema"]["descriptions_present"]
        and observed == report["expected"]
        and all(case["mock_http_calls"] == 1 for case in report["mock_cases"])
        and q002["selected_chunk_count"] == 12
        and q002["pre_required_gold_recall"] == {"recalled": 10, "total": 10, "recall": 1.0}
        and q002["post_required_gold_recall"] == q002["pre_required_gold_recall"]
        and q002["parent_partial_inclusion_count"] == 0
        and set(q002["required_branches"]) == {"adult", "pediatric"}
        and q002["source_ordered"]
        and q002["exact_answerable"]
        and q002["exact_citation_passed"]
        and report["q006"]["mock_http_calls"] == 0
        and report["limits"]["within_admission_cap"]
        and report["limits"]["headroom_passed"]
    ):
        raise RuntimeError("unsupported sentence mock acceptance failed")


def write_html(path, report):
    rows = "".join(
        "<tr>"
        f"<td>{escape(case['name'])}</td>"
        f"<td>{case['mock_http_calls']}</td>"
        f"<td>{case['answerable']}</td>"
        f"<td>{escape(str(case['failure_code']))}</td>"
        f"<td>{escape(str(case['validation_reason']))}</td>"
        "</tr>"
        for case in report["mock_cases"]
    )
    q002 = report["q002"]
    limits = report["limits"]
    path.write_text(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>SCHAT unsupported sentence Mock 검수</title><style>
body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#17202a}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd1d1;padding:8px;text-align:left}}
th{{background:#eef3f6}}.pass{{color:#196f3d;font-weight:700}}
</style></head><body><h1>Unsupported sentence / strict schema Mock 검수</h1>
<p>실제 Groq 호출 0회 · 생성 {escape(report['generated_at'])}</p>
<p class="pass">schema descriptions: {report['strict_schema']['descriptions_present']}</p>
<h2>MockTransport 경계</h2><table><thead><tr><th>case</th><th>호출</th><th>answerable</th>
<th>failure code</th><th>validation reason</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Q002 불변성</h2><ul>
<li>selected chunks: {q002['selected_chunk_count']}</li>
<li>pre required gold: {q002['pre_required_gold_recall']['recalled']}/{q002['pre_required_gold_recall']['total']}</li>
<li>post required gold: {q002['post_required_gold_recall']['recalled']}/{q002['post_required_gold_recall']['total']}</li>
<li>parent partial inclusion: {q002['parent_partial_inclusion_count']}</li>
<li>required branches: {escape(', '.join(q002['required_branches']))}</li>
<li>source order: {q002['source_ordered']}</li></ul>
<h2>Budget</h2><ul><li>reservation: {limits['reservation']} / {limits['request_admission']}</li>
<li>headroom: {limits['headroom']} (required {limits['required_headroom']})</li></ul>
<h2>Q006</h2><p>MockTransport 호출: {report['q006']['mock_http_calls']}회</p>
</body></html>""",
        encoding="utf-8",
    )


def main():
    report = build_report()
    assert_acceptance(report)
    DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=False)
    (DEFAULT_OUTPUT / "unsupported_sentence_mock_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_html(DEFAULT_OUTPUT / "review.html", report)
    print(
        json.dumps(
            {
                "output": str(DEFAULT_OUTPUT),
                "descriptions_present": report["strict_schema"]["descriptions_present"],
                "reservation": report["limits"]["reservation"],
                "headroom": report["limits"]["headroom"],
                "q002_selected_chunks": report["q002"]["selected_chunk_count"],
                "q002_pre_required": report["q002"]["pre_required_gold_recall"],
                "q002_post_required": report["q002"]["post_required_gold_recall"],
                "q006_mock_http_calls": report["q006"]["mock_http_calls"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
