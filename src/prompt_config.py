"""Fail-closed loading for evaluation-only controlled-generation prompts."""

from __future__ import annotations

import hashlib
import json
import string
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

PROMPT_ROOT = Path(__file__).resolve().parent / "config" / "prompts"
REQUIRED_RUNTIME_TEMPLATES = {
    "evidence_header": frozenset(),
    "evidence_row": frozenset({"source_unit_id", "exact_text"}),
    "intent": frozenset({"intent"}),
    "requested_phase": frozenset({"requested_phase"}),
    "before_phase_guard": frozenset(),
    "source_order": frozenset(),
    "required_coverage_header": frozenset(),
    "required_coverage_instruction": frozenset(),
    "required_coverage_row": frozenset(
        {"slot_id", "category", "supporting_source_unit_ids"}
    ),
}
SEMANTIC_EQUIVALENCE_OPTIONS = (
    "동일",
    "경미한 변화",
    "의미 변경",
    "판단 어려움",
)
EXPECTED_PURPOSE = "grounded_natural_answer_offline_evaluation"
EXPECTED_SAFETY_CONTRACT = "controlled-generation-safety-v1"
EXPECTED_VALIDATOR_VERSION = "ai-19-controlled-v1"


class PromptConfigError(ValueError):
    """A stable fail-closed reason for an invalid evaluation Prompt config."""


