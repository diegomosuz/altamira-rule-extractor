"""Tests del analizador puro principal (Fase 11 Parte 9+10,
`pipeline/unified_candidates_shadow_analyzer.py`)."""

from __future__ import annotations

import pytest

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessmentArtifact,
    CandidateSource,
    PromotionDisposition,
    UnifiedCandidateReference,
)
from altamira_extractor.contracts.candidate_promotion_plan import CandidatePromotionPlanArtifact
from altamira_extractor.contracts.candidate_promotion_review import (
    CandidatePromotionDecision,
    CandidatePromotionDecisionManifest,
    CandidatePromotionReviewPackage,
    DecisionReasonCode,
    ReviewDecision,
)
from altamira_extractor.contracts.unified_candidates_shadow import (
    UnifiedShadowComparisonKind,
    UnifiedShadowExclusionReason,
    UnifiedShadowGroupStatus,
)
from altamira_extractor.pipeline.candidate_promotion_plan_builder import (
    build_candidate_promotion_plan,
)
from altamira_extractor.pipeline.candidate_promotion_review_generator import (
    generate_candidate_promotion_review_package,
    review_item_id_for,
)
from altamira_extractor.pipeline.errors import UnifiedCandidatesShadowError
from altamira_extractor.pipeline.unified_candidates_shadow_analyzer import (
    analyze_unified_candidates_shadow,
)

from .unified_candidates_shadow_helpers import (
    CAND_HASH,
    RUN_ID,
    TwoEquivalentProposalsScenario,
    assessment_of,
    dump_hash,
    stable_hash,
    v1_artifact,
    v1_candidate,
    v1_reference,
    v2_artifact,
    v2_candidate,
    v2_reference,
)


def _plan_for_single_reference(
    *,
    disposition: PromotionDisposition,
    reference: UnifiedCandidateReference,
    decision_verb: ReviewDecision | None = None,
    exact_match_pairs: list[tuple[str, str]] | None = None,
    v1_hash: str | None = None,
    v2_hash: str | None = None,
    interprocedural_hash: str | None = None,
) -> tuple[
    CandidatePromotionAssessmentArtifact,
    CandidatePromotionReviewPackage,
    CandidatePromotionPlanArtifact,
]:
    assessment = assessment_of(
        [reference],
        dispositions={reference.unified_reference_id: disposition},
        exact_match_pairs=exact_match_pairs,
        v1_hash=v1_hash,
        v2_hash=v2_hash,
        interprocedural_hash=interprocedural_hash,
    )
    review_package = generate_candidate_promotion_review_package(assessment)
    decisions = []
    if decision_verb is not None:
        assessment_hash = stable_hash(assessment)
        decisions.append(
            CandidatePromotionDecision(
                decision_id=f"decision::{reference.unified_reference_id}",
                review_item_id=review_item_id_for(reference.unified_reference_id),
                assessment_id=f"assessment::{reference.unified_reference_id}",
                reference_id=reference.unified_reference_id,
                assessment_artifact_hash=assessment_hash,
                decision=decision_verb,
                reason_code=(
                    DecisionReasonCode.EVIDENCE_CONFIRMED
                    if decision_verb == ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION
                    else (
                        DecisionReasonCode.DUPLICATE_RULE
                        if decision_verb == ReviewDecision.REJECT
                        else DecisionReasonCode.OUT_OF_SCOPE
                    )
                ),
                reviewer_reference="analyst@example.com",
            )
        )
    manifest = CandidatePromotionDecisionManifest(
        review_package_hash=stable_hash(review_package),
        assessment_artifact_hash=stable_hash(assessment),
        run_id=RUN_ID,
        decisions=decisions,
    )
    plan = build_candidate_promotion_plan(
        assessment=assessment, review_package=review_package, manifest=manifest
    )
    return assessment, review_package, plan


