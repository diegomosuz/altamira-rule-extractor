"""Contrato tipado del reporte de validacion funcional (Fase 15B2-A,
Parte F). Compara `FunctionalGroundTruthSet` (Parte E,
`contracts/functional_ground_truth.py`) contra `UnifiedCandidateReference`
reales de un run (`CandidatePromotionAssessmentArtifact.candidate_references`,
Fase 9), y persiste en `<run_dir>/diagnostics/functional-validation-report.json`.

Decision arquitectonica #10 (obligatoria): el matching primario es
DETERMINISTICO y EXACTO tras normalizacion -- nunca LLM, nunca embeddings,
nunca similitud semantica. `_match_expected_rule` (`pipeline/
functional_validation_matcher.py`) compara UNICAMENTE
`(rule_family, program, paragraph opcional)` como igualdad exacta de
string tras un `.strip()` (la unica "normalizacion" permitida); nunca hay
umbral de similitud, nunca hay un score continuo de coincidencia.

Granularidad HONESTA de precision/recall/F1 (ver `FunctionalValidationMetrics`):
este catalogo de ground truth es un conjunto de aserciones puntuales
(spot-check), NUNCA una etiqueta exhaustiva de cada candidato real como
verdadero/falso positivo -- construir eso exigiria etiquetar manualmente
TODOS los candidatos de TODOS los runs, fuera de alcance de este bloque.
Por eso las metricas se computan a nivel de EXPECTATION (para casos
POSITIVE, cada `GroundTruthExpectedRule` es una unidad TP/FN) y de CASO
completo (para casos NEGATIVE, sin desglose por candidato individual, ver
`GroundTruthCaseResult.unexpected_candidate_reference_ids`): un
"precision=1.0" de este reporte significa "todas las aserciones
verificadas se cumplieron", NUNCA "el detector nunca produce falsos
positivos en general".

Aplicabilidad (checkpoint correctivo, cierre de Fase 15B2-A): un
`GroundTruthCase` solo se evalua contra un run cuando TODAS sus
`fixtures[].sha256` (Parte E) aparecen entre los archivos realmente
ingeridos por ESE run (`run_dir/work/extracted/**`, ver
`pipeline/functional_validation_service.py::_compute_run_fixture_hashes`).
Un caso cuyo fixture set no esta presente en el run queda
`applicability=NOT_APPLICABLE`/`outcome=NOT_EVALUATED`: NUNCA contribuye
a TP/FP/FN/TN, NUNCA se cuenta como MISSING solo porque el paquete
evaluado es otro. Esto evita el defecto original: evaluar los 4 casos del
catalogo sintetico contra CUALQUIER run, incluyendo paquetes reales de
ingenieria que nunca podrian contener esas fixtures.

Completitud del dataset (segundo checkpoint correctivo): aplicabilidad
de UN caso a UN run es una nocion DISTINTA de completitud de la
evaluacion GLOBAL del dataset. Un run que evalua 3 de 4 casos del
catalogo (porque el cuarto vive en otro paquete, p. ej.
BY_REFERENCE_OUTPUT) nunca puede afirmar por si solo que el dataset
"paso" -- el caso ausente sigue PENDIENTE, no aprobado ni reprobado.
`FunctionalDatasetCoverageStatus`/`pending_case_ids`/`required_case_count`
hacen esta distincion explicita en `FunctionalValidationReport`: un
reporte de UN SOLO run con casos REQUIRED pendientes queda
`coverage_status=PARTIALLY_EVALUATED`, y `dataset_disposition` NUNCA
puede ser `PASS_ENGINEERING` mientras `coverage_status !=
COMPLETELY_EVALUATED` -- sin importar que todos los casos que SI se
evaluaron hayan resultado correctos. Combinar varios reportes de runs
distintos hasta cubrir el catalogo completo es responsabilidad de
`pipeline/functional_validation_aggregator.py::FunctionalDatasetValidationReport`
(un contrato separado, multi-run)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, Sha256Hex
from .candidate_promotion_assessment import UnifiedRuleFamily
from .functional_ground_truth import GroundTruthCaseKind


class Applicability(StrEnum):
    """Si el fixture set de un `GroundTruthCase` (o, agregado, de todo el
    `FunctionalGroundTruthSet`) esta presente en el run evaluado.
    APPLICABLE nunca implica MATCHED -- solo que el caso fue realmente
    evaluado contra evidencia real de ese run."""

    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FunctionalDatasetCoverageStatus(StrEnum):
    """Completitud de la evaluacion del catalogo COMPLETO (todos los
    `GroundTruthCase`, REQUIRED=POSITIVE y FORBIDDEN=NEGATIVE), distinta
    de `Applicability` (que es por-caso, por-run). NOT_EVALUATED: ningun
    caso del catalogo fue aplicable en el/los run(s) considerados.
    PARTIALLY_EVALUATED: al menos un caso evaluado, pero al menos uno
    todavia pendiente (`pending_case_ids` no vacio).
    COMPLETELY_EVALUATED: todos los casos del catalogo fueron aplicables
    en algun run considerado -- unico estado que habilita
    `PASS_ENGINEERING`."""

    NOT_EVALUATED = "NOT_EVALUATED"
    PARTIALLY_EVALUATED = "PARTIALLY_EVALUATED"
    COMPLETELY_EVALUATED = "COMPLETELY_EVALUATED"


class FunctionalDatasetDisposition(StrEnum):
    """Disposicion agregada del dataset completo contra UN run (o, en
    `FunctionalDatasetValidationReport`, contra la union de varios).
    NOT_EVALUATED cuando `dataset_applicability=NOT_APPLICABLE` O cuando
    `coverage_status != COMPLETELY_EVALUATED` (checkpoint correctivo:
    un reporte parcial JAMAS puede afirmar PASS_ENGINEERING, sin importar
    que los casos evaluados hayan resultado correctos). PASS_ENGINEERING
    exige ADEMAS que TODOS los casos REQUIRED (POSITIVE) esten MATCHED y
    TODOS los casos FORBIDDEN (NEGATIVE) esten CONFIRMED_ABSENT -- nunca
    implica aprobacion de dominio (CLAUDE.md: FUNCTIONALLY_APPROVED fuera
    de alcance V1)."""

    NOT_EVALUATED = "NOT_EVALUATED"
    PASS_ENGINEERING = "PASS_ENGINEERING"
    FAIL_ENGINEERING = "FAIL_ENGINEERING"


class CaseMetricStatus(StrEnum):
    """Si fue posible asociar de forma INEQUIVOCA un caso MATCHED con su
    candidato/evidencia/guardrail de origen para calcular metricas a
    nivel de CASO (nunca copiadas del run completo)."""

    EVALUATED = "EVALUATED"
    NOT_EVALUATED = "NOT_EVALUATED"


class CaseMetricReasonCode(StrEnum):
    """Catalogo CERRADO de motivos por los que `CaseLevelMetrics` queda
    `NOT_EVALUATED` -- nunca texto libre sin codigo."""

    CASE_ARTIFACT_TRACEABILITY_UNAVAILABLE = "CASE_ARTIFACT_TRACEABILITY_UNAVAILABLE"


class CaseLevelMetrics(AltamiraBaseModel):
    """Metricas a nivel de UN caso MATCHED especifico -- NUNCA una copia
    de `FunctionalValidationMetrics` (nivel de run). Solo se adjunta a
    `GroundTruthCaseResult` cuando `outcome=MATCHED`; para cualquier otro
    outcome, `GroundTruthCaseResult.case_metrics` es `None` (nada que
    trazar). `status=EVALUATED` exige asociacion inequivoca con un
    `UnifiedCandidateReference` real (`unified_reference_id`) Y, cuando
    `source=V1`, con su `GuardrailCandidateArtifact` real
    (`candidate_id` = `source_candidate_id`) -- V2/interprocedural nunca
    pasan por guardrails, asi que quedan `NOT_EVALUATED` con
    `reason_code=CASE_ARTIFACT_TRACEABILITY_UNAVAILABLE`, nunca con
    valores inventados."""

    status: CaseMetricStatus
    reason_code: CaseMetricReasonCode | None = None
    unified_reference_id: str | None = None
    source_candidate_id: str | None = None
    evidence_reference_count: int | None = Field(default=None, ge=0)
    provenance_reference_count: int | None = Field(default=None, ge=0)
    guardrail_verdict: str | None = None
    guardrail_repair_attempts: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_status_shape(self) -> CaseLevelMetrics:
        if self.status == CaseMetricStatus.NOT_EVALUATED:
            if self.reason_code is None:
                raise ValueError("status=NOT_EVALUATED exige reason_code")
            populated = (
                self.unified_reference_id,
                self.source_candidate_id,
                self.evidence_reference_count,
                self.provenance_reference_count,
                self.guardrail_verdict,
                self.guardrail_repair_attempts,
            )
            if any(value is not None for value in populated):
                raise ValueError(
                    "status=NOT_EVALUATED no puede declarar ningun valor de metrica "
                    "(evitar sustituir con null parcial)"
                )
        else:
            if self.reason_code is not None:
                raise ValueError("status=EVALUATED no puede declarar reason_code")
            if self.unified_reference_id is None or self.source_candidate_id is None:
                raise ValueError(
                    "status=EVALUATED exige unified_reference_id y source_candidate_id"
                )
        return self


class MatchOutcome(StrEnum):
    """Resultado de UNA unidad de clasificacion (expectation POSITIVE o
    caso NEGATIVE completo). NOT_EVALUATED es exclusivo de
    `applicability=NOT_APPLICABLE` -- nunca coexiste con un resultado de
    matching real."""

    MATCHED = "MATCHED"
    MISSING = "MISSING"
    CONFIRMED_ABSENT = "CONFIRMED_ABSENT"
    UNEXPECTED_CANDIDATES = "UNEXPECTED_CANDIDATES"
    NOT_EVALUATED = "NOT_EVALUATED"


class ExpectedRuleMatchResult(AltamiraBaseModel):
    """Resultado del matching de UN `GroundTruthExpectedRule` (siempre
    dentro de un caso `POSITIVE`). `outcome` es MATCHED cuando
    `matched_count >= minimum_count`, MISSING en caso contrario -- nunca
    un tercer valor intermedio (decision #10: matching exacto, sin
    resultados parciales/difusos)."""

    expectation_id: str = Field(min_length=1, max_length=100)
    rule_family: UnifiedRuleFamily
    minimum_count: int = Field(ge=1)
    matched_count: int = Field(ge=0)
    outcome: Literal[MatchOutcome.MATCHED, MatchOutcome.MISSING]
    matched_unified_reference_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_outcome_matches_counts(self) -> ExpectedRuleMatchResult:
        expected_outcome = (
            MatchOutcome.MATCHED
            if self.matched_count >= self.minimum_count
            else MatchOutcome.MISSING
        )
        if self.outcome != expected_outcome:
            raise ValueError(
                f"expectation_id={self.expectation_id!r}: outcome={self.outcome.value} "
                f"no coincide con matched_count={self.matched_count} vs "
                f"minimum_count={self.minimum_count} (deberia ser {expected_outcome.value})"
            )
        if len(self.matched_unified_reference_ids) != self.matched_count:
            raise ValueError(
                f"expectation_id={self.expectation_id!r}: matched_count "
                f"({self.matched_count}) != len(matched_unified_reference_ids) "
                f"({len(self.matched_unified_reference_ids)})"
            )
        if self.matched_unified_reference_ids != sorted(set(self.matched_unified_reference_ids)):
            raise ValueError(
                f"expectation_id={self.expectation_id!r}: matched_unified_reference_ids debe "
                "estar ordenado y sin duplicados"
            )
        return self


class GroundTruthCaseResult(AltamiraBaseModel):
    """Resultado del matching de UN `GroundTruthCase` completo.

    Cuando `applicability=NOT_APPLICABLE` (el fixture set del caso no
    esta presente en el run evaluado): `outcome=NOT_EVALUATED`,
    `expectation_results`/`unexpected_candidate_reference_ids` SIEMPRE
    vacios, sin importar `kind` -- el caso nunca se compara contra
    `candidate_references` en absoluto (ver `pipeline/functional_
    validation_matcher.py::_evaluate_positive_case`/
    `_evaluate_negative_case`, que cortocircuitan antes de iterar
    candidatos).

    Cuando `applicability=APPLICABLE`: para `kind=POSITIVE`,
    `expectation_results` siempre tiene la misma cantidad de entradas
    que `expected_rules` del caso original y
    `unexpected_candidate_reference_ids` siempre esta vacia. Para
    `kind=NEGATIVE`, `expectation_results` siempre esta vacia."""

    case_id: str = Field(min_length=1, max_length=100)
    kind: GroundTruthCaseKind
    program: str = Field(min_length=1)
    applicability: Applicability
    outcome: MatchOutcome
    expectation_results: list[ExpectedRuleMatchResult] = Field(default_factory=list)
    unexpected_candidate_reference_ids: list[str] = Field(default_factory=list)
    case_metrics: CaseLevelMetrics | None = None

    @model_validator(mode="after")
    def _check_case_metrics_only_when_matched(self) -> GroundTruthCaseResult:
        if self.outcome != MatchOutcome.MATCHED and self.case_metrics is not None:
            raise ValueError(
                f"case_id={self.case_id!r}: case_metrics solo aplica a outcome=MATCHED "
                f"(recibido outcome={self.outcome.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_kind_shape(self) -> GroundTruthCaseResult:
        if self.applicability == Applicability.NOT_APPLICABLE:
            if self.outcome != MatchOutcome.NOT_EVALUATED:
                raise ValueError(
                    f"case_id={self.case_id!r}: applicability=NOT_APPLICABLE exige "
                    f"outcome=NOT_EVALUATED (recibido {self.outcome.value})"
                )
            if self.expectation_results:
                raise ValueError(
                    f"case_id={self.case_id!r}: applicability=NOT_APPLICABLE no puede "
                    "declarar expectation_results (el caso nunca se evaluo)"
                )
            if self.unexpected_candidate_reference_ids:
                raise ValueError(
                    f"case_id={self.case_id!r}: applicability=NOT_APPLICABLE no puede "
                    "declarar unexpected_candidate_reference_ids (el caso nunca se evaluo)"
                )
            return self
        if self.outcome == MatchOutcome.NOT_EVALUATED:
            raise ValueError(
                f"case_id={self.case_id!r}: applicability=APPLICABLE no puede tener "
                "outcome=NOT_EVALUATED"
            )
        if self.kind == GroundTruthCaseKind.POSITIVE:
            if self.unexpected_candidate_reference_ids:
                raise ValueError(
                    f"case_id={self.case_id!r}: kind=POSITIVE no puede declarar "
                    "unexpected_candidate_reference_ids"
                )
            if not self.expectation_results:
                raise ValueError(
                    f"case_id={self.case_id!r}: kind=POSITIVE exige al menos un "
                    "ExpectedRuleMatchResult"
                )
            expected_outcome = (
                MatchOutcome.MATCHED
                if all(r.outcome == MatchOutcome.MATCHED for r in self.expectation_results)
                else MatchOutcome.MISSING
            )
        else:
            if self.expectation_results:
                raise ValueError(
                    f"case_id={self.case_id!r}: kind=NEGATIVE no puede declarar "
                    "expectation_results"
                )
            expected_outcome = (
                MatchOutcome.UNEXPECTED_CANDIDATES
                if self.unexpected_candidate_reference_ids
                else MatchOutcome.CONFIRMED_ABSENT
            )
        if self.outcome != expected_outcome:
            raise ValueError(
                f"case_id={self.case_id!r}: outcome={self.outcome.value} no coincide con el "
                f"resultado real de sus componentes (deberia ser {expected_outcome.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_unexpected_ids_sorted_and_unique(self) -> GroundTruthCaseResult:
        ids = self.unexpected_candidate_reference_ids
        if ids != sorted(set(ids)):
            raise ValueError(
                f"case_id={self.case_id!r}: unexpected_candidate_reference_ids debe estar "
                "ordenado y sin duplicados"
            )
        return self

    @model_validator(mode="after")
    def _check_expectation_results_sorted_and_unique(self) -> GroundTruthCaseResult:
        ids = [r.expectation_id for r in self.expectation_results]
        if len(ids) != len(set(ids)):
            raise ValueError(f"case_id={self.case_id!r}: expectation_results duplicado")
        if ids != sorted(ids):
            raise ValueError(
                f"case_id={self.case_id!r}: expectation_results no esta ordenado "
                "deterministicamente por expectation_id"
            )
        return self


class FunctionalValidationMetrics(AltamiraBaseModel):
    """Precision/recall/F1 a la granularidad HONESTA descrita en el
    docstring del modulo: expectation-level para POSITIVE, case-level
    para NEGATIVE. `precision`/`recall`/`f1_score` son `None` unicamente
    cuando su denominador real es cero (nunca se sustituye por 0.0/1.0
    arbitrario: `precision` sin ningun caso NEGATIVE, o `recall` sin
    ningun caso POSITIVE, no tienen un valor matematicamente definido)."""

    true_positive_count: int = Field(ge=0)
    false_negative_count: int = Field(ge=0)
    false_positive_count: int = Field(ge=0)
    true_negative_count: int = Field(ge=0)
    precision: float | None = Field(default=None, ge=0.0, le=1.0)
    recall: float | None = Field(default=None, ge=0.0, le=1.0)
    f1_score: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_metrics_match_counts(self) -> FunctionalValidationMetrics:
        tp, fp, fn = self.true_positive_count, self.false_positive_count, self.false_negative_count

        precision_denominator = tp + fp
        expected_precision = (tp / precision_denominator) if precision_denominator > 0 else None
        if not _close(self.precision, expected_precision):
            raise ValueError(
                f"precision ({self.precision}) no coincide con tp/(tp+fp) "
                f"({expected_precision})"
            )

        recall_denominator = tp + fn
        expected_recall = (tp / recall_denominator) if recall_denominator > 0 else None
        if not _close(self.recall, expected_recall):
            raise ValueError(
                f"recall ({self.recall}) no coincide con tp/(tp+fn) ({expected_recall})"
            )

        if self.precision is None or self.recall is None:
            expected_f1 = None
        elif self.precision + self.recall == 0:
            expected_f1 = 0.0
        else:
            expected_f1 = 2 * self.precision * self.recall / (self.precision + self.recall)
        if not _close(self.f1_score, expected_f1):
            raise ValueError(
                f"f1_score ({self.f1_score}) no coincide con el valor esperado ({expected_f1})"
            )
        return self


def _close(a: float | None, b: float | None, *, tolerance: float = 1e-9) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tolerance


class FunctionalValidationReport(AltamiraBaseModel):
    """Contenedor persistido en `<run_dir>/diagnostics/functional-
    validation-report.json`. NO contractual, sin timestamps: dos
    ejecuciones sobre los mismos artefactos de entrada deben producir
    bytes identicos."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    ground_truth_catalog_edition: str = Field(min_length=1, max_length=100)
    dataset_applicability: Applicability
    coverage_status: FunctionalDatasetCoverageStatus
    required_case_count: int = Field(ge=0)
    evaluated_required_case_count: int = Field(ge=0)
    forbidden_case_count: int = Field(ge=0)
    evaluated_forbidden_case_count: int = Field(ge=0)
    pending_case_ids: list[str] = Field(default_factory=list)
    dataset_disposition: FunctionalDatasetDisposition
    case_results: list[GroundTruthCaseResult] = Field(default_factory=list)
    metrics: FunctionalValidationMetrics

    @model_validator(mode="after")
    def _check_case_results_sorted_and_unique(self) -> FunctionalValidationReport:
        ids = [c.case_id for c in self.case_results]
        if len(ids) != len(set(ids)):
            raise ValueError("case_results contiene case_id duplicado")
        if ids != sorted(ids):
            raise ValueError("case_results no esta ordenado deterministicamente por case_id")
        return self

    @model_validator(mode="after")
    def _check_metrics_match_case_results(self) -> FunctionalValidationReport:
        """Solo los casos `applicability=APPLICABLE` contribuyen a
        `metrics` -- un caso NOT_APPLICABLE (fixture set ausente de este
        run) nunca cuenta como FN/TN ni de ninguna otra forma (checkpoint
        correctivo: el defecto original contaba cada caso NOT_APPLICABLE
        como si el run lo hubiese fallado)."""
        expected_tp = 0
        expected_fn = 0
        expected_fp = 0
        expected_tn = 0
        for case in self.case_results:
            if case.applicability == Applicability.NOT_APPLICABLE:
                continue
            if case.kind == GroundTruthCaseKind.POSITIVE:
                expected_tp += sum(
                    1 for r in case.expectation_results if r.outcome == MatchOutcome.MATCHED
                )
                expected_fn += sum(
                    1 for r in case.expectation_results if r.outcome == MatchOutcome.MISSING
                )
            else:
                if case.outcome == MatchOutcome.UNEXPECTED_CANDIDATES:
                    expected_fp += 1
                else:
                    expected_tn += 1
        if (
            self.metrics.true_positive_count != expected_tp
            or self.metrics.false_negative_count != expected_fn
            or self.metrics.false_positive_count != expected_fp
            or self.metrics.true_negative_count != expected_tn
        ):
            raise ValueError(
                "metrics no coincide con la agregacion real de case_results (solo casos "
                "APPLICABLE): esperado "
                f"tp={expected_tp} fn={expected_fn} fp={expected_fp} tn={expected_tn}, "
                f"recibido tp={self.metrics.true_positive_count} "
                f"fn={self.metrics.false_negative_count} fp={self.metrics.false_positive_count} "
                f"tn={self.metrics.true_negative_count}"
            )
        return self

    @model_validator(mode="after")
    def _check_dataset_applicability_matches_case_results(self) -> FunctionalValidationReport:
        any_applicable = any(
            c.applicability == Applicability.APPLICABLE for c in self.case_results
        )
        expected_applicability = (
            Applicability.APPLICABLE if any_applicable else Applicability.NOT_APPLICABLE
        )
        if self.dataset_applicability != expected_applicability:
            raise ValueError(
                f"dataset_applicability={self.dataset_applicability.value} no coincide con "
                f"case_results (deberia ser {expected_applicability.value}: "
                f"{'al menos un caso APPLICABLE' if any_applicable else 'ningun caso APPLICABLE'})"
            )
        return self

    @model_validator(mode="after")
    def _check_coverage_counts_match_case_results(self) -> FunctionalValidationReport:
        required_cases = [c for c in self.case_results if c.kind == GroundTruthCaseKind.POSITIVE]
        forbidden_cases = [c for c in self.case_results if c.kind == GroundTruthCaseKind.NEGATIVE]
        expected_required_count = len(required_cases)
        expected_evaluated_required = sum(
            1 for c in required_cases if c.applicability == Applicability.APPLICABLE
        )
        expected_forbidden_count = len(forbidden_cases)
        expected_evaluated_forbidden = sum(
            1 for c in forbidden_cases if c.applicability == Applicability.APPLICABLE
        )
        if self.required_case_count != expected_required_count:
            raise ValueError(
                f"required_case_count ({self.required_case_count}) != cantidad real de casos "
                f"POSITIVE ({expected_required_count})"
            )
        if self.evaluated_required_case_count != expected_evaluated_required:
            raise ValueError(
                "evaluated_required_case_count "
                f"({self.evaluated_required_case_count}) != cantidad real de casos POSITIVE "
                f"APPLICABLE ({expected_evaluated_required})"
            )
        if self.forbidden_case_count != expected_forbidden_count:
            raise ValueError(
                f"forbidden_case_count ({self.forbidden_case_count}) != cantidad real de casos "
                f"NEGATIVE ({expected_forbidden_count})"
            )
        if self.evaluated_forbidden_case_count != expected_evaluated_forbidden:
            raise ValueError(
                "evaluated_forbidden_case_count "
                f"({self.evaluated_forbidden_case_count}) != cantidad real de casos NEGATIVE "
                f"APPLICABLE ({expected_evaluated_forbidden})"
            )
        return self

    @model_validator(mode="after")
    def _check_pending_case_ids_match_case_results(self) -> FunctionalValidationReport:
        expected_pending = sorted(
            c.case_id for c in self.case_results if c.applicability == Applicability.NOT_APPLICABLE
        )
        if self.pending_case_ids != expected_pending:
            raise ValueError(
                f"pending_case_ids ({self.pending_case_ids}) no coincide con los case_id "
                f"NOT_APPLICABLE reales ({expected_pending})"
            )
        return self

    @model_validator(mode="after")
    def _check_coverage_status_matches_pending(self) -> FunctionalValidationReport:
        if self.dataset_applicability == Applicability.NOT_APPLICABLE:
            expected_coverage = FunctionalDatasetCoverageStatus.NOT_EVALUATED
        elif self.pending_case_ids:
            expected_coverage = FunctionalDatasetCoverageStatus.PARTIALLY_EVALUATED
        else:
            expected_coverage = FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED
        if self.coverage_status != expected_coverage:
            raise ValueError(
                f"coverage_status={self.coverage_status.value} no coincide con "
                f"pending_case_ids/dataset_applicability (deberia ser {expected_coverage.value})"
            )
        return self

    @model_validator(mode="after")
    def _check_dataset_disposition_matches_coverage(self) -> FunctionalValidationReport:
        """Checkpoint correctivo: PASS_ENGINEERING exige
        `coverage_status=COMPLETELY_EVALUATED` -- un reporte de un solo
        run con casos REQUIRED/FORBIDDEN todavia pendientes en otro
        paquete NUNCA puede afirmar que el dataset completo paso, sin
        importar que los casos que si evaluo hayan resultado correctos."""
        if self.coverage_status != FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED:
            expected_disposition = FunctionalDatasetDisposition.NOT_EVALUATED
        else:
            applicable_cases = [
                c for c in self.case_results if c.applicability == Applicability.APPLICABLE
            ]
            required_satisfied = all(
                c.outcome == MatchOutcome.MATCHED
                for c in applicable_cases
                if c.kind == GroundTruthCaseKind.POSITIVE
            )
            forbidden_satisfied = all(
                c.outcome == MatchOutcome.CONFIRMED_ABSENT
                for c in applicable_cases
                if c.kind == GroundTruthCaseKind.NEGATIVE
            )
            expected_disposition = (
                FunctionalDatasetDisposition.PASS_ENGINEERING
                if required_satisfied and forbidden_satisfied
                else FunctionalDatasetDisposition.FAIL_ENGINEERING
            )
        if self.dataset_disposition != expected_disposition:
            raise ValueError(
                f"dataset_disposition={self.dataset_disposition.value} no coincide con el "
                f"resultado real de case_results/coverage_status (deberia ser "
                f"{expected_disposition.value})"
            )
        return self
