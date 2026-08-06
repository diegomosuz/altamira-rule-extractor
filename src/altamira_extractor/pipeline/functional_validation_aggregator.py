"""Analizador puro del agregador MULTI-RUN de validacion funcional
(cierre de Fase 15B2-A, Seccion 2): sin filesystem, sin Neo4j, sin LLM.
Combina N `FunctionalValidationReport` (Parte F, cada uno de UN run) en
UN `FunctionalDatasetValidationReport` (`contracts/functional_dataset_
validation.py`) que cubre el catalogo COMPLETO de un
`FunctionalGroundTruthSet`.

Reglas de agregacion (ver tambien el docstring del contrato):

1. Todos los reportes de entrada deben declarar el MISMO
   `ground_truth_catalog_edition` que `ground_truth.catalog_edition`
   (nunca mezclar datasets/versiones distintos).
2. Ningun `run_id` puede repetirse entre reportes de entrada.
3. Para cada `case_id` del catalogo COMPLETO: se toman los reportes
   donde ese caso resulto `APPLICABLE`. Cero -> pendiente
   (`Applicability.NOT_APPLICABLE`, nunca FN). Uno -> se usa
   directamente. Dos o mas con el MISMO `outcome` -> duplicado,
   reconciliado eligiendo el de `run_id` menor (orden deterministico).
   Dos o mas con `outcome` DISTINTO -> conflicto BLOQUEANTE:
   `FunctionalDatasetAggregationError` de inmediato, nunca se construye
   un reporte parcial."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from itertools import groupby

from ..contracts.functional_dataset_validation import (
    DatasetCaseAggregateResult,
    FunctionalDatasetLane,
    FunctionalDatasetValidationReport,
)
from ..contracts.functional_ground_truth import FunctionalGroundTruthSet, GroundTruthCaseKind
from ..contracts.functional_validation import (
    Applicability,
    FunctionalDatasetCoverageStatus,
    FunctionalDatasetDisposition,
    FunctionalValidationMetrics,
    FunctionalValidationReport,
    GroundTruthCaseResult,
    MatchOutcome,
)
from .errors import FunctionalDatasetAggregationError

_CASE_LEVEL_METRICS_LIMITATION = (
    "case_metrics unicamente disponible para casos POSITIVE con outcome=MATCHED "
    "respaldados por exactamente un UnifiedCandidateReference; guardrail_verdict/"
    "guardrail_repair_attempts solo cuando el candidato es source=V1 con "
    "GuardrailCandidateArtifact real -- V2/interprocedural quedan sin esos dos campos "
    "(nunca un valor inventado)."
)


def _content_hash(report: FunctionalValidationReport) -> str:
    return hashlib.sha256(report.to_stable_json().encode("utf-8")).hexdigest()


def _resolve_case(
    case_id: str,
    kind: GroundTruthCaseKind,
    occurrences: list[tuple[str, GroundTruthCaseResult]],
) -> tuple[DatasetCaseAggregateResult, bool]:
    """`occurrences`: lista de (run_id, GroundTruthCaseResult) donde ese
    caso resulto APPLICABLE, ya ordenada por run_id ascendente. Devuelve
    (resultado_resuelto, es_duplicado)."""
    if not occurrences:
        return (
            DatasetCaseAggregateResult(
                case_id=case_id,
                kind=kind,
                applicability=Applicability.NOT_APPLICABLE,
                outcome=MatchOutcome.NOT_EVALUATED,
            ),
            False,
        )

    distinct_outcomes = {result.outcome for _run_id, result in occurrences}
    if len(distinct_outcomes) > 1:
        conflicting_runs = ", ".join(run_id for run_id, _result in occurrences)
        raise FunctionalDatasetAggregationError(
            f"case_id={case_id!r}: conflicto bloqueante -- outcomes distintos "
            f"({sorted(o.value for o in distinct_outcomes)}) entre runs [{conflicting_runs}]"
        )

    is_duplicate = len(occurrences) > 1
    chosen_run_id, chosen_result = occurrences[0]
    return (
        DatasetCaseAggregateResult(
            case_id=case_id,
            kind=kind,
            applicability=Applicability.APPLICABLE,
            outcome=chosen_result.outcome,
            source_run_id=chosen_run_id,
            case_metrics=chosen_result.case_metrics,
        ),
        is_duplicate,
    )


def aggregate_functional_validation_reports(
    reports: Sequence[FunctionalValidationReport],
    *,
    ground_truth: FunctionalGroundTruthSet,
    dataset_id: str,
    lane: FunctionalDatasetLane,
) -> FunctionalDatasetValidationReport:
    """Analizador puro: agrega `reports` (cada uno ya calculado por
    `functional_validation_matcher.validate_ground_truth` para UN run)
    contra el catalogo COMPLETO de `ground_truth`. Lanza
    `FunctionalDatasetAggregationError` ante cualquier combinacion
    invalida (dataset/version mezclados, run_id repetido, conflicto de
    caso) -- nunca construye un `FunctionalDatasetValidationReport`
    parcial en esos casos."""
    if not reports:
        raise FunctionalDatasetAggregationError(
            "aggregate_functional_validation_reports requiere al menos un reporte"
        )

    for report in reports:
        if report.ground_truth_catalog_edition != ground_truth.catalog_edition:
            raise FunctionalDatasetAggregationError(
                f"run_id={report.run_id!r}: ground_truth_catalog_edition="
                f"{report.ground_truth_catalog_edition!r} no coincide con el dataset "
                f"esperado ({ground_truth.catalog_edition!r}) -- no se mezclan versiones "
                "incompatibles del catalogo"
            )

    run_ids = [report.run_id for report in reports]
    if len(run_ids) != len(set(run_ids)):
        raise FunctionalDatasetAggregationError(
            f"run_id repetido entre los reportes de entrada: {sorted(run_ids)}"
        )

    reports_by_case_id: dict[str, list[tuple[str, GroundTruthCaseResult]]] = {
        case.case_id: [] for case in ground_truth.cases
    }
    for report in reports:
        for case_result in report.case_results:
            if case_result.applicability == Applicability.APPLICABLE:
                reports_by_case_id[case_result.case_id].append((report.run_id, case_result))

    case_results: list[DatasetCaseAggregateResult] = []
    duplicate_case_ids: list[str] = []
    for case in ground_truth.cases:
        occurrences = sorted(reports_by_case_id[case.case_id], key=lambda item: item[0])
        resolved, is_duplicate = _resolve_case(case.case_id, case.kind, occurrences)
        case_results.append(resolved)
        if is_duplicate:
            duplicate_case_ids.append(case.case_id)
    case_results.sort(key=lambda c: c.case_id)
    duplicate_case_ids.sort()

    required_cases = [c for c in case_results if c.kind == GroundTruthCaseKind.POSITIVE]
    forbidden_cases = [c for c in case_results if c.kind == GroundTruthCaseKind.NEGATIVE]
    required_case_count = len(required_cases)
    evaluated_required_case_count = sum(
        1 for c in required_cases if c.applicability == Applicability.APPLICABLE
    )
    forbidden_case_count = len(forbidden_cases)
    evaluated_forbidden_case_count = sum(
        1 for c in forbidden_cases if c.applicability == Applicability.APPLICABLE
    )
    pending_case_ids = sorted(
        c.case_id for c in case_results if c.applicability == Applicability.NOT_APPLICABLE
    )

    any_applicable = any(c.applicability == Applicability.APPLICABLE for c in case_results)
    if not any_applicable:
        coverage_status = FunctionalDatasetCoverageStatus.NOT_EVALUATED
    elif pending_case_ids:
        coverage_status = FunctionalDatasetCoverageStatus.PARTIALLY_EVALUATED
    else:
        coverage_status = FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED

    tp = fn = fp = tn = 0
    for aggregated_case in case_results:
        if aggregated_case.applicability == Applicability.NOT_APPLICABLE:
            continue
        if aggregated_case.kind == GroundTruthCaseKind.POSITIVE:
            if aggregated_case.outcome == MatchOutcome.MATCHED:
                tp += 1
            elif aggregated_case.outcome == MatchOutcome.MISSING:
                fn += 1
        elif aggregated_case.outcome == MatchOutcome.UNEXPECTED_CANDIDATES:
            fp += 1
        else:
            tn += 1
    precision = (tp / (tp + fp)) if (tp + fp) > 0 else None
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else None
    if precision is None or recall is None:
        f1_score = None
    elif precision + recall == 0:
        f1_score = 0.0
    else:
        f1_score = 2 * precision * recall / (precision + recall)
    metrics = FunctionalValidationMetrics(
        true_positive_count=tp,
        false_negative_count=fn,
        false_positive_count=fp,
        true_negative_count=tn,
        precision=precision,
        recall=recall,
        f1_score=f1_score,
    )

    if coverage_status != FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED:
        dataset_disposition = FunctionalDatasetDisposition.NOT_EVALUATED
    else:
        required_satisfied = all(
            c.outcome == MatchOutcome.MATCHED
            for c in case_results
            if c.kind == GroundTruthCaseKind.POSITIVE
        )
        forbidden_satisfied = all(
            c.outcome == MatchOutcome.CONFIRMED_ABSENT
            for c in case_results
            if c.kind == GroundTruthCaseKind.NEGATIVE
        )
        dataset_disposition = (
            FunctionalDatasetDisposition.PASS_ENGINEERING
            if required_satisfied and forbidden_satisfied
            else FunctionalDatasetDisposition.FAIL_ENGINEERING
        )

    source_report_ids = sorted({_content_hash(report) for report in reports})
    source_run_ids = sorted({report.run_id for report in reports})
    report_id_payload = json.dumps(
        {
            "dataset_id": dataset_id,
            "dataset_version": ground_truth.catalog_edition,
            "lane": lane.value,
            "source_report_ids": source_report_ids,
        },
        sort_keys=True,
    )
    report_id = hashlib.sha256(report_id_payload.encode("utf-8")).hexdigest()

    diagnostics: list[str] = []
    for case_id, group in groupby(duplicate_case_ids):
        count = len(list(group))
        if count:
            diagnostics.append(f"case_id={case_id!r} reconciliado como duplicado")

    return FunctionalDatasetValidationReport(
        report_id=report_id,
        dataset_id=dataset_id,
        dataset_version=ground_truth.catalog_edition,
        lane=lane,
        source_report_ids=source_report_ids,
        source_run_ids=source_run_ids,
        case_results=case_results,
        required_case_count=required_case_count,
        evaluated_required_case_count=evaluated_required_case_count,
        forbidden_case_count=forbidden_case_count,
        evaluated_forbidden_case_count=evaluated_forbidden_case_count,
        pending_case_ids=pending_case_ids,
        duplicate_case_ids=duplicate_case_ids,
        coverage_status=coverage_status,
        metrics=metrics,
        dataset_disposition=dataset_disposition,
        limitations=[_CASE_LEVEL_METRICS_LIMITATION],
        diagnostics=diagnostics,
    )