def test_two_equivalent_proposals_produce_single_valid_group() -> None:
    scenario = TwoEquivalentProposalsScenario()
    artifact = analyze_unified_candidates_shadow(**scenario.analyzer_kwargs())  # type: ignore[arg-type]
    assert len(artifact.shadow_members) == 2
    assert len(artifact.shadow_groups) == 1
    group = artifact.shadow_groups[0]
    assert group.status == UnifiedShadowGroupStatus.VALID
    assert group.comparison_to_v1 == UnifiedShadowComparisonKind.NOT_IN_BASELINE
    assert {m.source for m in artifact.shadow_members} == {
        CandidateSource.V2,
        CandidateSource.INTERPROCEDURAL,
    }
    assert len(artifact.excluded_plan_items) == 0


def test_determinism_same_inputs_same_bytes() -> None:
    scenario = TwoEquivalentProposalsScenario()
    a1 = analyze_unified_candidates_shadow(**scenario.analyzer_kwargs())  # type: ignore[arg-type]
    a2 = analyze_unified_candidates_shadow(**scenario.analyzer_kwargs())  # type: ignore[arg-type]
    assert a1.to_stable_json() == a2.to_stable_json()


def test_all_v1_candidates_appear_in_baseline_regardless_of_plan() -> None:
    v1 = v1_artifact(
        candidates=[
            v1_candidate(candidate_id="candidate::1"),
            v1_candidate(candidate_id="candidate::2"),
        ],
        run_id=RUN_ID,
    )
    reference = v1_reference(reference_id="unified::v1::a", source_candidate_id="candidate::1")
    v1_hash = stable_hash(v1)
    assessment, review_package, plan = _plan_for_single_reference(
        disposition=PromotionDisposition.BASELINE_V1, reference=reference, v1_hash=v1_hash
    )
    artifact = analyze_unified_candidates_shadow(
        run_id=RUN_ID,
        v1_candidates=v1,
        v2_candidates=None,
        interprocedural_candidates=None,
        assessment=assessment,
        review_package=review_package,
        plan=plan,
        source_package_hash=CAND_HASH,
        candidate_v1_artifact_hash=v1_hash,
        v2_artifact_hash=None,
        interprocedural_artifact_hash=None,
        assessment_artifact_hash=stable_hash(assessment),
        review_package_hash=stable_hash(review_package),
        promotion_plan_hash=stable_hash(plan),
        source_artifact_hashes=assessment.source_artifact_hashes,
    )
    assert len(artifact.baseline_candidates) == 2


@pytest.mark.parametrize(
    ("disposition", "decision_verb", "expected_reason"),
    [
        (PromotionDisposition.BASELINE_V1, None, UnifiedShadowExclusionReason.BASELINE_ITEM),
        (PromotionDisposition.BLOCKED, None, UnifiedShadowExclusionReason.BLOCKED_ITEM),
        (PromotionDisposition.NOT_EVALUATED, None, UnifiedShadowExclusionReason.DEFERRED),
        (
            PromotionDisposition.REVIEW_REQUIRED,
            None,
            UnifiedShadowExclusionReason.PENDING_DECISION,
        ),
        (
            PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
            ReviewDecision.REJECT,
            UnifiedShadowExclusionReason.REJECTED,
        ),
        (
            PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
            ReviewDecision.DEFER,
            UnifiedShadowExclusionReason.DEFERRED,
        ),
    ],
)
def test_non_approved_plan_items_are_excluded_with_expected_reason(
    disposition: PromotionDisposition,
    decision_verb: ReviewDecision | None,
    expected_reason: UnifiedShadowExclusionReason,
) -> None:
    source = (
        CandidateSource.V1
        if disposition == PromotionDisposition.BASELINE_V1
        else CandidateSource.V2
    )
    reference = (
        v1_reference(reference_id="unified::v1::a", source_candidate_id="candidate::1")
        if source == CandidateSource.V1
        else v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    )
    v1 = v1_artifact(candidates=[], run_id=RUN_ID)
    v1_hash = stable_hash(v1)
    v2_art = None
    v2_hash = None
    if source == CandidateSource.V2:
        v2_cand = v2_candidate(
            candidate_id="v2::1", target_variable="WS-X", resolved_literal="R001"
        )
        v2_art = v2_artifact(candidates=[v2_cand], run_id=RUN_ID)
        v2_hash = dump_hash(v2_art)

    assessment, review_package, plan = _plan_for_single_reference(
        disposition=disposition,
        reference=reference,
        decision_verb=decision_verb,
        v1_hash=v1_hash,
        v2_hash=v2_hash,
    )
    artifact = analyze_unified_candidates_shadow(
        run_id=RUN_ID,
        v1_candidates=v1,
        v2_candidates=v2_art,
        interprocedural_candidates=None,
        assessment=assessment,
        review_package=review_package,
        plan=plan,
        source_package_hash=CAND_HASH,
        candidate_v1_artifact_hash=v1_hash,
        v2_artifact_hash=v2_hash,
        interprocedural_artifact_hash=None,
        assessment_artifact_hash=stable_hash(assessment),
        review_package_hash=stable_hash(review_package),
        promotion_plan_hash=stable_hash(plan),
        source_artifact_hashes=assessment.source_artifact_hashes,
    )
    assert len(artifact.shadow_members) == 0
    [excluded] = artifact.excluded_plan_items
    assert excluded.reason == expected_reason


