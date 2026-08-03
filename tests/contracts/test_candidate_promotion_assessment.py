"""Tests del contrato del catalogo unificado de candidatos y
evaluacion de promocion (Fase 9, `feat/unified-candidate-promotion-
assessment`). Items 33-40 de los 50 tests obligatorios: reconciliacion
de criterios/summary, IDs deterministicos, orden independiente de la
entrada, serializacion byte-a-byte, deduplicacion/simetria de
relaciones -- ademas de las invariantes contractuales puras (1-9,
17-21, 24-25 del runbook)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateConflict,
    CandidateConflictType,
    CandidatePromotionAssessment,
    CandidatePromotionAssessmentArtifact,
    CandidatePromotionAssessmentSummary,
    CandidateRelation,
    CandidateRelationKind,
    CandidateSource,
    PromotionCriterionKind,
    PromotionCriterionResult,
    PromotionCriterionStatus,
    PromotionDisposition,
    RecommendedAction,
    UnifiedCandidateReference,
    UnifiedRuleFamily,
    recommended_action_for,
)

HASH = "a" * 64


def _reference(
    *,
    unified_reference_id: str = "unified::v1::aaa",
    source: CandidateSource = CandidateSource.V1,
    source_candidate_id: str = "candidate::1",
    rule_family: UnifiedRuleFamily = UnifiedRuleFamily.RETURN_CODE,
) -> UnifiedCandidateReference:
    return UnifiedCandidateReference(
        unified_reference_id=unified_reference_id,
        source=source,
        source_candidate_id=source_candidate_id,
        source_artifact_hash=HASH,
        rule_family=rule_family,
        original_support="DETECTED_CANDIDATE",
        program="CALLER",
    )


def _baseline_assessment(reference_id: str) -> CandidatePromotionAssessment:
    criteria = [
        PromotionCriterionResult(
            criterion=criterion,
            status=PromotionCriterionStatus.NOT_APPLICABLE,
            reason="V1 baseline.",
        )
        for criterion in PromotionCriterionKind
    ]
    return CandidatePromotionAssessment(
        assessment_id=f"assessment::{reference_id}",
        reference_id=reference_id,
        disposition=PromotionDisposition.BASELINE_V1,
        criteria=criteria,
        recommended_action=RecommendedAction.NONE_BASELINE_V1,
    )


def _summary_for(
    references: list[UnifiedCandidateReference],
) -> CandidatePromotionAssessmentSummary:
    return CandidatePromotionAssessmentSummary(
        v1_candidate_count=len(references),
        v2_candidate_count=0,
        interprocedural_candidate_count=0,
        unified_reference_count=len(references),
        exact_match_relation_count=0,
        related_relation_count=0,
        conflict_count=0,
        baseline_v1_count=len(references),
        already_covered_count=0,
        ready_for_controlled_review_count=0,
        review_required_count=0,
        blocked_count=0,
        conflicting_count=0,
        not_evaluated_count=0,
        counts_by_source={CandidateSource.V1: len(references)},
        counts_by_rule_family={UnifiedRuleFamily.RETURN_CODE: len(references)},
        counts_by_disposition={PromotionDisposition.BASELINE_V1: len(references)},
    )


def _minimal_artifact() -> CandidatePromotionAssessmentArtifact:
    reference = _reference()
    return CandidatePromotionAssessmentArtifact(
        run_id="run1",
        source_package_hash=HASH,
        summary=_summary_for([reference]),
        candidate_references=[reference],
        assessments=[_baseline_assessment(reference.unified_reference_id)],
    )


def test_recommended_action_catalog_is_stable_never_free_text() -> None:
    for disposition in PromotionDisposition:
        action = recommended_action_for(disposition)
        assert isinstance(action, RecommendedAction)


def test_promotion_disposition_never_includes_promoted_or_auto_promoted() -> None:
    values = {member.value for member in PromotionDisposition}
    assert "PROMOTED" not in values
    assert "AUTO_PROMOTED" not in values


def test_minimal_artifact_is_valid() -> None:
    artifact = _minimal_artifact()
    assert artifact.summary.unified_reference_count == 1


def test_reference_evidence_ids_must_be_sorted_and_unique() -> None:
    with pytest.raises(ValidationError):
        UnifiedCandidateReference(
            unified_reference_id="unified::v1::x",
            source=CandidateSource.V1,
            source_candidate_id="c1",
            source_artifact_hash=HASH,
            rule_family=UnifiedRuleFamily.RETURN_CODE,
            original_support="DETECTED_CANDIDATE",
            evidence_ids=["b", "a"],
        )


def test_relation_left_must_be_alphabetically_before_right() -> None:
    with pytest.raises(ValidationError):
        CandidateRelation(
            relation_id="relation::x",
            left_reference_id="unified::v2::b",
            right_reference_id="unified::v1::a",
            relation_kind=CandidateRelationKind.EXACT_MATCH,
            reason="test",
        )


def test_relation_cannot_self_relate() -> None:
    with pytest.raises(ValidationError):
        CandidateRelation(
            relation_id="relation::x",
            left_reference_id="unified::v1::a",
            right_reference_id="unified::v1::a",
            relation_kind=CandidateRelationKind.EXACT_MATCH,
            reason="test",
        )


def test_conflict_requires_at_least_one_reference_id() -> None:
    with pytest.raises(ValidationError):
        CandidateConflict(
            conflict_id="conflict::x",
            reference_ids=[],
            conflict_type=CandidateConflictType.SAME_DECISION_DIFFERENT_OUTPUT,
            reason="test",
        )


def test_conflict_allows_a_single_reference_id_for_invalid_provenance() -> None:
    """`INVALID_PROVENANCE` es la unica excepcion deliberada al patron
    "conflicto entre dos referencias": una unica referencia DETERMINISTIC
    sin evidencia incumple, por si sola, el contrato de su propia fuente."""
    conflict = CandidateConflict(
        conflict_id="conflict::x",
        reference_ids=["unified::v2::a"],
        conflict_type=CandidateConflictType.INVALID_PROVENANCE,
        reason="test",
    )
    assert conflict.reference_ids == ["unified::v2::a"]


def test_recommended_action_must_match_disposition() -> None:
    with pytest.raises(ValidationError):
        CandidatePromotionAssessment(
            assessment_id="assessment::x",
            reference_id="unified::v1::a",
            disposition=PromotionDisposition.BASELINE_V1,
            recommended_action=RecommendedAction.RESOLVE_CONFLICT_BEFORE_REVIEW,
        )


def test_ready_for_controlled_review_cannot_declare_conflict_ids() -> None:
    criteria = [
        PromotionCriterionResult(
            criterion=criterion, status=PromotionCriterionStatus.PASS, reason="ok"
        )
        for criterion in PromotionCriterionKind
    ]
    with pytest.raises(ValidationError):
        CandidatePromotionAssessment(
            assessment_id="assessment::x",
            reference_id="unified::v2::a",
            disposition=PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
            criteria=criteria,
            conflict_ids=["conflict::x"],
            recommended_action=RecommendedAction.SUBMIT_FOR_CONTROLLED_FUNCTIONAL_REVIEW,
        )


def test_conflicting_requires_at_least_one_conflict_id() -> None:
    with pytest.raises(ValidationError):
        CandidatePromotionAssessment(
            assessment_id="assessment::x",
            reference_id="unified::v2::a",
            disposition=PromotionDisposition.CONFLICTING,
            conflict_ids=[],
            recommended_action=RecommendedAction.RESOLVE_CONFLICT_BEFORE_REVIEW,
        )


def test_blocked_requires_at_least_one_fail_criterion() -> None:
    criteria = [
        PromotionCriterionResult(
            criterion=criterion, status=PromotionCriterionStatus.PASS, reason="ok"
        )
        for criterion in PromotionCriterionKind
    ]
    with pytest.raises(ValidationError):
        CandidatePromotionAssessment(
            assessment_id="assessment::x",
            reference_id="unified::v2::a",
            disposition=PromotionDisposition.BLOCKED,
            criteria=criteria,
            recommended_action=RecommendedAction.RESOLVE_BLOCKING_CRITERIA_BEFORE_REVIEW,
        )


def test_not_evaluated_requires_at_least_one_not_evaluated_criterion() -> None:
    criteria = [
        PromotionCriterionResult(
            criterion=criterion, status=PromotionCriterionStatus.PASS, reason="ok"
        )
        for criterion in PromotionCriterionKind
    ]
    with pytest.raises(ValidationError):
        CandidatePromotionAssessment(
            assessment_id="assessment::x",
            reference_id="unified::v2::a",
            disposition=PromotionDisposition.NOT_EVALUATED,
            criteria=criteria,
            recommended_action=RecommendedAction.AWAIT_REQUIRED_SOURCE_AVAILABILITY,
        )


def test_artifact_rejects_duplicate_unified_reference_id() -> None:
    reference = _reference()
    with pytest.raises(ValidationError):
        CandidatePromotionAssessmentArtifact(
            run_id="run1",
            source_package_hash=HASH,
            summary=_summary_for([reference, reference]),
            candidate_references=[reference, reference],
            assessments=[_baseline_assessment(reference.unified_reference_id)],
        )


def test_artifact_rejects_source_candidate_id_duplicate_within_same_source() -> None:
    reference_a = _reference(unified_reference_id="unified::v1::a", source_candidate_id="dup")
    reference_b = _reference(unified_reference_id="unified::v1::b", source_candidate_id="dup")
    with pytest.raises(ValidationError):
        CandidatePromotionAssessmentArtifact(
            run_id="run1",
            source_package_hash=HASH,
            summary=_summary_for([reference_a, reference_b]),
            candidate_references=sorted(
                [reference_a, reference_b], key=lambda r: r.unified_reference_id
            ),
            assessments=[
                _baseline_assessment(reference_a.unified_reference_id),
                _baseline_assessment(reference_b.unified_reference_id),
            ],
        )


def test_artifact_relation_must_reference_existing_ids() -> None:
    reference = _reference()
    relation = CandidateRelation(
        relation_id="relation::x",
        left_reference_id="unified::v1::0missing",
        right_reference_id=reference.unified_reference_id,
        relation_kind=CandidateRelationKind.NO_RELATION,
        reason="test",
    )
    with pytest.raises(ValidationError):
        CandidatePromotionAssessmentArtifact(
            run_id="run1",
            source_package_hash=HASH,
            summary=_summary_for([reference]),
            candidate_references=[reference],
            relations=[relation],
            assessments=[_baseline_assessment(reference.unified_reference_id)],
        )


def test_artifact_conflict_must_reference_existing_ids() -> None:
    reference = _reference()
    conflict = CandidateConflict(
        conflict_id="conflict::x",
        reference_ids=sorted(["unified::v1::0missing", reference.unified_reference_id]),
        conflict_type=CandidateConflictType.SAME_TARGET_CONTRADICTORY_OUTPUT,
        reason="test",
    )
    with pytest.raises(ValidationError):
        CandidatePromotionAssessmentArtifact(
            run_id="run1",
            source_package_hash=HASH,
            summary=_summary_for([reference]),
            candidate_references=[reference],
            conflicts=[conflict],
            assessments=[_baseline_assessment(reference.unified_reference_id)],
        )


def test_artifact_every_reference_needs_exactly_one_assessment() -> None:
    reference = _reference()
    with pytest.raises(ValidationError):
        CandidatePromotionAssessmentArtifact(
            run_id="run1",
            source_package_hash=HASH,
            summary=_summary_for([reference]),
            candidate_references=[reference],
            assessments=[],
        )


def test_artifact_rejects_reference_pair_appearing_in_two_relations() -> None:
    left = _reference(unified_reference_id="unified::v1::a")
    right = _reference(
        unified_reference_id="unified::v2::b",
        source=CandidateSource.V2,
        rule_family=UnifiedRuleFamily.RETURN_CODE,
    )
    relation_1 = CandidateRelation(
        relation_id="relation::1",
        left_reference_id=left.unified_reference_id,
        right_reference_id=right.unified_reference_id,
        relation_kind=CandidateRelationKind.EXACT_MATCH,
        reason="first",
    )
    relation_2 = CandidateRelation(
        relation_id="relation::2",
        left_reference_id=left.unified_reference_id,
        right_reference_id=right.unified_reference_id,
        relation_kind=CandidateRelationKind.RELATED,
        reason="second",
    )
    summary = CandidatePromotionAssessmentSummary(
        v1_candidate_count=1,
        v2_candidate_count=1,
        interprocedural_candidate_count=0,
        unified_reference_count=2,
        exact_match_relation_count=1,
        related_relation_count=1,
        conflict_count=0,
        baseline_v1_count=1,
        already_covered_count=1,
        ready_for_controlled_review_count=0,
        review_required_count=0,
        blocked_count=0,
        conflicting_count=0,
        not_evaluated_count=0,
        counts_by_source={CandidateSource.V1: 1, CandidateSource.V2: 1},
        counts_by_rule_family={UnifiedRuleFamily.RETURN_CODE: 2},
        counts_by_disposition={
            PromotionDisposition.BASELINE_V1: 1,
            PromotionDisposition.ALREADY_COVERED: 1,
        },
    )
    with pytest.raises(ValidationError):
        CandidatePromotionAssessmentArtifact(
            run_id="run1",
            source_package_hash=HASH,
            summary=summary,
            candidate_references=[left, right],
            relations=[relation_1, relation_2],
            assessments=[
                _baseline_assessment(left.unified_reference_id),
                CandidatePromotionAssessment(
                    assessment_id=f"assessment::{right.unified_reference_id}",
                    reference_id=right.unified_reference_id,
                    disposition=PromotionDisposition.ALREADY_COVERED,
                    exact_match_reference_ids=[left.unified_reference_id],
                    recommended_action=RecommendedAction.NO_ACTION_ALREADY_COVERED_BY_V1,
                ),
            ],
        )


def test_baseline_v1_only_allowed_for_v1_source() -> None:
    reference = _reference(source=CandidateSource.V2, unified_reference_id="unified::v2::a")
    summary = CandidatePromotionAssessmentSummary(
        v1_candidate_count=0,
        v2_candidate_count=1,
        interprocedural_candidate_count=0,
        unified_reference_count=1,
        exact_match_relation_count=0,
        related_relation_count=0,
        conflict_count=0,
        baseline_v1_count=1,
        already_covered_count=0,
        ready_for_controlled_review_count=0,
        review_required_count=0,
        blocked_count=0,
        conflicting_count=0,
        not_evaluated_count=0,
        counts_by_source={CandidateSource.V2: 1},
        counts_by_rule_family={UnifiedRuleFamily.RETURN_CODE: 1},
        counts_by_disposition={PromotionDisposition.BASELINE_V1: 1},
    )
    with pytest.raises(ValidationError):
        CandidatePromotionAssessmentArtifact(
            run_id="run1",
            source_package_hash=HASH,
            summary=summary,
            candidate_references=[reference],
            assessments=[_baseline_assessment(reference.unified_reference_id)],
        )


def test_unknown_rule_family_blocks_ready_for_controlled_review() -> None:
    reference = _reference(
        source=CandidateSource.V2,
        unified_reference_id="unified::v2::a",
        rule_family=UnifiedRuleFamily.UNKNOWN,
    )
    criteria = [
        PromotionCriterionResult(
            criterion=criterion, status=PromotionCriterionStatus.PASS, reason="ok"
        )
        for criterion in PromotionCriterionKind
    ]
    assessment = CandidatePromotionAssessment(
        assessment_id=f"assessment::{reference.unified_reference_id}",
        reference_id=reference.unified_reference_id,
        disposition=PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
        criteria=criteria,
        recommended_action=RecommendedAction.SUBMIT_FOR_CONTROLLED_FUNCTIONAL_REVIEW,
    )
    summary = CandidatePromotionAssessmentSummary(
        v1_candidate_count=0,
        v2_candidate_count=1,
        interprocedural_candidate_count=0,
        unified_reference_count=1,
        exact_match_relation_count=0,
        related_relation_count=0,
        conflict_count=0,
        baseline_v1_count=0,
        already_covered_count=0,
        ready_for_controlled_review_count=1,
        review_required_count=0,
        blocked_count=0,
        conflicting_count=0,
        not_evaluated_count=0,
        counts_by_source={CandidateSource.V2: 1},
        counts_by_rule_family={UnifiedRuleFamily.UNKNOWN: 1},
        counts_by_disposition={PromotionDisposition.READY_FOR_CONTROLLED_REVIEW: 1},
    )
    with pytest.raises(ValidationError):
        CandidatePromotionAssessmentArtifact(
            run_id="run1",
            source_package_hash=HASH,
            summary=summary,
            candidate_references=[reference],
            assessments=[assessment],
        )


def test_summary_must_reconcile_with_content() -> None:
    reference = _reference()
    bad_summary = _summary_for([reference]).model_copy(update={"unified_reference_count": 5})
    with pytest.raises(ValidationError):
        CandidatePromotionAssessmentArtifact(
            run_id="run1",
            source_package_hash=HASH,
            summary=bad_summary,
            candidate_references=[reference],
            assessments=[_baseline_assessment(reference.unified_reference_id)],
        )


def test_schema_analyzer_policy_versions_are_literal_one_point_zero() -> None:
    artifact = _minimal_artifact()
    assert artifact.schema_version == "1.0"
    assert artifact.analyzer_version == "1.0"
    assert artifact.policy_version == "1.0"


def test_artifact_serialization_is_deterministic_byte_for_byte() -> None:
    artifact_a = _minimal_artifact()
    artifact_b = _minimal_artifact()
    assert artifact_a.model_dump_json() == artifact_b.model_dump_json()


def test_artifact_never_carries_timestamp_field() -> None:
    artifact = _minimal_artifact()
    dumped = artifact.model_dump()
    assert "timestamp" not in dumped
    assert "generated_at" not in dumped
    assert "created_at" not in dumped


def test_incompatible_schema_version_is_rejected() -> None:
    """`schema_version`/`analyzer_version`/`policy_version` son
    `Literal["1.0"]`: un diagnostico persistido con otra version (p. ej.
    de una Fase 9 futura incompatible) nunca se carga silenciosamente."""
    artifact = _minimal_artifact()
    payload = artifact.model_dump(mode="json")
    payload["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        CandidatePromotionAssessmentArtifact.model_validate(payload)
