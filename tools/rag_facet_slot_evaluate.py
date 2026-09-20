"""Facet-slot SourceUnit selection을 외부 호출 없이 검증하고 안전한 산출물을 만듭니다."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from functools import lru_cache
from html import escape
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JSONSchemaError

from src.ai import (
    AI_VERSION,
    GROQ_REQUEST_TOKEN_BUDGET,
    OUTPUT_LIMIT,
    PROMPT_EVIDENCE_SCHEMA_VERSION,
    RESPONSE_SELECTION_SCHEMA_VERSION,
    answer_text,
    generate,
    groq_answer_json_schema,
    prompt_messages,
    response_schema_tokens,
    validate_source_unit_selection,
)
from src.evidence import assess_evidence
from src.library import NO_GUIDELINE
from src.query import plan_query
from src.settings import GuideError, Settings
from tools.rag_phase1_evaluate import stage_recall
from tools.rag_phase2_evaluate import _load_q002


class MockQuota:
    def __init__(self):
        self.reservations = []

    def reserve(self, settings, user_id, tokens):
        self.reservations.append(tokens)
        return "facet-slot-mock"

    def settle(self, identifier, usage):
        pass

    def cancel(self, identifier):
        pass


class RejectTransport(httpx.BaseTransport):
    def __init__(self):
        self.calls = 0

    def handle_request(self, request):
        self.calls += 1
        raise RuntimeError("Q006 transport must remain unused")


def settings():
    return Settings(
        llm_provider="groq_free",
        llm_key="fixture",
        llm_model="openai/gpt-oss-20b",
        llm_approved=True,
        groq_free_confirmed=True,
    )


def facet_response(contract, identifiers, *, preserve_order=True):
    target = tuple(identifiers)
    if preserve_order:
        @lru_cache(maxsize=None)
        def visit(slot_index, introduced):
            if slot_index == len(contract.slots):
                return () if introduced == len(target) else None
            allowed = set(contract.slots[slot_index].eligible_source_unit_ids)
            choices = []
            if introduced < len(target) and target[introduced] in allowed:
                choices.append(target[introduced])
            choices.extend(identifier for identifier in target[:introduced] if identifier in allowed)
            for identifier in choices:
                next_introduced = introduced + int(
                    introduced < len(target) and identifier == target[introduced]
                )
                tail = visit(slot_index + 1, next_introduced)
                if tail is not None:
                    return (identifier,) + tail
            return None

        chosen = visit(0, 0)
        if chosen is None:
            raise RuntimeError("requested IDs cannot be introduced in source order")
    else:
        matched = {}

        def place(identifier, visited):
            for slot_index, slot in enumerate(contract.slots):
                if slot_index in visited or identifier not in slot.eligible_source_unit_ids:
                    continue
                visited.add(slot_index)
                if slot_index not in matched or place(matched[slot_index], visited):
                    matched[slot_index] = identifier
                    return True
            return False

        if not all(place(identifier, set()) for identifier in target):
            raise RuntimeError("requested IDs cannot be assigned to distinct facets")
        chosen = tuple(
            matched.get(index)
            or next(
                identifier for identifier in target
                if identifier in slot.eligible_source_unit_ids
            )
            for index, slot in enumerate(contract.slots)
        )
    return json.dumps(
        {
            "facet_selections": {
                slot.prompt_facet_id: identifier
                for slot, identifier in zip(contract.slots, chosen)
            }
        },
        ensure_ascii=False,
    )


def validation_reason(raw, catalog, contract, coverage, plan):
    trace = {}
    try:
        selection, units = validate_source_unit_selection(
            raw, catalog, contract, coverage, plan, trace=trace
        )
    except GuideError:
        return trace.get("validation_reason"), trace, None, ()
    return None, trace, selection, units


def q002_inputs():
    metadata, gold, hits = _load_q002()
    plan = plan_query(gold["question"], documents=[metadata])
    before = assess_evidence(plan, hits)
    prompt_trace = {}
    _, selected, catalog, contract = prompt_messages(
        plan.query,
        before.hits,
        14000,
        token_budget=GROQ_REQUEST_TOKEN_BUDGET,
        plan=plan,
        groups=before.groups,
        trace=prompt_trace,
        return_catalog=True,
        return_contract=True,
    )
    after = assess_evidence(plan, selected)
    by_source = {(unit.chunk_id, unit.source_order[1]): unit for unit in catalog}
    required_stages = [
        stage for stage in gold["stages"] if stage["importance"] == "required"
    ]
    gold_ids = []
    for stage in required_stages:
        for item in stage["source_unit_requirements"][0]["all_of"]:
            unit = by_source[(item["chunk_id"], item["source_unit_position"])]
            if hashlib.sha256(unit.exact_text.encode()).hexdigest() != item["exact_text_sha256"]:
                raise RuntimeError("Q002 gold source-unit fingerprint drift")
            if unit.source_unit_id not in gold_ids:
                gold_ids.append(unit.source_unit_id)
    return gold, hits, plan, before, selected, after, catalog, contract, required_stages, gold_ids, prompt_trace


def build_report():
    (
        gold,
        hits,
        plan,
        before,
        selected,
        after,
        catalog,
        contract,
        stages,
        gold_ids,
        prompt_trace,
    ) = q002_inputs()
    schema = groq_answer_json_schema(contract)
    schema_validator = Draft202012Validator(schema)
    Draft202012Validator.check_schema(schema)
    empty_allowlists = sum(not slot.eligible_source_unit_ids for slot in contract.slots)
    minimum_ids = (
        "su002", "su003", "su005", "su012", "su017",
        "su020", "su021", "su024", "su029", "su032",
    )
    minimum_reason, minimum_trace, minimum_selection, minimum_units = validation_reason(
        facet_response(contract, minimum_ids),
        catalog,
        contract,
        after.procedure_coverage,
        plan,
    )

    calls = []
    gold_content = facet_response(contract, gold_ids)

    def handle(request):
        calls.append(request)
        payload = json.loads(request.content)
        request_schema = payload["response_format"]["json_schema"]["schema"]
        Draft202012Validator(request_schema).validate(json.loads(gold_content))
        if payload["max_completion_tokens"] != OUTPUT_LIMIT:
            raise RuntimeError("output limit drift")
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-oss-20b",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": gold_content, "refusal": None},
                }],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 100,
                    "total_tokens": 1100,
                },
            },
        )

    mock_trace = {}
    quota = MockQuota()
    answer, used_hits = generate(
        settings(),
        plan.query,
        hits,
        "facet-slot-mock",
        quota=quota,
        transport=httpx.MockTransport(handle),
        plan=plan,
        trace=mock_trace,
    )
    citations = [
        evidence for statement in answer.statements for evidence in statement.evidence
    ]
    exact = all(
        statement.text == statement.evidence[0].quote
        for statement in answer.statements
    )
    selected_gold = set(gold_ids)
    by_source = {(unit.chunk_id, unit.source_order[1]): unit for unit in catalog}
    recalled = sum(
        any(
            all(
                by_source[(item["chunk_id"], item["source_unit_position"])].source_unit_id
                in selected_gold
                for item in alternative["all_of"]
            )
            for alternative in stage["source_unit_requirements"]
        )
        for stage in stages
    )

    selectable_ids = [unit.source_unit_id for unit in catalog if unit.selectable]
    by_id = {unit.source_unit_id: unit for unit in catalog}
    extras = [identifier for identifier in selectable_ids if identifier not in gold_ids]
    seventeen = sorted(
        gold_ids + extras[:3], key=lambda identifier: by_id[identifier].source_order
    )
    over_cases = {}
    for label, identifiers in (("17", seventeen), ("21", selectable_ids)):
        reason, trace, _, _ = validation_reason(
            facet_response(contract, identifiers, preserve_order=False),
            catalog,
            contract,
            after.procedure_coverage,
            plan,
        )
        over_cases[label] = {
            "distinct_ids": len(set(identifiers)),
            "reason": reason,
            "reported_selected_count": trace.get("selected_source_unit_count"),
        }

    invalid_cases = {}
    missing = json.loads(gold_content)
    missing["facet_selections"].pop(contract.slots[0].prompt_facet_id)
    outside = json.loads(gold_content)
    outside["facet_selections"][contract.slots[0].prompt_facet_id] = "su999"
    extra = json.loads(gold_content)
    extra["facet_selections"]["f99"] = gold_ids[0]
    for label, value in (("missing_facet", missing), ("outside_allowlist", outside), ("extra_property", extra)):
        try:
            schema_validator.validate(value)
            schema_rejected = False
        except JSONSchemaError:
            schema_rejected = True
        reason, _, _, _ = validation_reason(
            json.dumps(value), catalog, contract, after.procedure_coverage, plan
        )
        invalid_cases[label] = {
            "schema_rejected": schema_rejected,
            "server_reason": reason,
        }

    reversed_value = json.loads(gold_content)
    second = contract.slots[1]
    third = contract.slots[2]
    reversed_value["facet_selections"][second.prompt_facet_id] = (
        second.eligible_source_unit_ids[-1]
    )
    assert by_id[reversed_value["facet_selections"][second.prompt_facet_id]].source_order > by_id[
        reversed_value["facet_selections"][third.prompt_facet_id]
    ].source_order
    reverse_reason, _, _, _ = validation_reason(
        json.dumps(reversed_value), catalog, contract, after.procedure_coverage, plan
    )
    invalid_cases["source_order_reversal"] = {
        "schema_rejected": False,
        "server_reason": reverse_reason,
    }

    reject = RejectTransport()
    q006_trace = {}
    q006, q006_hits = generate(
        settings(),
        "화성 우주선의 궤도 계산 공식은?",
        [],
        "facet-slot-q006",
        transport=reject,
        trace=q006_trace,
    )
    reservation = prompt_trace["estimated_request_tokens"]
    headroom = prompt_trace["request_token_headroom"]
    minimum_headroom = max(256, math.ceil(reservation * 0.08))
    pre_recall = stage_recall(gold, [hit.chunk.id for hit in before.hits])
    post_recall = stage_recall(gold, [hit.chunk.id for hit in after.hits])
    required_facets = len(contract.slots)
    facet_assignments = mock_trace.get("selected_facet_count")
    passed = all((
        len(selected) == len(used_hits) == 12,
        required_facets == facet_assignments == 41,
        empty_allowlists == 0,
        minimum_reason is None,
        minimum_trace.get("answer_coverage", {}).get("complete") is True,
        len(minimum_units) == 10,
        minimum_selection is not None,
        len(calls) == 1,
        len(gold_ids) == len(answer.statements) == 14,
        answer.answerable,
        exact,
        len(citations) == len(answer.statements),
        recalled == len(stages) == 10,
        mock_trace.get("citation_assessment") == "supported",
        mock_trace.get("citation_branch_metadata") == "server_evidence_group",
        mock_trace.get("answer_coverage", {}).get("complete") is True,
        all(case["reason"] == "selection_limit" for case in over_cases.values()),
        invalid_cases["missing_facet"]["schema_rejected"],
        invalid_cases["outside_allowlist"]["schema_rejected"],
        invalid_cases["extra_property"]["schema_rejected"],
        invalid_cases["source_order_reversal"]["server_reason"] == "selection_source_order",
        reject.calls == 0,
        q006_hits == [],
        answer_text(q006) == NO_GUIDELINE,
        reservation <= GROQ_REQUEST_TOKEN_BUDGET,
        headroom >= minimum_headroom,
    ))
    return {
        "schema_version": 1,
        "scope": "MockTransport and local validation only; actual Groq calls 0",
        "versions": {
            "ai": AI_VERSION,
            "prompt_evidence": PROMPT_EVIDENCE_SCHEMA_VERSION,
            "response_selection": RESPONSE_SELECTION_SCHEMA_VERSION,
        },
        "facet_contract": {
            "required_facets": required_facets,
            "empty_allowlists": empty_allowlists,
            "allowlist_sizes": [len(slot.eligible_source_unit_ids) for slot in contract.slots],
            "arrays_used": False,
        },
        "minimum_witness": {
            "distinct_ids": len(minimum_units),
            "facet_assignments": len(minimum_selection.facet_selections),
            "answer_coverage_complete": minimum_trace.get("answer_coverage", {}).get("complete"),
            "reason": minimum_reason,
        },
        "gold_mock": {
            "transport_calls": len(calls),
            "selected_evidence_chunks": len(used_hits),
            "pre_required_gold": pre_recall["required"],
            "post_required_gold": post_recall["required"],
            "source_unit_gold_recall": {"recalled": recalled, "total": len(stages)},
            "facet_assignments": facet_assignments,
            "distinct_ids": mock_trace.get("selected_source_unit_count"),
            "verified_statements": len(answer.statements),
            "server_answerable": answer.answerable,
            "exact_text_quote": exact,
            "citation_coverage": len(citations) / len(answer.statements),
            "citation_assessment": mock_trace.get("citation_assessment"),
            "duplicate_evidence": mock_trace.get("citation_assessment") == "duplicate_evidence",
            "branch_metadata_preserved": (
                mock_trace.get("citation_branch_metadata") == "server_evidence_group"
            ),
            "source_order_reversal": 0,
            "answer_coverage_complete": mock_trace.get("answer_coverage", {}).get("complete"),
        },
        "over_selection": over_cases,
        "invalid_responses": invalid_cases,
        "q006": {
            "catalog_units": 0,
            "transport_calls": reject.calls,
            "llm_called": q006_trace.get("llm_called"),
            "fixed_abstention": answer_text(q006),
        },
        "budget": {
            "request_reservation": reservation,
            "admission_cap": GROQ_REQUEST_TOKEN_BUDGET,
            "headroom": headroom,
            "minimum_headroom": minimum_headroom,
            "response_schema_serialized_tokens": response_schema_tokens(contract),
            "passed": reservation <= GROQ_REQUEST_TOKEN_BUDGET and headroom >= minimum_headroom,
        },
        "passed": passed,
    }


def write_artifacts(output, report):
    output.mkdir(parents=True, exist_ok=False)
    (output / "mock_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    gold = report["gold_mock"]
    budget = report["budget"]
    invalid_rows = "".join(
        f"<tr><td>{escape(name)}</td><td>{escape(str(value['schema_rejected']))}</td>"
        f"<td>{escape(str(value['server_reason']))}</td></tr>"
        for name, value in report["invalid_responses"].items()
    )
    (output / "review.html").write_text(
        f"""<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">
