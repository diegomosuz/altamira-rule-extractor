"""Tests del constructor puro del plan de promocion controlada (Fase
10, `feat/controlled-candidate-promotion-plan`). Items 11-41 de los 55
tests obligatorios: decisiones validas/invalidas por disposition,
identidad de hashes, unicidad, acciones/estados del plan,
determinismo y no-mutacion."""

from __future__ import annotations

import copy
import hashlib

import pytest

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    PromotionDisposition,
)
from altamira_extractor.contracts.candidate_promotion_plan import (
    PromotionPlanAction,
    PromotionPlanItemStatus,
)
from altamira_extractor.contracts.candidate_promotion_review import (
    DecisionReasonCode,
    ReviewDecision,
)
from altamira_extractor.pipeline.candidate_promotion_plan_builder import (
    build_candidate_promotion_plan,
)
from altamira_extractor.pipeline.candidate_promotion_review_generator import (
    generate_candidate_promotion_review_package,
)
from altamira_extractor.pipeline.errors import CandidatePromotionPlanError

from .candidate_promotion_review_helpers import decision, manifest, single_disposition_artifact


def _hash(obj: object) -> str:
    return hashlib.sha256(obj.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def _scenario(disposition: PromotionDisposition):
    artifact = single_disposition_artifact(disposition)
    package = generate_candidate_promotion_review_package(artifact)
    item = next(i for i in package.review_items if i.disposition == disposition)
    return artifact, package, item


def _manifest_with(package, artifact, decisions):
    return manifest(
        review_package_hash=_hash(package),
        assessment_artifact_hash=package.assessment_artifact_hash,
        run_id=artifact.run_id,
        decisions=decisions,
    )


def test_approve_valid_on_ready() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    plan_item = next(p for p in plan.plan_items if p.review_item_id == item.review_item_id)
    assert plan_item.action == PromotionPlanAction.PROPOSE_SHADOW_PROMOTION
    assert plan_item.status == PromotionPlanItemStatus.VALID


def test_reject_valid_on_ready() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.REJECT,
        reason_code=DecisionReasonCode.DUPLICATE_RULE,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    plan_item = plan.plan_items[0]
    assert plan_item.action == PromotionPlanAction.REJECT
    assert plan_item.status == PromotionPlanItemStatus.VALID


def test_defer_valid_on_ready() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.DEFER,
        reason_code=DecisionReasonCode.DEFERRED_FOR_DOMAIN_REVIEW,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    plan_item = plan.plan_items[0]
    assert plan_item.action == PromotionPlanAction.DEFER
    assert plan_item.status == PromotionPlanItemStatus.VALID


def test_approve_on_review_required_is_invalid() -> None:
    artifact, package, item = _scenario(PromotionDisposition.REVIEW_REQUIRED)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    plan_item = plan.plan_items[0]
    assert plan_item.status == PromotionPlanItemStatus.INVALID_DECISION
    assert plan_item.action == PromotionPlanAction.PENDING_REVIEW
    assert plan_item.action != PromotionPlanAction.PROPOSE_SHADOW_PROMOTION


def test_approve_on_blocked_is_invalid() -> None:
    artifact, package, item = _scenario(PromotionDisposition.BLOCKED)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    plan_item = plan.plan_items[0]
    assert plan_item.status == PromotionPlanItemStatus.INVALID_DECISION
    assert plan_item.action == PromotionPlanAction.BLOCK


def test_approve_on_conflicting_is_invalid() -> None:
    artifact, package, item = _scenario(PromotionDisposition.CONFLICTING)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    plan_item = plan.plan_items[0]
    assert plan_item.status == PromotionPlanItemStatus.INVALID_DECISION
    assert plan_item.action == PromotionPlanAction.BLOCK


def test_reject_on_not_evaluated_is_invalid() -> None:
    artifact, package, item = _scenario(PromotionDisposition.NOT_EVALUATED)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.REJECT,
        reason_code=DecisionReasonCode.INSUFFICIENT_EVIDENCE,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    plan_item = plan.plan_items[0]
    assert plan_item.status == PromotionPlanItemStatus.INVALID_DECISION
    assert plan_item.action == PromotionPlanAction.DEFER


def test_decision_for_baseline_is_invalid() -> None:
    artifact, package, item = _scenario(PromotionDisposition.BASELINE_V1)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.DEFER,
        reason_code=DecisionReasonCode.OUT_OF_SCOPE,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    plan_item = plan.plan_items[0]
    assert plan_item.action == PromotionPlanAction.KEEP_BASELINE
    assert plan_item.status == PromotionPlanItemStatus.NO_DECISION_REQUIRED
    assert plan_item.decision is None
    assert any("BASELINE" in diagnostic for diagnostic in plan.diagnostics)


def test_approve_on_already_covered_is_invalid() -> None:
    artifact, package, item = _scenario(PromotionDisposition.ALREADY_COVERED)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    plan_item = next(p for p in plan.plan_items if p.review_item_id == item.review_item_id)
    assert plan_item.status == PromotionPlanItemStatus.INVALID_DECISION
    assert plan_item.action == PromotionPlanAction.SKIP_ALREADY_COVERED


def test_reason_code_incompatible_with_approve_rejected_at_construction() -> None:
    from pydantic import ValidationError

    from altamira_extractor.contracts.candidate_promotion_review import (
        CandidatePromotionDecision,
    )

    with pytest.raises(ValidationError):
        CandidatePromotionDecision(
            decision_id="decision::1",
            review_item_id="review::1",
            assessment_id="assessment::1",
            reference_id="unified::v2::a",
            assessment_artifact_hash="a" * 64,
            decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
            reason_code=DecisionReasonCode.INSUFFICIENT_EVIDENCE,
            reviewer_reference="analyst@example.com",
        )


def test_reviewer_reference_blank_rejected_at_construction() -> None:
    from pydantic import ValidationError

    from altamira_extractor.contracts.candidate_promotion_review import (
        CandidatePromotionDecision,
    )

    with pytest.raises(ValidationError):
        CandidatePromotionDecision(
            decision_id="decision::1",
            review_item_id="review::1",
            assessment_id="assessment::1",
            reference_id="unified::v2::a",
            assessment_artifact_hash="a" * 64,
            decision=ReviewDecision.DEFER,
            reason_code=DecisionReasonCode.DEFERRED_FOR_DOMAIN_REVIEW,
            reviewer_reference="   ",
        )


def test_decision_id_duplicate_rejected_at_manifest_construction() -> None:
    from pydantic import ValidationError

    from altamira_extractor.contracts.candidate_promotion_review import (
        CandidatePromotionDecisionManifest,
    )

    d1 = decision(
        decision_id="decision::dup",
        review_item_id="review::1",
        assessment_id="assessment::1",
        reference_id="unified::v2::a",
        assessment_artifact_hash="a" * 64,
        verb=ReviewDecision.DEFER,
        reason_code=DecisionReasonCode.DEFERRED_FOR_DOMAIN_REVIEW,
    )
    d2 = decision(
        decision_id="decision::dup",
        review_item_id="review::2",
        assessment_id="assessment::2",
        reference_id="unified::v2::b",
        assessment_artifact_hash="a" * 64,
        verb=ReviewDecision.REJECT,
        reason_code=DecisionReasonCode.OUT_OF_SCOPE,
    )
    with pytest.raises(ValidationError):
        CandidatePromotionDecisionManifest(
            review_package_hash="b" * 64,
            assessment_artifact_hash="a" * 64,
            run_id="run1",
            decisions=[d1, d2],
        )


def test_review_item_duplicate_ambiguous_decisions_rejected() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    d1 = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    d2 = decision(
        decision_id="decision::2",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.REJECT,
        reason_code=DecisionReasonCode.DUPLICATE_RULE,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact,
        review_package=package,
        manifest=_manifest_with(package, artifact, [d1, d2]),
    )
    plan_item = plan.plan_items[0]
    assert plan_item.status == PromotionPlanItemStatus.PENDING_DECISION
    assert plan_item.decision is None
    assert any("AMBIGUOUS_MULTIPLE_DECISIONS" in diagnostic for diagnostic in plan.diagnostics)


def test_assessment_hash_incorrect_on_decision_is_invalid() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash="f" * 64,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    plan_item = plan.plan_items[0]
    assert plan_item.status == PromotionPlanItemStatus.INVALID_DECISION
    assert "DECISION_ASSESSMENT_ARTIFACT_HASH_MISMATCH" in plan_item.blocking_reasons


def test_review_package_hash_incorrect_raises() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    bad_manifest = manifest(
        review_package_hash="0" * 64,
        assessment_artifact_hash=package.assessment_artifact_hash,
        run_id=artifact.run_id,
    )
    with pytest.raises(CandidatePromotionPlanError):
        build_candidate_promotion_plan(
            assessment=artifact, review_package=package, manifest=bad_manifest
        )


def test_run_id_incorrect_raises() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    bad_manifest = manifest(
        review_package_hash=_hash(package),
        assessment_artifact_hash=package.assessment_artifact_hash,
        run_id="some-other-run",
    )
    with pytest.raises(CandidatePromotionPlanError):
        build_candidate_promotion_plan(
            assessment=artifact, review_package=package, manifest=bad_manifest
        )


def test_reference_id_incorrect_on_decision_is_invalid() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id="unified::v2::wrong",
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    plan_item = plan.plan_items[0]
    assert plan_item.status == PromotionPlanItemStatus.INVALID_DECISION
    assert "DECISION_REFERENCE_ID_MISMATCH" in plan_item.blocking_reasons


def test_assessment_id_incorrect_on_decision_is_invalid() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id="assessment::wrong",
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    plan_item = plan.plan_items[0]
    assert plan_item.status == PromotionPlanItemStatus.INVALID_DECISION
    assert "DECISION_ASSESSMENT_ID_MISMATCH" in plan_item.blocking_reasons


def test_manifest_stale_relative_to_current_assessment_raises() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    valid_manifest = _manifest_with(package, artifact, [])
    changed_artifact = artifact.model_copy(update={"diagnostics": ["SOMETHING_CHANGED"]})
    with pytest.raises(CandidatePromotionPlanError):
        build_candidate_promotion_plan(
            assessment=changed_artifact, review_package=package, manifest=valid_manifest
        )


def test_keep_baseline_action() -> None:
    artifact, package, item = _scenario(PromotionDisposition.BASELINE_V1)
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [])
    )
    plan_item = plan.plan_items[0]
    assert plan_item.action == PromotionPlanAction.KEEP_BASELINE
    assert plan_item.status == PromotionPlanItemStatus.NO_DECISION_REQUIRED