def test_already_covered_excluded_with_already_covered_reason() -> None:
    v1_ref = v1_reference(reference_id="unified::v1::a", source_candidate_id="candidate::1")
    v2_ref = v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    v1 = v1_artifact(candidates=[v1_candidate(candidate_id="candidate::1")], run_id=RUN_ID)
    v2_cand = v2_candidate(candidate_id="v2::1", target_variable="WS-X", resolved_literal="R001")
    v2_art = v2_artifact(candidates=[v2_cand], run_id=RUN_ID)

    assessment = assessment_of(
        [v1_ref, v2_ref],
        dispositions={
            v1_ref.unified_reference_id: PromotionDisposition.BASELINE_V1,
            v2_ref.unified_reference_id: PromotionDisposition.ALREADY_COVERED,
        },
        exact_match_pairs=[(v1_ref.unified_reference_id, v2_ref.unified_reference_id)],
        v1_hash=stable_hash(v1),
        v2_hash=dump_hash(v2_art),
    )
    review_package = generate_candidate_promotion_review_package(assessment)
    manifest = CandidatePromotionDecisionManifest(
        review_package_hash=stable_hash(review_package),
        assessment_artifact_hash=stable_hash(assessment),
        run_id=RUN_ID,
        decisions=[],
    )
    plan = build_candidate_promotion_plan(
        assessment=assessment, review_package=review_package, manifest=manifest
    )
    artifact = analyze_unified_candidates_shadow(
        run_id=RUN_ID,
        v1_candidates=v1,
        v2_candidates=v2_art,
        interprocedural_candidates=None,
        assessment=assessment,
        review_package=review_package,
        plan=plan,
        source_package_hash=CAND_HASH,
        candidate_v1_artifact_hash=stable_hash(v1),
        v2_artifact_hash=dump_hash(v2_art),
        interprocedural_artifact_hash=None,
        assessment_artifact_hash=stable_hash(assessment),
        review_package_hash=stable_hash(review_package),
        promotion_plan_hash=stable_hash(plan),
        source_artifact_hashes=assessment.source_artifact_hashes,
    )
    reasons = {item.reason for item in artifact.excluded_plan_items}
    assert UnifiedShadowExclusionReason.ALREADY_COVERED in reasons
    assert UnifiedShadowExclusionReason.BASELINE_ITEM in reasons


def test_stale_assessment_hash_raises() -> None:
    scenario = TwoEquivalentProposalsScenario()
    kwargs = scenario.analyzer_kwargs()
    kwargs["assessment_artifact_hash"] = "f" * 64
    with pytest.raises(UnifiedCandidatesShadowError):
        analyze_unified_candidates_shadow(**kwargs)  # type: ignore[arg-type]


