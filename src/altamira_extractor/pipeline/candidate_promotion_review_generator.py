"""Generador PURO del paquete de revision humana (Fase 10 de la
ampliacion semantica, `feat/controlled-candidate-promotion-plan`).

Recibe un `CandidatePromotionAssessmentArtifact` (Fase 9) ya validado y
produce, sin filesystem, sin Neo4j, sin LLM y sin mutar el assessment,
un `CandidatePromotionReviewPackage`: UNA `CandidateReviewItem` por cada
`CandidatePromotionAssessment` del assessment de origen -- nunca
reinterpreta `PromotionCriterionResult`, nunca recalcula relaciones ni
conflictos (se preservan tal cual, misma identidad de valores).

`ReviewEligibility` se deriva EXCLUSIVAMENTE de `PromotionDisposition`
via `_ELIGIBILITY_BY_DISPOSITION`, unica fuente de verdad (Fase 10,
Parte 2):

- `BASELINE_V1` -> `BASELINE` (nunca requiere decision, nunca se
  promueve).
- `ALREADY_COVERED` -> `ALREADY_COVERED` (nunca debe agregarse otra
  vez; aparece solo para trazabilidad).
- `READY_FOR_CONTROLLED_REVIEW` -> `ELIGIBLE` (unica disposition que
  puede recibir `APPROVE_FOR_SHADOW_PROMOTION`).
- `REVIEW_REQUIRED` -> `NOT_ELIGIBLE` (solo `REJECT`/`DEFER`).
- `BLOCKED`/`CONFLICTING` -> `BLOCKED` (solo `DEFER`/`REJECT`, nunca
  `APPROVE`).
- `NOT_EVALUATED` -> `NOT_ELIGIBLE` (unicamente `DEFER`: la ausencia de
  evidencia nunca se convierte en un `REJECT` automatico)."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from ..contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessment,
    CandidatePromotionAssessmentArtifact,
    CandidateSource,
    PromotionDisposition,
    UnifiedCandidateReference,
    UnifiedRuleFamily,
)
from ..contracts.candidate_promotion_review import (
    CandidatePromotionReviewPackage,
    CandidatePromotionReviewPackageSummary,
    CandidateReviewItem,
    ReviewEligibility,
)
from .errors import CandidatePromotionReviewError

GENERATOR_VERSION = "1.0"

_ELIGIBILITY_BY_DISPOSITION: dict[PromotionDisposition, ReviewEligibility] = {
    PromotionDisposition.BASELINE_V1: ReviewEligibility.BASELINE,
    PromotionDisposition.ALREADY_COVERED: ReviewEligibility.ALREADY_COVERED,
    PromotionDisposition.READY_FOR_CONTROLLED_REVIEW: ReviewEligibility.ELIGIBLE,
    PromotionDisposition.REVIEW_REQUIRED: ReviewEligibility.NOT_ELIGIBLE,
    PromotionDisposition.BLOCKED: ReviewEligibility.BLOCKED,
    PromotionDisposition.CONFLICTING: ReviewEligibility.BLOCKED,
    PromotionDisposition.NOT_EVALUATED: ReviewEligibility.NOT_ELIGIBLE,
}


def _digest(*parts: str) -> str:
    canonical = "\x1f".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def review_item_id_for(reference_id: str) -> str:
    """Unica funcion que genera `review_item_id` -- determinista
    (SHA-256, nunca UUID/timestamp/`hash()` de Python)."""
    return f"review::{_digest(reference_id)}"


def _sorted_unique(values: Sequence[str]) -> list[str]:
    return sorted(set(values))


def _eligibility_for(disposition: PromotionDisposition) -> ReviewEligibility:
    return _ELIGIBILITY_BY_DISPOSITION[disposition]


def _review_reasons_for(
    *, disposition: PromotionDisposition, eligibility: ReviewEligibility
) -> list[str]:
    """Motivo canonico, unico y determinista -- nunca texto libre."""
    return [f"ELIGIBILITY_{eligibility.value}_FROM_DISPOSITION_{disposition.value}"]


def _build_review_item(
    assessment: CandidatePromotionAssessment,
    reference: UnifiedCandidateReference,
) -> CandidateReviewItem:
    if reference.program is None:
        raise CandidatePromotionReviewError(
            f"referencia {reference.unified_reference_id!r} no tiene program resuelto: "
            "no se puede construir un CandidateReviewItem sin fabricar un valor"
        )
    eligibility = _eligibility_for(assessment.disposition)
    return CandidateReviewItem(
        review_item_id=review_item_id_for(reference.unified_reference_id),
        assessment_id=assessment.assessment_id,
        reference_id=reference.unified_reference_id,
        source=reference.source,
        source_candidate_id=reference.source_candidate_id,
        rule_family=reference.rule_family,
        disposition=assessment.disposition,
        eligibility=eligibility,
        program=reference.program,
        paragraph=reference.paragraph,
        decision_id=reference.decision_id,
        call_site_id=reference.call_site_id,
        target=reference.target,
        input_literal=reference.input_literal,
        output_literal=reference.output_literal,
        evidence_ids=_sorted_unique(reference.evidence_ids),
        exact_match_reference_ids=_sorted_unique(assessment.exact_match_reference_ids),
        related_reference_ids=_sorted_unique(assessment.related_reference_ids),
        conflict_ids=_sorted_unique(assessment.conflict_ids),
        criteria=list(assessment.criteria),
        recommended_action=assessment.recommended_action,
        review_reasons=_review_reasons_for(
            disposition=assessment.disposition, eligibility=eligibility
        ),
        provenance_references=_sorted_unique(reference.provenance_references),
        diagnostics=_sorted_unique(reference.diagnostics),
    )


def _build_summary(items: Sequence[CandidateReviewItem]) -> CandidatePromotionReviewPackageSummary:
    counts_by_source: dict[CandidateSource, int] = {}
    counts_by_family: dict[UnifiedRuleFamily, int] = {}
    counts_by_disposition: dict[PromotionDisposition, int] = {}
    counts_by_eligibility: dict[ReviewEligibility, int] = {}
    for item in items:
        counts_by_source[item.source] = counts_by_source.get(item.source, 0) + 1
        counts_by_family[item.rule_family] = counts_by_family.get(item.rule_family, 0) + 1
        counts_by_disposition[item.disposition] = counts_by_disposition.get(item.disposition, 0) + 1
        counts_by_eligibility[item.eligibility] = counts_by_eligibility.get(item.eligibility, 0) + 1
    return CandidatePromotionReviewPackageSummary(
        total_items=len(items),
        eligible_count=counts_by_eligibility.get(ReviewEligibility.ELIGIBLE, 0),
        not_eligible_count=counts_by_eligibility.get(ReviewEligibility.NOT_ELIGIBLE, 0),
        already_covered_count=counts_by_eligibility.get(ReviewEligibility.ALREADY_COVERED, 0),
        baseline_count=counts_by_eligibility.get(ReviewEligibility.BASELINE, 0),
        blocked_count=counts_by_eligibility.get(ReviewEligibility.BLOCKED, 0),
        counts_by_source=counts_by_source,
        counts_by_family=counts_by_family,
        counts_by_disposition=counts_by_disposition,
        counts_by_eligibility=counts_by_eligibility,
    )


def generate_candidate_promotion_review_package(
    assessment: CandidatePromotionAssessmentArtifact,
) -> CandidatePromotionReviewPackage:
    """Punto de entrada puro. Nunca muta `assessment`. Determinista:
    mismo assessment de entrada siempre produce el mismo
    `CandidatePromotionReviewPackage` (mismos bytes JSON)."""
    reference_by_id = {ref.unified_reference_id: ref for ref in assessment.candidate_references}
    items = sorted(
        (
            _build_review_item(
                candidate_assessment, reference_by_id[candidate_assessment.reference_id]
            )
            for candidate_assessment in assessment.assessments
        ),
        key=lambda item: item.review_item_id,
    )
    # `to_stable_json()` (claves ordenadas), nunca `model_dump_json()`: el
    # assessment (o este mismo review package) puede terminar
    # persistido/releido de disco via `atomic_write_json` (que serializa
    # con `to_stable_json()`); hashear con un metodo sensible al orden de
    # insercion de un `dict` produciria un hash distinto tras ese
    # roundtrip, aunque el contenido sea identico.
    assessment_artifact_hash = hashlib.sha256(
        assessment.to_stable_json().encode("utf-8")
    ).hexdigest()
    return CandidatePromotionReviewPackage(
        run_id=assessment.run_id,
        source_package_hash=assessment.source_package_hash,
        assessment_artifact_hash=assessment_artifact_hash,
        assessment_policy_version=assessment.policy_version,
        source_artifact_hashes=dict(assessment.source_artifact_hashes),
        summary=_build_summary(items),
        review_items=items,
        diagnostics=_sorted_unique(assessment.diagnostics),
    )
