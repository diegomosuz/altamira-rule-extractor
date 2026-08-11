"""Contrato tipado del catalogo unificado de candidatos y evaluacion de
promocion (Fase 9 de la ampliacion semantica,
`feat/unified-candidate-promotion-assessment`).

Diagnostico, NO contractual: persiste en `<run_dir>/diagnostics/
candidate-promotion-assessment.json`, fuera de `artifacts/01-10` -- mismo
patron que `contracts/v2_shadow_candidates.py`/`contracts/
interprocedural_rule_candidates.py`: un artefacto adyacente, opcional,
generado exclusivamente bajo demanda, irrelevante para que un run llegue
a COMPLETED.

Cataloga, sin fusionar ni modificar, las TRES superficies de candidatos
ya existentes:

- `CandidateArtifact` V1 (`contracts/candidate.py`, productivo, salida
  de Q0).
- `V2ShadowCandidatesArtifact` (`contracts/v2_shadow_candidates.py`,
  Fase 5, experimental, shadow mode).
- `InterproceduralRuleCandidatesArtifact` (`contracts/
  interprocedural_rule_candidates.py`, Fase 8, experimental, shadow
  mode).

`UnifiedCandidateReference` es una REFERENCIA inmutable hacia el
candidato de origen -- nunca una fusion fisica: `source`+
`source_candidate_id` siempre permiten recuperar el objeto original en
su propio artefacto. Este modulo NUNCA reescribe, reclasifica ni
"corrige" un candidato de origen; solo cataloga, relaciona y evalua
criterios de preparacion para una promocion que, en si misma, esta
completamente FUERA de alcance de Fase 9 -- ver
`PromotionDisposition` (nunca incluye `PROMOTED`/`AUTO_PROMOTED`) y
`docs/CANDIDATE_PROMOTION_ASSESSMENT.md`.

Deliberadamente SIN una puntuacion numerica de confianza: cada
`PromotionCriterionResult` es un juicio binario/discreto
(`PromotionCriterionStatus`) sobre un hecho estructural ya demostrado
por las tres fuentes, nunca un promedio, peso o umbral arbitrario."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex


class CandidateSource(StrEnum):
    """Las tres superficies de candidatos ya existentes, nunca
    ampliadas ni fusionadas por Fase 9."""

    V1 = "V1"
    V2 = "V2"
    INTERPROCEDURAL = "INTERPROCEDURAL"


class UnifiedRuleFamily(StrEnum):
    """Familia funcional normalizada, mapeada UNICAMENTE cuando la
    equivalencia esta demostrada por el propio diseno de los contratos
    existentes (ver `pipeline/candidate_source_adapters.py` para el
    razonamiento exacto de cada mapping) -- nunca por semejanza textual
    entre nombres de enum. En particular, `V2RuleType.STATE_CHANGE_RULE`
    NUNCA se mapea a `STATE_TRANSITION`: son conceptos estructuralmente
    distintos (V2 es intraprograma, siempre `support=PARTIAL`, sin
    exigir un `semantic_tag` de estado; Fase 8 es interprocedural,
    `DETERMINISTIC` unicamente con `semantic_tag=status/status_flag`) --
    se conserva como `UNKNOWN`."""

    RETURN_CODE = "RETURN_CODE"
    LEVEL_88_RETURN_CODE = "LEVEL_88_RETURN_CODE"
    BY_REFERENCE_OUTPUT = "BY_REFERENCE_OUTPUT"
    STATE_TRANSITION = "STATE_TRANSITION"
    CALCULATION = "CALCULATION"
    UNKNOWN = "UNKNOWN"


class SourceAvailability(StrEnum):
    """Estado real de disponibilidad de UNA fuente para este run.
    `NOT_AVAILABLE` (el artefacto nunca se genero/cargo, situacion
    legitima) es DISTINTO de `INVALID` (el artefacto existe pero no pudo
    validarse contra su propio contrato -- bloquea evaluaciones
    dependientes, nunca se oculta el error)."""

    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    INVALID = "INVALID"


class CandidateRelationKind(StrEnum):
    """Resultado de comparar DOS `UnifiedCandidateReference`
    (necesariamente de fuentes distintas, Fase 9 nunca relaciona dos
    candidatos de la misma fuente). `NOT_EVALUATED` -- nunca
    `NO_RELATION` -- cuando una de las dos fuentes involucradas no
    estuvo disponible: la ausencia de evidencia nunca se presenta como
    evidencia de ausencia."""

    EXACT_MATCH = "EXACT_MATCH"
    RELATED = "RELATED"
    CONFLICT = "CONFLICT"
    NO_RELATION = "NO_RELATION"
    NOT_EVALUATED = "NOT_EVALUATED"


class CandidateConflictType(StrEnum):
    """Unicamente conflictos demostrables estructuralmente -- ver
    `pipeline/candidate_conflict_analyzer.py`. Nunca se declara un
    conflicto por ausencia de literal/decision_id, candidato parcial,
    diferencia de provenance, orden distinto o fuente no disponible."""

    SAME_DECISION_DIFFERENT_OUTPUT = "SAME_DECISION_DIFFERENT_OUTPUT"
    SAME_CALL_SITE_DIFFERENT_OUTPUT = "SAME_CALL_SITE_DIFFERENT_OUTPUT"
    SAME_TARGET_CONTRADICTORY_OUTPUT = "SAME_TARGET_CONTRADICTORY_OUTPUT"
    INCOMPATIBLE_RULE_FAMILY = "INCOMPATIBLE_RULE_FAMILY"
    INVALID_PROVENANCE = "INVALID_PROVENANCE"


class PromotionCriterionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_EVALUATED = "NOT_EVALUATED"


class PromotionCriterionKind(StrEnum):
    """Los doce criterios de preparacion evaluados para CADA
    `UnifiedCandidateReference` de fuente V2/INTERPROCEDURAL (para V1,
    todos quedan `NOT_APPLICABLE`: V1 nunca se evalua para promocion,
    ver `PromotionDisposition.BASELINE_V1`). Ver `pipeline/
    candidate_promotion_policy.py` para la semantica exacta de cada
    criterio."""

    DETERMINISTIC_SUPPORT = "DETERMINISTIC_SUPPORT"
    COMPLETE_PROVENANCE = "COMPLETE_PROVENANCE"
    TARGET_AVAILABLE = "TARGET_AVAILABLE"
    OUTPUT_LITERAL_AVAILABLE = "OUTPUT_LITERAL_AVAILABLE"
    NO_BARRIERS = "NO_BARRIERS"
    NO_CONFLICTS = "NO_CONFLICTS"
    V1_COMPARISON_AVAILABLE = "V1_COMPARISON_AVAILABLE"
    V2_COMPARISON_AVAILABLE = "V2_COMPARISON_AVAILABLE"
    INTERPROCEDURAL_COMPARISON_AVAILABLE = "INTERPROCEDURAL_COMPARISON_AVAILABLE"
    INDEPENDENT_CORROBORATION = "INDEPENDENT_CORROBORATION"
    SUPPORTED_RULE_FAMILY = "SUPPORTED_RULE_FAMILY"
    SOURCE_ARTIFACT_VALID = "SOURCE_ARTIFACT_VALID"


class PromotionDisposition(StrEnum):
    """Nunca incluye `PROMOTED`/`AUTO_PROMOTED`: Fase 9 es
    exclusivamente diagnostica, ninguna disposition implica una accion
    automatica sobre ningun candidato de origen."""

    BASELINE_V1 = "BASELINE_V1"
    ALREADY_COVERED = "ALREADY_COVERED"
    READY_FOR_CONTROLLED_REVIEW = "READY_FOR_CONTROLLED_REVIEW"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"
    CONFLICTING = "CONFLICTING"
    NOT_EVALUATED = "NOT_EVALUATED"


class RecommendedAction(StrEnum):
    """Catalogo ESTABLE, nunca texto libre generado dinamicamente --
    correspondencia 1:1 con `PromotionDisposition`."""

    NONE_BASELINE_V1 = "NONE_BASELINE_V1"
    NO_ACTION_ALREADY_COVERED_BY_V1 = "NO_ACTION_ALREADY_COVERED_BY_V1"
    SUBMIT_FOR_CONTROLLED_FUNCTIONAL_REVIEW = "SUBMIT_FOR_CONTROLLED_FUNCTIONAL_REVIEW"
    GATHER_INDEPENDENT_CORROBORATION = "GATHER_INDEPENDENT_CORROBORATION"
    RESOLVE_BLOCKING_CRITERIA_BEFORE_REVIEW = "RESOLVE_BLOCKING_CRITERIA_BEFORE_REVIEW"
    RESOLVE_CONFLICT_BEFORE_REVIEW = "RESOLVE_CONFLICT_BEFORE_REVIEW"
    AWAIT_REQUIRED_SOURCE_AVAILABILITY = "AWAIT_REQUIRED_SOURCE_AVAILABILITY"


_RECOMMENDED_ACTION_BY_DISPOSITION: dict[PromotionDisposition, RecommendedAction] = {
    PromotionDisposition.BASELINE_V1: RecommendedAction.NONE_BASELINE_V1,
    PromotionDisposition.ALREADY_COVERED: RecommendedAction.NO_ACTION_ALREADY_COVERED_BY_V1,
    PromotionDisposition.READY_FOR_CONTROLLED_REVIEW: (
        RecommendedAction.SUBMIT_FOR_CONTROLLED_FUNCTIONAL_REVIEW
    ),
    PromotionDisposition.REVIEW_REQUIRED: RecommendedAction.GATHER_INDEPENDENT_CORROBORATION,
    PromotionDisposition.BLOCKED: RecommendedAction.RESOLVE_BLOCKING_CRITERIA_BEFORE_REVIEW,
    PromotionDisposition.CONFLICTING: RecommendedAction.RESOLVE_CONFLICT_BEFORE_REVIEW,
    PromotionDisposition.NOT_EVALUATED: RecommendedAction.AWAIT_REQUIRED_SOURCE_AVAILABILITY,
}


def recommended_action_for(disposition: PromotionDisposition) -> RecommendedAction:
    """Unica fuente de verdad de la correspondencia disposition ->
    accion recomendada -- usada tanto por `candidate_promotion_policy.py`
    como por el validador de coherencia de este contrato."""
    return _RECOMMENDED_ACTION_BY_DISPOSITION[disposition]


def _ordered_unique_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


class UnifiedCandidateReference(AltamiraBaseModel):
    """Referencia inmutable hacia UN candidato de una de las tres
    fuentes. `source`+`source_candidate_id` siempre permiten recuperar
    el objeto original completo en su propio artefacto -- esta clase
    nunca copia la totalidad de los campos originales, solo los
    necesarios para relacionar/evaluar (ver Fase 2 del runbook: "no
    fusionar fisicamente, no perder evidencia especifica de ninguna
    fuente" -- la evidencia completa sigue viviendo en el artefacto
    fuente, citado por `source_candidate_id`)."""

    unified_reference_id: str = Field(min_length=1)
    source: CandidateSource
    source_candidate_id: str = Field(min_length=1)
    source_artifact_hash: Sha256Hex
    rule_family: UnifiedRuleFamily
    original_rule_type: str | None = None
    original_support: str = Field(min_length=1)
    program: str | None = None
    paragraph: str | None = None
    decision_id: str | None = None
    call_site_id: str | None = None
    target: str | None = None
    input_literal: str | None = None
    output_literal: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    barrier_codes: list[str] = Field(default_factory=list)
    provenance_references: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> UnifiedCandidateReference:
        label = f"UnifiedCandidateReference({self.unified_reference_id!r})"
        if self.evidence_ids != _ordered_unique_strings(self.evidence_ids):
            raise ValueError(f"{label}: evidence_ids debe estar ordenado y sin duplicados")
        if self.barrier_codes != _ordered_unique_strings(self.barrier_codes):
            raise ValueError(f"{label}: barrier_codes debe estar ordenado y sin duplicados")
        if self.provenance_references != _ordered_unique_strings(self.provenance_references):
            raise ValueError(
                f"{label}: provenance_references debe estar ordenado y sin duplicados"
            )
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError(f"{label}: diagnostics debe estar ordenado y sin duplicados")
        return self


class CandidateRelation(AltamiraBaseModel):
    """Relacion de solo lectura entre DOS `UnifiedCandidateReference`.
    Semanticamente simetrica (A~B es lo mismo que B~A) pero serializada
    una unica vez -- `left_reference_id`/`right_reference_id` se ordenan
    siempre de forma deterministica (nunca por orden de deteccion), ver
    `pipeline/candidate_relation_analyzer.py`."""

    relation_id: str = Field(min_length=1)
    left_reference_id: str = Field(min_length=1)
    right_reference_id: str = Field(min_length=1)
    relation_kind: CandidateRelationKind
    shared_program: str | None = None
    shared_paragraph: str | None = None
    shared_decision_id: str | None = None
    shared_call_site_id: str | None = None
    shared_target: str | None = None
    shared_output_literal: str | None = None
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_not_self_relation(self) -> CandidateRelation:
        if self.left_reference_id == self.right_reference_id:
            raise ValueError(
                f"CandidateRelation({self.relation_id!r}): left_reference_id y "
                "right_reference_id no pueden ser el mismo (nunca se relaciona una "
                "referencia consigo misma)"
            )
        return self

    @model_validator(mode="after")
    def _check_pair_is_ordered(self) -> CandidateRelation:
        if self.left_reference_id > self.right_reference_id:
            raise ValueError(
                f"CandidateRelation({self.relation_id!r}): left_reference_id debe ser "
                "alfabeticamente menor que right_reference_id (orden canonico del par "
                "simetrico, nunca por orden de deteccion)"
            )
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> CandidateRelation:
        label = f"CandidateRelation({self.relation_id!r})"
        if self.evidence != _ordered_unique_strings(self.evidence):
            raise ValueError(f"{label}: evidence debe estar ordenado y sin duplicados")
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError(f"{label}: diagnostics debe estar ordenado y sin duplicados")
        return self


class CandidateConflict(AltamiraBaseModel):
    """Conflicto DEMOSTRABLE sobre una o mas `UnifiedCandidateReference`
    -- ver `pipeline/candidate_conflict_analyzer.py`. La mayoria de los
    tipos son contradicciones ENTRE dos o mas referencias (mismo
    decision_id/call_site_id/target con literales distintos, familias
    incompatibles); `INVALID_PROVENANCE` es la excepcion deliberada: una
    UNICA referencia DETERMINISTIC de V2/INTERPROCEDURAL sin
    `evidence_ids` incumple, por si sola, el contrato de su propia
    fuente -- no requiere una segunda referencia para ser demostrable."""

    conflict_id: str = Field(min_length=1)
    reference_ids: list[str] = Field(min_length=1)
    conflict_type: CandidateConflictType
    program: str | None = None
    decision_id: str | None = None
    call_site_id: str | None = None
    target: str | None = None
    competing_literals: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=2000)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> CandidateConflict:
        label = f"CandidateConflict({self.conflict_id!r})"
        if self.reference_ids != _ordered_unique_strings(self.reference_ids):
            raise ValueError(f"{label}: reference_ids debe estar ordenado y sin duplicados")
        if self.competing_literals != _ordered_unique_strings(self.competing_literals):
            raise ValueError(f"{label}: competing_literals debe estar ordenado y sin duplicados")
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError(f"{label}: diagnostics debe estar ordenado y sin duplicados")
        return self


class PromotionCriterionResult(AltamiraBaseModel):
    criterion: PromotionCriterionKind
    status: PromotionCriterionStatus
    reason: str = Field(min_length=1, max_length=2000)
    supporting_reference_ids: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> PromotionCriterionResult:
        label = f"PromotionCriterionResult({self.criterion.value})"
        if self.supporting_reference_ids != _ordered_unique_strings(
            self.supporting_reference_ids
        ):
            raise ValueError(
                f"{label}: supporting_reference_ids debe estar ordenado y sin duplicados"
            )
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError(f"{label}: diagnostics debe estar ordenado y sin duplicados")
        return self


class CandidatePromotionAssessment(AltamiraBaseModel):
    """Evaluacion de preparacion para promocion de UNA
    `UnifiedCandidateReference`. Cada referencia tiene EXACTAMENTE una
    (invariante de artefacto) -- nunca implica una promocion real."""

    assessment_id: str = Field(min_length=1)
    reference_id: str = Field(min_length=1)
    disposition: PromotionDisposition
    criteria: list[PromotionCriterionResult] = Field(default_factory=list)
    exact_match_reference_ids: list[str] = Field(default_factory=list)
    related_reference_ids: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    recommended_action: RecommendedAction
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_recommended_action_matches_disposition(self) -> CandidatePromotionAssessment:
        expected = recommended_action_for(self.disposition)
        if self.recommended_action != expected:
            raise ValueError(
                f"CandidatePromotionAssessment({self.assessment_id!r}): "
                f"recommended_action={self.recommended_action.value} no coincide con el "
                f"catalogo esperado para disposition={self.disposition.value} "
                f"({expected.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_disposition_coherence(self) -> CandidatePromotionAssessment:
        label = f"CandidatePromotionAssessment({self.assessment_id!r})"
        statuses = {result.criterion: result.status for result in self.criteria}

        if self.disposition == PromotionDisposition.ALREADY_COVERED:
            if not self.exact_match_reference_ids:
                raise ValueError(
                    f"{label}: ALREADY_COVERED exige al menos un exact_match_reference_id"
                )
        if self.disposition == PromotionDisposition.READY_FOR_CONTROLLED_REVIEW:
            for criterion, status in statuses.items():
                if status in (
                    PromotionCriterionStatus.FAIL,
                    PromotionCriterionStatus.NOT_EVALUATED,
                ):
                    raise ValueError(
                        f"{label}: READY_FOR_CONTROLLED_REVIEW exige todos los criterios "
                        f"obligatorios PASS/NOT_APPLICABLE -- {criterion.value}="
                        f"{status.value}"
                    )
            if self.conflict_ids:
                raise ValueError(
                    f"{label}: READY_FOR_CONTROLLED_REVIEW no puede declarar conflict_ids "
                    "(nunca simultaneamente READY y CONFLICTING)"
                )
        if self.disposition == PromotionDisposition.CONFLICTING:
            if not self.conflict_ids:
                raise ValueError(f"{label}: CONFLICTING exige al menos un conflict_id")
        else:
            if self.conflict_ids:
                raise ValueError(
                    f"{label}: solo CONFLICTING puede declarar conflict_ids "
                    f"(disposition={self.disposition.value})"
                )
        if self.disposition == PromotionDisposition.BLOCKED:
            if not any(status == PromotionCriterionStatus.FAIL for status in statuses.values()):
                raise ValueError(
                    f"{label}: BLOCKED exige al menos un criterio FAIL"
                )
        if self.disposition == PromotionDisposition.NOT_EVALUATED:
            if not any(
                status == PromotionCriterionStatus.NOT_EVALUATED
                for status in statuses.values()
            ):
                raise ValueError(
                    f"{label}: NOT_EVALUATED exige al menos un criterio NOT_EVALUATED "
                    "(una fuente requerida no disponible)"
                )
        return self

    @model_validator(mode="after")
    def _check_lists_sorted_and_unique(self) -> CandidatePromotionAssessment:
        label = f"CandidatePromotionAssessment({self.assessment_id!r})"
        if self.exact_match_reference_ids != _ordered_unique_strings(
            self.exact_match_reference_ids
        ):
            raise ValueError(
                f"{label}: exact_match_reference_ids debe estar ordenado y sin duplicados"
            )
        if self.related_reference_ids != _ordered_unique_strings(self.related_reference_ids):
            raise ValueError(
                f"{label}: related_reference_ids debe estar ordenado y sin duplicados"
            )
        if self.conflict_ids != _ordered_unique_strings(self.conflict_ids):
            raise ValueError(f"{label}: conflict_ids debe estar ordenado y sin duplicados")
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError(f"{label}: diagnostics debe estar ordenado y sin duplicados")
        criterion_kinds = [result.criterion for result in self.criteria]
        if len(criterion_kinds) != len(set(criterion_kinds)):
            raise ValueError(f"{label}: criteria contiene un PromotionCriterionKind duplicado")
        return self


class CandidatePromotionAssessmentSummary(AltamiraBaseModel):
    v1_candidate_count: int = Field(ge=0)
    v2_candidate_count: int = Field(ge=0)
    interprocedural_candidate_count: int = Field(ge=0)
    unified_reference_count: int = Field(ge=0)
    exact_match_relation_count: int = Field(ge=0)
    related_relation_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    baseline_v1_count: int = Field(ge=0)
    already_covered_count: int = Field(ge=0)
    ready_for_controlled_review_count: int = Field(ge=0)
    review_required_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    conflicting_count: int = Field(ge=0)
    not_evaluated_count: int = Field(ge=0)
    counts_by_source: dict[CandidateSource, int] = Field(default_factory=dict)
    counts_by_rule_family: dict[UnifiedRuleFamily, int] = Field(default_factory=dict)
    counts_by_disposition: dict[PromotionDisposition, int] = Field(default_factory=dict)
    source_availability: dict[CandidateSource, SourceAvailability] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_counts_non_negative(self) -> CandidatePromotionAssessmentSummary:
        for mapping, label in (
            (self.counts_by_source, "counts_by_source"),
            (self.counts_by_rule_family, "counts_by_rule_family"),
            (self.counts_by_disposition, "counts_by_disposition"),
        ):
            for key, count in mapping.items():
                if count < 0:
                    raise ValueError(f"{label}[{key}] no puede ser negativo")
        return self

    @model_validator(mode="after")
    def _check_source_counts_match_counts_by_source(
        self,
    ) -> CandidatePromotionAssessmentSummary:
        if self.counts_by_source.get(CandidateSource.V1, 0) != self.v1_candidate_count:
            raise ValueError("v1_candidate_count no coincide con counts_by_source[V1]")
        if self.counts_by_source.get(CandidateSource.V2, 0) != self.v2_candidate_count:
            raise ValueError("v2_candidate_count no coincide con counts_by_source[V2]")
        if (
            self.counts_by_source.get(CandidateSource.INTERPROCEDURAL, 0)
            != self.interprocedural_candidate_count
        ):
            raise ValueError(
                "interprocedural_candidate_count no coincide con "
                "counts_by_source[INTERPROCEDURAL]"
            )
        if sum(self.counts_by_source.values()) != self.unified_reference_count:
            raise ValueError("suma de counts_by_source no coincide con unified_reference_count")
        if sum(self.counts_by_rule_family.values()) != self.unified_reference_count:
            raise ValueError(
                "suma de counts_by_rule_family no coincide con unified_reference_count"
            )
        return self

    @model_validator(mode="after")
    def _check_disposition_partition(self) -> CandidatePromotionAssessmentSummary:
        total = (
            self.baseline_v1_count
            + self.already_covered_count
            + self.ready_for_controlled_review_count
            + self.review_required_count
            + self.blocked_count
            + self.conflicting_count
            + self.not_evaluated_count
        )
        if total != self.unified_reference_count:
            raise ValueError(
                "la suma de los conteos por disposition no coincide con "
                "unified_reference_count"
            )
        if sum(self.counts_by_disposition.values()) != self.unified_reference_count:
            raise ValueError(
                "suma de counts_by_disposition no coincide con unified_reference_count"
            )
        expected = {
            PromotionDisposition.BASELINE_V1: self.baseline_v1_count,
            PromotionDisposition.ALREADY_COVERED: self.already_covered_count,
            PromotionDisposition.READY_FOR_CONTROLLED_REVIEW: (
                self.ready_for_controlled_review_count
            ),
            PromotionDisposition.REVIEW_REQUIRED: self.review_required_count,
            PromotionDisposition.BLOCKED: self.blocked_count,
            PromotionDisposition.CONFLICTING: self.conflicting_count,
            PromotionDisposition.NOT_EVALUATED: self.not_evaluated_count,
        }
        for disposition, named_count in expected.items():
            if self.counts_by_disposition.get(disposition, 0) != named_count:
                raise ValueError(
                    f"counts_by_disposition[{disposition.value}] no coincide con el "
                    "contador nombrado correspondiente"
                )
        return self


class CandidatePromotionAssessmentArtifact(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/
    candidate-promotion-assessment.json`. NO contractual, sin
    timestamps, sin `source_text` completo, sin rutas absolutas: dos
    ejecuciones sobre los mismos artefactos de entrada deben producir
    bytes identicos."""

    schema_version: Literal["1.0"] = "1.0"
    analyzer_version: Literal["1.0"] = "1.0"
    policy_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    source_artifact_hashes: dict[str, Sha256Hex] = Field(default_factory=dict)
    source_availability: dict[CandidateSource, SourceAvailability] = Field(default_factory=dict)
    summary: CandidatePromotionAssessmentSummary
    candidate_references: list[UnifiedCandidateReference] = Field(default_factory=list)
    relations: list[CandidateRelation] = Field(default_factory=list)
    conflicts: list[CandidateConflict] = Field(default_factory=list)
    assessments: list[CandidatePromotionAssessment] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_references_sorted_and_unique(self) -> CandidatePromotionAssessmentArtifact:
        ids = [reference.unified_reference_id for reference in self.candidate_references]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate_references contiene unified_reference_id duplicado")
        if ids != sorted(ids):
            raise ValueError(
                "candidate_references no esta ordenado deterministicamente por "
                "unified_reference_id"
            )
        return self

    @model_validator(mode="after")
    def _check_source_candidate_id_unique_per_source(
        self,
    ) -> CandidatePromotionAssessmentArtifact:
        seen: dict[tuple[CandidateSource, str], str] = {}
        for reference in self.candidate_references:
            key = (reference.source, reference.source_candidate_id)
            previous = seen.get(key)
            if previous is not None:
                raise ValueError(
                    f"source_candidate_id {reference.source_candidate_id!r} de la fuente "
                    f"{reference.source.value} aparece en mas de una referencia "
                    f"({previous!r} y {reference.unified_reference_id!r})"
                )
            seen[key] = reference.unified_reference_id
        return self

    @model_validator(mode="after")
    def _check_relations_sorted_and_unique(self) -> CandidatePromotionAssessmentArtifact:
        ids = [relation.relation_id for relation in self.relations]
        if len(ids) != len(set(ids)):
            raise ValueError("relations contiene relation_id duplicado")
        if ids != sorted(ids):
            raise ValueError("relations no esta ordenado deterministicamente por relation_id")
        return self

    @model_validator(mode="after")
    def _check_relations_reference_existing_ids(self) -> CandidatePromotionAssessmentArtifact:
        reference_ids = {reference.unified_reference_id for reference in self.candidate_references}
        for relation in self.relations:
            if relation.left_reference_id not in reference_ids:
                raise ValueError(
                    f"CandidateRelation({relation.relation_id!r}) referencia "
                    f"left_reference_id inexistente: {relation.left_reference_id!r}"
                )
            if relation.right_reference_id not in reference_ids:
                raise ValueError(
                    f"CandidateRelation({relation.relation_id!r}) referencia "
                    f"right_reference_id inexistente: {relation.right_reference_id!r}"
                )
        return self

    @model_validator(mode="after")
    def _check_relation_pairs_appear_at_most_once(self) -> CandidatePromotionAssessmentArtifact:
        seen_pairs: dict[tuple[str, str], str] = {}
        for relation in self.relations:
            pair = (relation.left_reference_id, relation.right_reference_id)
            previous = seen_pairs.get(pair)
            if previous is not None:
                raise ValueError(
                    f"el par {pair!r} aparece en mas de una relacion ({previous!r} y "
                    f"{relation.relation_id!r}) -- un par de referencias aparece como "
                    "maximo una vez"
                )
            seen_pairs[pair] = relation.relation_id
        return self

    @model_validator(mode="after")
    def _check_conflicts_sorted_and_unique(self) -> CandidatePromotionAssessmentArtifact:
        ids = [conflict.conflict_id for conflict in self.conflicts]
        if len(ids) != len(set(ids)):
            raise ValueError("conflicts contiene conflict_id duplicado")
        if ids != sorted(ids):
            raise ValueError("conflicts no esta ordenado deterministicamente por conflict_id")
        return self

    @model_validator(mode="after")
    def _check_conflicts_reference_existing_ids(self) -> CandidatePromotionAssessmentArtifact:
        reference_ids = {reference.unified_reference_id for reference in self.candidate_references}
        for conflict in self.conflicts:
            missing = [rid for rid in conflict.reference_ids if rid not in reference_ids]
            if missing:
                raise ValueError(
                    f"CandidateConflict({conflict.conflict_id!r}) referencia IDs "
                    f"inexistentes: {missing}"
                )
        return self

    @model_validator(mode="after")
    def _check_assessments_sorted_and_unique(self) -> CandidatePromotionAssessmentArtifact:
        ids = [assessment.assessment_id for assessment in self.assessments]
        if len(ids) != len(set(ids)):
            raise ValueError("assessments contiene assessment_id duplicado")
        if ids != sorted(ids):
            raise ValueError(
                "assessments no esta ordenado deterministicamente por assessment_id"
            )
        return self

    @model_validator(mode="after")
    def _check_every_reference_has_exactly_one_assessment(
        self,
    ) -> CandidatePromotionAssessmentArtifact:
        reference_ids = {reference.unified_reference_id for reference in self.candidate_references}
        seen: dict[str, str] = {}
        for assessment in self.assessments:
            if assessment.reference_id not in reference_ids:
                raise ValueError(
                    f"CandidatePromotionAssessment({assessment.assessment_id!r}) referencia "
                    f"un reference_id inexistente: {assessment.reference_id!r}"
                )
            previous = seen.get(assessment.reference_id)
            if previous is not None:
                raise ValueError(
                    f"la referencia {assessment.reference_id!r} tiene mas de una "
                    f"evaluacion ({previous!r} y {assessment.assessment_id!r})"
                )
            seen[assessment.reference_id] = assessment.assessment_id
        missing = reference_ids - set(seen)
        if missing:
            raise ValueError(f"referencias sin evaluacion: {sorted(missing)}")
        return self

    @model_validator(mode="after")
    def _check_baseline_v1_only_for_v1_source(self) -> CandidatePromotionAssessmentArtifact:
        source_by_reference = {
            reference.unified_reference_id: reference.source
            for reference in self.candidate_references
        }
        for assessment in self.assessments:
            source = source_by_reference[assessment.reference_id]
            if assessment.disposition == PromotionDisposition.BASELINE_V1:
                if source != CandidateSource.V1:
                    raise ValueError(
                        f"CandidatePromotionAssessment({assessment.assessment_id!r}): "
                        "BASELINE_V1 solo puede aplicarse a source=V1, referencia de "
                        f"source={source.value}"
                    )
            elif source == CandidateSource.V1:
                raise ValueError(
                    f"CandidatePromotionAssessment({assessment.assessment_id!r}): toda "
                    "referencia source=V1 debe tener disposition=BASELINE_V1 "
                    f"(tiene {assessment.disposition.value})"
                )
        return self

    @model_validator(mode="after")
    def _check_unknown_rule_family_blocks_readiness(
        self,
    ) -> CandidatePromotionAssessmentArtifact:
        family_by_reference = {
            reference.unified_reference_id: reference.rule_family
            for reference in self.candidate_references
        }
        for assessment in self.assessments:
            family = family_by_reference[assessment.reference_id]
            if family == UnifiedRuleFamily.UNKNOWN and assessment.disposition in (
                PromotionDisposition.READY_FOR_CONTROLLED_REVIEW,
                PromotionDisposition.ALREADY_COVERED,
            ):
                raise ValueError(
                    f"CandidatePromotionAssessment({assessment.assessment_id!r}): "
                    "rule_family=UNKNOWN nunca puede resultar en "
                    f"{assessment.disposition.value}"
                )
        return self

    @model_validator(mode="after")
    def _check_diagnostics_sorted_and_unique(self) -> CandidatePromotionAssessmentArtifact:
        if self.diagnostics != _ordered_unique_strings(self.diagnostics):
            raise ValueError("diagnostics debe estar ordenado alfabeticamente y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_summary_matches_content(self) -> CandidatePromotionAssessmentArtifact:
        expected_by_source: dict[CandidateSource, int] = {}
        expected_by_family: dict[UnifiedRuleFamily, int] = {}
        for reference in self.candidate_references:
            expected_by_source[reference.source] = expected_by_source.get(reference.source, 0) + 1
            expected_by_family[reference.rule_family] = (
                expected_by_family.get(reference.rule_family, 0) + 1
            )
        if self.summary.unified_reference_count != len(self.candidate_references):
            raise ValueError(
                "summary.unified_reference_count no coincide con la cantidad real de "
                "candidate_references"
            )
        if self.summary.counts_by_source != expected_by_source:
            raise ValueError("summary.counts_by_source no coincide con la agregacion real")
        if self.summary.counts_by_rule_family != expected_by_family:
            raise ValueError("summary.counts_by_rule_family no coincide con la agregacion real")

        expected_relation_counts: dict[CandidateRelationKind, int] = {}
        for relation in self.relations:
            expected_relation_counts[relation.relation_kind] = (
                expected_relation_counts.get(relation.relation_kind, 0) + 1
            )
        if self.summary.exact_match_relation_count != expected_relation_counts.get(
            CandidateRelationKind.EXACT_MATCH, 0
        ):
            raise ValueError(
                "summary.exact_match_relation_count no coincide con la agregacion real"
            )
        if self.summary.related_relation_count != expected_relation_counts.get(
            CandidateRelationKind.RELATED, 0
        ):
            raise ValueError("summary.related_relation_count no coincide con la agregacion real")

        if self.summary.conflict_count != len(self.conflicts):
            raise ValueError(
                "summary.conflict_count no coincide con la cantidad real de conflicts"
            )

        expected_by_disposition: dict[PromotionDisposition, int] = {}
        for assessment in self.assessments:
            expected_by_disposition[assessment.disposition] = (
                expected_by_disposition.get(assessment.disposition, 0) + 1
            )
        if self.summary.counts_by_disposition != expected_by_disposition:
            raise ValueError(
                "summary.counts_by_disposition no coincide con la agregacion real"
            )
        return self