def test_stale_v1_hash_raises() -> None:
    scenario = TwoEquivalentProposalsScenario()
    kwargs = scenario.analyzer_kwargs()
    kwargs["candidate_v1_artifact_hash"] = "f" * 64
    with pytest.raises(UnifiedCandidatesShadowError):
        analyze_unified_candidates_shadow(**kwargs)  # type: ignore[arg-type]


def test_run_id_mismatch_raises() -> None:
    scenario = TwoEquivalentProposalsScenario()
    kwargs = scenario.analyzer_kwargs()
    kwargs["run_id"] = "different-run"
    with pytest.raises(UnifiedCandidatesShadowError):
        analyze_unified_candidates_shadow(**kwargs)  # type: ignore[arg-type]


def test_duplicate_baseline_coverage_when_exact_match_with_v1() -> None:
    """Regla de seguridad Parte 8: un plan item PROPOSE_SHADOW_PROMOTION
    cuyo resultado sea EXACT_BASELINE_MATCH nunca se incorpora como
    grupo VALID -- debe quedar DUPLICATE_BASELINE_COVERAGE."""
    v1_ref = v1_reference(reference_id="unified::v1::a", source_candidate_id="candidate::1")
    v2_ref = v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    v1 = v1_artifact(candidates=[v1_candidate(candidate_id="candidate::1")], run_id=RUN_ID)
    v2_cand = v2_candidate(candidate_id="v2::1", target_variable="WS-X", resolved_literal="R001")
    v2_art = v2_artifact(candidates=[v2_cand], run_id=RUN_ID)

    assessment = assessment_of(
        [v1_ref, v2_ref],
        dispositions={
            v1_ref.unified_reference_id: PromotionDisposition.BASELINE_V1,
            # READY_FOR_CONTROLLED_REVIEW aunque exista un EXACT_MATCH con V1:
            # simula un plan desalineado con las fuentes actuales.
            v2_ref.unified_reference_id: PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
        },
        exact_match_pairs=[(v1_ref.unified_reference_id, v2_ref.unified_reference_id)],
        v1_hash=stable_hash(v1),
        v2_hash=dump_hash(v2_art),
    )
    review_package = generate_candidate_promotion_review_package(assessment)
    manifest = CandidatePromotionDecisionManifest(
        review_package_hash=stable_hash(review_package),
        assessment_artifact_hash=stable_hash(assessment),
        run_id=RUN_ID,
        decisions=[
            CandidatePromotionDecision(
                decision_id="decision::v2",
                review_item_id=review_item_id_for(v2_ref.unified_reference_id),
                assessment_id=f"assessment::{v2_ref.unified_reference_id}",
                reference_id=v2_ref.unified_reference_id,
                assessment_artifact_hash=stable_hash(assessment),
                decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
                reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
                reviewer_reference="analyst@example.com",
            )
        ],
    )
    plan = build_candidate_promotion_plan(
        assessment=assessment, review_package=review_package, manifest=manifest
    )
    artifact = analyze_unified_candidates_shadow(
        run_id=RUN_ID,
        v1_candidates=v1,
        v2_candidates=v2_art,
        interprocedural_candidates=None,
        assessment=assessment,
        review_package=review_package,
        plan=plan,
        source_package_hash=CAND_HASH,
        candidate_v1_artifact_hash=stable_hash(v1),
        v2_artifact_hash=dump_hash(v2_art),
        interprocedural_artifact_hash=None,
        assessment_artifact_hash=stable_hash(assessment),
        review_package_hash=stable_hash(review_package),
        promotion_plan_hash=stable_hash(plan),
        source_artifact_hashes=assessment.source_artifact_hashes,
    )
    assert len(artifact.shadow_groups) == 1
    group = artifact.shadow_groups[0]
    assert group.status == UnifiedShadowGroupStatus.DUPLICATE_BASELINE_COVERAGE
    assert group.comparison_to_v1 == UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH
