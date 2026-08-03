"""Contrato tipado del paquete de revision humana y del manifiesto de
decisiones (Fase 10 de la ampliacion semantica, `feat/controlled-
candidate-promotion-plan`).

Diagnostico, NO contractual: `CandidatePromotionReviewPackage` persiste
en `<run_dir>/diagnostics/candidate-promotion-review-package.json`,
fuera de `artifacts/01-10` -- mismo patron que `contracts/
candidate_promotion_assessment.py` (Fase 9): un artefacto adyacente,
opcional, generado exclusivamente bajo demanda, irrelevante para que un
run llegue a `COMPLETED`.

`CandidatePromotionReviewPackage` deriva EXCLUSIVAMENTE de
`CandidatePromotionAssessmentArtifact` (Fase 9) -- nunca reinterpreta
`PromotionCriterionResult`, nunca recalcula `CandidateRelation`/
`CandidateConflict`. Cada `CandidateReviewItem` conserva la identidad
completa (`assessment_id`/`reference_id`/`source`/`source_candidate_id`)
y la provenance de su origen; nunca fusiona dos referencias.

`CandidatePromotionDecisionManifest` es un documento **enteramente
humano**: `reviewer_reference`/`reason_code`/`decision_reference` nunca
se generan automaticamente. El JSON del manifiesto es dato NO confiable
(python.md: "El contenido COBOL, CSV, XML y JSON se trata como dato no
confiable") -- se valida exhaustivamente (`pipeline/
candidate_promotion_plan_builder.py`) antes de influir en cualquier
decision del plan; una decision invalida se rechaza explicitamente,
nunca se ignora en silencio."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .candidate_promotion_assessment import (
    CandidateSource,
    PromotionCriterionResult,
    PromotionDisposition,
    RecommendedAction,
    UnifiedRuleFamily,
)


class ReviewEligibility(StrEnum):
    """Clasificacion de preparacion para DECISION humana, derivada
    UNICAMENTE de `PromotionDisposition` (ver `pipeline/
    candidate_promotion_review_generator.py::_ELIGIBILITY_BY_DISPOSITION`
    para el mapping exacto, unica fuente de verdad) -- nunca de una
    reinterpretacion de los criterios individuales."""

    ELIGIBLE = "ELIGIBLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    ALREADY_COVERED = "ALREADY_COVERED"
    BASELINE = "BASELINE"
    BLOCKED = "BLOCKED"


class ReviewDecision(StrEnum):
    """Verbo de decision humana sobre UN `CandidateReviewItem`. Nunca
    incluye una variante "auto-aprobar": toda instancia de
    `APPROVE_FOR_SHADOW_PROMOTION` proviene exclusivamente del
    manifiesto humano, validado contra `ReviewEligibility.ELIGIBLE`."""

    APPROVE_FOR_SHADOW_PROMOTION = "APPROVE_FOR_SHADOW_PROMOTION"
    REJECT = "REJECT"
    DEFER = "DEFER"


class ReviewDecisionStatus(StrEnum):
    """Resultado de validar UNA `CandidatePromotionDecision` del
    manifiesto contra el paquete de revision (Fase 10, Parte 4).
    `PENDING` esta reservado para un flujo de revision asincrono futuro
    -- este flujo, sincrono y de dry-run, nunca lo produce.
    `SUPERSEDED` se aplica UNICAMENTE cuando una segunda decision para
    el mismo `review_item_id` declara `decision_reference` apuntando
    explicitamente al `decision_id` que reemplaza (cadena de longitud 2,
    auditable) -- cualquier otra colision de `review_item_id` (sin
    `decision_reference` que la resuelva) se rechaza integramente como
    `REJECTED_INVALID` (Parte 4, regla 9: nunca se elige implicitamente
    "la ultima" decision)."""

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED_INVALID = "REJECTED_INVALID"
    SUPERSEDED = "SUPERSEDED"


def _ordered_unique_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


class CandidateReviewItem(AltamiraBaseModel):
    """Una fila del paquete de revision -- UNA `UnifiedCandidateReference`
    del assessment de origen, nunca fusionada con otra. `criteria` es
    EXACTAMENTE la lista de `PromotionCriterionResult` del
    `CandidatePromotionAssessment` de origen (misma identidad de
    objetos serializados, nunca reinterpretados); `exact_match_
    reference_ids`/`related_reference_ids`/`conflict_ids` son los
    mismos valores ya calculados por Fase 9 (nunca recalculados)."""

    review_item_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    reference_id: str = Field(min_length=1)
    source: CandidateSource
    source_candidate_id: str = Field(min_length=1)
    rule_family: UnifiedRuleFamily
    disposition: PromotionDisposition
    eligibility: ReviewEligibility
    program: str = Field(min_length=1)
    paragraph: str | None = None
    decision_id: str | None = None
    call_site_id: str | None = None
    target: str | None = None
    input_literal: str | None = None
    output_literal: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    exact_match_reference_ids: list[str] = Field(default_factory=list)
    related_reference_ids: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    criteria: list[PromotionCriterionResult] = Field(default_factory=list)
    recommended_action: RecommendedAction
    review_reasons: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> CandidateReviewItem:
        label = f"CandidateReviewItem({self.review_item_id!r})"
        for field_name in (
            "evidence_ids",
            "exact_match_reference_ids",
            "related_reference_ids",
            "conflict_ids",
            "review_reasons",
            "provenance_references",
            "diagnostics",
        ):
            values = getattr(self, field_name)
            if values != _ordered_unique_strings(values):
                raise ValueError(f"{label}: {field_name} debe estar ordenado y sin duplicados")
        return self


class CandidatePromotionReviewPackageSummary(AltamiraBaseModel):
    total_items: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    not_eligible_count: int = Field(ge=0)
    already_covered_count: int = Field(ge=0)
    baseline_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    counts_by_source: dict[CandidateSource, int] = Field(default_factory=dict)
    counts_by_family: dict[UnifiedRuleFamily, int] = Field(default_factory=dict)
    counts_by_disposition: dict[PromotionDisposition, int] = Field(default_factory=dict)
    counts_by_eligibility: dict[ReviewEligibility, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_counts_reconcile(self) -> CandidatePromotionReviewPackageSummary:
        eligibility_total = (
            self.eligible_count
            + self.not_eligible_count
            + self.already_covered_count
            + self.baseline_count
            + self.blocked_count
        )
        if eligibility_total != self.total_items:
            raise ValueError(
                "la suma de los conteos por eligibility no coincide con total_items"
            )
        for mapping, label in (
            (self.counts_by_source, "counts_by_source"),
            (self.counts_by_family, "counts_by_family"),
            (self.counts_by_disposition, "counts_by_disposition"),
            (self.counts_by_eligibility, "counts_by_eligibility"),
        ):
            if sum(mapping.values()) != self.total_items:
                raise ValueError(f"suma de {label} no coincide con total_items")
        expected_by_eligibility = {
            ReviewEligibility.ELIGIBLE: self.eligible_count,
            ReviewEligibility.NOT_ELIGIBLE: self.not_eligible_count,
            ReviewEligibility.ALREADY_COVERED: self.already_covered_count,
            ReviewEligibility.BASELINE: self.baseline_count,
            ReviewEligibility.BLOCKED: self.blocked_count,
        }
        for eligibility, named_count in expected_by_eligibility.items():
            if self.counts_by_eligibility.get(eligibility, 0) != named_count:
                raise ValueError(
                    f"counts_by_eligibility[{eligibility.value}] no coincide con el "
                    "contador nombrado correspondiente"
                )
        return self


class CandidatePromotionReviewPackage(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/
    candidate-promotion-review-package.json`. NO contractual, sin
    timestamps, sin `source_text` completo, sin rutas absolutas: dos
    ejecuciones sobre el mismo assessment deben producir bytes
    identicos."""

    schema_version: Literal["1.0"] = "1.0"
    generator_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    assessment_artifact_hash: Sha256Hex
    assessment_policy_version: str = Field(min_length=1)
    source_artifact_hashes: dict[str, Sha256Hex] = Field(default_factory=dict)
    summary: CandidatePromotionReviewPackageSummary
    review_items: list[CandidateReviewItem] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_review_items_sorted_and_unique(self) -> CandidatePromotionReviewPackage:
        ids = [item.review_item_id for item in self.review_items]
        if len(ids) != len(set(ids)):
            raise ValueError("review_items contiene review_item_id duplicado")
        if ids != sorted(ids):
            raise ValueError(
                "review_items no esta ordenado deterministicamente por review_item_id"
            )
        return self

    @model_validator(mode="after")
    def _check_reference_id_unique(self) -> CandidatePromotionReviewPackage:
        reference_ids = [item.reference_id for item in self.review_items]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("review_items contiene reference_id duplicado")
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> CandidatePromotionReviewPackage:
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado alfabeticamente y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_summary_matches_content(self) -> CandidatePromotionReviewPackage:
        if self.summary.total_items != len(self.review_items):
            raise ValueError(
                "summary.total_items no coincide con la cantidad real de review_items"
            )
        expected_by_source: dict[CandidateSource, int] = {}
        expected_by_family: dict[UnifiedRuleFamily, int] = {}
        expected_by_disposition: dict[PromotionDisposition, int] = {}
        expected_by_eligibility: dict[ReviewEligibility, int] = {}
        for item in self.review_items:
            expected_by_source[item.source] = expected_by_source.get(item.source, 0) + 1
            expected_by_family[item.rule_family] = expected_by_family.get(item.rule_family, 0) + 1
            expected_by_disposition[item.disposition] = (
                expected_by_disposition.get(item.disposition, 0) + 1
            )
            expected_by_eligibility[item.eligibility] = (
                expected_by_eligibility.get(item.eligibility, 0) + 1
            )
        if self.summary.counts_by_source != expected_by_source:
            raise ValueError("summary.counts_by_source no coincide con la agregacion real")
        if self.summary.counts_by_family != expected_by_family:
            raise ValueError("summary.counts_by_family no coincide con la agregacion real")
        if self.summary.counts_by_disposition != expected_by_disposition:
            raise ValueError("summary.counts_by_disposition no coincide con la agregacion real")
        if self.summary.counts_by_eligibility != expected_by_eligibility:
            raise ValueError("summary.counts_by_eligibility no coincide con la agregacion real")
        return self