<title>SCHAT Facet-slot SourceUnit Mock 검수</title><style>
body{{font-family:system-ui,sans-serif;max-width:1050px;margin:24px auto;padding:0 18px;color:#17202a}}
table{{border-collapse:collapse;width:100%;margin:14px 0 28px}}th,td{{border:1px solid #ccd1d1;padding:8px;text-align:left}}
th{{background:#eef3f6}}.pass{{color:#196f3d;font-weight:700}}</style></head><body>
<h1>Facet-slot SourceUnit Selection Mock 검수</h1>
<p class=\"pass\">전체 판정: {escape(str(report['passed']))}</p>
<table><tr><th>required facets / empty allowlist</th><td>{report['facet_contract']['required_facets']} / {report['facet_contract']['empty_allowlists']}</td></tr>
<tr><th>minimum witness</th><td>{report['minimum_witness']['distinct_ids']} distinct IDs · coverage {report['minimum_witness']['answer_coverage_complete']}</td></tr>
<tr><th>Gold</th><td>{gold['distinct_ids']} distinct IDs · {gold['source_unit_gold_recall']['recalled']}/{gold['source_unit_gold_recall']['total']} stages</td></tr>
<tr><th>Reconstruction</th><td>{gold['verified_statements']} statements · citation {gold['citation_coverage'] * 100:.1f}%</td></tr>
<tr><th>Request budget</th><td>{budget['request_reservation']}/{budget['admission_cap']} · headroom {budget['headroom']} (minimum {budget['minimum_headroom']})</td></tr>
<tr><th>Response schema tokens</th><td>{budget['response_schema_serialized_tokens']}</td></tr>
<tr><th>Q006 transport</th><td>{report['q006']['transport_calls']}</td></tr></table>
<h2>Fail-closed fixtures</h2><table><tr><th>case</th><th>schema rejected</th><th>server reason</th></tr>{invalid_rows}</table>
<p>병원 원문, 전체 prompt, raw provider response와 API credential은 저장하지 않았습니다.</p>
</body></html>""",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report()
    if not report["passed"]:
        raise RuntimeError("facet-slot Mock acceptance failed")
    write_artifacts(args.output, report)
    print(json.dumps({
        "output": str(args.output),
        "passed": report["passed"],
        "required_facets": report["facet_contract"]["required_facets"],
        "minimum_witness": report["minimum_witness"]["distinct_ids"],
        "gold_distinct_ids": report["gold_mock"]["distinct_ids"],
        "q006_calls": report["q006"]["transport_calls"],
        "actual_groq_calls": 0,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
