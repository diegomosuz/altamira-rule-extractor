"""Tests del comparador puro contra el baseline V1 (Fase 11 Parte 8,
`pipeline/unified_shadow_baseline_comparator.py`)."""

from __future__ import annotations

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateConflict,
    CandidateConflictType,
    CandidateRelation,
    CandidateRelationKind,
    CandidateSource,
    PromotionDisposition,
    SourceAvailability,
)
from altamira_extractor.contracts.unified_candidates_shadow import (
    UnifiedBaselineCandidateReference,
    UnifiedShadowComparisonKind,
)
from altamira_extractor.pipeline.unified_shadow_baseline_adapter import (
    adapt_v1_baseline_candidates,
)
from altamira_extractor.pipeline.unified_shadow_baseline_comparator import (
    compare_group_to_baseline,
)

from .unified_candidates_shadow_helpers import (
    assessment_of,
    v1_artifact,
    v1_candidate,
    v1_reference,
    v2_reference,
)


def _baseline_setup() -> tuple[
    list[UnifiedBaselineCandidateReference], dict[str, UnifiedBaselineCandidateReference]
]:
    v1 = v1_artifact(
        candidates=[
            v1_candidate(candidate_id="candidate::1", program="CALLER", outcome_code="R001")
        ]
    )
    baseline_candidates = adapt_v1_baseline_candidates(v1, source_artifact_hash="a" * 64)
    baseline_by_ref_id = {baseline_candidates[0].baseline_reference_id: baseline_candidates[0]}
    return baseline_candidates, baseline_by_ref_id


def test_v1_not_available_returns_not_evaluated() -> None:
    v2_ref = v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    assessment = assessment_of(
        [v2_ref],
        dispositions={
            v2_ref.unified_reference_id: PromotionDisposition.READY_FOR_CONTROLLED_REVIEW
        },
    )
    assessment = assessment.model_copy(
        update={
            "source_availability": {
                **assessment.source_availability,
                CandidateSource.V1: SourceAvailability.NOT_AVAILABLE,
            }
        }
    )
    result = compare_group_to_baseline(
        member_assessment_reference_ids=[v2_ref.unified_reference_id],
        group_program="CALLER",
        group_target="WS-X",
        group_output_literal="R001",
        assessment=assessment,
        baseline_reference_id_by_assessment_reference_id={},
        baseline_candidates_by_reference_id={},
    )
    assert result.comparison_to_v1 == UnifiedShadowComparisonKind.NOT_EVALUATED


def test_no_relation_returns_not_in_baseline() -> None:
    _, baseline_by_ref_id = _baseline_setup()
    v2_ref = v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    assessment = assessment_of(
        [v2_ref],
        dispositions={
            v2_ref.unified_reference_id: PromotionDisposition.READY_FOR_CONTROLLED_REVIEW
        },
    )
    result = compare_group_to_baseline(
        member_assessment_reference_ids=[v2_ref.unified_reference_id],
        group_program="CALLER",
        group_target="WS-X",
        group_output_literal="R001",
        assessment=assessment,
        baseline_reference_id_by_assessment_reference_id={},
        baseline_candidates_by_reference_id=baseline_by_ref_id,
    )
    assert result.comparison_to_v1 == UnifiedShadowComparisonKind.NOT_IN_BASELINE


def test_exact_match_relation_returns_exact_baseline_match() -> None:
    baseline_candidates, baseline_by_ref_id = _baseline_setup()
    baseline_ref_id = baseline_candidates[0].baseline_reference_id
    v1_ref = v1_reference(reference_id="unified::v1::a", source_candidate_id="candidate::1")
    v2_ref = v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    assessment = assessment_of(
        [v1_ref, v2_ref],
        dispositions={
            v1_ref.unified_reference_id: PromotionDisposition.BASELINE_V1,
            v2_ref.unified_reference_id: PromotionDisposition.ALREADY_COVERED,
        },
        exact_match_pairs=[(v1_ref.unified_reference_id, v2_ref.unified_reference_id)],
    )
    result = compare_group_to_baseline(
        member_assessment_reference_ids=[v2_ref.unified_reference_id],
        group_program="CALLER",
        group_target="WS-X",
        group_output_literal="R001",
        assessment=assessment,
        baseline_reference_id_by_assessment_reference_id={
            v1_ref.unified_reference_id: baseline_ref_id
        },
        baseline_candidates_by_reference_id=baseline_by_ref_id,
    )
    assert result.comparison_to_v1 == UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH
    assert result.exact_baseline_reference_ids == [baseline_ref_id]


