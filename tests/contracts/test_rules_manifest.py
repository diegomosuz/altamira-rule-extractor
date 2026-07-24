"""Tests de RulesDirectoryManifest (artifacts/10-rules/rules-manifest.json,
Prompt 13a). Sin JSON Schema propio.

No prueba aqui las validaciones de orquestacion en tiempo de ejecucion
(correspondencia 1:1 con GuardrailDirectoryManifest, convencion de
nombre de archivo sha256(candidate_id)+".md") — esas viven en
`pipeline/rules_rendered_stage.py` y se prueban en
`tests/pipeline/test_rules_rendered_stage.py`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import RulesDirectoryManifest, RulesRecord

_VALID_HASH = "a" * 64


def _record(**overrides: object) -> RulesRecord:
    defaults: dict[str, object] = {
        "candidate_id": "candidate::det::1.0::" + _VALID_HASH + "::dec-1",
        "source_guardrail_artifact_hash": _VALID_HASH,
        "final_rule_draft_hash": _VALID_HASH,
        "relative_filename": "aa" * 32 + ".md",
        "markdown_hash": _VALID_HASH,
    }
    defaults.update(overrides)
    return RulesRecord(**defaults)  # type: ignore[arg-type]


def _manifest(**overrides: object) -> RulesDirectoryManifest:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": _VALID_HASH,
        "guardrail_manifest_hash": _VALID_HASH,
        "renderer_version": "1.0",
        "records": [],
        "rule_count": 0,
        "warnings": [],
    }
    defaults.update(overrides)
    return RulesDirectoryManifest(**defaults)  # type: ignore[arg-type]


def test_manifest_with_no_records_is_valid() -> None:
    manifest = _manifest()
    assert manifest.rule_count == 0
    assert manifest.schema_version == "1.0"


def test_manifest_round_trips() -> None:
    manifest = _manifest(records=[_record()], rule_count=1)
    restored = RulesDirectoryManifest.model_validate_json(manifest.to_stable_json())
    assert restored == manifest


def test_record_rejects_invalid_hash() -> None:
    with pytest.raises(ValidationError):
        _record(markdown_hash="not-a-hash")
    with pytest.raises(ValidationError):
        _record(source_guardrail_artifact_hash="not-a-hash")
    with pytest.raises(ValidationError):
        _record(final_rule_draft_hash="not-a-hash")


def test_record_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError):
        _record(relative_filename="/etc/passwd")
    with pytest.raises(ValidationError):
        _record(relative_filename="C:\\evil.md")


def test_record_rejects_parent_segment() -> None:
    with pytest.raises(ValidationError):
        _record(relative_filename="../escape.md")


def test_records_must_be_sorted_by_candidate_id() -> None:
    first = _record(candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-2")
    second = _record(
        candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-1",
        relative_filename="bb" * 32 + ".md",
    )
    with pytest.raises(ValidationError):
        _manifest(records=[first, second], rule_count=2)


def test_records_reject_duplicate_candidate_id() -> None:
    record = _record()
    other = record.model_copy(update={"relative_filename": "bb" * 32 + ".md"})
    with pytest.raises(ValidationError):
        _manifest(records=[record, other], rule_count=2)


def test_records_reject_duplicate_relative_filename() -> None:
    record = _record()
    other = _record(candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-2")
    with pytest.raises(ValidationError):
        _manifest(records=[record, other], rule_count=2)


def test_rule_count_must_match_len_of_records() -> None:
    with pytest.raises(ValidationError):
        _manifest(records=[_record()], rule_count=0)


def test_warnings_must_be_sorted_and_deduplicated() -> None:
    with pytest.raises(ValidationError):
        _manifest(warnings=["z", "a"])
    with pytest.raises(ValidationError):
        _manifest(warnings=["a", "a"])


def test_renderer_version_cannot_be_empty() -> None:
    with pytest.raises(ValidationError):
        _manifest(renderer_version="")


def test_schema_version_is_fixed() -> None:
    with pytest.raises(ValidationError):
        _manifest(schema_version="2.0")