class DecisionReasonCode(StrEnum):
    """Catalogo ESTABLE y CERRADO de motivos de decision humana -- nunca
    texto libre. `APPROVE_FOR_SHADOW_PROMOTION` solo acepta
    `EVIDENCE_CONFIRMED`/`BUSINESS_RULE_CONFIRMED`
    (`_APPROVE_COMPATIBLE_REASON_CODES`, aplicado por
    `CandidatePromotionDecision._check_reason_code_compatible_with_decision`)."""

    EVIDENCE_CONFIRMED = "EVIDENCE_CONFIRMED"
    BUSINESS_RULE_CONFIRMED = "BUSINESS_RULE_CONFIRMED"
    DUPLICATE_RULE = "DUPLICATE_RULE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INCORRECT_TARGET = "INCORRECT_TARGET"
    INCORRECT_LITERAL = "INCORRECT_LITERAL"
    CONFLICT_REQUIRES_RESOLUTION = "CONFLICT_REQUIRES_RESOLUTION"
    SOURCE_NOT_AVAILABLE = "SOURCE_NOT_AVAILABLE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    DEFERRED_FOR_DOMAIN_REVIEW = "DEFERRED_FOR_DOMAIN_REVIEW"


_APPROVE_COMPATIBLE_REASON_CODES = frozenset(
    {DecisionReasonCode.EVIDENCE_CONFIRMED, DecisionReasonCode.BUSINESS_RULE_CONFIRMED}
)