def test_skip_already_covered_action() -> None:
    artifact, package, item = _scenario(PromotionDisposition.ALREADY_COVERED)
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [])
    )
    plan_item = next(p for p in plan.plan_items if p.review_item_id == item.review_item_id)
    assert plan_item.action == PromotionPlanAction.SKIP_ALREADY_COVERED
    assert plan_item.status == PromotionPlanItemStatus.NO_DECISION_REQUIRED


def test_propose_shadow_promotion_action() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.BUSINESS_RULE_CONFIRMED,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    assert plan.plan_items[0].action == PromotionPlanAction.PROPOSE_SHADOW_PROMOTION


def test_reject_action() -> None:
    artifact, package, item = _scenario(PromotionDisposition.REVIEW_REQUIRED)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.REJECT,
        reason_code=DecisionReasonCode.INCORRECT_TARGET,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    assert plan.plan_items[0].action == PromotionPlanAction.REJECT


def test_defer_action() -> None:
    artifact, package, item = _scenario(PromotionDisposition.NOT_EVALUATED)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.DEFER,
        reason_code=DecisionReasonCode.SOURCE_NOT_AVAILABLE,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    assert plan.plan_items[0].action == PromotionPlanAction.DEFER


def test_block_action() -> None:
    artifact, package, item = _scenario(PromotionDisposition.BLOCKED)
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [])
    )
    assert plan.plan_items[0].action == PromotionPlanAction.BLOCK


