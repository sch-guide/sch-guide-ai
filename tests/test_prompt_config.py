from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from src.prompt_config import PromptConfigError, load_evaluation_prompt

PROMPT_ROOT = Path("src/config/prompts")


def copy_prompt_tree(tmp_path: Path) -> Path:
    target = tmp_path / "prompts"
    shutil.copytree(PROMPT_ROOT, target)
    return target


def replace_text(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    assert old in content
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


def test_loads_registered_evaluation_prompt_with_stable_hash() -> None:
    first = load_evaluation_prompt()
    second = load_evaluation_prompt()

    assert first.prompt_version == "v1.0-natural-grounded"
    assert first.config_sha256 == second.config_sha256
    assert len(first.config_sha256) == 64
    assert first.publish_controlled is False
    assert first.semantic_support_status == "pending"
    assert first.follow_up_enabled is False
    assert first.semantic_equivalence_options == (
        "동일",
        "경미한 변화",
        "의미 변경",
        "판단 어려움",
    )
    assert first.system
    assert first.instruction
    assert set(first.runtime_templates) == {
        "evidence_header",
        "evidence_row",
        "intent",
        "requested_phase",
        "before_phase_guard",
        "source_order",
        "required_coverage_header",
        "required_coverage_instruction",
        "required_coverage_row",
    }


def test_loaded_prompt_uses_korean_human_facing_runtime_and_safety_text() -> None:
    config = load_evaluation_prompt()

    assert config.runtime_templates == {
        "evidence_header": "검증된 근거:",
        "evidence_row": "{source_unit_id}: {exact_text}",
        "intent": "질문 의도: {intent}.",
        "requested_phase": (
            "요청된 업무 단계: {requested_phase}. 해당 단계의 근거만 사용한다."
        ),
        "before_phase_guard": (
            "시행 전·준비 단계 질문에는 시행 중 또는 시행 후의 행위를 추가하지 않는다."
        ),
        "source_order": (
            "행위 문장은 SourceUnit 순서의 오름차순으로 유지한다. 서로 다른 SourceUnit의 "
            "업무 단계를 재정렬하거나 병합하거나 이동하지 않는다."
        ),
        "required_coverage_header": (
            "내부 필수 coverage checklist "
            "(slot ID와 category는 사용자 답변에 출력하지 않음):"
        ),
        "required_coverage_instruction": (
            "필수 답변 범위는 간결성, 요약 및 중복 제거보다 우선한다. 아래 각 required "
            "slot은 생략할 수 없는 필수 항목이다. 각 slot마다 최소 하나의 statement에서 "
            "연결된 SourceUnit의 관련 임상 의미를 조건, 한정 표현, 행위 종류와 강도, "
            "숫자·단위·시간·속도·빈도·간격 및 각각이 수식하는 대상·제제·행위의 관계까지 "
            "포함하여 완전하게 표현한다. 필수 의미를 여러 statement에 불완전하게 나누지 "
            "않고, 이유·전제만 요약한 채 적용·시행 등 결론 행위를 생략하지 않는다. 서로 "
            "다른 제제 또는 SourceUnit의 수치 관계를 혼합하지 않으며, 같은 제제의 수치라도 "
            "서로 다른 조건이나 표 행에 속하면 각각의 조건과 묶어 분리한다. 해당 "
            "statement의 supporting_source_unit_ids에는 연결된 request-local SourceUnit ID를 "
            "반드시 포함한다. 자연스러움보다 임상 의미와 필수 coverage 보존을 우선한다. "
            "slot_id, category 및 checklist는 최종 사용자 답변에 출력하지 않는다. JSON을 "
            "반환하기 전에 모든 required slot 충족 여부를 내부적으로 확인한다. 검증된 "
            "SourceUnit에 없는 새로운 임상 정보를 추가하지 않는다. coverage 검증 실패 시 "
            "재생성하지 않고 기존 extractive fallback을 유지한다."
        ),
        "required_coverage_row": (
            "- {slot_id} [{category}]: 생략 금지 · 완전한 statement 최소 1개 · "
            "supporting SourceUnit {supporting_source_unit_ids}"
        ),
    }
    assert config.safety_rules == (
        "요청 범위에서 검증된 SourceUnit만 사용한다.",
        "모든 문장은 실제로 해당 문장을 뒷받침하는 SourceUnit ID를 하나 이상 포함해야 한다.",
        "숫자, 단위, 시간, 속도, 빈도 및 간격은 해당 대상·제제·행위와 결합된 하나의 사실로 "
        "보존하고 서로 다른 제제 또는 SourceUnit 사이에서 혼합하지 않으며, "
        "조건·예외·부정·금기의 의미도 보존한다.",
        "성인 branch와 소아 branch를 혼합하지 않는다.",
        "서로 다른 workflow phase를 한 문장에 혼합하지 않는다.",
        "절차에서는 필요한 SourceUnit 순서를 유지한다.",
        "문장(statement) text 안에 목록 번호나 단계 표지를 작성하지 않는다. 문장의 순서는 "
        "SourceUnit 순서로 별도 관리한다.",
        "간결하게 만들기 위해 필수 AnswerCoverage의 전제와 결론 행위를 누락하지 않는다.",
        "자연스러움보다 임상 의미와 필수 AnswerCoverage 보존을 우선하며, 어순 변경, "
        "연결어 추가, 중복 제거 및 의미가 동일한 자연스러운 표현만 허용한다.",
        "새로운 임상 개념, 인과관계 또는 근거 없는 강조를 추가하지 않으며, 행위 주체, "
        "행위 종류(시행·확인·측정·투여·중단 등) 또는 "
        "행위 강도(반드시·즉시·권고·고려·가능·금지 등)를 변경하지 않는다.",
        "서버에서 검증한 metadata가 없으면 제목, 예외, 주의사항 또는 후속 질문을 "
        "추가하지 않는다.",
        "검증에 하나라도 실패하면 재시도하거나 수정하지 않고 기존 extractive fallback을 유지한다.",
    )


def test_localization_keeps_registry_status_purpose_and_strategy_contracts() -> None:
    registry = yaml.safe_load((PROMPT_ROOT / "registry.yaml").read_text(encoding="utf-8"))
    prompt = yaml.safe_load(
        (PROMPT_ROOT / "v1.0-natural-grounded.yaml").read_text(encoding="utf-8")
    )

    assert registry["registry_status"] == "draft"
    assert registry["prompts"][0]["status"] == "draft_evaluation_only"
    assert (
        registry["prompts"][0]["purpose"]
        == "grounded_natural_answer_offline_evaluation"
    )
    assert prompt["status"] == "draft_evaluation_only"
    assert prompt["purpose"] == "grounded_natural_answer_offline_evaluation"
    assert {
        name: value["strategy"]
        for name, value in prompt["presentation"].items()
        if isinstance(value, dict) and "strategy" in value
    } == {
        "fact": "core_statement_then_supported_details",
        "preparation": "before_phase_in_source_order",
        "procedure": "numbered_source_order_with_verified_branch_and_phase",
        "condition_exception": (
            "show_only_when_source_condition_or_exception_marker_is_verified"
        ),
        "comparison": "separate_only_sides_with_actual_supporting_citations",
        "summary": "core_summary_without_required_coverage_loss",
        "numeric": "source_supported_number_unit_time_only",
        "prohibition": "preserve_verified_negation_and_prohibition",
        "table_related": "require_verified_structured_table_source_identity",
    }


def test_config_hash_ignores_yaml_comments_and_whitespace(tmp_path: Path) -> None:
    root = copy_prompt_tree(tmp_path)
    baseline = load_evaluation_prompt(prompt_root=root)
    version_path = root / "v1.0-natural-grounded.yaml"
    version_path.write_text(
        version_path.read_text(encoding="utf-8") + "\n# formatting only\n",
        encoding="utf-8",
    )

    reformatted = load_evaluation_prompt(prompt_root=root)

    assert reformatted.config_sha256 == baseline.config_sha256


def test_config_hash_changes_when_prompt_meaning_changes(tmp_path: Path) -> None:
    root = copy_prompt_tree(tmp_path)
    baseline = load_evaluation_prompt(prompt_root=root)
    version_path = root / "v1.0-natural-grounded.yaml"
    replace_text(
        version_path,
        "created_at: '2026-09-20'",
        "created_at: '2026-09-21'",
    )

    changed = load_evaluation_prompt(prompt_root=root)

    assert changed.config_sha256 != baseline.config_sha256


@pytest.mark.parametrize(
    ("file_name", "old", "new", "reason"),
    [
        (
            "registry.yaml",
            "registry_status: draft",
            "registry_status: active",
            "registry_status",
        ),
        (
            "registry.yaml",
            "production_enabled: false",
            "production_enabled: true",
            "production_enabled",
        ),
        (
            "registry.yaml",
            "publish_controlled: false",
            "publish_controlled: true",
            "publish_controlled",
        ),
        (
            "registry.yaml",
            "semantic_support_status: pending",
            "semantic_support_status: verified",
            "semantic_support_status",
        ),
        (
            "registry.yaml",
            "follow_up_enabled: false",
            "follow_up_enabled: true",
            "follow_up_enabled",
        ),
        (
            "v1.0-natural-grounded.yaml",
            "compatible_validator_version: ai-19-controlled-v1",
            "compatible_validator_version: unknown-validator",
            "compatible_validator_version",
        ),
        (
            "v1.0-natural-grounded.yaml",
            "max_statements: 16",
            "max_statements: 17",
            "max_statements",
        ),
        (
            "v1.0-natural-grounded.yaml",
            "allow_model_headings: false",
            "allow_model_headings: true",
            "output_contract",
        ),
        (
            "v1.0-natural-grounded.yaml",
            "maximum_count: 1",
            "maximum_count: 2",
            "follow_up_contract",
        ),
        (
            "v1.0-natural-grounded.yaml",
            "publish_controlled: false",
            "publish_controlled: true",
            "publish_controlled",
        ),
        (
            "v1.0-natural-grounded.yaml",
            "semantic_support_status: pending",
            "semantic_support_status: verified",
            "semantic_support_status",
        ),
        (
            "v1.0-natural-grounded.yaml",
            "enabled_for_evaluation: false",
            "enabled_for_evaluation: true",
            "follow_up_enabled",
        ),
        (
            "v1.0-natural-grounded.yaml",
            "fallback_to_extractive: true",
            "fallback_to_extractive: false",
            "fallback_to_extractive",
        ),
        (
            "v1.0-natural-grounded.yaml",
            "retry_count: 0",
            "retry_count: 1",
            "retry_count",
        ),
    ],
)
def test_safety_flags_fail_closed(
    tmp_path: Path,
    file_name: str,
    old: str,
    new: str,
    reason: str,
) -> None:
    root = copy_prompt_tree(tmp_path)
    replace_text(root / file_name, old, new)

    with pytest.raises(PromptConfigError, match=reason):
        load_evaluation_prompt(prompt_root=root)


def test_registered_path_cannot_escape_prompt_root(tmp_path: Path) -> None:
    root = copy_prompt_tree(tmp_path)
    outside = tmp_path / "outside.yaml"
    shutil.copy2(root / "v1.0-natural-grounded.yaml", outside)
    replace_text(
        root / "registry.yaml",
        "path: v1.0-natural-grounded.yaml",
        "path: ../outside.yaml",
    )

    with pytest.raises(PromptConfigError, match="prompt_path"):
        load_evaluation_prompt(prompt_root=root)


def test_registry_and_prompt_purpose_must_match(tmp_path: Path) -> None:
    root = copy_prompt_tree(tmp_path)
    replace_text(
        root / "registry.yaml",
        "purpose: grounded_natural_answer_offline_evaluation",
        "purpose: production_generation",
    )

    with pytest.raises(PromptConfigError, match="prompt_purpose"):
        load_evaluation_prompt(prompt_root=root)


def test_registry_and_version_must_match(tmp_path: Path) -> None:
    root = copy_prompt_tree(tmp_path)
    replace_text(
        root / "v1.0-natural-grounded.yaml",
        "prompt_version: v1.0-natural-grounded",
        "prompt_version: v9-incompatible",
    )

    with pytest.raises(PromptConfigError, match="prompt_version"):
        load_evaluation_prompt(prompt_root=root)


def test_duplicate_registry_version_fails_closed(tmp_path: Path) -> None:
    root = copy_prompt_tree(tmp_path)
    registry = root / "registry.yaml"
    content = registry.read_text(encoding="utf-8")
    duplicate = """
  - prompt_version: "v1.0-natural-grounded"
    path: "v1.0-natural-grounded.yaml"
    status: "draft_evaluation_only"
    purpose: "grounded_natural_answer_offline_evaluation"
    publish_controlled: false
    semantic_support_status: "pending"
    follow_up_enabled: false
"""
    registry.write_text(content + duplicate, encoding="utf-8")

    with pytest.raises(PromptConfigError, match="prompt_registration"):
        load_evaluation_prompt(prompt_root=root)


def test_missing_runtime_template_fails_closed(tmp_path: Path) -> None:
    root = copy_prompt_tree(tmp_path)
    version_path = root / "v1.0-natural-grounded.yaml"
    content = version_path.read_text(encoding="utf-8")
    line = "  evidence_header: '검증된 근거:'\n"
    assert line in content
    version_path.write_text(content.replace(line, "", 1), encoding="utf-8")

    with pytest.raises(PromptConfigError, match="runtime_templates"):
        load_evaluation_prompt(prompt_root=root)


def test_unknown_runtime_placeholder_fails_closed(tmp_path: Path) -> None:
    root = copy_prompt_tree(tmp_path)
    replace_text(
        root / "v1.0-natural-grounded.yaml",
        "  intent: '질문 의도: {intent}.'",
        "  intent: '질문 의도: {intent} {unknown}.'",
    )

    with pytest.raises(PromptConfigError, match="runtime_template_placeholder"):
        load_evaluation_prompt(prompt_root=root)


def test_invalid_yaml_fails_closed(tmp_path: Path) -> None:
    root = copy_prompt_tree(tmp_path)
    replace_text(root / "registry.yaml", "schema_version: 1", "schema_version: [")

    with pytest.raises(PromptConfigError, match="prompt_yaml"):
        load_evaluation_prompt(prompt_root=root)


def test_duplicate_yaml_key_fails_closed(tmp_path: Path) -> None:
    root = copy_prompt_tree(tmp_path)
    registry = root / "registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8") + "\nschema_version: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(PromptConfigError, match="prompt_yaml"):
        load_evaluation_prompt(prompt_root=root)


def test_registered_toml_path_is_rejected(tmp_path: Path) -> None:
    root = copy_prompt_tree(tmp_path)
    replace_text(
        root / "registry.yaml",
        "path: v1.0-natural-grounded.yaml",
        "path: v1.0-natural-grounded.toml",
    )

    with pytest.raises(PromptConfigError, match="prompt_path"):
        load_evaluation_prompt(prompt_root=root)
