"""Analizador PURO principal del catalogo unificado de candidatos y
evaluacion de promocion (Fase 9 de la ampliacion semantica,
`feat/unified-candidate-promotion-assessment`).

Orquesta, en orden: (1) adaptar las tres fuentes
(`candidate_source_adapters.py`) -- nunca las modifica; (2) ordenar
referencias deterministicamente; (3) detectar conflictos
(`candidate_conflict_analyzer.py`) -- ANTES que las relaciones, para que
ningun par conflictivo se reclasifique como coincidencia; (4) construir
relaciones (`candidate_relation_analyzer.py`), reconciliando los pares
ya conflictivos; (5) aplicar la politica de promocion
(`candidate_promotion_policy.py`) a cada referencia; (6) reconciliar el
summary; (7) producir el artefacto final.

Puro: sin filesystem, sin Neo4j, sin LLM, nunca muta ninguno de sus
argumentos, deterministico (mismo conjunto de artefactos de entrada
siempre produce el mismo `CandidatePromotionAssessmentArtifact`)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..contracts.candidate import CandidateArtifact
from ..contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessment,
    CandidatePromotionAssessmentArtifact,
    CandidatePromotionAssessmentSummary,
    CandidateRelation,
    CandidateRelationKind,
    CandidateSource,
    PromotionDisposition,
    SourceAvailability,
    UnifiedCandidateReference,
    UnifiedRuleFamily,
)
from ..contracts.interprocedural_rule_candidates import InterproceduralRuleCandidatesArtifact
from ..contracts.v2_shadow_candidates import V2ShadowCandidatesArtifact
from .candidate_conflict_analyzer import analyze_candidate_conflicts, conflicting_pairs
from .candidate_promotion_policy import evaluate_candidate
from .candidate_relation_analyzer import build_candidate_relations
from .candidate_source_adapters import (
    adapt_interprocedural_candidates,
    adapt_v1_candidates,
    adapt_v2_candidates,
)

ANALYZER_VERSION = "1.0"


def _build_summary(
    references: Sequence[UnifiedCandidateReference],
    relations: Sequence[CandidateRelation],
    conflict_count: int,
    assessments: Sequence[CandidatePromotionAssessment],
    source_availability: Mapping[CandidateSource, SourceAvailability],
) -> CandidatePromotionAssessmentSummary:
    counts_by_source: dict[CandidateSource, int] = {}
    counts_by_rule_family: dict[UnifiedRuleFamily, int] = {}
    for reference in references:
        counts_by_source[reference.source] = counts_by_source.get(reference.source, 0) + 1
        counts_by_rule_family[reference.rule_family] = (
            counts_by_rule_family.get(reference.rule_family, 0) + 1
        )

    exact_match_count = sum(
        1 for relation in relations if relation.relation_kind == CandidateRelationKind.EXACT_MATCH
    )
    related_count = sum(
        1 for relation in relations if relation.relation_kind == CandidateRelationKind.RELATED
    )

    counts_by_disposition: dict[PromotionDisposition, int] = {}
    for assessment in assessments:
        counts_by_disposition[assessment.disposition] = (
            counts_by_disposition.get(assessment.disposition, 0) + 1
        )

    return CandidatePromotionAssessmentSummary(
        v1_candidate_count=counts_by_source.get(CandidateSource.V1, 0),
        v2_candidate_count=counts_by_source.get(CandidateSource.V2, 0),
        interprocedural_candidate_count=counts_by_source.get(CandidateSource.INTERPROCEDURAL, 0),
        unified_reference_count=len(references),
        exact_match_relation_count=exact_match_count,
        related_relation_count=related_count,
        conflict_count=conflict_count,
        baseline_v1_count=counts_by_disposition.get(PromotionDisposition.BASELINE_V1, 0),
        already_covered_count=counts_by_disposition.get(PromotionDisposition.ALREADY_COVERED, 0),
        ready_for_controlled_review_count=counts_by_disposition.get(
            PromotionDisposition.READY_FOR_CONTROLLED_REVIEW, 0
        ),
        review_required_count=counts_by_disposition.get(
            PromotionDisposition.REVIEW_REQUIRED, 0
        ),
        blocked_count=counts_by_disposition.get(PromotionDisposition.BLOCKED, 0),
        conflicting_count=counts_by_disposition.get(PromotionDisposition.CONFLICTING, 0),
        not_evaluated_count=counts_by_disposition.get(PromotionDisposition.NOT_EVALUATED, 0),
        counts_by_source=counts_by_source,
        counts_by_rule_family=counts_by_rule_family,
        counts_by_disposition=counts_by_disposition,
        source_availability=dict(source_availability),
    )


def analyze_candidate_promotion_assessment(
    *,
    v1_candidates: CandidateArtifact | None,
    v2_candidates: V2ShadowCandidatesArtifact | None,
    interprocedural_candidates: InterproceduralRuleCandidatesArtifact | None,
    source_availability: Mapping[CandidateSource, SourceAvailability],
    source_artifact_hash_by_source: Mapping[CandidateSource, str],
    run_id: str,
    source_package_hash: str,
    source_artifact_hashes: Mapping[str, str],
) -> CandidatePromotionAssessmentArtifact:
    """Punto de entrada del analizador puro (Fase 9). Determinista:
    misma entrada siempre produce el mismo
    `CandidatePromotionAssessmentArtifact`."""
    v1_references = adapt_v1_candidates(
        v1_candidates,
        source_artifact_hash=source_artifact_hash_by_source.get(
            CandidateSource.V1, source_package_hash
        ),
    )
    v2_references = adapt_v2_candidates(
        v2_candidates,
        source_artifact_hash=source_artifact_hash_by_source.get(
            CandidateSource.V2, source_package_hash
        ),
    )
    interprocedural_references = adapt_interprocedural_candidates(
        interprocedural_candidates,
        source_artifact_hash=source_artifact_hash_by_source.get(
            CandidateSource.INTERPROCEDURAL, source_package_hash
        ),
    )

    all_references = sorted(
        [*v1_references, *v2_references, *interprocedural_references],
        key=lambda reference: reference.unified_reference_id,
    )

    conflicts = analyze_candidate_conflicts(all_references)
    conflict_pairs = conflicting_pairs(conflicts)

    relations = build_candidate_relations(
        v1_references=v1_references,
        v2_references=v2_references,
        interprocedural_references=interprocedural_references,
        v2_artifact=v2_candidates,
        interprocedural_artifact=interprocedural_candidates,
        conflicting_pairs=conflict_pairs,
    )

    reference_by_id = {reference.unified_reference_id: reference for reference in all_references}
    exact_match_by_reference: dict[str, set[str]] = {}
    related_by_reference: dict[str, set[str]] = {}
    for relation in relations:
        if relation.relation_kind == CandidateRelationKind.EXACT_MATCH:
            exact_match_by_reference.setdefault(relation.left_reference_id, set()).add(
                relation.right_reference_id
            )
            exact_match_by_reference.setdefault(relation.right_reference_id, set()).add(
                relation.left_reference_id
            )
        elif relation.relation_kind == CandidateRelationKind.RELATED:
            related_by_reference.setdefault(relation.left_reference_id, set()).add(
                relation.right_reference_id
            )
            related_by_reference.setdefault(relation.right_reference_id, set()).add(
                relation.left_reference_id
            )

    conflict_by_reference: dict[str, set[str]] = {}
    for conflict in conflicts:
        for reference_id in conflict.reference_ids:
            conflict_by_reference.setdefault(reference_id, set()).add(conflict.conflict_id)

    assessments = sorted(
        (
            evaluate_candidate(
                reference,
                reference_by_id=reference_by_id,
                exact_match_reference_ids=sorted(
                    exact_match_by_reference.get(reference.unified_reference_id, set())
                ),
                related_reference_ids=sorted(
                    related_by_reference.get(reference.unified_reference_id, set())
                ),
                conflict_ids=sorted(
                    conflict_by_reference.get(reference.unified_reference_id, set())
                ),
                source_availability=source_availability,
            )
            for reference in all_references
        ),
        key=lambda assessment: assessment.assessment_id,
    )

    summary = _build_summary(
        all_references, relations, len(conflicts), assessments, source_availability
    )

    return CandidatePromotionAssessmentArtifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        source_artifact_hashes=dict(source_artifact_hashes),
        source_availability=dict(source_availability),
        summary=summary,
        candidate_references=all_references,
        relations=relations,
        conflicts=conflicts,
        assessments=assessments,
        diagnostics=[],
    )