def test_pending_review_action() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [])
    )
    assert plan.plan_items[0].action == PromotionPlanAction.PENDING_REVIEW
    assert plan.plan_items[0].status == PromotionPlanItemStatus.PENDING_DECISION


def test_invalid_decision_status() -> None:
    artifact, package, item = _scenario(PromotionDisposition.BLOCKED)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [d])
    )
    assert plan.plan_items[0].status == PromotionPlanItemStatus.INVALID_DECISION


def test_plan_summary_is_reconciled() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [])
    )
    assert plan.summary.total_items == len(plan.plan_items)
    assert plan.summary.pending_review_count == 1


def test_plan_serialization_is_byte_for_byte_deterministic() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    m = _manifest_with(package, artifact, [d])
    plan_1 = build_candidate_promotion_plan(assessment=artifact, review_package=package, manifest=m)
    plan_2 = build_candidate_promotion_plan(assessment=artifact, review_package=package, manifest=m)
    assert plan_1.model_dump_json() == plan_2.model_dump_json()


def test_plan_is_order_independent_of_decisions_input_order() -> None:
    artifact = single_disposition_artifact(PromotionDisposition.BASELINE_V1)
    package = generate_candidate_promotion_review_package(artifact)
    # Sin decisiones, pero verifica que el orden de construccion del
    # manifest (vacio o no) nunca afecta el resultado.
    m1 = _manifest_with(package, artifact, [])
    plan_1 = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=m1
    )
    plan_2 = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=m1
    )
    assert plan_1.model_dump_json() == plan_2.model_dump_json()


