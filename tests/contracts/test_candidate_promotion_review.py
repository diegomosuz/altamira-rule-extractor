"""Tests del contrato del paquete de revision humana y del manifiesto
de decisiones (Fase 10, `feat/controlled-candidate-promotion-plan`).
Invariantes puras del contrato: unicidad, reason_code compatible con
decision, reviewer_reference no vacio, decision_reference no
auto-referente."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    PromotionDisposition,
    RecommendedAction,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.candidate_promotion_review import (
    CandidatePromotionDecision,
    CandidatePromotionDecisionManifest,
    CandidatePromotionReviewPackage,
    CandidatePromotionReviewPackageSummary,
    CandidateReviewItem,
    DecisionReasonCode,
    ReviewDecision,
    ReviewEligibility,
)

HASH = "9" * 64


def _item(review_item_id: str = "review::1") -> CandidateReviewItem:
    return CandidateReviewItem(
        review_item_id=review_item_id,
        assessment_id="assessment::1",
        reference_id=f"unified::v1::{review_item_id}",
        source=CandidateSource.V1,
        source_candidate_id="candidate::1",
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        disposition=PromotionDisposition.BASELINE_V1,
        eligibility=ReviewEligibility.BASELINE,
        program="CALLER",
        recommended_action=RecommendedAction.NONE_BASELINE_V1,
    )


def _summary_for(items: list[CandidateReviewItem]) -> CandidatePromotionReviewPackageSummary:
    return CandidatePromotionReviewPackageSummary(
        total_items=len(items),
        eligible_count=0,
        not_eligible_count=0,
        already_covered_count=0,
        baseline_count=len(items),
        blocked_count=0,
        counts_by_source={CandidateSource.V1: len(items)},
        counts_by_family={UnifiedRuleFamily.RETURN_CODE: len(items)},
        counts_by_disposition={PromotionDisposition.BASELINE_V1: len(items)},
        counts_by_eligibility={ReviewEligibility.BASELINE: len(items)},
    )


def test_review_package_rejects_duplicate_review_item_id() -> None:
    item = _item()
    with pytest.raises(ValidationError):
        CandidatePromotionReviewPackage(
            run_id="run1",
            source_package_hash=HASH,
            assessment_artifact_hash=HASH,
            assessment_policy_version="1.0",
            summary=_summary_for([item, item]),
            review_items=[item, item],
        )


def test_review_package_rejects_duplicate_reference_id() -> None:
    item_a = _item("review::a")
    item_b = _item("review::b").model_copy(update={"reference_id": item_a.reference_id})
    with pytest.raises(ValidationError):
        CandidatePromotionReviewPackage(
            run_id="run1",
            source_package_hash=HASH,
            assessment_artifact_hash=HASH,
            assessment_policy_version="1.0",
            summary=_summary_for([item_a, item_b]),
            review_items=sorted([item_a, item_b], key=lambda i: i.review_item_id),
        )


def test_review_package_valid_minimal() -> None:
    item = _item()
    package = CandidatePromotionReviewPackage(
        run_id="run1",
        source_package_hash=HASH,
        assessment_artifact_hash=HASH,
        assessment_policy_version="1.0",
        summary=_summary_for([item]),
        review_items=[item],
    )
    assert package.summary.total_items == 1


def _decision(**overrides: object) -> CandidatePromotionDecision:
    fields: dict[str, object] = dict(
        decision_id="decision::1",
        review_item_id="review::1",
        assessment_id="assessment::1",
        reference_id="unified::v2::1",
        assessment_artifact_hash=HASH,
        decision=ReviewDecision.DEFER,
        reason_code=DecisionReasonCode.DEFERRED_FOR_DOMAIN_REVIEW,
        reviewer_reference="analyst@example.com",
    )
    fields.update(overrides)
    return CandidatePromotionDecision(**fields)  # type: ignore[arg-type]


def test_decision_reason_code_incompatible_with_approve_rejected() -> None:
    with pytest.raises(ValidationError):
        _decision(
            decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
            reason_code=DecisionReasonCode.OUT_OF_SCOPE,
        )


def test_decision_reason_code_compatible_with_approve_accepted() -> None:
    d = _decision(
        decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    assert d.decision == ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION


def test_decision_reviewer_reference_blank_rejected() -> None:
    with pytest.raises(ValidationError):
        _decision(reviewer_reference="")


def test_decision_reviewer_reference_whitespace_only_rejected() -> None:
    with pytest.raises(ValidationError):
        _decision(reviewer_reference="   ")


def test_decision_reference_cannot_self_reference() -> None:
    with pytest.raises(ValidationError):
        _decision(decision_id="decision::x", decision_reference="decision::x")


def test_manifest_rejects_duplicate_decision_id() -> None:
    d1 = _decision(decision_id="decision::dup", review_item_id="review::1")
    d2 = _decision(decision_id="decision::dup", review_item_id="review::2")
    with pytest.raises(ValidationError):
        CandidatePromotionDecisionManifest(
            review_package_hash=HASH,
            assessment_artifact_hash=HASH,
            run_id="run1",
            decisions=[d1, d2],
        )


def test_manifest_valid_minimal() -> None:
    manifest = CandidatePromotionDecisionManifest(
        review_package_hash=HASH,
        assessment_artifact_hash=HASH,
        run_id="run1",
        decisions=[_decision()],
    )
    assert len(manifest.decisions) == 1
