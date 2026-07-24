"""Tests de GuardrailDirectoryManifest (artifacts/09-guardrails/
guardrail-manifest.json, Prompt 12). Sin JSON Schema propio."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import (
    EvidenceValidationStatus,
    GuardrailDirectoryManifest,
    GuardrailRecord,
)

_VALID_HASH = "a" * 64


def _record(**overrides: object) -> GuardrailRecord:
    defaults: dict[str, object] = {
        "candidate_id": "candidate::det::1.0::" + _VALID_HASH + "::dec-1",
        "relative_filename": "aa" * 32 + ".json",
        "initial_rule_draft_hash": _VALID_HASH,
        "final_rule_draft_hash": _VALID_HASH,
        "guardrail_artifact_hash": _VALID_HASH,
        "final_evidence_validation_status": EvidenceValidationStatus.EVIDENCE_VALIDATED,
        "repair_attempts_used": 0,
        "repair_response_hashes": [],
    }
    defaults.update(overrides)
    return GuardrailRecord(**defaults)  # type: ignore[arg-type]


def _manifest(**overrides: object) -> GuardrailDirectoryManifest:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": _VALID_HASH,
        "rule_draft_manifest_hash": _VALID_HASH,
        "rule_draft_schema_hash": _VALID_HASH,
        "provider": "openai",
        "model": "gpt-4o-test",
        "llm_repair_attempts": 2,
        "guardrail_version": "1.0",
        "repair_system_template_hash": _VALID_HASH,
        "repair_user_template_hash": _VALID_HASH,
        "records": [],
        "guardrail_count": 0,
        "warnings": [],
    }
    defaults.update(overrides)
    return GuardrailDirectoryManifest(**defaults)  # type: ignore[arg-type]


def test_manifest_with_no_records_is_valid() -> None:
    manifest = _manifest()
    assert manifest.guardrail_count == 0
    assert manifest.temperature == 0


def test_manifest_round_trips() -> None:
    manifest = _manifest(records=[_record()], guardrail_count=1)
    restored = GuardrailDirectoryManifest.model_validate_json(manifest.to_stable_json())
    assert restored == manifest


def test_record_rejects_non_evidence_validated_status() -> None:
    with pytest.raises(ValidationError):
        _record(final_evidence_validation_status=EvidenceValidationStatus.REJECTED)
    with pytest.raises(ValidationError):
        _record(final_evidence_validation_status=EvidenceValidationStatus.PENDING)


def test_record_requires_repair_attempts_consistent_with_hashes() -> None:
    with pytest.raises(ValidationError):
        _record(repair_attempts_used=1, repair_response_hashes=[])


def test_record_with_repairs_is_valid() -> None:
    record = _record(repair_attempts_used=2, repair_response_hashes=[_VALID_HASH, _VALID_HASH])
    assert record.repair_attempts_used == 2


def test_records_must_be_sorted_by_candidate_id() -> None:
    first = _record(candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-2")
    second = _record(
        candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-1",
        relative_filename="bb" * 32 + ".json",
    )
    with pytest.raises(ValidationError):
        _manifest(records=[first, second], guardrail_count=2)


def test_records_reject_duplicate_candidate_id() -> None:
    record = _record()
    other = record.model_copy(update={"relative_filename": "bb" * 32 + ".json"})
    with pytest.raises(ValidationError):
        _manifest(records=[record, other], guardrail_count=2)


def test_records_reject_duplicate_relative_filename() -> None:
    record = _record()
    other = _record(candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-2")
    with pytest.raises(ValidationError):
        _manifest(records=[record, other], guardrail_count=2)


def test_guardrail_count_must_match_len_of_records() -> None:
    with pytest.raises(ValidationError):
        _manifest(records=[_record()], guardrail_count=0)


def test_warnings_must_be_sorted_and_deduplicated() -> None:
    with pytest.raises(ValidationError):
        _manifest(warnings=["z", "a"])


def test_llm_repair_attempts_out_of_range_is_invalid() -> None:
    with pytest.raises(ValidationError):
        _manifest(llm_repair_attempts=3)
    with pytest.raises(ValidationError):
        _manifest(llm_repair_attempts=-1)


def test_temperature_only_accepts_zero() -> None:
    with pytest.raises(ValidationError):
        _manifest(temperature=1)
