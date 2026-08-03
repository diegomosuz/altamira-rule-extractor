"""Politica de evaluacion de preparacion para promocion, DECLARATIVA,
DETERMINISTICA y versionada (`policy_version="1.0"`) -- Fase 9 de la
ampliacion semantica, `feat/unified-candidate-promotion-assessment`.

Nunca promueve automaticamente ningun candidato: produce exclusivamente
un `CandidatePromotionAssessment` diagnostico. Deliberadamente SIN
puntuacion numerica de confianza -- cada criterio es un juicio discreto
(`PromotionCriterionStatus`) sobre un hecho estructural ya demostrado.

Reglas (verbatim del runbook de Fase 9):

- V1: `disposition=BASELINE_V1` siempre, nunca se evalua para
  promocion.
- V2/INTERPROCEDURAL: `ALREADY_COVERED` cuando existe `EXACT_MATCH` con
  V1 y no hay conflicto; `READY_FOR_CONTROLLED_REVIEW` cuando el
  candidato es deterministico, con familia soportada, provenance
  completo, target y output_literal presentes, cero barreras, cero
  conflictos, V1 y la fuente de corroboracion relevante estuvieron
  disponibles, y existe corroboracion independiente exacta de una
  fuente no-V1; `REVIEW_REQUIRED` cuando el candidato es solido pero
  falta esa corroboracion (incluye "interprocedural-only"); `BLOCKED`
  cuando el candidato en si mismo (o su fuente) esta comprometido;
  `CONFLICTING` cuando existe al menos un conflicto; `NOT_EVALUATED`
  UNICAMENTE cuando V1 -- la unica fuente cuya ausencia impide siquiera
  decidir ALREADY_COVERED vs. el resto -- no estuvo disponible (nunca
  se confunde con REVIEW_REQUIRED/BLOCKED)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from ..contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessment,
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

POLICY_VERSION = "1.0"

_ALL_CRITERIA: tuple[PromotionCriterionKind, ...] = tuple(PromotionCriterionKind)

# Criterios cuyo FAIL bloquea (BLOCKED) al candidato en si mismo -- nunca
# incluye V1_COMPARISON_AVAILABLE/INDEPENDENT_CORROBORATION, que tienen
# semantica propia (NOT_EVALUATED vs. REVIEW_REQUIRED, ver docstring).
_BLOCKING_QUALITY_CRITERIA = (
    PromotionCriterionKind.DETERMINISTIC_SUPPORT,
    PromotionCriterionKind.COMPLETE_PROVENANCE,
    PromotionCriterionKind.TARGET_AVAILABLE,
    PromotionCriterionKind.OUTPUT_LITERAL_AVAILABLE,
    PromotionCriterionKind.NO_BARRIERS,
    PromotionCriterionKind.SUPPORTED_RULE_FAMILY,
    PromotionCriterionKind.SOURCE_ARTIFACT_VALID,
)


def _digest(*parts: str) -> str:
    canonical = "\x1f".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def assessment_id_for(reference_id: str) -> str:
    return f"assessment::{_digest(reference_id)}"


def _corroboration_source(reference_source: CandidateSource) -> CandidateSource:
    """V2 se corrobora con INTERPROCEDURAL y viceversa -- V1 nunca es la
    fuente de corroboracion independiente (su match ya se evalua por
    separado como `ALREADY_COVERED`, ver `PromotionCriterionKind.
    INDEPENDENT_CORROBORATION`)."""
    if reference_source == CandidateSource.V2:
        return CandidateSource.INTERPROCEDURAL
    return CandidateSource.V2


def _result(
    criterion: PromotionCriterionKind,
    status: PromotionCriterionStatus,
    reason: str,
    supporting_reference_ids: Sequence[str] = (),
) -> PromotionCriterionResult:
    return PromotionCriterionResult(
        criterion=criterion,
        status=status,
        reason=reason,
        supporting_reference_ids=sorted(set(supporting_reference_ids)),
    )


def _availability_criterion_status(
    availability: SourceAvailability,
) -> PromotionCriterionStatus:
    if availability == SourceAvailability.AVAILABLE:
        return PromotionCriterionStatus.PASS
    if availability == SourceAvailability.NOT_AVAILABLE:
        return PromotionCriterionStatus.NOT_EVALUATED
    return PromotionCriterionStatus.FAIL


def _evaluate_v1_reference(reference: UnifiedCandidateReference) -> CandidatePromotionAssessment:
    criteria = [
        _result(
            criterion,
            PromotionCriterionStatus.NOT_APPLICABLE,
            "V1 es la fuente baseline productiva: nunca se evalua para promocion "
            "(disposition=BASELINE_V1 siempre).",
        )
        for criterion in _ALL_CRITERIA
    ]
    disposition = PromotionDisposition.BASELINE_V1
    return CandidatePromotionAssessment(
        assessment_id=assessment_id_for(reference.unified_reference_id),
        reference_id=reference.unified_reference_id,
        disposition=disposition,
        criteria=criteria,
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=[],
        recommended_action=recommended_action_for(disposition),
        diagnostics=[],
    )


def evaluate_candidate(
    reference: UnifiedCandidateReference,
    *,
    reference_by_id: Mapping[str, UnifiedCandidateReference],
    exact_match_reference_ids: Sequence[str],
    related_reference_ids: Sequence[str],
    conflict_ids: Sequence[str],
    source_availability: Mapping[CandidateSource, SourceAvailability],
) -> CandidatePromotionAssessment:
    """Punto de entrada puro de la politica. `reference.source == V1`
    siempre produce `BASELINE_V1` sin excepcion; cualquier otra fuente
    sigue el arbol de decision documentado en el modulo."""
    if reference.source == CandidateSource.V1:
        return _evaluate_v1_reference(reference)

    exact_match_reference_ids = sorted(set(exact_match_reference_ids))
    related_reference_ids = sorted(set(related_reference_ids))
    conflict_ids = sorted(set(conflict_ids))

    criteria: list[PromotionCriterionResult] = []

    deterministic_status = (
        PromotionCriterionStatus.PASS
        if reference.original_support == "DETERMINISTIC"
        else PromotionCriterionStatus.FAIL
    )
    criteria.append(
        _result(
            PromotionCriterionKind.DETERMINISTIC_SUPPORT,
            deterministic_status,
            f"original_support={reference.original_support!r}",
        )
    )

    provenance_status = (
        PromotionCriterionStatus.PASS
        if reference.evidence_ids or reference.provenance_references
        else PromotionCriterionStatus.FAIL
    )
    criteria.append(
        _result(
            PromotionCriterionKind.COMPLETE_PROVENANCE,
            provenance_status,
            f"evidence_ids={len(reference.evidence_ids)}, "
            f"provenance_references={len(reference.provenance_references)}",
        )
    )

    criteria.append(
        _result(
            PromotionCriterionKind.TARGET_AVAILABLE,
            PromotionCriterionStatus.PASS
            if reference.target is not None
            else PromotionCriterionStatus.FAIL,
            f"target={reference.target!r}",
        )
    )

    criteria.append(
        _result(
            PromotionCriterionKind.OUTPUT_LITERAL_AVAILABLE,
            PromotionCriterionStatus.PASS
            if reference.output_literal is not None
            else PromotionCriterionStatus.FAIL,
            f"output_literal={reference.output_literal!r}",
        )
    )

    criteria.append(
        _result(
            PromotionCriterionKind.NO_BARRIERS,
            PromotionCriterionStatus.PASS
            if not reference.barrier_codes
            else PromotionCriterionStatus.FAIL,
            f"barrier_codes={reference.barrier_codes}",
        )
    )

    criteria.append(
        _result(
            PromotionCriterionKind.NO_CONFLICTS,
            PromotionCriterionStatus.PASS
            if not conflict_ids
            else PromotionCriterionStatus.FAIL,
            f"conflict_ids={conflict_ids}",
            supporting_reference_ids=conflict_ids,
        )
    )

    criteria.append(
        _result(
            PromotionCriterionKind.SUPPORTED_RULE_FAMILY,
            PromotionCriterionStatus.PASS
            if reference.rule_family != UnifiedRuleFamily.UNKNOWN
            else PromotionCriterionStatus.FAIL,
            f"rule_family={reference.rule_family.value}",
        )
    )

    own_availability = source_availability.get(reference.source, SourceAvailability.NOT_AVAILABLE)
    criteria.append(
        _result(
            PromotionCriterionKind.SOURCE_ARTIFACT_VALID,
            PromotionCriterionStatus.PASS
            if own_availability == SourceAvailability.AVAILABLE
            else PromotionCriterionStatus.FAIL,
            f"source_availability[{reference.source.value}]={own_availability.value}",
        )
    )

    v1_availability = source_availability.get(CandidateSource.V1, SourceAvailability.NOT_AVAILABLE)
    v1_status = _availability_criterion_status(v1_availability)
    criteria.append(
        _result(
            PromotionCriterionKind.V1_COMPARISON_AVAILABLE,
            v1_status,
            f"source_availability[V1]={v1_availability.value}",
        )
    )

    exact_match_v1_ids = sorted(
        rid
        for rid in exact_match_reference_ids
        if reference_by_id[rid].source == CandidateSource.V1
    )

    corroboration_source = _corroboration_source(reference.source)
    corroboration_availability = source_availability.get(
        corroboration_source, SourceAvailability.NOT_AVAILABLE
    )
    corroboration_status = _availability_criterion_status(corroboration_availability)
    exact_match_corroboration_ids = sorted(
        rid
        for rid in exact_match_reference_ids
        if reference_by_id[rid].source == corroboration_source
    )

    if reference.source == CandidateSource.V2:
        criteria.append(
            _result(
                PromotionCriterionKind.V2_COMPARISON_AVAILABLE,
                PromotionCriterionStatus.NOT_APPLICABLE,
                "V2 nunca se corrobora contra si mismo.",
            )
        )
        criteria.append(
            _result(
                PromotionCriterionKind.INTERPROCEDURAL_COMPARISON_AVAILABLE,
                corroboration_status,
                f"source_availability[INTERPROCEDURAL]={corroboration_availability.value}",
            )
        )
    else:
        criteria.append(
            _result(
                PromotionCriterionKind.V2_COMPARISON_AVAILABLE,
                corroboration_status,
                f"source_availability[V2]={corroboration_availability.value}",
            )
        )
        criteria.append(
            _result(
                PromotionCriterionKind.INTERPROCEDURAL_COMPARISON_AVAILABLE,
                PromotionCriterionStatus.NOT_APPLICABLE,
                "INTERPROCEDURAL nunca se corrobora contra si mismo.",
            )
        )

    if corroboration_availability == SourceAvailability.AVAILABLE:
        corroboration_result_status = (
            PromotionCriterionStatus.PASS
            if exact_match_corroboration_ids
            else PromotionCriterionStatus.FAIL
        )
    elif corroboration_availability == SourceAvailability.NOT_AVAILABLE:
        corroboration_result_status = PromotionCriterionStatus.NOT_EVALUATED
    else:
        corroboration_result_status = PromotionCriterionStatus.FAIL
    criteria.append(
        _result(
            PromotionCriterionKind.INDEPENDENT_CORROBORATION,
            corroboration_result_status,
            f"corroboration_source={corroboration_source.value}, "
            f"availability={corroboration_availability.value}, "
            f"exact_match_ids={exact_match_corroboration_ids}",
            supporting_reference_ids=exact_match_corroboration_ids,
        )
    )

    status_by_criterion = {result.criterion: result.status for result in criteria}
    diagnostics: list[str] = []

    if conflict_ids:
        disposition = PromotionDisposition.CONFLICTING
    elif any(
        status_by_criterion[criterion] == PromotionCriterionStatus.FAIL
        for criterion in _BLOCKING_QUALITY_CRITERIA
    ):
        disposition = PromotionDisposition.BLOCKED
    elif v1_status == PromotionCriterionStatus.FAIL:
        disposition = PromotionDisposition.BLOCKED
        diagnostics.append("V1_SOURCE_ARTIFACT_INVALID_BLOCKS_EVALUATION")
    elif status_by_criterion[
        PromotionCriterionKind.V2_COMPARISON_AVAILABLE
        if reference.source == CandidateSource.INTERPROCEDURAL
        else PromotionCriterionKind.INTERPROCEDURAL_COMPARISON_AVAILABLE
    ] == PromotionCriterionStatus.FAIL:
        disposition = PromotionDisposition.BLOCKED
        diagnostics.append(f"{corroboration_source.value}_SOURCE_ARTIFACT_INVALID_BLOCKS_EVALUATION")
    elif v1_status == PromotionCriterionStatus.NOT_EVALUATED:
        disposition = PromotionDisposition.NOT_EVALUATED
        diagnostics.append("V1_REQUIRED_TO_DECIDE_ALREADY_COVERED_UNAVAILABLE")
    elif exact_match_v1_ids:
        disposition = PromotionDisposition.ALREADY_COVERED
    elif corroboration_result_status == PromotionCriterionStatus.PASS:
        disposition = PromotionDisposition.READY_FOR_CONTROLLED_REVIEW
    else:
        disposition = PromotionDisposition.REVIEW_REQUIRED

    return CandidatePromotionAssessment(
        assessment_id=assessment_id_for(reference.unified_reference_id),
        reference_id=reference.unified_reference_id,
        disposition=disposition,
        criteria=criteria,
        exact_match_reference_ids=exact_match_reference_ids,
        related_reference_ids=related_reference_ids,
        conflict_ids=conflict_ids if disposition == PromotionDisposition.CONFLICTING else [],
        recommended_action=recommended_action_for(disposition),
        diagnostics=sorted(set(diagnostics)),
    )
