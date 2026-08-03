"""Tests del contrato del plan de promocion controlada (Fase 10,
`feat/controlled-candidate-promotion-plan`). Invariantes puras del
contrato: `PROPOSE_SHADOW_PROMOTION` nunca valido para BLOCKED/
CONFLICTING, unicidad de `plan_item_id`/`review_item_id`, coherencia de
`decision_id`/`decision`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    PromotionDisposition,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.candidate_promotion_plan import (
    CandidatePromotionPlanArtifact,
    CandidatePromotionPlanItem,
    CandidatePromotionPlanSummary,
    PromotionPlanAction,
    PromotionPlanItemStatus,
)
from altamira_extractor.contracts.candidate_promotion_review import ReviewEligibility

HASH = "8" * 64


def _item(**overrides: object) -> CandidatePromotionPlanItem:
    fields: dict[str, object] = dict(
        plan_item_id="plan::1",
        review_item_id="review::1",
        assessment_id="assessment::1",
        reference_id="unified::v2::1",
        source=CandidateSource.V2,
        source_candidate_id="candidate::1",
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        assessment_disposition=PromotionDisposition.BLOCKED,
        eligibility=ReviewEligibility.BLOCKED,
        action=PromotionPlanAction.BLOCK,
        status=PromotionPlanItemStatus.NO_DECISION_REQUIRED,
    )
    fields.update(overrides)
    return CandidatePromotionPlanItem(**fields)  # type: ignore[arg-type]


def test_blocked_disposition_can_never_propose_shadow_promotion() -> None:
    with pytest.raises(ValidationError):
        _item(
            assessment_disposition=PromotionDisposition.BLOCKED,
            action=PromotionPlanAction.PROPOSE_SHADOW_PROMOTION,
            status=PromotionPlanItemStatus.VALID,
        )


def test_conflicting_disposition_can_never_propose_shadow_promotion() -> None:
    with pytest.raises(ValidationError):
        _item(
            assessment_disposition=PromotionDisposition.CONFLICTING,
            action=PromotionPlanAction.PROPOSE_SHADOW_PROMOTION,
            status=PromotionPlanItemStatus.VALID,
        )


def test_decision_id_and_decision_must_both_be_present_or_absent() -> None:
    with pytest.raises(ValidationError):
        _item(decision_id="decision::1", decision=None)


def _summary_for(items: list[CandidatePromotionPlanItem]) -> CandidatePromotionPlanSummary:
    return CandidatePromotionPlanSummary(
        total_items=len(items),
        keep_baseline_count=0,
        skip_already_covered_count=0,
        propose_shadow_promotion_count=0,
        reject_count=0,
        defer_count=0,
        block_count=len(items),
        pending_review_count=0,
        invalid_decision_count=0,
        counts_by_action={PromotionPlanAction.BLOCK: len(items)},
        counts_by_source={CandidateSource.V2: len(items)},
        counts_by_family={UnifiedRuleFamily.RETURN_CODE: len(items)},
    )


def test_plan_artifact_rejects_duplicate_plan_item_id() -> None:
    item = _item()
    with pytest.raises(ValidationError):
        CandidatePromotionPlanArtifact(
            run_id="run1",
            source_package_hash=HASH,
            assessment_artifact_hash=HASH,
            review_package_hash=HASH,
            decision_manifest_hash=HASH,
            assessment_policy_version="1.0",
            summary=_summary_for([item, item]),
            plan_items=[item, item],
        )


def test_plan_artifact_rejects_duplicate_review_item_id() -> None:
    item_a = _item(plan_item_id="plan::a")
    item_b = _item(plan_item_id="plan::b")
    with pytest.raises(ValidationError):
        CandidatePromotionPlanArtifact(
            run_id="run1",
            source_package_hash=HASH,
            assessment_artifact_hash=HASH,
            review_package_hash=HASH,
            decision_manifest_hash=HASH,
            assessment_policy_version="1.0",
            summary=_summary_for([item_a, item_b]),
            plan_items=sorted([item_a, item_b], key=lambda i: i.plan_item_id),
        )


def test_plan_artifact_valid_minimal() -> None:
    item = _item()
    artifact = CandidatePromotionPlanArtifact(
        run_id="run1",
        source_package_hash=HASH,
        assessment_artifact_hash=HASH,
        review_package_hash=HASH,
        decision_manifest_hash=HASH,
        assessment_policy_version="1.0",
        summary=_summary_for([item]),
        plan_items=[item],
    )
    assert artifact.summary.total_items == 1