def test_builder_never_mutates_inputs() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    d = decision(
        decision_id="decision::1",
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        assessment_artifact_hash=package.assessment_artifact_hash,
        verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
    )
    m = _manifest_with(package, artifact, [d])
    artifact_before = copy.deepcopy(artifact.model_dump())
    package_before = copy.deepcopy(package.model_dump())
    manifest_before = copy.deepcopy(m.model_dump())

    build_candidate_promotion_plan(assessment=artifact, review_package=package, manifest=m)

    assert artifact.model_dump() == artifact_before
    assert package.model_dump() == package_before
    assert m.model_dump() == manifest_before


def test_plan_never_promotes_blocked_or_conflicting_disposition() -> None:
    for disposition in (PromotionDisposition.BLOCKED, PromotionDisposition.CONFLICTING):
        artifact, package, item = _scenario(disposition)
        d = decision(
            decision_id="decision::1",
            review_item_id=item.review_item_id,
            assessment_id=item.assessment_id,
            reference_id=item.reference_id,
            assessment_artifact_hash=package.assessment_artifact_hash,
            verb=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
            reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
        )
        plan = build_candidate_promotion_plan(
            assessment=artifact,
            review_package=package,
            manifest=_manifest_with(package, artifact, [d]),
        )
        assert plan.plan_items[0].action != PromotionPlanAction.PROPOSE_SHADOW_PROMOTION


def test_plan_never_creates_a_new_candidate_reference() -> None:
    artifact, package, item = _scenario(PromotionDisposition.READY_FOR_CONTROLLED_REVIEW)
    plan = build_candidate_promotion_plan(
        assessment=artifact, review_package=package, manifest=_manifest_with(package, artifact, [])
    )
    plan_reference_ids = {p.reference_id for p in plan.plan_items}
    source_reference_ids = {r.unified_reference_id for r in artifact.candidate_references}
    assert plan_reference_ids <= source_reference_ids


def test_source_candidate_source_is_never_altered() -> None:
    """`CandidateSource.V1/V2/INTERPROCEDURAL` de cada plan_item debe
    coincidir exactamente con el reference de origen -- el plan nunca
    reclasifica una referencia a otra fuente."""
    for disposition, expected_source in (
        (PromotionDisposition.BASELINE_V1, CandidateSource.V1),
        (PromotionDisposition.READY_FOR_CONTROLLED_REVIEW, CandidateSource.V2),
    ):
        artifact, package, item = _scenario(disposition)
        plan = build_candidate_promotion_plan(
            assessment=artifact,
            review_package=package,
            manifest=_manifest_with(package, artifact, []),
        )
        assert plan.plan_items[0].source == expected_source


def test_promotion_plan_action_enum_never_includes_a_real_promoted_value() -> None:
    """Item 51: `PromotionPlanAction` nunca incluye `PROMOTED`/
    `AUTO_PROMOTED` -- `PROPOSE_SHADOW_PROMOTION` es la unica accion
    relacionada con promocion, y es explicitamente una propuesta de
    dry-run, nunca una promocion real."""
    values = {member.value for member in PromotionPlanAction}
    assert "PROMOTED" not in values
    assert "AUTO_PROMOTED" not in values
    assert "PROPOSE_SHADOW_PROMOTION" in values
