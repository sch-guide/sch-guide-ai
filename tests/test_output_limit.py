import math

from mvp.ai import GROQ_REQUEST_TOKEN_BUDGET, OUTPUT_LIMIT
from tools.rag_prompt_budget_evaluate import build_report


def test_q002_output_limit_keeps_required_evidence_and_reserves_estimated_tokens():
    report = build_report()
    budget = report["budget"]
    q002 = report["q002"]

    assert OUTPUT_LIMIT == 2048
    assert GROQ_REQUEST_TOKEN_BUDGET == 5120
    assert budget["request_token_budget"] == 5120
    assert budget["estimated_request_tokens"] <= 5120
    assert budget["headroom"] == 5120 - budget["estimated_request_tokens"]
    assert budget["required_headroom"] == max(
        256, math.ceil(budget["estimated_request_tokens"] * 0.08)
    )
    assert budget["headroom_passed"] is True
    assert len(q002["selected_chunk_ids"]) == 12
    assert q002["pre_gold_recall"]["required"] == {
        "recalled": 10,
        "total": 10,
        "recall": 1.0,
    }
    assert q002["post_gold_recall"]["required"] == q002["pre_gold_recall"]["required"]
    assert q002["required_groups_retained"] is True
    assert q002["parent_partial_inclusion_count"] == 0
    assert set(q002["required_branches_after"]) == {"adult", "pediatric"}
    assert set(q002["selected_branches"]) == {"common", "adult", "pediatric"}
    assert q002["source_ordered"] is True
    assert q002["quota_reserved_tokens"] == budget["estimated_request_tokens"]
    assert q002["quota_reserved_tokens"] != GROQ_REQUEST_TOKEN_BUDGET
    assert q002["mock_http_calls"] == 1
    assert q002["answerable"] is True
    assert q002["exact_citation_passed"] is True
    assert report["q006"]["mock_http_calls"] == 0
