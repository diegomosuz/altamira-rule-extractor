"""Constructor PURO del plan de promocion controlada (Fase 10 de la
ampliacion semantica, `feat/controlled-candidate-promotion-plan`).

Recibe el `CandidatePromotionAssessmentArtifact` (Fase 9), el
`CandidatePromotionReviewPackage` (Fase 10, Parte 1) derivado de el, y
un `CandidatePromotionDecisionManifest` humano -- documento NO
confiable (python.md) -- y produce un `CandidatePromotionPlanArtifact`
determinista, sin filesystem, sin Neo4j, sin LLM, sin mutar ninguno de
los tres argumentos y sin promover ningun candidato.

Dos niveles de validacion (Fase 10, Parte 4):

1. **Identidad/frescura global** (`_check_global_identity`): hashes de
   `review_package_hash`/`assessment_artifact_hash`/`run_id` deben
   coincidir EXACTAMENTE entre manifest/review_package/assessment --
   cualquier discrepancia aborta la construccion completa
   (`CandidatePromotionPlanError`, nunca un plan parcial): un manifiesto
   que no corresponde al assessment/review package actuales nunca se
   procesa parcialmente.
2. **Por decision** (`_resolve_active_decision`/`_evaluate_decision`):
   cada decision se valida contra el review item que reclama
   (`review_item_id`/`assessment_id`/`reference_id`/`assessment_
   artifact_hash`) y contra la matriz de elegibilidad (Fase 10, Parte
   2/6). Una decision invalida NUNCA se ignora en silencio: se
   registra en `diagnostics` (global, para `BASELINE_V1`, la unica
   disposition que nunca acepta ninguna decision) o en el
   `CandidatePromotionPlanItem.status=INVALID_DECISION` propio (para
   el resto de las dispositions, con los campos de la decision
   preservados para trazabilidad) -- la `action` resultante SIEMPRE
   cae al valor seguro por defecto de esa disposition, nunca a lo que
   la decision invalida solicitaba.

`PromotionPlanAction.PROPOSE_SHADOW_PROMOTION` es una propuesta de
dry-run: este modulo nunca escribe un candidato nuevo, nunca contacta
V1/V2/interprocedural, nunca promueve nada realmente."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from ..contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessmentArtifact,
    CandidateSource,
    PromotionDisposition,
    UnifiedRuleFamily,
)
from ..contracts.candidate_promotion_plan import (
    CandidatePromotionPlanArtifact,
    CandidatePromotionPlanItem,
    CandidatePromotionPlanSummary,
    PromotionPlanAction,
    PromotionPlanItemStatus,
)
from ..contracts.candidate_promotion_review import (
    CandidatePromotionDecision,
    CandidatePromotionDecisionManifest,
    CandidatePromotionReviewPackage,
    CandidateReviewItem,
    ReviewDecision,
    ReviewEligibility,
)
from .errors import CandidatePromotionPlanError

PLANNER_VERSION = "1.0"

# (accion por defecto sin decision, status por defecto sin decision).
_NO_DECISION_DEFAULT: dict[
    PromotionDisposition, tuple[PromotionPlanAction, PromotionPlanItemStatus]
] = {
    PromotionDisposition.BASELINE_V1: (
        PromotionPlanAction.KEEP_BASELINE,
        PromotionPlanItemStatus.NO_DECISION_REQUIRED,
    ),
    PromotionDisposition.ALREADY_COVERED: (
        PromotionPlanAction.SKIP_ALREADY_COVERED,
        PromotionPlanItemStatus.NO_DECISION_REQUIRED,
    ),
    PromotionDisposition.READY_FOR_CONTROLLED_REVIEW: (
        PromotionPlanAction.PENDING_REVIEW,
        PromotionPlanItemStatus.PENDING_DECISION,
    ),
    PromotionDisposition.REVIEW_REQUIRED: (
        PromotionPlanAction.PENDING_REVIEW,
        PromotionPlanItemStatus.PENDING_DECISION,
    ),
    PromotionDisposition.BLOCKED: (
        PromotionPlanAction.BLOCK,
        PromotionPlanItemStatus.NO_DECISION_REQUIRED,
    ),
    PromotionDisposition.CONFLICTING: (
        PromotionPlanAction.BLOCK,
        PromotionPlanItemStatus.NO_DECISION_REQUIRED,
    ),
    PromotionDisposition.NOT_EVALUATED: (
        PromotionPlanAction.DEFER,
        PromotionPlanItemStatus.NO_DECISION_REQUIRED,
    ),
}

_ACTION_BY_VERB: dict[ReviewDecision, PromotionPlanAction] = {
    ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION: PromotionPlanAction.PROPOSE_SHADOW_PROMOTION,
    ReviewDecision.REJECT: PromotionPlanAction.REJECT,
    ReviewDecision.DEFER: PromotionPlanAction.DEFER,
}

# Verbos de decision aceptados por eligibility -- unica fuente de verdad
# de la matriz de la Parte 2/6 (nunca reinterpretada en otro lugar).
_ALLOWED_VERBS_BY_ELIGIBILITY: dict[ReviewEligibility, frozenset[ReviewDecision]] = {
    ReviewEligibility.BASELINE: frozenset(),
    ReviewEligibility.ALREADY_COVERED: frozenset({ReviewDecision.REJECT, ReviewDecision.DEFER}),
    ReviewEligibility.ELIGIBLE: frozenset(
        {
            ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
            ReviewDecision.REJECT,
            ReviewDecision.DEFER,
        }
    ),
    ReviewEligibility.NOT_ELIGIBLE: frozenset({ReviewDecision.REJECT, ReviewDecision.DEFER}),
    ReviewEligibility.BLOCKED: frozenset({ReviewDecision.REJECT, ReviewDecision.DEFER}),
}

# NOT_EVALUATED es la unica disposition NOT_ELIGIBLE que ademas prohibe
# REJECT (regla 11): "no REJECT automatico por falta de evidencia" --
# unicamente DEFER. Se distingue de REVIEW_REQUIRED (tambien NOT_ELIGIBLE
# pero SI admite REJECT) por disposition, no por eligibility.
_NOT_EVALUATED_ALLOWED_VERBS = frozenset({ReviewDecision.DEFER})


def _digest(*parts: str) -> str:
    canonical = "\x1f".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def plan_item_id_for(review_item_id: str) -> str:
    """Unica funcion que genera `plan_item_id` -- determinista
    (SHA-256, nunca UUID/timestamp/`hash()` de Python)."""
    return f"plan::{_digest(review_item_id)}"


def _sorted_unique(values: Sequence[str]) -> list[str]:
    return sorted(set(values))


def _hash_artifact(artifact: object) -> str:
    """`to_stable_json()` (claves ordenadas), nunca `model_dump_json()`:
    `review_package`/`manifest` pueden provenir de un archivo releido de
    disco (`atomic_write_json` serializa con `to_stable_json()`, y el
    manifiesto humano se hashea sobre sus propios bytes de archivo) --
    hashear con un metodo sensible al orden de insercion de un `dict`
    produciria un hash distinto de lo que el humano/servicio realmente
    escribio o leyo, aunque el contenido sea identico."""
    return hashlib.sha256(artifact.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def _allowed_verbs_for(item: CandidateReviewItem) -> frozenset[ReviewDecision]:
    if item.disposition == PromotionDisposition.NOT_EVALUATED:
        return _NOT_EVALUATED_ALLOWED_VERBS
    return _ALLOWED_VERBS_BY_ELIGIBILITY[item.eligibility]


def _resolve_active_decision(
    group: Sequence[CandidatePromotionDecision],
) -> tuple[CandidatePromotionDecision | None, list[str]]:
    """Aplica la regla 9 (maximo una decision activa por
    `review_item_id`): un grupo de un solo elemento es trivialmente
    activo. Un grupo de dos SOLO se resuelve cuando una declara
    `decision_reference` apuntando exactamente a la otra (cadena de
    supersesion explicita, auditable) -- esa referenciada queda
    `SUPERSEDED`. Cualquier otro caso (3+, o 2 sin cadena limpia) se
    rechaza INTEGRAMENTE: nunca se elige implicitamente "la ultima"."""
    if len(group) == 1:
        return group[0], []
    if len(group) == 2:
        first, second = group
        if first.decision_reference == second.decision_id and (
            second.decision_reference != first.decision_id
        ):
            return first, [f"SUPERSEDED_DECISION::{second.decision_id}"]
        if second.decision_reference == first.decision_id and (
            first.decision_reference != second.decision_id
        ):
            return second, [f"SUPERSEDED_DECISION::{first.decision_id}"]
    ids = sorted(decision.decision_id for decision in group)
    return None, [f"AMBIGUOUS_MULTIPLE_DECISIONS_FOR_REVIEW_ITEM::{'|'.join(ids)}"]


def _identity_mismatches(
    decision: CandidatePromotionDecision,
    item: CandidateReviewItem,
    *,
    assessment_artifact_hash: str,
) -> list[str]:
    reasons = []
    if decision.assessment_id != item.assessment_id:
        reasons.append("DECISION_ASSESSMENT_ID_MISMATCH")
    if decision.reference_id != item.reference_id:
        reasons.append("DECISION_REFERENCE_ID_MISMATCH")
    if decision.assessment_artifact_hash != assessment_artifact_hash:
        reasons.append("DECISION_ASSESSMENT_ARTIFACT_HASH_MISMATCH")
    return reasons


def _eligibility_incompatibility(
    decision: CandidatePromotionDecision, item: CandidateReviewItem
) -> str | None:
    if decision.decision not in _allowed_verbs_for(item):
        if decision.decision == ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION:
            return f"APPROVE_NOT_ALLOWED_FOR_ELIGIBILITY_{item.eligibility.value}"
        if decision.decision == ReviewDecision.REJECT:
            return f"REJECT_NOT_ALLOWED_FOR_DISPOSITION_{item.disposition.value}"
        return f"DECISION_NOT_ALLOWED_FOR_DISPOSITION_{item.disposition.value}"
    return None


def _build_plan_item(
    item: CandidateReviewItem,
    active_decision: CandidatePromotionDecision | None,
    *,
    assessment_artifact_hash: str,
    global_diagnostics: list[str],
) -> CandidatePromotionPlanItem:
    default_action, default_status = _NO_DECISION_DEFAULT[item.disposition]

    if active_decision is None:
        return CandidatePromotionPlanItem(
            plan_item_id=plan_item_id_for(item.review_item_id),
            review_item_id=item.review_item_id,
            assessment_id=item.assessment_id,
            reference_id=item.reference_id,
            source=item.source,
            source_candidate_id=item.source_candidate_id,
            rule_family=item.rule_family,
            assessment_disposition=item.disposition,
            eligibility=item.eligibility,
            action=default_action,
            status=default_status,
            target=item.target,
            output_literal=item.output_literal,
            evidence_ids=_sorted_unique(item.evidence_ids),
            provenance_references=_sorted_unique(item.provenance_references),
            blocking_reasons=_blocking_reasons_without_decision(item.disposition),
        )

    if item.disposition == PromotionDisposition.BASELINE_V1:
        # Regla 14: BASELINE_V1 nunca acepta ninguna decision -- ni
        # siquiera para trazabilidad en el item; se registra solo a
        # nivel global (nunca en silencio).
        global_diagnostics.append(
            f"INVALID_DECISION::{active_decision.decision_id}::DECISION_NOT_ALLOWED_FOR_BASELINE"
        )
        return CandidatePromotionPlanItem(
            plan_item_id=plan_item_id_for(item.review_item_id),
            review_item_id=item.review_item_id,
            assessment_id=item.assessment_id,
            reference_id=item.reference_id,
            source=item.source,
            source_candidate_id=item.source_candidate_id,
            rule_family=item.rule_family,
            assessment_disposition=item.disposition,
            eligibility=item.eligibility,
            action=default_action,
            status=default_status,
            target=item.target,
            output_literal=item.output_literal,
            evidence_ids=_sorted_unique(item.evidence_ids),
            provenance_references=_sorted_unique(item.provenance_references),
            blocking_reasons=[],
        )

    reasons = _identity_mismatches(
        active_decision, item, assessment_artifact_hash=assessment_artifact_hash
    )
    incompatibility = _eligibility_incompatibility(active_decision, item)
    if incompatibility is not None:
        reasons.append(incompatibility)

    if reasons:
        action, status = default_action, PromotionPlanItemStatus.INVALID_DECISION
    else:
        action = _ACTION_BY_VERB[active_decision.decision]
        status = PromotionPlanItemStatus.VALID
        if (
            item.disposition in (PromotionDisposition.BLOCKED, PromotionDisposition.CONFLICTING)
            or item.disposition == PromotionDisposition.ALREADY_COVERED
        ):
            # DEFER/REJECT validos sobre BLOCKED/CONFLICTING/ALREADY_COVERED
            # se registran (trazabilidad), pero la accion NUNCA cambia del
            # valor fijo de esa disposition (Parte 6: "ninguna decision
            # puede transformarlo en promocion" / "puede aparecer para
            # trazabilidad").
            action = default_action

    return CandidatePromotionPlanItem(
        plan_item_id=plan_item_id_for(item.review_item_id),
        review_item_id=item.review_item_id,
        assessment_id=item.assessment_id,
        reference_id=item.reference_id,
        source=item.source,
        source_candidate_id=item.source_candidate_id,
        rule_family=item.rule_family,
        assessment_disposition=item.disposition,
        eligibility=item.eligibility,
        decision_id=active_decision.decision_id,
        decision=active_decision.decision,
        action=action,
        status=status,
        target=item.target,
        output_literal=item.output_literal,
        evidence_ids=_sorted_unique(item.evidence_ids),
        provenance_references=_sorted_unique(item.provenance_references),
        reason_code=active_decision.reason_code,
        reviewer_reference=active_decision.reviewer_reference,
        blocking_reasons=_sorted_unique(reasons),
    )


def _blocking_reasons_without_decision(disposition: PromotionDisposition) -> list[str]:
    if disposition in (PromotionDisposition.BLOCKED, PromotionDisposition.CONFLICTING):
        return [f"DISPOSITION_{disposition.value}_NEVER_ELIGIBLE_FOR_PROMOTION"]
    return []


def _build_summary(items: Sequence[CandidatePromotionPlanItem]) -> CandidatePromotionPlanSummary:
    counts_by_action: dict[PromotionPlanAction, int] = {}
    counts_by_source: dict[CandidateSource, int] = {}
    counts_by_family: dict[UnifiedRuleFamily, int] = {}
    invalid_decision_count = 0
    for item in items:
        counts_by_action[item.action] = counts_by_action.get(item.action, 0) + 1
        counts_by_source[item.source] = counts_by_source.get(item.source, 0) + 1
        counts_by_family[item.rule_family] = counts_by_family.get(item.rule_family, 0) + 1
        if item.status == PromotionPlanItemStatus.INVALID_DECISION:
            invalid_decision_count += 1
    return CandidatePromotionPlanSummary(
        total_items=len(items),
        keep_baseline_count=counts_by_action.get(PromotionPlanAction.KEEP_BASELINE, 0),
        skip_already_covered_count=counts_by_action.get(
            PromotionPlanAction.SKIP_ALREADY_COVERED, 0
        ),
        propose_shadow_promotion_count=counts_by_action.get(
            PromotionPlanAction.PROPOSE_SHADOW_PROMOTION, 0
        ),
        reject_count=counts_by_action.get(PromotionPlanAction.REJECT, 0),
        defer_count=counts_by_action.get(PromotionPlanAction.DEFER, 0),
        block_count=counts_by_action.get(PromotionPlanAction.BLOCK, 0),
        pending_review_count=counts_by_action.get(PromotionPlanAction.PENDING_REVIEW, 0),
        invalid_decision_count=invalid_decision_count,
        counts_by_action=counts_by_action,
        counts_by_source=counts_by_source,
        counts_by_family=counts_by_family,
    )


def _check_global_identity(
    *,
    assessment: CandidatePromotionAssessmentArtifact,
    review_package: CandidatePromotionReviewPackage,
    manifest: CandidatePromotionDecisionManifest,
) -> None:
    assessment_artifact_hash_now = _hash_artifact(assessment)
    if assessment_artifact_hash_now != review_package.assessment_artifact_hash:
        raise CandidatePromotionPlanError(
            "el review package esta desactualizado: su assessment_artifact_hash no "
            "coincide con el assessment actual"
        )
    review_package_hash_now = _hash_artifact(review_package)
    if manifest.review_package_hash != review_package_hash_now:
        raise CandidatePromotionPlanError(
            "el manifiesto de decisiones no corresponde al review package actual "
            "(review_package_hash no coincide)"
        )
    if manifest.assessment_artifact_hash != review_package.assessment_artifact_hash:
        raise CandidatePromotionPlanError(
            "el manifiesto de decisiones no corresponde al assessment actual "
            "(assessment_artifact_hash no coincide)"
        )
    if manifest.run_id != assessment.run_id or manifest.run_id != review_package.run_id:
        raise CandidatePromotionPlanError(
            "el manifiesto de decisiones pertenece a un run_id distinto"
        )


def build_candidate_promotion_plan(
    *,
    assessment: CandidatePromotionAssessmentArtifact,
    review_package: CandidatePromotionReviewPackage,
    manifest: CandidatePromotionDecisionManifest,
) -> CandidatePromotionPlanArtifact:
    """Punto de entrada puro. Nunca muta `assessment`/`review_package`/
    `manifest`. Determinista: misma terna de entrada siempre produce el
    mismo `CandidatePromotionPlanArtifact` (mismos bytes JSON),
    independientemente del orden de `manifest.decisions`."""
    _check_global_identity(assessment=assessment, review_package=review_package, manifest=manifest)

    review_item_by_id = {item.review_item_id: item for item in review_package.review_items}
    global_diagnostics: list[str] = []

    decisions_by_review_item: dict[str, list[CandidatePromotionDecision]] = {}
    for decision in manifest.decisions:
        if decision.review_item_id not in review_item_by_id:
            global_diagnostics.append(
                f"INVALID_DECISION::{decision.decision_id}::UNKNOWN_REVIEW_ITEM_ID"
            )
            continue
        decisions_by_review_item.setdefault(decision.review_item_id, []).append(decision)

    active_decision_by_review_item: dict[str, CandidatePromotionDecision] = {}
    for review_item_id, group in decisions_by_review_item.items():
        active, notes = _resolve_active_decision(group)
        if active is not None:
            active_decision_by_review_item[review_item_id] = active
        global_diagnostics.extend(notes)

    plan_items = sorted(
        (
            _build_plan_item(
                item,
                active_decision_by_review_item.get(item.review_item_id),
                assessment_artifact_hash=review_package.assessment_artifact_hash,
                global_diagnostics=global_diagnostics,
            )
            for item in review_package.review_items
        ),
        key=lambda plan_item: plan_item.plan_item_id,
    )

    return CandidatePromotionPlanArtifact(
        run_id=assessment.run_id,
        source_package_hash=assessment.source_package_hash,
        assessment_artifact_hash=review_package.assessment_artifact_hash,
        review_package_hash=manifest.review_package_hash,
        decision_manifest_hash=_hash_artifact(manifest),
        assessment_policy_version=assessment.policy_version,
        summary=_build_summary(plan_items),
        plan_items=plan_items,
        diagnostics=_sorted_unique(global_diagnostics),
    )
