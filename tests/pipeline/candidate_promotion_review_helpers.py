"""Fixtures compartidas del plan de promocion controlada (Fase 10 de la
ampliacion semantica, `feat/controlled-candidate-promotion-plan`). NO
es un modulo de test (sin prefijo `test_`): expone builders de
`CandidatePromotionAssessmentArtifact` (uno por cada `PromotionDisposition`,
construidos directamente -- no via el analizador de Fase 9 -- para
control total sobre el escenario de cada test) y de manifiestos de
decisiones humanos, reutilizados por los tests de generador/builder/
servicio/CLI de Fase 10."""

from __future__ import annotations

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
    SourceAvailability,
    UnifiedCandidateReference,
    UnifiedRuleFamily,
    recommended_action_for,
)
from altamira_extractor.contracts.candidate_promotion_review import (
    CandidatePromotionDecision,
    CandidatePromotionDecisionManifest,
    DecisionReasonCode,
    ReviewDecision,
)

HASH = "c" * 64


def reference(
    *,
    reference_id: str,
    source: CandidateSource = CandidateSource.V2,
    source_candidate_id: str | None = None,
    rule_family: UnifiedRuleFamily = UnifiedRuleFamily.RETURN_CODE,
    original_support: str = "DETERMINISTIC",
    program: str = "CALLER",
    target: str | None = "WS-X",
    output_literal: str | None = "R001",
    evidence_ids: list[str] | None = None,
    barrier_codes: list[str] | None = None,
) -> UnifiedCandidateReference:
    return UnifiedCandidateReference(
        unified_reference_id=reference_id,
        source=source,
        source_candidate_id=source_candidate_id or reference_id,
        source_artifact_hash=HASH,
        rule_family=rule_family,
        original_support=original_support,
        program=program,
        target=target,
        output_literal=output_literal,
        evidence_ids=sorted(evidence_ids if evidence_ids is not None else ["evidence::1"]),
        barrier_codes=sorted(barrier_codes or []),
    )


def _criteria(
    overrides: dict[PromotionCriterionKind, PromotionCriterionStatus],
) -> list[PromotionCriterionResult]:
    return [
        PromotionCriterionResult(
            criterion=criterion,
            status=overrides.get(criterion, PromotionCriterionStatus.PASS),
            reason="fixture de test Fase 10",
        )
        for criterion in PromotionCriterionKind
    ]


def all_not_applicable_criteria() -> list[PromotionCriterionResult]:
    return [
        PromotionCriterionResult(
            criterion=criterion,
            status=PromotionCriterionStatus.NOT_APPLICABLE,
            reason="V1 es baseline, nunca se evalua.",
        )
        for criterion in PromotionCriterionKind
    ]


def assessment_for(
    ref: UnifiedCandidateReference,
    *,
    disposition: PromotionDisposition,
    exact_match_reference_ids: list[str] | None = None,
    conflict_ids: list[str] | None = None,
    criteria: list[PromotionCriterionResult] | None = None,
) -> CandidatePromotionAssessment:
    if disposition == PromotionDisposition.BASELINE_V1:
        criteria = criteria or all_not_applicable_criteria()
    elif disposition == PromotionDisposition.ALREADY_COVERED:
        criteria = criteria or _criteria({})
    elif disposition == PromotionDisposition.READY_FOR_CONTROLLED_REVIEW:
        criteria = criteria or _criteria({})
    elif disposition == PromotionDisposition.REVIEW_REQUIRED:
        criteria = criteria or _criteria(
            {PromotionCriterionKind.INDEPENDENT_CORROBORATION: PromotionCriterionStatus.FAIL}
        )
    elif disposition == PromotionDisposition.BLOCKED:
        criteria = criteria or _criteria(
            {PromotionCriterionKind.NO_BARRIERS: PromotionCriterionStatus.FAIL}
        )
    elif disposition == PromotionDisposition.CONFLICTING:
        criteria = criteria or _criteria({})
    elif disposition == PromotionDisposition.NOT_EVALUATED:
        criteria = criteria or _criteria(
            {PromotionCriterionKind.V1_COMPARISON_AVAILABLE: PromotionCriterionStatus.NOT_EVALUATED}
        )
    return CandidatePromotionAssessment(
        assessment_id=f"assessment::{ref.unified_reference_id}",
        reference_id=ref.unified_reference_id,
        disposition=disposition,
        criteria=criteria or [],
        exact_match_reference_ids=sorted(exact_match_reference_ids or []),
        conflict_ids=sorted(conflict_ids or []),
        recommended_action=recommended_action_for(disposition),
    )