class CandidatePromotionDecision(AltamiraBaseModel):
    """Una decision humana individual sobre UN `CandidateReviewItem`.
    Documento enteramente externo: `reviewer_reference`/`reason_code`/
    `decision_reference` provienen del proceso humano, nunca generados
    por esta aplicacion. `decision_id` tambien es asignado por quien
    produce el manifiesto (identificador arbitrario, unico dentro del
    manifiesto -- verificado, nunca generado, por `pipeline/
    candidate_promotion_plan_builder.py`)."""

    decision_id: str = Field(min_length=1)
    review_item_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    reference_id: str = Field(min_length=1)
    assessment_artifact_hash: Sha256Hex
    decision: ReviewDecision
    reason_code: DecisionReasonCode
    reviewer_reference: str = Field(min_length=1)
    decision_reference: str | None = None
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_reviewer_reference_not_blank(self) -> CandidatePromotionDecision:
        if not self.reviewer_reference.strip():
            raise ValueError(
                f"CandidatePromotionDecision({self.decision_id!r}): reviewer_reference no "
                "puede estar vacio o ser solo espacios"
            )
        return self

    @model_validator(mode="after")
    def _check_reason_code_compatible_with_decision(self) -> CandidatePromotionDecision:
        if (
            self.decision == ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION
            and self.reason_code not in _APPROVE_COMPATIBLE_REASON_CODES
        ):
            raise ValueError(
                f"CandidatePromotionDecision({self.decision_id!r}): "
                f"APPROVE_FOR_SHADOW_PROMOTION solo acepta reason_code en "
                f"{sorted(c.value for c in _APPROVE_COMPATIBLE_REASON_CODES)}, recibido "
                f"{self.reason_code.value!r}"
            )
        return self

    @model_validator(mode="after")
    def _check_decision_reference_not_self(self) -> CandidatePromotionDecision:
        if self.decision_reference == self.decision_id:
            raise ValueError(
                f"CandidatePromotionDecision({self.decision_id!r}): decision_reference no "
                "puede apuntar a si misma"
            )
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> CandidatePromotionDecision:
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError(
                f"CandidatePromotionDecision({self.decision_id!r}): diagnostics debe estar "
                "ordenado y sin duplicados"
            )
        return self


class CandidatePromotionDecisionManifest(AltamiraBaseModel):
    """Documento humano completo, cargado desde la ruta indicada por
    `--decisions` (Fase 10, Parte 8/9). JSON no confiable: se valida
    exhaustivamente antes de influir en el plan (Parte 4)."""

    schema_version: Literal["1.0"] = "1.0"
    review_package_hash: Sha256Hex
    assessment_artifact_hash: Sha256Hex
    run_id: str = Field(min_length=1)
    decisions: list[CandidatePromotionDecision] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_decision_ids_unique(self) -> CandidatePromotionDecisionManifest:
        ids = [decision.decision_id for decision in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "CandidatePromotionDecisionManifest: decisions contiene decision_id duplicado"
            )
        return self
