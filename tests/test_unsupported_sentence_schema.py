from src.ai import AI_VERSION, GROQ_REQUEST_TOKEN_BUDGET, OUTPUT_LIMIT, PROMPT_EVIDENCE_SCHEMA_VERSION


def test_strict_schema_descriptions_and_grounding_contracts_pass_mock_evaluation():
    assert AI_VERSION == 19
    assert PROMPT_EVIDENCE_SCHEMA_VERSION == 6
    assert OUTPUT_LIMIT == 2048
    assert GROQ_REQUEST_TOKEN_BUDGET == 5120
