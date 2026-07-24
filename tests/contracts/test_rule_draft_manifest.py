"""Tests de RuleDraftDirectoryManifest (artifacts/08-rule-drafts/
rule-draft-manifest.json, Prompt 12). Sin JSON Schema propio (igual que
ContextDirectoryManifest)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import RuleDraftDirectoryManifest, RuleDraftRecord

_VALID_HASH = "a" * 64


def _record(**overrides: object) -> RuleDraftRecord:
    defaults: dict[str, object] = {
        "candidate_id": "candidate::det::1.0::" + _VALID_HASH + "::dec-1",
        "context_hash": _VALID_HASH,
        "relative_filename": "aa" * 32 + ".json",
        "rule_draft_hash": _VALID_HASH,
        "writer_user_effective_hash": _VALID_HASH,
        "response_hash": _VALID_HASH,
    }
    defaults.update(overrides)
    return RuleDraftRecord(**defaults)  # type: ignore[arg-type]


def _manifest(**overrides: object) -> RuleDraftDirectoryManifest:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": _VALID_HASH,
        "context_manifest_hash": _VALID_HASH,
        "rule_draft_schema_hash": _VALID_HASH,
        "provider": "openai",
        "model": "gpt-4o-test",
        "writer_system_template_hash": _VALID_HASH,
        "writer_user_template_hash": _VALID_HASH,
        "records": [],
        "draft_count": 0,
        "warnings": [],
    }
    defaults.update(overrides)
    return RuleDraftDirectoryManifest(**defaults)  # type: ignore[arg-type]


def test_manifest_with_no_records_is_valid() -> None:
    manifest = _manifest()
    assert manifest.draft_count == 0
    assert manifest.temperature == 0


def test_manifest_round_trips() -> None:
    manifest = _manifest(records=[_record()], draft_count=1)
    restored = RuleDraftDirectoryManifest.model_validate_json(manifest.to_stable_json())
    assert restored == manifest


def test_records_must_be_sorted_by_candidate_id() -> None:
    first = _record(candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-2")
    second = _record(
        candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-1",
        relative_filename="bb" * 32 + ".json",
    )
    with pytest.raises(ValidationError):
        _manifest(records=[first, second], draft_count=2)


def test_records_reject_duplicate_candidate_id() -> None:
    record = _record()
    other = record.model_copy(update={"relative_filename": "bb" * 32 + ".json"})
    with pytest.raises(ValidationError):
        _manifest(records=[record, other], draft_count=2)


def test_records_reject_duplicate_relative_filename() -> None:
    record = _record()
    other = _record(candidate_id="candidate::det::1.0::" + _VALID_HASH + "::dec-2")
    with pytest.raises(ValidationError):
        _manifest(records=[record, other], draft_count=2)


def test_draft_count_must_match_len_of_records() -> None:
    with pytest.raises(ValidationError):
        _manifest(records=[_record()], draft_count=0)


def test_warnings_must_be_sorted_and_deduplicated() -> None:
    with pytest.raises(ValidationError):
        _manifest(warnings=["z", "a"])
    with pytest.raises(ValidationError):
        _manifest(warnings=["a", "a"])


def test_temperature_only_accepts_zero() -> None:
    with pytest.raises(ValidationError):
        _manifest(temperature=1)
