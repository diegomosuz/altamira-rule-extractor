"""Contrato tipado del plan de promocion controlada (Fase 10 de la
ampliacion semantica, `feat/controlled-candidate-promotion-plan`).

Diagnostico, NO contractual: persiste en `<run_dir>/diagnostics/
candidate-promotion-plan.json`. Es el resultado de VALIDAR un
`CandidatePromotionDecisionManifest` humano contra un
`CandidatePromotionReviewPackage` (que a su vez deriva de un
`CandidatePromotionAssessmentArtifact`, Fase 9) -- nunca crea
candidatos nuevos, nunca promueve nada. `PromotionPlanAction.
PROPOSE_SHADOW_PROMOTION` es una PROPUESTA de dry-run, nunca una
promocion real: ningun efecto se aplica a `CandidateArtifact` V1,
`V2ShadowCandidatesArtifact` ni `InterproceduralRuleCandidatesArtifact`.

Ver `docs/CONTROLLED_CANDIDATE_PROMOTION_PLAN.md` para la semantica
completa de cada `PromotionPlanAction`/`PromotionPlanItemStatus` segun
la `PromotionDisposition` de origen y la decision (si existe)."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .candidate_promotion_assessment import (
    CandidateSource,
    PromotionDisposition,
    UnifiedRuleFamily,
)
from .candidate_promotion_review import DecisionReasonCode, ReviewDecision, ReviewEligibility


class PromotionPlanAction(StrEnum):
    """Accion de dry-run resultante para UN `CandidateReviewItem`.
    `PROPOSE_SHADOW_PROMOTION` NUNCA implica una promocion real -- ver
    docstring del modulo."""

    KEEP_BASELINE = "KEEP_BASELINE"
    SKIP_ALREADY_COVERED = "SKIP_ALREADY_COVERED"
    PROPOSE_SHADOW_PROMOTION = "PROPOSE_SHADOW_PROMOTION"
    REJECT = "REJECT"
    DEFER = "DEFER"
    BLOCK = "BLOCK"
    PENDING_REVIEW = "PENDING_REVIEW"


class PromotionPlanItemStatus(StrEnum):
    """Estado de la decision (si existe) que produjo `action`.
    `INVALID_DECISION` significa que SI se declaro una decision
    incompatible con la disposition/eligibility del item (nunca se
    ignora en silencio: se conserva en el item con sus `blocking_
    reasons`), pero `action` cae al valor seguro por defecto de esa
    disposition -- nunca a la accion que la decision invalida
    solicitaba."""

    VALID = "VALID"
    INVALID_DECISION = "INVALID_DECISION"
    NO_DECISION_REQUIRED = "NO_DECISION_REQUIRED"
    PENDING_DECISION = "PENDING_DECISION"


def _ordered_unique_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


class CandidatePromotionPlanItem(AltamiraBaseModel):
    """Una fila del plan -- UN `CandidateReviewItem` de origen, nunca
    fusionado con otro. `evidence_ids`/`provenance_references` se
    preservan tal cual del review item de origen (nunca recalculados);
    el plan NUNCA crea un candidato nuevo, nunca genera evidencia
    nueva."""

    plan_item_id: str = Field(min_length=1)
    review_item_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    reference_id: str = Field(min_length=1)
    source: CandidateSource
    source_candidate_id: str = Field(min_length=1)
    rule_family: UnifiedRuleFamily
    assessment_disposition: PromotionDisposition
    eligibility: ReviewEligibility
    decision_id: str | None = None
    decision: ReviewDecision | None = None
    action: PromotionPlanAction
    status: PromotionPlanItemStatus
    target: str | None = None
    output_literal: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    reason_code: DecisionReasonCode | None = None
    reviewer_reference: str | None = None
    blocking_reasons: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> CandidatePromotionPlanItem:
        label = f"CandidatePromotionPlanItem({self.plan_item_id!r})"
        for field_name in (
            "evidence_ids",
            "provenance_references",
            "blocking_reasons",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique_strings(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_decision_fields_coherent(self) -> CandidatePromotionPlanItem:
        label = f"CandidatePromotionPlanItem({self.plan_item_id!r})"
        has_decision = self.decision_id is not None
        if has_decision != (self.decision is not None):
            raise ValueError(
                f"{label}: decision_id y decision deben estar ambos presentes o ambos ausentes"
            )
        if self.status == PromotionPlanItemStatus.INVALID_DECISION and not has_decision:
            raise ValueError(
                f"{label}: status=INVALID_DECISION exige decision_id/decision presentes "
                "(la decision invalida se conserva para trazabilidad)"
            )
        if self.status == PromotionPlanItemStatus.PENDING_DECISION and self.decision is not None:
            raise ValueError(
                f"{label}: status=PENDING_DECISION no puede declarar una decision ya "
                "aplicada (todavia no hay decision)"
            )
        return self

    @model_validator(mode="after")
    def _check_action_never_promotes_blocked(self) -> CandidatePromotionPlanItem:
        if (
            self.assessment_disposition
            in (PromotionDisposition.BLOCKED, PromotionDisposition.CONFLICTING)
            and self.action == PromotionPlanAction.PROPOSE_SHADOW_PROMOTION
        ):
            raise ValueError(
                f"CandidatePromotionPlanItem({self.plan_item_id!r}): "
                f"{self.assessment_disposition.value} nunca puede resultar en "
                "PROPOSE_SHADOW_PROMOTION"
            )
        return self


class CandidatePromotionPlanSummary(AltamiraBaseModel):
    total_items: int = Field(ge=0)
    keep_baseline_count: int = Field(ge=0)
    skip_already_covered_count: int = Field(ge=0)
    propose_shadow_promotion_count: int = Field(ge=0)
    reject_count: int = Field(ge=0)
    defer_count: int = Field(ge=0)
    block_count: int = Field(ge=0)
    pending_review_count: int = Field(ge=0)
    invalid_decision_count: int = Field(ge=0)
    counts_by_action: dict[PromotionPlanAction, int] = Field(default_factory=dict)
    counts_by_source: dict[CandidateSource, int] = Field(default_factory=dict)
    counts_by_family: dict[UnifiedRuleFamily, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_counts_reconcile(self) -> CandidatePromotionPlanSummary:
        action_total = (
            self.keep_baseline_count
            + self.skip_already_covered_count
            + self.propose_shadow_promotion_count
            + self.reject_count
            + self.defer_count
            + self.block_count
            + self.pending_review_count
        )
        if action_total != self.total_items:
            raise ValueError(
                "la suma de los conteos por action no coincide con total_items "
                "(invalid_decision_count no particiona: una accion INVALID_DECISION "
                "siempre cae en alguna de las siete acciones seguras por defecto)"
            )
        if sum(self.counts_by_source.values()) != self.total_items:
            raise ValueError("suma de counts_by_source no coincide con total_items")
        if sum(self.counts_by_family.values()) != self.total_items:
            raise ValueError("suma de counts_by_family no coincide con total_items")
        if sum(self.counts_by_action.values()) != self.total_items:
            raise ValueError("suma de counts_by_action no coincide con total_items")
        expected_by_action = {
            PromotionPlanAction.KEEP_BASELINE: self.keep_baseline_count,
            PromotionPlanAction.SKIP_ALREADY_COVERED: self.skip_already_covered_count,
            PromotionPlanAction.PROPOSE_SHADOW_PROMOTION: self.propose_shadow_promotion_count,
            PromotionPlanAction.REJECT: self.reject_count,
            PromotionPlanAction.DEFER: self.defer_count,
            PromotionPlanAction.BLOCK: self.block_count,
            PromotionPlanAction.PENDING_REVIEW: self.pending_review_count,
        }
        for action, named_count in expected_by_action.items():
            if self.counts_by_action.get(action, 0) != named_count:
                raise ValueError(
                    f"counts_by_action[{action.value}] no coincide con el contador nombrado"
                )
        if self.invalid_decision_count > self.total_items:
            raise ValueError("invalid_decision_count no puede exceder total_items")
        return self


class CandidatePromotionPlanArtifact(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/
    candidate-promotion-plan.json`. NO contractual, sin timestamps, sin
    `source_text` completo, sin rutas absolutas: dos ejecuciones sobre
    los mismos assessment/review package/manifest deben producir bytes
    identicos."""

    schema_version: Literal["1.0"] = "1.0"
    planner_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    assessment_artifact_hash: Sha256Hex
    review_package_hash: Sha256Hex
    decision_manifest_hash: Sha256Hex
    assessment_policy_version: str = Field(min_length=1)
    summary: CandidatePromotionPlanSummary
    plan_items: list[CandidatePromotionPlanItem] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_plan_items_sorted_and_unique(self) -> CandidatePromotionPlanArtifact:
        ids = [item.plan_item_id for item in self.plan_items]
        if len(ids) != len(set(ids)):
            raise ValueError("plan_items contiene plan_item_id duplicado")
        if ids != sorted(ids):
            raise ValueError("plan_items no esta ordenado deterministicamente por plan_item_id")
        return self

    @model_validator(mode="after")
    def _check_review_item_id_unique(self) -> CandidatePromotionPlanArtifact:
        review_item_ids = [item.review_item_id for item in self.plan_items]
        if len(review_item_ids) != len(set(review_item_ids)):
            raise ValueError("plan_items contiene review_item_id duplicado")
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> CandidatePromotionPlanArtifact:
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado alfabeticamente y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_summary_matches_content(self) -> CandidatePromotionPlanArtifact:
        if self.summary.total_items != len(self.plan_items):
            raise ValueError("summary.total_items no coincide con la cantidad real de plan_items")
        expected_by_action: dict[PromotionPlanAction, int] = {}
        expected_by_source: dict[CandidateSource, int] = {}
        expected_by_family: dict[UnifiedRuleFamily, int] = {}
        invalid_decision_count = 0
        for item in self.plan_items:
            expected_by_action[item.action] = expected_by_action.get(item.action, 0) + 1
            expected_by_source[item.source] = expected_by_source.get(item.source, 0) + 1
            expected_by_family[item.rule_family] = expected_by_family.get(item.rule_family, 0) + 1
            if item.status == PromotionPlanItemStatus.INVALID_DECISION:
                invalid_decision_count += 1
        if self.summary.counts_by_action != expected_by_action:
            raise ValueError("summary.counts_by_action no coincide con la agregacion real")
        if self.summary.counts_by_source != expected_by_source:
            raise ValueError("summary.counts_by_source no coincide con la agregacion real")
        if self.summary.counts_by_family != expected_by_family:
            raise ValueError("summary.counts_by_family no coincide con la agregacion real")
        if self.summary.invalid_decision_count != invalid_decision_count:
            raise ValueError("summary.invalid_decision_count no coincide con la agregacion real")
        return self
