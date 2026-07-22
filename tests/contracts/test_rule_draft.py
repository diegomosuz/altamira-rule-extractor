"""Tests de RuleDraft, incluida compatibilidad con
schemas/rule-draft.schema.json."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import Claim, ClaimField, EvidenceValidationStatus, RuleDraft

from .conftest import assert_matches_schema


def test_valid_rule_draft_matches_schema(
    valid_rule_draft: RuleDraft, rule_draft_schema: dict[str, Any]
) -> None:
    assert_matches_schema(valid_rule_draft.model_dump(mode="json"), rule_draft_schema)


def test_rule_draft_round_trips(valid_rule_draft: RuleDraft) -> None:
    restored = RuleDraft.model_validate_json(valid_rule_draft.to_stable_json())
    assert restored == valid_rule_draft


def test_rule_draft_defaults_to_needs_functional_review(valid_rule_draft: RuleDraft) -> None:
    assert valid_rule_draft.functional_review_status.value == "NEEDS_FUNCTIONAL_REVIEW"


def test_rule_draft_rejects_functionally_approved_status(valid_rule_draft: RuleDraft) -> None:
    payload = valid_rule_draft.model_dump(mode="json")
    payload["functional_review_status"] = "FUNCTIONALLY_APPROVED"
    with pytest.raises(ValidationError):
        RuleDraft.model_validate(payload)


def test_rule_draft_requires_at_least_one_limitation(valid_rule_draft: RuleDraft) -> None:
    payload = valid_rule_draft.model_dump(mode="json")
    payload["limitations"] = []
    with pytest.raises(ValidationError):
        RuleDraft.model_validate(payload)


def test_rule_draft_requires_at_least_one_claim(valid_rule_draft: RuleDraft) -> None:
    payload = valid_rule_draft.model_dump(mode="json")
    payload["claims"] = []
    with pytest.raises(ValidationError):
        RuleDraft.model_validate(payload)


def test_rule_draft_rejects_additional_properties(valid_rule_draft: RuleDraft) -> None:
    payload = valid_rule_draft.model_dump(mode="json")
    payload["confidence"] = 0.99
    with pytest.raises(ValidationError):
        RuleDraft.model_validate(payload)


def test_rule_draft_rejects_wrong_schema_version(valid_rule_draft: RuleDraft) -> None:
    payload = valid_rule_draft.model_dump(mode="json")
    payload["schema_version"] = "3.0"
    with pytest.raises(ValidationError):
        RuleDraft.model_validate(payload)


def test_rule_draft_rejects_invalid_evidence_validation_status(valid_rule_draft: RuleDraft) -> None:
    payload = valid_rule_draft.model_dump(mode="json")
    payload["evidence_validation_status"] = "APPROVED"
    with pytest.raises(ValidationError):
        RuleDraft.model_validate(payload)


def test_claim_evidence_path_must_be_jsonpath() -> None:
    with pytest.raises(ValidationError):
        Claim(
            claim_id="c1",
            field=ClaimField.STATEMENT,
            evidence_paths=["decision.expression"],
            evidence_ids=["ev-1"],
        )


def test_claim_rejects_invalid_field_enum() -> None:
    with pytest.raises(ValidationError):
        Claim.model_validate(
            {
                "claim_id": "c1",
                "field": "not_a_real_field",
                "evidence_paths": ["$.decision.expression"],
                "evidence_ids": ["ev-1"],
            }
        )


def test_evidence_validated_and_pending_are_both_representable() -> None:
    for status in (EvidenceValidationStatus.PENDING, EvidenceValidationStatus.EVIDENCE_VALIDATED):
        assert status.value in {"PENDING", "EVIDENCE_VALIDATED"}
