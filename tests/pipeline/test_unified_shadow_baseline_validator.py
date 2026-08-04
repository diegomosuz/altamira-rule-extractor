"""Tests del validador PURO de completitud del baseline V1 (Fase 12
Parte 5, `feat/unified-shadow-differential-validation`)."""

from __future__ import annotations

from altamira_extractor.contracts.candidate import CandidateStatus, RuleCandidate
from altamira_extractor.contracts.unified_shadow_validation import UnifiedShadowValidationIssueCode
from altamira_extractor.pipeline.unified_shadow_baseline_validator import (
    validate_baseline_completeness,
)

from ._unified_shadow_validation_fixtures import HASH, golden_path

Code = UnifiedShadowValidationIssueCode


def test_empty_v1_reconciles_with_empty_baseline_candidates() -> None:
    gp = golden_path()
    result = validate_baseline_completeness(
        v1_candidates=gp.v1, unified_shadow=gp.unified_shadow, candidate_v1_artifact_hash=HASH
    )
    assert result.gate_passed is True
    assert result.findings == ()


def test_missing_baseline_candidate_detected() -> None:
    gp = golden_path()
    v1_with_candidate = gp.v1.model_copy(
        update={
            "candidates": [
                RuleCandidate(
                    candidate_id="candidate::v1::1",
                    paragraph_id="program::CO::CALLER10::CALLER10::1::abc123456789::paragraph::MAIN",
                    paragraph_name="MAIN",
                    decision_id="decision::1",
                    detector_id="q0-return-code-decision",
                    detector_version="1.0",
                    detector_score=1.0,
                    condition="RETURN-CODE = 0",
                    line_start=10,
                    source_file="src/CALLER10.cbl",
                    source_package_hash=HASH,
                    status=CandidateStatus.DETECTED_CANDIDATE,
                )
            ]
        }
    )
    result = validate_baseline_completeness(
        v1_candidates=v1_with_candidate,
        unified_shadow=gp.unified_shadow,
        candidate_v1_artifact_hash=HASH,
    )
    assert result.gate_passed is False
    assert any(f.code == Code.BASELINE_CANDIDATE_MISSING for f in result.findings)


def test_extra_baseline_candidate_not_present_in_current_v1_detected() -> None:
    gp = golden_path()
    from altamira_extractor.contracts.candidate_promotion_assessment import UnifiedRuleFamily
    from altamira_extractor.contracts.unified_candidates_shadow import (
        UnifiedBaselineCandidateReference,
    )

    stale_reference = UnifiedBaselineCandidateReference(
        baseline_reference_id="baseline::stale::1",
        source_candidate_id="v1::stale::1",
        source_artifact_hash=HASH,
        original_candidate_hash=HASH,
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        original_support="DETERMINISTIC",
        program="CALLER10",
    )
    unified_shadow_with_extra = gp.unified_shadow.model_copy(
        update={"baseline_candidates": [stale_reference]}
    )
    result = validate_baseline_completeness(
        v1_candidates=gp.v1,
        unified_shadow=unified_shadow_with_extra,
        candidate_v1_artifact_hash=HASH,
    )
    assert result.gate_passed is False
    assert any(f.code == Code.BASELINE_COUNT_MISMATCH for f in result.findings)


def test_checked_baseline_reference_ids_covers_expected_and_actual() -> None:
    gp = golden_path()
    result = validate_baseline_completeness(
        v1_candidates=gp.v1, unified_shadow=gp.unified_shadow, candidate_v1_artifact_hash=HASH
    )
    assert result.checked_baseline_reference_ids == ()


def test_never_mutates_inputs() -> None:
    gp = golden_path()
    v1_before = gp.v1.model_copy(deep=True)
    unified_shadow_before = gp.unified_shadow.model_copy(deep=True)
    validate_baseline_completeness(
        v1_candidates=gp.v1, unified_shadow=gp.unified_shadow, candidate_v1_artifact_hash=HASH
    )
    assert gp.v1 == v1_before
    assert gp.unified_shadow == unified_shadow_before