def single_disposition_artifact(
    disposition: PromotionDisposition,
    *,
    source: CandidateSource = CandidateSource.V2,
    run_id: str = "run1",
) -> CandidatePromotionAssessmentArtifact:
    """Un assessment MINIMO con UNA sola referencia/assessment de la
    `disposition` pedida -- para BASELINE_V1 la fuente es siempre V1
    (nunca otra, invariante contractual)."""
    if disposition == PromotionDisposition.BASELINE_V1:
        source = CandidateSource.V1
    ref = reference(reference_id=f"unified::{source.value.lower()}::a", source=source)
    conflicts: list[CandidateConflict] = []
    conflict_ids: list[str] = []
    if disposition == PromotionDisposition.CONFLICTING:
        conflict_ids = ["conflict::a"]
        conflicts = [
            CandidateConflict(
                conflict_id="conflict::a",
                reference_ids=[ref.unified_reference_id],
                conflict_type=CandidateConflictType.INVALID_PROVENANCE,
                reason="fixture de conflicto para test Fase 10",
            )
        ]
    exact_match_ids: list[str] = []
    relations: list[CandidateRelation] = []
    all_refs = [ref]
    all_assessments: list[CandidatePromotionAssessment] = []
    if disposition == PromotionDisposition.ALREADY_COVERED:
        baseline_ref = reference(reference_id="unified::v1::baseline", source=CandidateSource.V1)
        exact_match_ids = [baseline_ref.unified_reference_id]
        left, right = sorted([ref.unified_reference_id, baseline_ref.unified_reference_id])
        relations = [
            CandidateRelation(
                relation_id="relation::a",
                left_reference_id=left,
                right_reference_id=right,
                relation_kind=CandidateRelationKind.EXACT_MATCH,
                reason="fixture de match para test Fase 10",
            )
        ]
        baseline_assessment = assessment_for(
            baseline_ref, disposition=PromotionDisposition.BASELINE_V1
        )
        all_refs = sorted([ref, baseline_ref], key=lambda r: r.unified_reference_id)

    assessment = assessment_for(
        ref,
        disposition=disposition,
        exact_match_reference_ids=exact_match_ids,
        conflict_ids=conflict_ids,
    )
    all_assessments = [assessment]
    if disposition == PromotionDisposition.ALREADY_COVERED:
        all_assessments = sorted([assessment, baseline_assessment], key=lambda a: a.assessment_id)

    counts_by_source: dict[CandidateSource, int] = {}
    counts_by_family: dict[UnifiedRuleFamily, int] = {}
    counts_by_disposition: dict[PromotionDisposition, int] = {}
    for r, a in zip(all_refs, all_assessments, strict=True):
        counts_by_source[r.source] = counts_by_source.get(r.source, 0) + 1
        counts_by_family[r.rule_family] = counts_by_family.get(r.rule_family, 0) + 1
        counts_by_disposition[a.disposition] = counts_by_disposition.get(a.disposition, 0) + 1

    summary = CandidatePromotionAssessmentSummary(
        v1_candidate_count=counts_by_source.get(CandidateSource.V1, 0),
        v2_candidate_count=counts_by_source.get(CandidateSource.V2, 0),
        interprocedural_candidate_count=counts_by_source.get(CandidateSource.INTERPROCEDURAL, 0),
        unified_reference_count=len(all_refs),
        exact_match_relation_count=len(
            [r for r in relations if r.relation_kind == CandidateRelationKind.EXACT_MATCH]
        ),
        related_relation_count=0,
        conflict_count=len(conflicts),
        baseline_v1_count=counts_by_disposition.get(PromotionDisposition.BASELINE_V1, 0),
        already_covered_count=counts_by_disposition.get(PromotionDisposition.ALREADY_COVERED, 0),
        ready_for_controlled_review_count=counts_by_disposition.get(
            PromotionDisposition.READY_FOR_CONTROLLED_REVIEW, 0
        ),
        review_required_count=counts_by_disposition.get(PromotionDisposition.REVIEW_REQUIRED, 0),
        blocked_count=counts_by_disposition.get(PromotionDisposition.BLOCKED, 0),
        conflicting_count=counts_by_disposition.get(PromotionDisposition.CONFLICTING, 0),
        not_evaluated_count=counts_by_disposition.get(PromotionDisposition.NOT_EVALUATED, 0),
        counts_by_source=counts_by_source,
        counts_by_rule_family=counts_by_family,
        counts_by_disposition=counts_by_disposition,
        source_availability={
            CandidateSource.V1: SourceAvailability.AVAILABLE,
            CandidateSource.V2: SourceAvailability.AVAILABLE,
            CandidateSource.INTERPROCEDURAL: SourceAvailability.AVAILABLE,
        },
    )

    return CandidatePromotionAssessmentArtifact(
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes={},
        source_availability={
            CandidateSource.V1: SourceAvailability.AVAILABLE,
            CandidateSource.V2: SourceAvailability.AVAILABLE,
            CandidateSource.INTERPROCEDURAL: SourceAvailability.AVAILABLE,
        },
        summary=summary,
        candidate_references=all_refs,
        relations=relations,
        conflicts=conflicts,
        assessments=all_assessments,
    )


def decision(
    *,
    decision_id: str,
    review_item_id: str,
    assessment_id: str,
    reference_id: str,
    assessment_artifact_hash: str,
    verb: ReviewDecision,
    reason_code: DecisionReasonCode,
    reviewer_reference: str = "analyst@example.com",
    decision_reference: str | None = None,
) -> CandidatePromotionDecision:
    return CandidatePromotionDecision(
        decision_id=decision_id,
        review_item_id=review_item_id,
        assessment_id=assessment_id,
        reference_id=reference_id,
        assessment_artifact_hash=assessment_artifact_hash,
        decision=verb,
        reason_code=reason_code,
        reviewer_reference=reviewer_reference,
        decision_reference=decision_reference,
    )


def manifest(
    *,
    review_package_hash: str,
    assessment_artifact_hash: str,
    run_id: str = "run1",
    decisions: list[CandidatePromotionDecision] | None = None,
) -> CandidatePromotionDecisionManifest:
    return CandidatePromotionDecisionManifest(
        review_package_hash=review_package_hash,
        assessment_artifact_hash=assessment_artifact_hash,
        run_id=run_id,
        decisions=decisions or [],
    )