def test_related_relation_returns_related_to_baseline() -> None:
    baseline_candidates, baseline_by_ref_id = _baseline_setup()
    baseline_ref_id = baseline_candidates[0].baseline_reference_id
    v1_ref = v1_reference(reference_id="unified::v1::a", source_candidate_id="candidate::1")
    v2_ref = v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    assessment = assessment_of(
        [v1_ref, v2_ref],
        dispositions={
            v1_ref.unified_reference_id: PromotionDisposition.BASELINE_V1,
            v2_ref.unified_reference_id: PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
        },
    )
    left, right = sorted([v1_ref.unified_reference_id, v2_ref.unified_reference_id])
    related_relation = CandidateRelation(
        relation_id="relation::related",
        left_reference_id=left,
        right_reference_id=right,
        relation_kind=CandidateRelationKind.RELATED,
        reason="fixture: related",
    )
    assessment = assessment.model_copy(update={"relations": [related_relation]})
    result = compare_group_to_baseline(
        member_assessment_reference_ids=[v2_ref.unified_reference_id],
        group_program="CALLER",
        group_target="WS-X",
        group_output_literal="R001",
        assessment=assessment,
        baseline_reference_id_by_assessment_reference_id={
            v1_ref.unified_reference_id: baseline_ref_id
        },
        baseline_candidates_by_reference_id=baseline_by_ref_id,
    )
    assert result.comparison_to_v1 == UnifiedShadowComparisonKind.RELATED_TO_BASELINE
    assert result.related_baseline_reference_ids == [baseline_ref_id]


def test_conflict_relation_returns_conflicts_with_baseline() -> None:
    baseline_candidates, baseline_by_ref_id = _baseline_setup()
    baseline_ref_id = baseline_candidates[0].baseline_reference_id
    v1_ref = v1_reference(reference_id="unified::v1::a", source_candidate_id="candidate::1")
    v2_ref = v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    assessment = assessment_of(
        [v1_ref, v2_ref],
        dispositions={
            v1_ref.unified_reference_id: PromotionDisposition.BASELINE_V1,
            v2_ref.unified_reference_id: PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
        },
    )
    conflict = CandidateConflict(
        conflict_id="conflict::1",
        reference_ids=sorted([v1_ref.unified_reference_id, v2_ref.unified_reference_id]),
        conflict_type=CandidateConflictType.SAME_TARGET_CONTRADICTORY_OUTPUT,
        reason="fixture: conflict",
    )
    assessment = assessment.model_copy(update={"conflicts": [conflict]})
    result = compare_group_to_baseline(
        member_assessment_reference_ids=[v2_ref.unified_reference_id],
        group_program="CALLER",
        group_target="WS-X",
        group_output_literal="R001",
        assessment=assessment,
        baseline_reference_id_by_assessment_reference_id={
            v1_ref.unified_reference_id: baseline_ref_id
        },
        baseline_candidates_by_reference_id=baseline_by_ref_id,
    )
    assert result.comparison_to_v1 == UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE
    assert result.conflicting_baseline_reference_ids == [baseline_ref_id]


def test_structural_conflict_never_fires_when_baseline_target_is_none() -> None:
    """El adaptador V1 real (`unified_shadow_baseline_adapter.py`)
    SIEMPRE deja `target=None` en `UnifiedBaselineCandidateReference`
    (Q0/`RuleCandidate` no tiene concepto de target) -- mismo motivo por
    el que `SAME_TARGET_CONTRADICTORY_OUTPUT` de Fase 9 tampoco puede
    disparar nunca contra V1. La verificacion estructural exige target
    no-nulo en AMBOS lados, asi que debe permanecer NOT_IN_BASELINE."""
    _, baseline_by_ref_id = _baseline_setup()
    v2_ref = v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    assessment = assessment_of(
        [v2_ref],
        dispositions={
            v2_ref.unified_reference_id: PromotionDisposition.READY_FOR_CONTROLLED_REVIEW
        },
    )
    result = compare_group_to_baseline(
        member_assessment_reference_ids=[v2_ref.unified_reference_id],
        group_program="CALLER",
        group_target="WS-X",
        group_output_literal="R999",
        assessment=assessment,
        baseline_reference_id_by_assessment_reference_id={},
        baseline_candidates_by_reference_id=baseline_by_ref_id,
    )
    assert result.comparison_to_v1 == UnifiedShadowComparisonKind.NOT_IN_BASELINE


def test_structural_conflict_fires_when_baseline_target_present() -> None:
    """Prueba unitaria del comparador en aislamiento: si el baseline
    SI expusiera un `target` (hipotetico, no ocurre con el adaptador V1
    real), la verificacion estructural debe disparar
    CONFLICTS_WITH_BASELINE ante mismo (program, target) con
    output_literal distinto -- sin depender de ninguna relacion CONFLICT
    explicita de Fase 9."""
    baseline_candidates, baseline_by_ref_id = _baseline_setup()
    baseline_ref_id = baseline_candidates[0].baseline_reference_id
    baseline_with_target = baseline_candidates[0].model_copy(update={"target": "WS-X"})
    baseline_by_ref_id = {baseline_ref_id: baseline_with_target}
    v2_ref = v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    assessment = assessment_of(
        [v2_ref],
        dispositions={
            v2_ref.unified_reference_id: PromotionDisposition.READY_FOR_CONTROLLED_REVIEW
        },
    )
    result = compare_group_to_baseline(
        member_assessment_reference_ids=[v2_ref.unified_reference_id],
        group_program="CALLER",
        group_target="WS-X",
        group_output_literal="R999",
        assessment=assessment,
        baseline_reference_id_by_assessment_reference_id={},
        baseline_candidates_by_reference_id=baseline_by_ref_id,
    )
    assert result.comparison_to_v1 == UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE
    assert result.conflicting_baseline_reference_ids == [baseline_ref_id]


