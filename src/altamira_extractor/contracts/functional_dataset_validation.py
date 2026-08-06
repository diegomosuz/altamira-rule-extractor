"""Contrato tipado del reporte agregado MULTI-RUN de validacion
funcional (cierre de Fase 15B2-A, Seccion 2 de la correccion de
aplicabilidad/completitud). Combina N `FunctionalValidationReport`
(Parte F, `contracts/functional_validation.py`, siempre de UN run cada
uno) en UN `FunctionalDatasetValidationReport` que cubre el catalogo
COMPLETO de un `FunctionalGroundTruthSet`, sin exigir que todas sus
fixtures vivan en el mismo ZIP/paquete/run -- un caso interprocedural
como BY_REFERENCE_OUTPUT puede vivir en su propio paquete dedicado
mientras el resto del catalogo se evalua contra otro.

Este contrato es la unica forma soportada de afirmar que el dataset
COMPLETO "paso": un `FunctionalValidationReport` de un solo run NUNCA
puede alcanzar `dataset_disposition=PASS_ENGINEERING` mientras existan
casos REQUIRED/FORBIDDEN pendientes en otro run (ver `coverage_status`
en `functional_validation.py`) -- ese es precisamente el checkpoint que
este contrato resuelve, combinando reportes reales ya validados, nunca
inventando resultados.

Reglas de agregacion (aplicadas por el analizador puro
`pipeline/functional_validation_aggregator.py`, nunca en el contrato
mismo -- este archivo solo valida la FORMA del resultado):

1. Cada `case_id` aporta COMO MAXIMO un resultado APPLICABLE al
   agregado. Un `Applicability.NOT_APPLICABLE` de un reporte NUNCA
   desplaza un `Applicability.APPLICABLE` ya elegido de otro.
2. Dos resultados APPLICABLE para el MISMO `case_id` con el mismo
   `outcome` (y, si aplica, el mismo `unified_reference_id` matched) se
   reconcilian como duplicado (`duplicate_case_ids`) -- se usa el
   primero en orden deterministico (`run_id` ascendente), nunca ambos.
3. Dos resultados APPLICABLE para el MISMO `case_id` con `outcome`
   DISTINTO son un conflicto BLOQUEANTE: el agregador lanza
   `FunctionalDatasetAggregationError` ANTES de construir este modelo
   -- `conflicting_case_ids` en este contrato SIEMPRE queda vacio por
   construccion (un validador lo exige explicitamente), documentado
   aqui unicamente para dejar registro del campo en el esquema.
4. Un caso nunca APPLICABLE en NINGUN reporte de entrada permanece en
   `pending_case_ids` -- NUNCA se convierte en falso negativo.

`report_id` es content-addressed (sha256 de `dataset_id`+
`dataset_version`+`lane`+`source_report_ids` ordenados, JSON canonico) y
NUNCA incluye un timestamp: la misma combinacion exacta de reportes de
entrada produce siempre el mismo `report_id`, sin importar cuando se
ejecute el agregador."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel
from .functional_ground_truth import GroundTruthCaseKind
from .functional_validation import (
    Applicability,
    CaseLevelMetrics,
    FunctionalDatasetCoverageStatus,
    FunctionalDatasetDisposition,
    FunctionalValidationMetrics,
    MatchOutcome,
)


class FunctionalDatasetLane(StrEnum):
    """Superficie de candidatos de la que provienen los
    `UnifiedCandidateReference` agregados. Motor cerrado (mismo
    principio que el resto del bloque): agregar un valor nuevo exige
    extender este enum y el agregador, nunca configurarlo por texto
    libre. UNIFIED es la unica superficie que produce
    `CandidatePromotionAssessmentArtifact.candidate_references` hoy."""

    UNIFIED = "UNIFIED"


class DatasetCaseAggregateResult(AltamiraBaseModel):
    """Resultado RESUELTO de UN `case_id` dentro del agregado -- ya
    reconciliado (nunca un resultado crudo copiado de un reporte de
    entrada sin pasar por las reglas de agregacion)."""

    case_id: str = Field(min_length=1, max_length=100)
    kind: GroundTruthCaseKind
    applicability: Applicability
    outcome: MatchOutcome
    source_run_id: str | None = None
    case_metrics: CaseLevelMetrics | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> DatasetCaseAggregateResult:
        if self.applicability == Applicability.NOT_APPLICABLE:
            if self.outcome != MatchOutcome.NOT_EVALUATED:
                raise ValueError(
                    f"case_id={self.case_id!r}: applicability=NOT_APPLICABLE exige "
                    f"outcome=NOT_EVALUATED (recibido {self.outcome.value})"
                )
            if self.source_run_id is not None:
                raise ValueError(
                    f"case_id={self.case_id!r}: applicability=NOT_APPLICABLE no puede "
                    "declarar source_run_id (ningun reporte de entrada lo evaluo)"
                )
        else:
            if self.outcome == MatchOutcome.NOT_EVALUATED:
                raise ValueError(
                    f"case_id={self.case_id!r}: applicability=APPLICABLE no puede tener "
                    "outcome=NOT_EVALUATED"
                )
            if self.source_run_id is None:
                raise ValueError(
                    f"case_id={self.case_id!r}: applicability=APPLICABLE exige source_run_id"
                )
        if self.outcome != MatchOutcome.MATCHED and self.case_metrics is not None:
            raise ValueError(
                f"case_id={self.case_id!r}: case_metrics solo aplica a outcome=MATCHED"
            )
        return self


class FunctionalDatasetValidationReport(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/functional-
    dataset-validation-report.json` del run que actua como "ancla" de la
    invocacion CLI (nunca en una ubicacion elegida por el usuario -- ver
    `pipeline/functional_dataset_validation_service.py`). NO
    contractual, sin timestamps: la misma combinacion de reportes de
    entrada produce bytes identicos."""

    schema_version: Literal["1.0"] = "1.0"
    report_id: str = Field(min_length=1, max_length=64)
    dataset_id: str = Field(min_length=1, max_length=100)
    dataset_version: str = Field(min_length=1, max_length=100)
    lane: FunctionalDatasetLane
    source_report_ids: list[str] = Field(default_factory=list)
    source_run_ids: list[str] = Field(default_factory=list)
    case_results: list[DatasetCaseAggregateResult] = Field(default_factory=list)
    required_case_count: int = Field(ge=0)
    evaluated_required_case_count: int = Field(ge=0)
    optional_case_count: int = Field(ge=0, default=0)
    evaluated_optional_case_count: int = Field(ge=0, default=0)
    forbidden_case_count: int = Field(ge=0)
    evaluated_forbidden_case_count: int = Field(ge=0)
    pending_case_ids: list[str] = Field(default_factory=list)
    duplicate_case_ids: list[str] = Field(default_factory=list)
    conflicting_case_ids: list[str] = Field(default_factory=list)
    coverage_status: FunctionalDatasetCoverageStatus
    metrics: FunctionalValidationMetrics
    dataset_disposition: FunctionalDatasetDisposition
    limitations: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_conflicting_case_ids_always_empty(self) -> FunctionalDatasetValidationReport:
        if self.conflicting_case_ids:
            raise ValueError(
                "conflicting_case_ids debe estar vacio: un conflicto real bloquea la "
                "agregacion (FunctionalDatasetAggregationError) antes de construir este "
                "modelo, nunca se persiste un reporte con conflictos sin resolver"
            )
        return self

    @model_validator(mode="after")
    def _check_ordering(self) -> FunctionalDatasetValidationReport:
        case_ids = [c.case_id for c in self.case_results]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_results contiene case_id duplicado")
        if case_ids != sorted(case_ids):
            raise ValueError("case_results no esta ordenado deterministicamente por case_id")
        for label, values in (
            ("source_report_ids", self.source_report_ids),
            ("source_run_ids", self.source_run_ids),
            ("pending_case_ids", self.pending_case_ids),
            ("duplicate_case_ids", self.duplicate_case_ids),
        ):
            if values != sorted(set(values)):
                raise ValueError(f"{label} debe estar ordenado y sin duplicados")
        return self

    @model_validator(mode="after")
    def _check_coverage_counts(self) -> FunctionalDatasetValidationReport:
        required = [c for c in self.case_results if c.kind == GroundTruthCaseKind.POSITIVE]
        forbidden = [c for c in self.case_results if c.kind == GroundTruthCaseKind.NEGATIVE]
        expected_required = len(required)
        expected_evaluated_required = sum(
            1 for c in required if c.applicability == Applicability.APPLICABLE
        )
        expected_forbidden = len(forbidden)
        expected_evaluated_forbidden = sum(
            1 for c in forbidden if c.applicability == Applicability.APPLICABLE
        )
        if self.required_case_count != expected_required:
            raise ValueError(
                f"required_case_count ({self.required_case_count}) != {expected_required}"
            )
        if self.evaluated_required_case_count != expected_evaluated_required:
            raise ValueError(
                "evaluated_required_case_count "
                f"({self.evaluated_required_case_count}) != {expected_evaluated_required}"
            )
        if self.forbidden_case_count != expected_forbidden:
            raise ValueError(
                f"forbidden_case_count ({self.forbidden_case_count}) != {expected_forbidden}"
            )
        if self.evaluated_forbidden_case_count != expected_evaluated_forbidden:
            raise ValueError(
                "evaluated_forbidden_case_count "
                f"({self.evaluated_forbidden_case_count}) != {expected_evaluated_forbidden}"
            )
        expected_pending = sorted(
            c.case_id for c in self.case_results if c.applicability == Applicability.NOT_APPLICABLE
        )
        if self.pending_case_ids != expected_pending:
            raise ValueError(
                f"pending_case_ids ({self.pending_case_ids}) != casos NOT_APPLICABLE reales "
                f"({expected_pending})"
            )
        return self

    @model_validator(mode="after")
    def _check_coverage_status(self) -> FunctionalDatasetValidationReport:
        any_applicable = any(c.applicability == Applicability.APPLICABLE for c in self.case_results)
        if not any_applicable:
            expected = FunctionalDatasetCoverageStatus.NOT_EVALUATED
        elif self.pending_case_ids:
            expected = FunctionalDatasetCoverageStatus.PARTIALLY_EVALUATED
        else:
            expected = FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED
        if self.coverage_status != expected:
            raise ValueError(
                f"coverage_status={self.coverage_status.value} no coincide con case_results "
                f"(deberia ser {expected.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_metrics_match_case_results(self) -> FunctionalDatasetValidationReport:
        tp = fn = fp = tn = 0
        for case in self.case_results:
            if case.applicability == Applicability.NOT_APPLICABLE:
                continue
            if case.kind == GroundTruthCaseKind.POSITIVE:
                if case.outcome == MatchOutcome.MATCHED:
                    tp += 1
                elif case.outcome == MatchOutcome.MISSING:
                    fn += 1
            elif case.outcome == MatchOutcome.UNEXPECTED_CANDIDATES:
                fp += 1
            else:
                tn += 1
        if (
            self.metrics.true_positive_count != tp
            or self.metrics.false_negative_count != fn
            or self.metrics.false_positive_count != fp
            or self.metrics.true_negative_count != tn
        ):
            raise ValueError(
                "metrics no coincide con la agregacion real de case_results: esperado "
                f"tp={tp} fn={fn} fp={fp} tn={tn}, recibido "
                f"tp={self.metrics.true_positive_count} fn={self.metrics.false_negative_count} "
                f"fp={self.metrics.false_positive_count} tn={self.metrics.true_negative_count}"
            )
        return self

    @model_validator(mode="after")
    def _check_dataset_disposition(self) -> FunctionalDatasetValidationReport:
        if self.coverage_status != FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED:
            expected = FunctionalDatasetDisposition.NOT_EVALUATED
        else:
            required_satisfied = all(
                c.outcome == MatchOutcome.MATCHED
                for c in self.case_results
                if c.kind == GroundTruthCaseKind.POSITIVE
            )
            forbidden_satisfied = all(
                c.outcome == MatchOutcome.CONFIRMED_ABSENT
                for c in self.case_results
                if c.kind == GroundTruthCaseKind.NEGATIVE
            )
            expected = (
                FunctionalDatasetDisposition.PASS_ENGINEERING
                if required_satisfied and forbidden_satisfied
                else FunctionalDatasetDisposition.FAIL_ENGINEERING
            )
        if self.dataset_disposition != expected:
            raise ValueError(
                f"dataset_disposition={self.dataset_disposition.value} no coincide con "
                f"coverage_status/case_results (deberia ser {expected.value})"
            )
        return self
