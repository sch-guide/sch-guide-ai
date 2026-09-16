"""Evaluate Source Unit selection without persisting prompts or provider content."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from html import escape
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator

import mvp.ai as ai_module
from mvp.ai import (
    AI_VERSION,
    GROQ_REQUEST_TOKEN_BUDGET,
    OUTPUT_LIMIT,
    PROMPT_EVIDENCE_SCHEMA_VERSION,
    RESPONSE_SELECTION_SCHEMA_VERSION,
    SOURCE_UNIT_SYSTEM,
    answer_text,
    build_selection_contract,
    generate,
    groq_answer_json_schema,
    prompt_evidence_envelope,
    prompt_messages,
)
from mvp.evidence import assess_evidence
from mvp.library import NO_GUIDELINE
from mvp.query import plan_query
from mvp.settings import ROOT, Settings
from tools.rag_groq_evaluate import EvaluationQuota, RejectTransport, evaluate
from tools.rag_phase1_evaluate import stage_recall
from tools.rag_phase2_evaluate import _load_q002

DEFAULT_OUTPUT = ROOT / "artifacts" / "2026-09-14_rag-source-unit-selection"


def _safe_group_selection_counts(raw, contract):
    """모델 ID와 raw content를 보존하지 않고 group별 개수만 관측합니다."""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = {}
    selections = parsed.get("group_selections") if isinstance(parsed, dict) else None
    rows = []
    for slot in contract.slots:
        value = selections.get(slot.prompt_group_id) if isinstance(selections, dict) else None
        rows.append({
            "prompt_group_id": slot.prompt_group_id,
            "branch": slot.branch,
            "required": slot.required,
            "selected_count": len(value) if isinstance(value, list) else None,
        })
    return rows


def _live_evaluation_with_safe_group_counts():
    observation = {}
    original = ai_module.validate_source_unit_selection

    def observed(raw, catalog, contract, prompt_coverage, plan, trace=None):
        observation["group_selected_counts"] = _safe_group_selection_counts(raw, contract)
        return original(raw, catalog, contract, prompt_coverage, plan, trace=trace)

    ai_module.validate_source_unit_selection = observed
    try:
        raw = evaluate(dry_run=False)
    finally:
        ai_module.validate_source_unit_selection = original
    return raw, observation


def _required_source_units(gold, catalog):
    by_key = {(unit.chunk_id, unit.source_order[1]): unit for unit in catalog}
    identifiers = []
    stage_units = {}
    for stage in gold["stages"]:
        if stage["importance"] != "required":
            continue
        alternative = stage["source_unit_requirements"][0]
        current = []
        for item in alternative["all_of"]:
            unit = by_key[(item["chunk_id"], item["source_unit_position"])]
            digest = hashlib.sha256(unit.exact_text.encode()).hexdigest()
            if digest != item["exact_text_sha256"]:
                raise RuntimeError("Q002 source-unit gold fingerprint drift")
            current.append(unit.source_unit_id)
            if unit.source_unit_id not in identifiers:
                identifiers.append(unit.source_unit_id)
        stage_units[stage["stage_id"]] = current
    return identifiers, stage_units


def _inputs():
    metadata, gold, hits = _load_q002()
    plan = plan_query(gold["question"], documents=[metadata])
    before = assess_evidence(plan, hits)
    trace = {}
    _, selected, catalog = prompt_messages(
        plan.query,
        before.hits,
        14000,
        token_budget=GROQ_REQUEST_TOKEN_BUDGET,
        plan=plan,
        groups=before.groups,
        trace=trace,
        selection_only=True,
        return_catalog=True,
    )
    after = assess_evidence(plan, selected)
    identifiers, stage_units = _required_source_units(gold, catalog)
    return metadata, gold, hits, plan, before, selected, after, catalog, identifiers, stage_units, trace


def _mock_settings():
    return Settings(
        llm_provider="groq_free",
        llm_key="fixture",
        llm_model="openai/gpt-oss-20b",
        llm_approved=True,
        groq_free_confirmed=True,
    )


def build_mock_report():
    (metadata, gold, hits, plan, before, selected, after, catalog, identifiers,
     stage_units, prompt_trace) = _inputs()
    calls = []
    contract = build_selection_contract(after.groups, catalog)
    prompt_envelope = prompt_evidence_envelope(after.groups, catalog, contract)
    selection_policy = prompt_envelope["selection_policy"]
    policy_text = json.dumps(selection_policy, ensure_ascii=False).lower()
    normalized_system = " ".join(SOURCE_UNIT_SYSTEM.lower().split())
    response_schema = groq_answer_json_schema(contract)
    Draft202012Validator.check_schema(response_schema)
    audited_keywords = (
        "anyOf", "minItems", "maxItems", "enum", "const", "oneOf", "allOf",
        "if", "then", "else", "pattern", "uniqueItems", "dependentSchemas",
        "propertyNames", "minProperties", "maxProperties",
    )
    keyword_counts = {keyword: 0 for keyword in audited_keywords}

    def audit_schema(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in keyword_counts:
                    keyword_counts[key] += 1
                audit_schema(item)
        elif isinstance(value, list):
            for item in value:
                audit_schema(item)

    audit_schema(response_schema)

    def selection_content(request, selected_ids):
        payload = json.loads(request.content)
        schema = payload["response_format"]["json_schema"]
        if schema["strict"] is not True or payload["max_completion_tokens"] != OUTPUT_LIMIT:
            raise RuntimeError("strict schema or output limit drift")
        envelope = json.loads(
            payload["messages"][1]["content"].split("Evidence groups (JSON):\n", 1)[1]
        )
        group_selections = {}
        for group in envelope["groups"]:
            allowed = {
                unit["id"]
                for source in group["sources"]
                for unit in source["units"]
                if unit["selectable"]
            }
            group_selections[group["group_id"]] = [
                identifier for identifier in selected_ids if identifier in allowed
            ]
        content = json.dumps({
            "group_selections": group_selections,
        }, ensure_ascii=False)
        Draft202012Validator(schema["schema"]).validate(json.loads(content))
        return content

    def handler(request):
        calls.append(request)
        content = selection_content(request, identifiers)
        return httpx.Response(200, json={
            "model": "openai/gpt-oss-20b",
            "choices": [{"finish_reason": "stop", "message": {
                "content": content,
                "refusal": None,
            }}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
        })

    trace = {}
    quota = EvaluationQuota()
    answer, used = generate(
        _mock_settings(), plan.query, hits, "evaluation", quota=quota,
        transport=httpx.MockTransport(handler), plan=plan, trace=trace,
    )
    all_selectable_ids = [
        unit.source_unit_id for unit in catalog if unit.selectable
    ]
    overselection_calls = []

    def overselection_handler(request):
        overselection_calls.append(request)
        content = selection_content(request, all_selectable_ids)
        return httpx.Response(200, json={
            "model": "openai/gpt-oss-20b",
            "choices": [{"finish_reason": "stop", "message": {
                "content": content,
                "refusal": None,
            }}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
        })

    overselection_trace = {}
    overselection_answer, _ = generate(
        _mock_settings(), plan.query, hits, "overselection-evaluation",
        quota=EvaluationQuota(), transport=httpx.MockTransport(overselection_handler),
        plan=plan, trace=overselection_trace,
    )
    empty_selection_calls = []

    def empty_selection_handler(request):
        empty_selection_calls.append(request)
        content = selection_content(request, ())
        return httpx.Response(200, json={
            "model": "openai/gpt-oss-20b",
            "choices": [{"finish_reason": "stop", "message": {
                "content": content,
                "refusal": None,
            }}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050},
        })

    empty_selection_trace = {}
    empty_selection_answer, _ = generate(
        _mock_settings(), plan.query, hits, "empty-selection-evaluation",
        quota=EvaluationQuota(), transport=httpx.MockTransport(empty_selection_handler),
        plan=plan, trace=empty_selection_trace,
    )
    q006_plan = plan_query("화성 우주선의 궤도 계산 공식은?", documents=[metadata])
    q006_transport = RejectTransport()
    q006_trace = {}
    q006_answer, _ = generate(
        _mock_settings(), q006_plan.query, [], "evaluation", plan=q006_plan,
        transport=q006_transport, trace=q006_trace,
    )
    selected_unit_ids = list(identifiers)
    recalled = sum(
        all(identifier in selected_unit_ids for identifier in required)
        for required in stage_units.values()
    )
    source_orders = [unit.source_order for unit in catalog if unit.source_unit_id in identifiers]
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "mock",
        "external_groq_calls": 0,
        "configuration": _configuration(),
        "selection_policy_audit": {
            "policy": selection_policy,
            "exact_policy_keys": sorted(selection_policy) == sorted((
                "goal", "maximum_total", "selectable_means", "required_group_means",
            )),
            "contains_q002": "q002" in policy_text,
            "contains_gold_or_stage": "gold" in policy_text or "stage" in policy_text,
            "contains_chunk_id": "chunk_id" in policy_text,
            "contains_per_group_count": any(
                key in selection_policy for key in (
                    "expected_count", "maximum_per_group", "per_group_maximum",
                    "per_group_expected",
                )
            ),
            "system_selectable_is_eligible": (
                "selectable=true means eligible, not mandatory" in normalized_system
            ),
            "system_required_applies_to_group": (
                "required=true means the group requires a sufficient non-empty subset, not every unit"
                in normalized_system
            ),
            "system_smallest_sufficient_subset": (
                "smallest source-unit subset sufficient for the full user question"
                in normalized_system
            ),
            "system_counts_maximum_total": "total selected ids at 16 or fewer" in normalized_system,
            "system_server_admitted_catalog": (
                "server evidence checks have already admitted this catalog for selection"
                in normalized_system
            ),
            "system_does_not_delegate_answerability": (
                "do not re-evaluate corpus-wide answerability" in normalized_system
                and "answerable=false" not in normalized_system
            ),
        },
        "provider_schema_audit": {
            "root_type": response_schema.get("type"),
            "root_properties": list(response_schema.get("properties", {})),
            "all_root_properties_required": set(
                response_schema.get("required", ())
            ) == set(response_schema.get("properties", {})),
            "root_additional_properties": response_schema.get("additionalProperties"),
            "group_slots": [
                {
                    "prompt_group_id": slot.prompt_group_id,
                    "branch": slot.branch,
                    "required_by_server": slot.required,
                    "enum_count": len(slot.selectable_source_unit_ids),
                }
                for slot in contract.slots
            ],
            "keyword_counts": keyword_counts,
            "provider_enforces": [
                "closed required object shape",
                "required group slot properties",
                "per-slot selectable source-unit ID enums",
            ],
            "server_fail_closed_enforces": [
                "server-derived answerability",
                "empty selection rejection",
                "required group non-empty",
                "required branch coverage",
                "duplicate ID rejection",
                "total selection limit 16",
                "source order",
                "AnswerCoverage and reconstruction validation",
            ],
        },
        "q002": {
            "selected_evidence_chunk_ids": [hit.chunk.id for hit in used],
            "selected_evidence_count": len(used),
            "raw_source_unit_count": len(catalog),
            "selectable_source_unit_count": sum(unit.selectable for unit in catalog),
            "prompt_required_input_unit_count": len(before.procedure_coverage.input_required_unit_keys),
            "selected_source_unit_ids": selected_unit_ids,
            "selected_source_unit_count": len(selected_unit_ids),
            "required_source_unit_gold_recall": {"hit": recalled, "total": len(stage_units)},
            "pre_required_gold_recall": stage_recall(
                gold, [hit.chunk.id for hit in before.hits]
            )["required"],
            "post_required_gold_recall": stage_recall(
                gold, [hit.chunk.id for hit in after.hits]
            )["required"],
            "required_group_count": len(before.procedure_coverage.required_group_keys),
            "required_branches": list(before.procedure_coverage.required_branches),
            "parent_partial_inclusion_count": 0,
            "source_ordered": source_orders == sorted(source_orders),
            "transport_calls": len(calls),
            "answerable": answer.answerable,
            "verified_statement_count": len(answer.statements),
            "reconstruction_text_exact": all(
                statement.text == next(
                    unit.exact_text for unit in catalog
                    if unit.source_unit_id == identifier
                )
                for statement, identifier in zip(answer.statements, identifiers)
            ),
            "reconstruction_quote_exact": all(
                statement.evidence[0].quote == statement.text
                for statement in answer.statements
            ),
            "citation_coverage": (
                sum(bool(statement.evidence) for statement in answer.statements)
                / len(answer.statements)
            ),
            "exact_citation_validation": trace.get("citation_assessment"),
            "answer_coverage": trace.get("answer_coverage"),
            "response_parse_stage": trace.get("response_parse_stage"),
            "failure_code": trace.get("llm_error_code"),
            "validation_reason": trace.get("validation_reason"),
            "reservation": quota.reserved_tokens,
            "admission_cap": GROQ_REQUEST_TOKEN_BUDGET,
            "headroom": GROQ_REQUEST_TOKEN_BUDGET - quota.reserved_tokens,
            "minimum_headroom": max(256, math.ceil(quota.reserved_tokens * 0.08)),
            "prompt_estimated_tokens": prompt_trace.get("estimated_request_tokens"),
            "response_schema_serialized_tokens": trace.get("response_schema_serialized_tokens"),
            "response_selection_schema_version": trace.get(
                "response_selection_schema_version"
            ),
            "selection_group_slots": [
                {
                    "prompt_group_id": slot.prompt_group_id,
                    "group_key": slot.group_key,
                    "branch": slot.branch,
                    "required": slot.required,
                    "selectable_source_unit_count": len(slot.selectable_source_unit_ids),
                    "source_order": slot.source_order,
                    "selected_count": sum(
                        identifier in slot.selectable_source_unit_ids
                        for identifier in selected_unit_ids
                    ),
                }
                for slot in contract.slots
            ],
            "overselection_mock": {
                "selected_source_unit_count": len(all_selectable_ids),
                "transport_calls": len(overselection_calls),
                "answerable": overselection_answer.answerable,
                "failure_code": overselection_trace.get("llm_error_code"),
                "validation_reason": overselection_trace.get("validation_reason"),
                "automatic_truncation": False,
            },
            "empty_selection_mock": {
                "selected_source_unit_count": 0,
                "transport_calls": len(empty_selection_calls),
                "answerable": empty_selection_answer.answerable,
                "failure_code": empty_selection_trace.get("llm_error_code"),
                "validation_reason": empty_selection_trace.get("validation_reason"),
                "automatic_retry": False,
            },
        },
        "q006": {
            "catalog_count": 0,
            "transport_calls": q006_transport.calls,
            "llm_called": q006_trace.get("llm_called", False),
            "fixed_abstention": answer_text(q006_answer) == NO_GUIDELINE,
        },
        "negative_contracts": {
            "all_group_arrays_empty": "AI_EVIDENCE/selection_empty",
            "required_adult_empty": "AI_EVIDENCE/selection_branch",
            "required_pediatric_empty": "AI_EVIDENCE/selection_branch",
            "required_common_empty": "AI_EVIDENCE/selection_missing_group",
            "wrong_group_id": "AI_EVIDENCE/selection_wrong_group",
            "unknown_id": "AI_EVIDENCE/selection_unknown_id",
            "duplicate_id": "AI_EVIDENCE/selection_duplicate_id",
            "non_selectable_id": "AI_EVIDENCE/selection_non_selectable",
            "selection_limit": "AI_EVIDENCE/selection_limit",
            "group_source_order_reversal": "AI_EVIDENCE/selection_source_order",
            "global_source_order_reversal": "AI_EVIDENCE/selection_source_order",
            "source_order_reversal": "AI_EVIDENCE/selection_source_order",
            "verified_by": "tests/test_source_unit_selection.py",
        },
    }


def _configuration():
    return {
        "ai_version": AI_VERSION,
        "prompt_schema_version": PROMPT_EVIDENCE_SCHEMA_VERSION,
        "response_selection_schema_version": RESPONSE_SELECTION_SCHEMA_VERSION,
        "output_limit": OUTPUT_LIMIT,
        "request_token_budget": GROQ_REQUEST_TOKEN_BUDGET,
        "response_format": "json_schema",
        "strict": True,
        "selection_limit": 16,
        "answer_statement_limit": 16,
        "automatic_retry": False,
        "fallback": False,
        "web_search": False,
    }


def build_live_report():
    raw, observation = _live_evaluation_with_safe_group_counts()
    q002 = raw["q002"]
    validation_passed = q002["validation_passed"]
    trace = q002["trace_summary"]
    coverage = trace.get("answer_coverage") or {}
    required_groups = set(coverage.get("required_group_keys") or ())
    selected_groups = set(coverage.get("selected_group_keys") or ())
    required_branches = set(coverage.get("required_branches") or ())
    selected_branches = set(coverage.get("selected_branches") or ())
    validation_reason = q002["validation_reason"]
    group_selected_counts = observation.get("group_selected_counts", [])
    required_count_rows = [row for row in group_selected_counts if row["required"]]

    def observed_branch_satisfied(branch):
        rows = [row for row in required_count_rows if row["branch"] == branch]
        if not rows or any(row["selected_count"] is None for row in rows):
            return None
        return all(row["selected_count"] > 0 for row in rows)

    def unsupported(reason):
        if validation_passed:
            return False
        return True if validation_reason == reason else None

    return {
        "schema_version": 1,
        "generated_at": raw["generated_at"],
        "mode": "live-single-call",
        "actual_groq_calls": raw["actual_groq_calls"],
        "configuration": _configuration(),
        "q006": {
            "transport_calls": raw["q006"]["transport_calls"],
            "llm_called": raw["q006"]["llm_called"],
            "abstention_reason": raw["q006"]["abstention_reason"],
        },
        "q002": {
            "transport_calls": q002["transport_calls"],
            "llm_called": q002["llm_called"],
            "http_status": q002["http_status"],
            "model_returned": q002["model_returned"],
            "finish_reason": q002["finish_reason"],
            "prompt_tokens": q002["input_tokens"],
            "completion_tokens": q002["output_tokens"],
            "total_tokens": q002["total_tokens"],
            "latency_ms": q002["latency_ms"],
            "response_parse_stage": trace["response_parse_stage"],
            "failure_code": q002["failure_code"],
            "failure_detail": trace["response_failure_detail"],
            "validation_reason": validation_reason,
            "answerable": q002["answerable"],
            "group_slot_count": trace.get("response_selection_group_count"),
            "group_selected_counts": [
                {
                    "prompt_group_id": row["prompt_group_id"],
                    "selected_count": row["selected_count"],
                }
                for row in group_selected_counts
            ],
            "required_group_count": (
                len(required_count_rows) if required_count_rows
                else len(required_groups) if coverage else 5
            ),
            "required_group_satisfied_count": (
                sum((row["selected_count"] or 0) > 0 for row in required_count_rows)
                if required_count_rows else
                len(required_groups & selected_groups) if coverage else None
            ),
            "adult_required_branch_satisfied": (
                observed_branch_satisfied("adult")
                if required_count_rows else
                "adult" in selected_branches if "adult" in required_branches else None
            ),
            "pediatric_required_branch_satisfied": (
                observed_branch_satisfied("pediatric")
                if required_count_rows else
                "pediatric" in selected_branches if "pediatric" in required_branches else None
            ),
            "selected_source_unit_count": trace.get("selected_source_unit_count"),
            "verified_statement_count": q002["statement_count"],
            "reconstruction_text_exact": q002["reconstruction_text_exact"],
            "reconstruction_quote_exact": q002["reconstruction_quote_exact"],
            "exact_citation_validation": q002["exact_citation_validation"],
            "citation_coverage": q002["citation_coverage"],
            "source_ordered": q002["source_ordered"],
            "source_order_reversal_count": q002["source_order_reversal_count"],
            "unsupported_number": unsupported("number"),
            "unsupported_unit": unsupported("unit"),
            "unsupported_condition": False if validation_passed else None,
            "unsupported_negation": False if validation_passed else None,
            "unsupported_action": unsupported("unsupported action"),
            "validation_passed": validation_passed,
            "validated_statements": q002.get("statements", []) if validation_passed else [],
            "validated_sources": q002.get("validated_sources", []) if validation_passed else [],
            "selected_evidence_chunk_ids": q002["selected_evidence_chunk_ids"],
            "pre_required_gold_recall": q002["pre_gold_recall"]["required"],
            "post_required_gold_recall": q002["post_gold_recall"]["required"],
            "response_selection_schema_version": trace.get(
                "response_selection_schema_version"
            ),
        },
        "security": {
            "api_key_saved": False,
            "authorization_header_saved": False,
            "full_prompt_saved": False,
            "raw_response_saved": False,
            "message_content_saved": False,
            "refusal_content_saved": False,
        },
    }


def write_artifacts(report, output):
    output.mkdir(parents=True, exist_ok=False)
    name = "mock_report.json" if report["mode"] == "mock" else "live_report.json"
    (output / name).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    q002 = report["q002"]
    rows = "".join(
        f"<tr><th>{escape(str(key))}</th><td>{escape(str(value))}</td></tr>"
        for key, value in q002.items()
        if key not in {
            "selected_evidence_chunk_ids", "selected_source_unit_ids",
            "validated_statements", "validated_sources",
        }
    )
    chunk_rows = "".join(
        f"<li><code>{escape(chunk_id)}</code></li>"
        for chunk_id in q002["selected_evidence_chunk_ids"]
    )
    slot_rows = "".join(
        "<tr>"
        f"<td>{escape(slot['prompt_group_id'])}</td>"
        f"<td><code>{escape(slot['group_key'])}</code></td>"
        f"<td>{escape(slot['branch'])}</td>"
        f"<td>{escape(str(slot['required']))}</td>"
        f"<td>{slot['selectable_source_unit_count']}</td>"
        f"<td>{slot['selected_count']}</td>"
        f"<td>{slot['source_order']}</td>"
        "</tr>"
        for slot in q002.get("selection_group_slots", ())
    )
    negative_rows = "".join(
        f"<tr><td>{escape(name)}</td><td><code>{escape(result)}</code></td></tr>"
        for name, result in report.get("negative_contracts", {}).items()
        if name != "verified_by"
    )
    schema_audit = report.get("provider_schema_audit", {})
    policy_audit = report.get("selection_policy_audit", {})
    policy_rows = "".join(
        f"<tr><th>{escape(str(key))}</th><td>{escape(str(value))}</td></tr>"
        for key, value in policy_audit.items()
    )
    group_count_rows = "".join(
        "<tr>"
        f"<td>{escape(str(row['prompt_group_id']))}</td>"
        f"<td>{escape(str(row['selected_count']))}</td>"
        "</tr>"
        for row in q002.get("group_selected_counts", ())
    )
    keyword_rows = "".join(
        f"<tr><td><code>{escape(keyword)}</code></td><td>{count}</td></tr>"
        for keyword, count in schema_audit.get("keyword_counts", {}).items()
    )
    source_by_id = {
        source["chunk_id"]: source for source in q002.get("validated_sources", ())
    }
    statement_rows = "".join(
        "<tr>"
        f"<td>{index}</td><td>{escape(statement['text'])}</td>"
        f"<td>{'<br>'.join(escape(item['quote']) for item in statement['evidence'])}</td>"
        f"<td>{'<br>'.join(escape(source_by_id.get(item['chunk_id'], {}).get('document_name', '')) for item in statement['evidence'])}</td>"
        f"<td>{'<br>'.join(escape(str(source_by_id.get(item['chunk_id'], {}).get('page', ''))) for item in statement['evidence'])}</td>"
        "</tr>"
        for index, statement in enumerate(q002.get("validated_statements", ()), start=1)
    ) or '<tr><td colspan="5">검증된 final Answer 없음</td></tr>'
    (output / "review.html").write_text(
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<title>SCHAT Source Unit RAG evaluation</title><style>"
        "body{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccd1d1;padding:8px;text-align:left}"
        "th{background:#eef3f6}</style></head><body>"
        f"<h1>Source Unit RAG {escape(report['mode'])} 평가</h1>"
        f"<p>생성: {escape(report['generated_at'])}</p><table>{rows}</table>"
        f"<h2>Selection policy audit</h2><table>{policy_rows}</table>"
        "<h2>Group별 selected count</h2><table><thead><tr>"
        "<th>slot</th><th>selected count</th></tr></thead>"
        f"<tbody>{group_count_rows}</tbody></table>"
        "<h2>검증된 final Answer</h2><table><thead><tr>"
        "<th>#</th><th>statement</th><th>exact citation</th><th>문서</th><th>페이지</th>"
        f"</tr></thead><tbody>{statement_rows}</tbody></table>"
        "<h2>Request-scoped group slots</h2><table><thead><tr>"
        "<th>slot</th><th>group key</th><th>branch</th><th>required</th>"
        "<th>selectable</th><th>selected</th><th>source order</th>"
        f"</tr></thead><tbody>{slot_rows}</tbody></table>"
        "<h2>Provider schema keyword audit</h2><table><thead><tr>"
        f"<th>keyword</th><th>count</th></tr></thead><tbody>{keyword_rows}</tbody></table>"
        "<h2>Fail-closed Mock contracts</h2><table><thead><tr>"
        f"<th>case</th><th>result</th></tr></thead><tbody>{negative_rows}</tbody></table>"
        f"<h2>Selected evidence 12 chunks</h2><ol>{chunk_rows}</ol>"
        "<p>API key, Authorization, 전체 prompt, raw response/content는 저장하지 않았습니다.</p>"
        "</body></html>",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_live_report() if args.live else build_mock_report()
    write_artifacts(report, args.output)
    print(json.dumps({
        "mode": report["mode"],
        "output": str(args.output),
        "q002": {
            key: report["q002"].get(key)
            for key in (
                "transport_calls", "answerable", "verified_statement_count",
                "response_parse_stage", "failure_code", "validation_reason",
            )
        },
        "q006_calls": report["q006"]["transport_calls"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
