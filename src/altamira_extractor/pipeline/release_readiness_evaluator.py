"""Analizador puro de release readiness funcional (Fase 15B2-A, Parte G):
sin filesystem, sin Neo4j, sin LLM. Evalua `ReleaseReadinessPolicy`
(Parte G) contra la reconciliacion estatica del catalogo semantico (Parte
C, `SemanticCoverageIssue[]`) y un `FunctionalValidationReport`
(`evaluate_release_readiness`, UN run) o un
`FunctionalDatasetValidationReport` (`evaluate_release_readiness_for_
dataset`, agregado multi-run, cierre de Fase 15B2-A). Motor de criterios
cerrado -- ver docstring de `contracts/release_readiness.py`.

Completitud (checkpoint correctivo, cierre de Fase 15B2-A): el gate para
evaluar los cuatro criterios funcionales YA NO es "algun caso
aplicable" -- es `coverage_status=COMPLETELY_EVALUATED`. Un
`FunctionalValidationReport` de UN SOLO run con casos REQUIRED/FORBIDDEN
todavia pendientes en otro run (`coverage_status=PARTIALLY_EVALUATED`)
NUNCA alcanza `engineering_functional_readiness=PASSED`, sin importar
que los casos que si evaluo hayan resultado correctos -- eso evitaba que
un run aislado (p. ej. GROUND_TRUTH_SYNTHETIC sin BY_REFERENCE_OUTPUT)
afirmara por error que el dataset completo paso. `NO_ERROR_SEVERITY_
COVERAGE_ISSUES` (estructural) se evalua siempre igual, independiente
de la completitud del ground truth."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ..contracts.enums import Severity
from ..contracts.functional_dataset_validation import FunctionalDatasetValidationReport
from ..contracts.functional_ground_truth import GroundTruthCaseKind
from ..contracts.functional_validation import (
    FunctionalDatasetCoverageStatus,
    FunctionalValidationReport,
    MatchOutcome,
)
from ..contracts.release_readiness import (
    DatasetReleaseReadinessAssessment,
    DomainFunctionalReadinessStatus,
    ReleaseReadinessAssessment,
    ReleaseReadinessCriterion,
    ReleaseReadinessCriterionKind,
    ReleaseReadinessCriterionResult,
    ReleaseReadinessCriterionStatus,
    ReleaseReadinessDisposition,
    ReleaseReadinessPolicy,
    ReleaseReadinessSummary,
    ReleaseReadinessWarning,
    ReleaseReadinessWarningCode,
)
from ..contracts.semantic_coverage import SemanticCoverageIssue

_FUNCTIONAL_CRITERION_KINDS = frozenset(
    {
        ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES,
        ReleaseReadinessCriterionKind.NO_UNEXPECTED_NEGATIVE_CASES,
        ReleaseReadinessCriterionKind.MINIMUM_RECALL,
        ReleaseReadinessCriterionKind.MINIMUM_PRECISION,
    }
)


class _CaseLike(Protocol):
    """Forma minima compartida por `GroundTruthCaseResult` (Parte F,
    por-run) y `DatasetCaseAggregateResult` (agregado multi-run) -- los
    evaluadores de criterios binarios solo necesitan `kind`/`outcome`,
    nunca el resto de cada contrato."""

    kind: GroundTruthCaseKind
    outcome: MatchOutcome


def _not_evaluated_result(
    criterion: ReleaseReadinessCriterion, *, reason: str
) -> ReleaseReadinessCriterionResult:
    return ReleaseReadinessCriterionResult(
        criterion_id=criterion.criterion_id,
        kind=criterion.kind,
        status=ReleaseReadinessCriterionStatus.NOT_EVALUATED,
        actual_value=None,
        threshold_value=criterion.minimum_value,
        message=reason,
    )


def _evaluate_no_error_coverage_issues(
    criterion: ReleaseReadinessCriterion, coverage_issues: Sequence[SemanticCoverageIssue]
) -> ReleaseReadinessCriterionResult:
    error_count = sum(1 for issue in coverage_issues if issue.severity == Severity.ERROR)
    status = (
        ReleaseReadinessCriterionStatus.PASSED
        if error_count == 0
        else ReleaseReadinessCriterionStatus.FAILED
    )
    return ReleaseReadinessCriterionResult(
        criterion_id=criterion.criterion_id,
        kind=criterion.kind,
        status=status,
        actual_value=float(error_count),
        threshold_value=None,
        message=f"{error_count} SemanticCoverageIssue(s) de severidad ERROR encontrados.",
    )


def _evaluate_no_missing_positive_cases(
    criterion: ReleaseReadinessCriterion, case_results: Sequence[_CaseLike]
) -> ReleaseReadinessCriterionResult:
    missing_count = sum(
        1
        for case in case_results
        if case.kind == GroundTruthCaseKind.POSITIVE and case.outcome == MatchOutcome.MISSING
    )
    status = (
        ReleaseReadinessCriterionStatus.PASSED
        if missing_count == 0
        else ReleaseReadinessCriterionStatus.FAILED
    )
    return ReleaseReadinessCriterionResult(
        criterion_id=criterion.criterion_id,
        kind=criterion.kind,
        status=status,
        actual_value=float(missing_count),
        threshold_value=None,
        message=f"{missing_count} caso(s) POSITIVE en outcome=MISSING.",
    )


def _evaluate_no_unexpected_negative_cases(
    criterion: ReleaseReadinessCriterion, case_results: Sequence[_CaseLike]
) -> ReleaseReadinessCriterionResult:
    unexpected_count = sum(
        1
        for case in case_results
        if case.kind == GroundTruthCaseKind.NEGATIVE
        and case.outcome == MatchOutcome.UNEXPECTED_CANDIDATES
    )
    status = (
        ReleaseReadinessCriterionStatus.PASSED
        if unexpected_count == 0
        else ReleaseReadinessCriterionStatus.FAILED
    )
    return ReleaseReadinessCriterionResult(
        criterion_id=criterion.criterion_id,
        kind=criterion.kind,
        status=status,
        actual_value=float(unexpected_count),
        threshold_value=None,
        message=f"{unexpected_count} caso(s) NEGATIVE en outcome=UNEXPECTED_CANDIDATES.",
    )


def _evaluate_minimum_metric(
    criterion: ReleaseReadinessCriterion, *, actual: float | None, metric_name: str
) -> ReleaseReadinessCriterionResult:
    assert criterion.minimum_value is not None  # garantizado por el contrato (Parte G)
    if actual is None:
        return ReleaseReadinessCriterionResult(
            criterion_id=criterion.criterion_id,
            kind=criterion.kind,
            status=ReleaseReadinessCriterionStatus.FAILED,
            actual_value=None,
            threshold_value=criterion.minimum_value,
            message=(
                f"{metric_name} no esta definido (denominador cero en "
                "FunctionalValidationMetrics); nunca se aprueba con una metrica indefinida."
            ),
        )
    status = (
        ReleaseReadinessCriterionStatus.PASSED
        if actual >= criterion.minimum_value
        else ReleaseReadinessCriterionStatus.FAILED
    )
    return ReleaseReadinessCriterionResult(
        criterion_id=criterion.criterion_id,
        kind=criterion.kind,
        status=status,
        actual_value=actual,
        threshold_value=criterion.minimum_value,
        message=f"{metric_name}={actual} (umbral minimo={criterion.minimum_value}).",
    )


_EVALUATORS_WITHOUT_METRICS = {
    ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES: (
        _evaluate_no_missing_positive_cases
    ),
    ReleaseReadinessCriterionKind.NO_UNEXPECTED_NEGATIVE_CASES: (
        _evaluate_no_unexpected_negative_cases
    ),
}


class _ReadinessComputation:
    """Resultado intermedio compartido entre `evaluate_release_
    readiness`/`evaluate_release_readiness_for_dataset` -- ambas
    funciones publicas solo difieren en como identifican al sujeto
    evaluado (`run_id`+`source_package_hash` vs `report_id`+
    `dataset_id`+`dataset_version`), nunca en la logica de evaluacion."""

    __slots__ = (
        "results",
        "structural_readiness",
        "engineering_functional_readiness",
        "domain_functional_readiness",
        "disposition",
        "warnings",
        "summary",
    )

    def __init__(
        self,
        results: list[ReleaseReadinessCriterionResult],
        structural_readiness: ReleaseReadinessCriterionStatus,
        engineering_functional_readiness: ReleaseReadinessCriterionStatus,
        domain_functional_readiness: DomainFunctionalReadinessStatus,
        disposition: ReleaseReadinessDisposition,
        warnings: list[ReleaseReadinessWarning],
        summary: ReleaseReadinessSummary,
    ) -> None:
        self.results = results
        self.structural_readiness = structural_readiness
        self.engineering_functional_readiness = engineering_functional_readiness
        self.domain_functional_readiness = domain_functional_readiness
        self.disposition = disposition
        self.warnings = warnings
        self.summary = summary


def _compute_readiness(
    policy: ReleaseReadinessPolicy,
    coverage_issues: Sequence[SemanticCoverageIssue],
    *,
    case_results: Sequence[_CaseLike],
    metrics_recall: float | None,
    metrics_precision: float | None,
    coverage_status: FunctionalDatasetCoverageStatus,
    pending_case_ids: Sequence[str],
) -> _ReadinessComputation:
    """Checkpoint correctivo central: el gate para los cuatro criterios
    funcionales es `coverage_status=COMPLETELY_EVALUATED` -- NUNCA
    "algun caso aplicable". Compartido por ambas funciones publicas."""
    coverage_complete = coverage_status == FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED

    if coverage_status == FunctionalDatasetCoverageStatus.NOT_EVALUATED:
        not_evaluated_reason = (
            "Ground truth no aplicable (ningun GroundTruthCase tiene su fixture set presente "
            "en los run(s) evaluados) -- criterio no evaluado, nunca FAILED por falta de senal."
        )
    else:
        not_evaluated_reason = (
            "Dataset coverage_status=PARTIALLY_EVALUATED -- case_id(s) REQUIRED/FORBIDDEN "
            f"todavia pendientes en otro run/paquete: {', '.join(pending_case_ids)}. Un "
            "reporte parcial nunca puede afirmar PASS_ENGINEERING del dataset completo."
        )

    results: list[ReleaseReadinessCriterionResult] = []
    for criterion in policy.criteria:
        if criterion.kind == ReleaseReadinessCriterionKind.NO_ERROR_SEVERITY_COVERAGE_ISSUES:
            results.append(_evaluate_no_error_coverage_issues(criterion, coverage_issues))
        elif criterion.kind not in _FUNCTIONAL_CRITERION_KINDS:
            raise AssertionError(f"kind sin evaluador registrado: {criterion.kind.value}")
        elif not coverage_complete:
            results.append(_not_evaluated_result(criterion, reason=not_evaluated_reason))
        elif criterion.kind == ReleaseReadinessCriterionKind.MINIMUM_RECALL:
            results.append(
                _evaluate_minimum_metric(criterion, actual=metrics_recall, metric_name="recall")
            )
        elif criterion.kind == ReleaseReadinessCriterionKind.MINIMUM_PRECISION:
            results.append(
                _evaluate_minimum_metric(
                    criterion, actual=metrics_precision, metric_name="precision"
                )
            )
        else:
            evaluator = _EVALUATORS_WITHOUT_METRICS[criterion.kind]
            results.append(evaluator(criterion, case_results))

    results.sort(key=lambda r: r.criterion_id)

    structural_results = [
        r
        for r in results
        if r.kind == ReleaseReadinessCriterionKind.NO_ERROR_SEVERITY_COVERAGE_ISSUES
    ]
    functional_results = [
        r
        for r in results
        if r.kind != ReleaseReadinessCriterionKind.NO_ERROR_SEVERITY_COVERAGE_ISSUES
    ]

    structural_readiness = (
        ReleaseReadinessCriterionStatus.PASSED
        if all(r.status == ReleaseReadinessCriterionStatus.PASSED for r in structural_results)
        else ReleaseReadinessCriterionStatus.FAILED
    )
    if not coverage_complete:
        engineering_functional_readiness = ReleaseReadinessCriterionStatus.NOT_EVALUATED
    elif all(r.status == ReleaseReadinessCriterionStatus.PASSED for r in functional_results):
        engineering_functional_readiness = ReleaseReadinessCriterionStatus.PASSED
    else:
        engineering_functional_readiness = ReleaseReadinessCriterionStatus.FAILED

    domain_functional_readiness = (
        DomainFunctionalReadinessStatus.PENDING_DOMAIN_REVIEW
        if engineering_functional_readiness == ReleaseReadinessCriterionStatus.PASSED
        else DomainFunctionalReadinessStatus.NOT_EVALUATED
    )

    if engineering_functional_readiness == ReleaseReadinessCriterionStatus.NOT_EVALUATED:
        disposition = ReleaseReadinessDisposition.NOT_EVALUATED
    else:
        all_passed = all(r.status == ReleaseReadinessCriterionStatus.PASSED for r in results)
        disposition = (
            ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_MET
            if all_passed and results
            else ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_NOT_MET
        )

    warnings: list[ReleaseReadinessWarning] = []
    if coverage_status == FunctionalDatasetCoverageStatus.NOT_EVALUATED:
        warnings.append(
            ReleaseReadinessWarning(
                code=ReleaseReadinessWarningCode.GROUND_TRUTH_NOT_AVAILABLE,
                message=(
                    "Ningun GroundTruthCase de config/ground_truth/synthetic_engineering.yaml "
                    "tiene su fixture set presente en este run -- readiness funcional de "
                    "ingenieria y de dominio quedan NOT_EVALUATED, nunca penalizadas."
                ),
            )
        )
    elif coverage_status == FunctionalDatasetCoverageStatus.PARTIALLY_EVALUATED:
        warnings.append(
            ReleaseReadinessWarning(
                code=ReleaseReadinessWarningCode.REQUIRED_GROUND_TRUTH_CASES_NOT_EXECUTED,
                message=(
                    "case_id(s) pendientes (nunca aplicables en ningun run considerado): "
                    f"{', '.join(pending_case_ids)} -- readiness funcional de ingenieria y de "
                    "dominio quedan NOT_EVALUATED hasta cubrir el dataset completo (ver "
                    "functional-validate-dataset)."
                ),
            )
        )

    summary = ReleaseReadinessSummary(
        criterion_count=len(results),
        passed_count=sum(1 for r in results if r.status == ReleaseReadinessCriterionStatus.PASSED),
        failed_count=sum(1 for r in results if r.status == ReleaseReadinessCriterionStatus.FAILED),
        not_evaluated_count=sum(
            1 for r in results if r.status == ReleaseReadinessCriterionStatus.NOT_EVALUATED
        ),
    )

    return _ReadinessComputation(
        results=results,
        structural_readiness=structural_readiness,
        engineering_functional_readiness=engineering_functional_readiness,
        domain_functional_readiness=domain_functional_readiness,
        disposition=disposition,
        warnings=warnings,
        summary=summary,
    )


def evaluate_release_readiness(
    policy: ReleaseReadinessPolicy,
    coverage_issues: Sequence[SemanticCoverageIssue],
    validation_report: FunctionalValidationReport,
) -> ReleaseReadinessAssessment:
    """Analizador puro: evalua cada `ReleaseReadinessCriterion` de
    `policy` contra `coverage_issues` (Parte C) y `validation_report`
    (Parte F, ya calculado para un run concreto). `run_id`/
    `source_package_hash` del assessment resultante se toman de
    `validation_report` -- ambas senales deben referirse al mismo run.
    `engineering_functional_readiness` NUNCA es PASSED mientras
    `validation_report.coverage_status != COMPLETELY_EVALUATED`
    (checkpoint correctivo: un run aislado con casos pendientes en otro
    run/paquete no puede afirmar que el dataset completo paso)."""
    computation = _compute_readiness(
        policy,
        coverage_issues,
        case_results=validation_report.case_results,
        metrics_recall=validation_report.metrics.recall,
        metrics_precision=validation_report.metrics.precision,
        coverage_status=validation_report.coverage_status,
        pending_case_ids=validation_report.pending_case_ids,
    )
    return ReleaseReadinessAssessment(
        run_id=validation_report.run_id,
        source_package_hash=validation_report.source_package_hash,
        policy_edition=policy.policy_edition,
        disposition=computation.disposition,
        structural_readiness=computation.structural_readiness,
        engineering_functional_readiness=computation.engineering_functional_readiness,
        domain_functional_readiness=computation.domain_functional_readiness,
        criteria_results=computation.results,
        summary=computation.summary,
        warnings=computation.warnings,
    )


def evaluate_release_readiness_for_dataset(
    policy: ReleaseReadinessPolicy,
    coverage_issues: Sequence[SemanticCoverageIssue],
    dataset_report: FunctionalDatasetValidationReport,
) -> DatasetReleaseReadinessAssessment:
    """Analizador puro: variante multi-run de `evaluate_release_
    readiness` (cierre de Fase 15B2-A, Seccion 4) -- consume un
    `FunctionalDatasetValidationReport` ya agregado (`pipeline/
    functional_validation_aggregator.py`) en vez de un
    `FunctionalValidationReport` de un solo run. Misma logica de gate
    (`coverage_status=COMPLETELY_EVALUATED`), mismo motor de criterios."""
    computation = _compute_readiness(
        policy,
        coverage_issues,
        case_results=dataset_report.case_results,
        metrics_recall=dataset_report.metrics.recall,
        metrics_precision=dataset_report.metrics.precision,
        coverage_status=dataset_report.coverage_status,
        pending_case_ids=dataset_report.pending_case_ids,
    )
    return DatasetReleaseReadinessAssessment(
        report_id=dataset_report.report_id,
        dataset_id=dataset_report.dataset_id,
        dataset_version=dataset_report.dataset_version,
        policy_edition=policy.policy_edition,
        disposition=computation.disposition,
        structural_readiness=computation.structural_readiness,
        engineering_functional_readiness=computation.engineering_functional_readiness,
        domain_functional_readiness=computation.domain_functional_readiness,
        criteria_results=computation.results,
        summary=computation.summary,
        warnings=computation.warnings,
    )