@dataclass(frozen=True)
class LoadedPromptConfig:
    prompt_version: str
    system: str
    instruction: str
    safety_rules: tuple[str, ...]
    runtime_templates: Mapping[str, str]
    config_sha256: str
    max_statements: int
    publish_controlled: bool
    semantic_support_status: str
    follow_up_enabled: bool
    semantic_equivalence_options: tuple[str, ...]


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = yaml.load(handle, Loader=_UniqueKeySafeLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise PromptConfigError("prompt_yaml") from exc
    if not isinstance(value, dict):
        raise PromptConfigError("prompt_yaml")
    return value


def _require_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise PromptConfigError(f"missing_{key}")
    return child


def _require_str(value: Mapping[str, Any], key: str) -> str:
    child = value.get(key)
    if not isinstance(child, str) or not child.strip():
        raise PromptConfigError(f"missing_{key}")
    return child


def _require_string_value(value: Mapping[str, Any], key: str) -> str:
    child = value.get(key)
    if not isinstance(child, str):
        raise PromptConfigError(f"missing_{key}")
    return child


def _require_bool(value: Mapping[str, Any], key: str) -> bool:
    child = value.get(key)
    if not isinstance(child, bool):
        raise PromptConfigError(f"missing_{key}")
    return child


def _require_int(value: Mapping[str, Any], key: str) -> int:
    child = value.get(key)
    if not isinstance(child, int) or isinstance(child, bool):
        raise PromptConfigError(f"missing_{key}")
    return child


def _select_unique_entry(
    registry: Mapping[str, Any], prompt_version: str
) -> Mapping[str, Any]:
    prompts = registry.get("prompts")
    if not isinstance(prompts, list):
        raise PromptConfigError("prompt_registration")
    matches = [
        entry
        for entry in prompts
        if isinstance(entry, dict) and entry.get("prompt_version") == prompt_version
    ]
    if len(matches) != 1:
        raise PromptConfigError("prompt_registration")
    return matches[0]


def _resolve_registered_path(root: Path, relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute():
        raise PromptConfigError("prompt_path")
    resolved = (root / path).resolve()
    if resolved.parent != root or resolved.suffix.casefold() != ".yaml":
        raise PromptConfigError("prompt_path")
    return resolved


def _template_fields(template: str) -> frozenset[str]:
    try:
        fields = {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(template)
            if field_name is not None
        }
    except ValueError as exc:
        raise PromptConfigError("runtime_template_placeholder") from exc
    if any("." in name or "[" in name or "]" in name for name in fields):
        raise PromptConfigError("runtime_template_placeholder")
    return frozenset(fields)


def _validated_runtime_templates(prompt: Mapping[str, Any]) -> dict[str, str]:
    values = _require_mapping(prompt, "runtime_templates")
    if set(values) != set(REQUIRED_RUNTIME_TEMPLATES):
        raise PromptConfigError("runtime_templates")
    validated: dict[str, str] = {}
    for key, allowed_fields in REQUIRED_RUNTIME_TEMPLATES.items():
        template = _require_str(values, key)
        if _template_fields(template) != allowed_fields:
            raise PromptConfigError("runtime_template_placeholder")
        validated[key] = template
    return validated


def _validate_evaluation_only_contract(
    registry: Mapping[str, Any],
    entry: Mapping[str, Any],
    prompt: Mapping[str, Any],
    prompt_version: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    if _require_int(registry, "schema_version") != 1:
        raise PromptConfigError("schema_version")
    if _require_str(registry, "registry_status") != "draft":
        raise PromptConfigError("registry_status")
    if _require_bool(registry, "production_enabled"):
        raise PromptConfigError("production_enabled")
    if _require_string_value(registry, "production_prompt_version") != "":
        raise PromptConfigError("production_prompt_version")
    if _require_str(entry, "status") != "draft_evaluation_only":
        raise PromptConfigError("prompt_status")
    if _require_str(entry, "purpose") != EXPECTED_PURPOSE:
        raise PromptConfigError("prompt_purpose")
    if _require_bool(entry, "publish_controlled"):
        raise PromptConfigError("publish_controlled")
    if _require_str(entry, "semantic_support_status") != "pending":
        raise PromptConfigError("semantic_support_status")
    if _require_bool(entry, "follow_up_enabled"):
        raise PromptConfigError("follow_up_enabled")

    if _require_int(prompt, "schema_version") != 1:
        raise PromptConfigError("schema_version")
    if _require_str(prompt, "prompt_version") != prompt_version:
        raise PromptConfigError("prompt_version")
    if _require_str(prompt, "status") != "draft_evaluation_only":
        raise PromptConfigError("prompt_status")
    if _require_str(prompt, "purpose") != EXPECTED_PURPOSE:
        raise PromptConfigError("prompt_purpose")
    if _require_str(prompt, "safety_contract_version") != EXPECTED_SAFETY_CONTRACT:
        raise PromptConfigError("safety_contract_version")
    if _require_str(prompt, "compatible_validator_version") != EXPECTED_VALIDATOR_VERSION:
        raise PromptConfigError("compatible_validator_version")
    if _require_bool(prompt, "publish_controlled"):
        raise PromptConfigError("publish_controlled")
    if _require_str(prompt, "semantic_support_status") != "pending":
        raise PromptConfigError("semantic_support_status")

    publication = _require_mapping(prompt, "publication")
    if _require_bool(publication, "production_connected"):
        raise PromptConfigError("production_connected")
    if _require_bool(publication, "publish_controlled"):
        raise PromptConfigError("publish_controlled")
    if not _require_bool(publication, "fallback_to_extractive"):
        raise PromptConfigError("fallback_to_extractive")
    if _require_str(publication, "semantic_support_status") != "pending":
        raise PromptConfigError("semantic_support_status")
    if _require_int(publication, "retry_count") != 0:
        raise PromptConfigError("retry_count")

    follow_up = _require_mapping(prompt, "follow_up")
    if _require_bool(follow_up, "enabled_for_evaluation"):
        raise PromptConfigError("follow_up_enabled")
    if _require_bool(follow_up, "persist_text"):
        raise PromptConfigError("follow_up_persistence")
    if (
        _require_int(follow_up, "maximum_count") != 1
        or _require_str(follow_up, "activation_status")
        != "deferred_until_separate_evaluation"
    ):
        raise PromptConfigError("follow_up_contract")

    output_contract = _require_mapping(prompt, "output_contract")
    if (
        _require_str(output_contract, "type") != "existing_controlled_response_schema"
        or output_contract.get("statement_fields")
        != ["text", "supporting_source_unit_ids"]
        or any(
            _require_bool(output_contract, key)
            for key in (
                "allow_extra_fields",
                "allow_model_headings",
                "allow_model_list_numbers",
                "allow_model_citations",
                "allow_model_follow_up",
            )
        )
    ):
        raise PromptConfigError("output_contract")
    answer_generation = _require_mapping(prompt, "answer_generation")
    if _require_int(answer_generation, "max_statements") != 16:
        raise PromptConfigError("max_statements")
    safety = _require_mapping(prompt, "safety")
    rules = safety.get("rules")
    if (
        not isinstance(rules, list)
        or not rules
        or not all(isinstance(rule, str) and rule.strip() for rule in rules)
    ):
        raise PromptConfigError("safety_rules")
    human_review = _require_mapping(prompt, "human_review")
    options = human_review.get("semantic_equivalence_options")
    normalized_options = tuple(options) if isinstance(options, list) else ()
    if normalized_options != SEMANTIC_EQUIVALENCE_OPTIONS:
        raise PromptConfigError("semantic_equivalence_options")
    return publication, follow_up, answer_generation


def _canonical_hash(entry: Mapping[str, Any], prompt: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"registry_entry": entry, "prompt": prompt},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_evaluation_prompt(
    version: str | None = None,
    *,
    prompt_root: Path = PROMPT_ROOT,
) -> LoadedPromptConfig:
    """Load exactly one registered draft Prompt or fail without a fallback config."""
    root = prompt_root.resolve()
    registry = _read_yaml(root / "registry.yaml")
    selected = version or _require_str(registry, "evaluation_candidate_version")
    entry = _select_unique_entry(registry, selected)
    prompt_path = _resolve_registered_path(root, _require_str(entry, "path"))
    prompt = _read_yaml(prompt_path)
    _, _, answer_generation = _validate_evaluation_only_contract(
        registry, entry, prompt, selected
    )
    runtime_templates = _validated_runtime_templates(prompt)
    safety = _require_mapping(prompt, "safety")
    return LoadedPromptConfig(
        prompt_version=selected,
        system=_require_str(answer_generation, "system").strip(),
        instruction=_require_str(answer_generation, "instruction").strip(),
        safety_rules=tuple(str(rule).strip() for rule in safety["rules"]),
        runtime_templates=MappingProxyType(runtime_templates),
        config_sha256=_canonical_hash(entry, prompt),
        max_statements=_require_int(answer_generation, "max_statements"),
        publish_controlled=False,
        semantic_support_status="pending",
        follow_up_enabled=False,
        semantic_equivalence_options=SEMANTIC_EQUIVALENCE_OPTIONS,
    )