def test_structural_conflict_never_fires_when_output_literal_absent() -> None:
    """Auditoria de cierre, Parte 4: la verificacion estructural exige
    `group_output_literal` no-nulo -- un literal ausente (target sin
    output resuelto) nunca fabrica un conflicto contra un baseline con
    target coincidente."""
    baseline_candidates, _ = _baseline_setup()
    baseline_ref_id = baseline_candidates[0].baseline_reference_id
    baseline_with_target = baseline_candidates[0].model_copy(update={"target": "WS-X"})
    baseline_by_ref_id = {baseline_ref_id: baseline_with_target}
    v2_ref = v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    assessment = assessment_of(
        [v2_ref],
        dispositions={
            v2_ref.unified_reference_id: PromotionDisposition.READY_FOR_CONTROLLED_REVIEW
        },
    )
    result = compare_group_to_baseline(
        member_assessment_reference_ids=[v2_ref.unified_reference_id],
        group_program="CALLER",
        group_target="WS-X",
        group_output_literal=None,
        assessment=assessment,
        baseline_reference_id_by_assessment_reference_id={},
        baseline_candidates_by_reference_id=baseline_by_ref_id,
    )
    assert result.comparison_to_v1 == UnifiedShadowComparisonKind.NOT_IN_BASELINE
    assert result.conflicting_baseline_reference_ids == []


def test_structural_conflict_never_fires_when_target_differs() -> None:
    """Auditoria de cierre, Parte 4: mismo `program`, targets DISTINTOS
    (ambos no-nulos) nunca fabrica un conflicto -- la verificacion
    exige igualdad exacta de target, nunca semejanza."""
    baseline_candidates, _ = _baseline_setup()
    baseline_ref_id = baseline_candidates[0].baseline_reference_id
    baseline_with_target = baseline_candidates[0].model_copy(update={"target": "WS-OTHER"})
    baseline_by_ref_id = {baseline_ref_id: baseline_with_target}
    v2_ref = v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    assessment = assessment_of(
        [v2_ref],
        dispositions={
            v2_ref.unified_reference_id: PromotionDisposition.READY_FOR_CONTROLLED_REVIEW
        },
    )
    result = compare_group_to_baseline(
        member_assessment_reference_ids=[v2_ref.unified_reference_id],
        group_program="CALLER",
        group_target="WS-X",
        group_output_literal="R999",
        assessment=assessment,
        baseline_reference_id_by_assessment_reference_id={},
        baseline_candidates_by_reference_id=baseline_by_ref_id,
    )
    assert result.comparison_to_v1 == UnifiedShadowComparisonKind.NOT_IN_BASELINE
    assert result.conflicting_baseline_reference_ids == []


def test_conflict_takes_priority_over_exact_match() -> None:
    baseline_candidates, baseline_by_ref_id = _baseline_setup()
    baseline_ref_id = baseline_candidates[0].baseline_reference_id
    v1_ref = v1_reference(reference_id="unified::v1::a", source_candidate_id="candidate::1")
    v2_ref = v2_reference(reference_id="unified::v2::a", source_candidate_id="v2::1")
    assessment = assessment_of(
        [v1_ref, v2_ref],
        dispositions={
            v1_ref.unified_reference_id: PromotionDisposition.BASELINE_V1,
            v2_ref.unified_reference_id: PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
        },
        exact_match_pairs=[(v1_ref.unified_reference_id, v2_ref.unified_reference_id)],
    )
    conflict = CandidateConflict(
        conflict_id="conflict::1",
        reference_ids=sorted([v1_ref.unified_reference_id, v2_ref.unified_reference_id]),
        conflict_type=CandidateConflictType.SAME_TARGET_CONTRADICTORY_OUTPUT,
        reason="fixture: conflict",
    )
    assessment = assessment.model_copy(update={"conflicts": [conflict]})
    result = compare_group_to_baseline(
        member_assessment_reference_ids=[v2_ref.unified_reference_id],
        group_program="CALLER",
        group_target="WS-X",
        group_output_literal="R001",
        assessment=assessment,
        baseline_reference_id_by_assessment_reference_id={
            v1_ref.unified_reference_id: baseline_ref_id
        },
        baseline_candidates_by_reference_id=baseline_by_ref_id,
    )
    assert result.comparison_to_v1 == UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE
