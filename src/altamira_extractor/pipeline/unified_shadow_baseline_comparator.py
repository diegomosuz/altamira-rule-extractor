"""Comparador PURO de un grupo shadow contra el baseline V1 (Fase 11 de
la ampliacion semantica, `feat/unified-candidate-artifact-shadow`).

Usa como evidencia PREFERENTE lo YA CALCULADO por Fase 9
(`CandidateRelation.relation_kind`/`CandidateConflict`) -- nunca
recalcula semejanzas aproximadas. La UNICA verificacion estructural
adicional (no aproximada, igual de demostrable que la de Fase 9) es
"mismo `(program, target)` con `output_literal` distinto", identica en
espiritu a `CandidateConflictType.SAME_TARGET_CONTRADICTORY_OUTPUT` de
Fase 9 -- nunca una heuristica nueva ni una semejanza de texto."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessmentArtifact,
    CandidateRelationKind,
    CandidateSource,
    SourceAvailability,
)
from ..contracts.unified_candidates_shadow import (
    UnifiedBaselineCandidateReference,
    UnifiedShadowComparisonKind,
)


class BaselineComparisonResult:
    __slots__ = (
        "comparison_to_v1",
        "exact_baseline_reference_ids",
        "related_baseline_reference_ids",
        "conflicting_baseline_reference_ids",
    )

    def __init__(
        self,
        *,
        comparison_to_v1: UnifiedShadowComparisonKind,
        exact_baseline_reference_ids: Iterable[str] = (),
        related_baseline_reference_ids: Iterable[str] = (),
        conflicting_baseline_reference_ids: Iterable[str] = (),
    ) -> None:
        self.comparison_to_v1 = comparison_to_v1
        self.exact_baseline_reference_ids = sorted(set(exact_baseline_reference_ids))
        self.related_baseline_reference_ids = sorted(set(related_baseline_reference_ids))
        self.conflicting_baseline_reference_ids = sorted(set(conflicting_baseline_reference_ids))


def compare_group_to_baseline(
    *,
    member_assessment_reference_ids: Sequence[str],
    group_program: str,
    group_target: str | None,
    group_output_literal: str | None,
    assessment: CandidatePromotionAssessmentArtifact,
    baseline_reference_id_by_assessment_reference_id: Mapping[str, str],
    baseline_candidates_by_reference_id: Mapping[str, UnifiedBaselineCandidateReference],
) -> BaselineComparisonResult:
    """Punto de entrada puro. Nunca muta `assessment`. `member_
    assessment_reference_ids` son los `unified_reference_id` (Fase 9)
    de TODOS los miembros del grupo."""
    v1_availability = assessment.source_availability.get(
        CandidateSource.V1, SourceAvailability.NOT_AVAILABLE
    )
    if v1_availability != SourceAvailability.AVAILABLE:
        return BaselineComparisonResult(comparison_to_v1=UnifiedShadowComparisonKind.NOT_EVALUATED)

    member_ids = set(member_assessment_reference_ids)
    v1_reference_ids = set(baseline_reference_id_by_assessment_reference_id)

    # Acumuladores keyed por assessment reference_id (Fase 9): mapeados a
    # baseline_reference_id al final, nunca mezclados con el otro
    # namespace (ver acumulador `conflicting_baseline_ids_direct`
    # abajo, ya keyed por baseline_reference_id).
    exact_v1_reference_ids: set[str] = set()
    related_v1_reference_ids: set[str] = set()
    conflicting_v1_reference_ids: set[str] = set()

    for relation in assessment.relations:
        pair = {relation.left_reference_id, relation.right_reference_id}
        if not (pair & member_ids):
            continue
        v1_side = pair & v1_reference_ids
        if not v1_side:
            continue
        if relation.relation_kind == CandidateRelationKind.EXACT_MATCH:
            exact_v1_reference_ids.update(v1_side)
        elif relation.relation_kind == CandidateRelationKind.RELATED:
            related_v1_reference_ids.update(v1_side)
        elif relation.relation_kind == CandidateRelationKind.CONFLICT:
            conflicting_v1_reference_ids.update(v1_side)

    for conflict in assessment.conflicts:
        conflict_ids = set(conflict.reference_ids)
        if conflict_ids & member_ids:
            conflicting_v1_reference_ids.update(conflict_ids & v1_reference_ids)

    exact_baseline_ids = {
        baseline_reference_id_by_assessment_reference_id[rid] for rid in exact_v1_reference_ids
    }
    related_baseline_ids = {
        baseline_reference_id_by_assessment_reference_id[rid] for rid in related_v1_reference_ids
    }
    conflicting_baseline_ids = {
        baseline_reference_id_by_assessment_reference_id[rid]
        for rid in conflicting_v1_reference_ids
    }

    # Verificacion estructural adicional, NO aproximada: mismo
    # (program, target) con output_literal distinto -- misma naturaleza
    # que `SAME_TARGET_CONTRADICTORY_OUTPUT` de Fase 9. Ya keyed por
    # baseline_reference_id directamente (nunca requiere el mapeo de
    # arriba).
    if group_target is not None and group_output_literal is not None:
        for baseline_reference_id, baseline in baseline_candidates_by_reference_id.items():
            if (
                baseline.program == group_program
                and baseline.target == group_target
                and baseline.output_literal is not None
                and baseline.output_literal != group_output_literal
            ):
                conflicting_baseline_ids.add(baseline_reference_id)

    if conflicting_baseline_ids:
        return BaselineComparisonResult(
            comparison_to_v1=UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE,
            conflicting_baseline_reference_ids=conflicting_baseline_ids,
        )
    if exact_baseline_ids:
        return BaselineComparisonResult(
            comparison_to_v1=UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH,
            exact_baseline_reference_ids=exact_baseline_ids,
        )
    if related_baseline_ids:
        return BaselineComparisonResult(
            comparison_to_v1=UnifiedShadowComparisonKind.RELATED_TO_BASELINE,
            related_baseline_reference_ids=related_baseline_ids,
        )
    return BaselineComparisonResult(comparison_to_v1=UnifiedShadowComparisonKind.NOT_IN_BASELINE)
