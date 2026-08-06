"""Tests contractuales del reporte de validacion funcional (Fase
15B2-A, Parte F): `contracts/functional_validation.py`. Incluye los
checkpoints correctivos de aplicabilidad y completitud del dataset
(cierre de Fase 15B2-A)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.functional_ground_truth import GroundTruthCaseKind
from altamira_extractor.contracts.functional_validation import (
    Applicability,
    CaseLevelMetrics,
    CaseMetricReasonCode,
    CaseMetricStatus,
    ExpectedRuleMatchResult,
    FunctionalDatasetCoverageStatus,
    FunctionalDatasetDisposition,
    FunctionalValidationMetrics,
    FunctionalValidationReport,
    GroundTruthCaseResult,
    MatchOutcome,
)

_HASH = "a" * 64


def _matched_result(**overrides: object) -> ExpectedRuleMatchResult:
    defaults: dict[str, object] = {
        "expectation_id": "case-1::e1",
        "rule_family": "RETURN_CODE",
        "minimum_count": 1,
        "matched_count": 1,
        "outcome": MatchOutcome.MATCHED,
        "matched_unified_reference_ids": ["ref-1"],
    }
    defaults.update(overrides)
    return ExpectedRuleMatchResult(**defaults)  # type: ignore[arg-type]


def test_expectation_outcome_must_match_counts() -> None:
    with pytest.raises(ValidationError, match="outcome"):
        _matched_result(outcome=MatchOutcome.MISSING)


def test_expectation_matched_count_must_match_reference_list_length() -> None:
    with pytest.raises(ValidationError, match="matched_count"):
        _matched_result(matched_count=2, matched_unified_reference_ids=["ref-1"])


def test_expectation_reference_ids_must_be_sorted_unique() -> None:
    with pytest.raises(ValidationError, match="ordenado"):
        _matched_result(
            matched_count=2, matched_unified_reference_ids=["ref-2", "ref-1"]
        )


def test_positive_case_result_all_matched() -> None:
    result = GroundTruthCaseResult(
        case_id="case-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.MATCHED,
        expectation_results=[_matched_result()],
    )
    assert result.outcome == MatchOutcome.MATCHED


def test_positive_case_result_outcome_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="outcome"):
        GroundTruthCaseResult(
            case_id="case-1",
            kind=GroundTruthCaseKind.POSITIVE,
            program="PROG1",
            applicability=Applicability.APPLICABLE,
            outcome=MatchOutcome.MISSING,
            expectation_results=[_matched_result()],
        )


def test_negative_case_result_cannot_declare_expectation_results() -> None:
    with pytest.raises(ValidationError, match="NEGATIVE"):
        GroundTruthCaseResult(
            case_id="case-2",
            kind=GroundTruthCaseKind.NEGATIVE,
            program="PROG2",
            applicability=Applicability.APPLICABLE,
            outcome=MatchOutcome.CONFIRMED_ABSENT,
            expectation_results=[_matched_result()],
        )


def test_negative_case_result_confirmed_absent() -> None:
    result = GroundTruthCaseResult(
        case_id="case-2",
        kind=GroundTruthCaseKind.NEGATIVE,
        program="PROG2",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.CONFIRMED_ABSENT,
    )
    assert result.unexpected_candidate_reference_ids == []


def test_negative_case_result_unexpected_candidates() -> None:
    result = GroundTruthCaseResult(
        case_id="case-2",
        kind=GroundTruthCaseKind.NEGATIVE,
        program="PROG2",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.UNEXPECTED_CANDIDATES,
        unexpected_candidate_reference_ids=["ref-9"],
    )
    assert result.outcome == MatchOutcome.UNEXPECTED_CANDIDATES


def test_not_applicable_case_requires_not_evaluated_outcome() -> None:
    with pytest.raises(ValidationError, match="NOT_EVALUATED"):
        GroundTruthCaseResult(
            case_id="case-3",
            kind=GroundTruthCaseKind.POSITIVE,
            program="PROG3",
            applicability=Applicability.NOT_APPLICABLE,
            outcome=MatchOutcome.MISSING,
        )


def test_not_applicable_case_cannot_declare_expectation_results() -> None:
    with pytest.raises(ValidationError, match="NOT_APPLICABLE"):
        GroundTruthCaseResult(
            case_id="case-3",
            kind=GroundTruthCaseKind.POSITIVE,
            program="PROG3",
            applicability=Applicability.NOT_APPLICABLE,
            outcome=MatchOutcome.NOT_EVALUATED,
            expectation_results=[_matched_result()],
        )


def test_not_applicable_case_cannot_declare_unexpected_candidates() -> None:
    with pytest.raises(ValidationError, match="NOT_APPLICABLE"):
        GroundTruthCaseResult(
            case_id="case-3",
            kind=GroundTruthCaseKind.NEGATIVE,
            program="PROG3",
            applicability=Applicability.NOT_APPLICABLE,
            outcome=MatchOutcome.NOT_EVALUATED,
            unexpected_candidate_reference_ids=["ref-1"],
        )


def test_applicable_case_cannot_have_not_evaluated_outcome() -> None:
    with pytest.raises(ValidationError, match="NOT_EVALUATED"):
        GroundTruthCaseResult(
            case_id="case-3",
            kind=GroundTruthCaseKind.NEGATIVE,
            program="PROG3",
            applicability=Applicability.APPLICABLE,
            outcome=MatchOutcome.NOT_EVALUATED,
        )


def test_not_applicable_case_valid_for_positive_kind() -> None:
    result = GroundTruthCaseResult(
        case_id="case-3",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG3",
        applicability=Applicability.NOT_APPLICABLE,
        outcome=MatchOutcome.NOT_EVALUATED,
    )
    assert result.outcome == MatchOutcome.NOT_EVALUATED
    assert result.expectation_results == []


# --- Checkpoint correctivo: metricas por caso -------------------------


def test_case_metrics_rejected_when_outcome_not_matched() -> None:
    with pytest.raises(ValidationError, match="case_metrics"):
        GroundTruthCaseResult(
            case_id="case-1",
            kind=GroundTruthCaseKind.NEGATIVE,
            program="PROG1",
            applicability=Applicability.APPLICABLE,
            outcome=MatchOutcome.CONFIRMED_ABSENT,
            case_metrics=CaseLevelMetrics(
                status=CaseMetricStatus.NOT_EVALUATED,
                reason_code=CaseMetricReasonCode.CASE_ARTIFACT_TRACEABILITY_UNAVAILABLE,
            ),
        )


def test_case_metrics_accepted_when_outcome_matched() -> None:
    result = GroundTruthCaseResult(
        case_id="case-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.MATCHED,
        expectation_results=[_matched_result()],
        case_metrics=CaseLevelMetrics(
            status=CaseMetricStatus.EVALUATED,
            unified_reference_id="ref-1",
            source_candidate_id="candidate-1",
            evidence_reference_count=2,
            provenance_reference_count=1,
        ),
    )
    assert result.case_metrics is not None
    assert result.case_metrics.status == CaseMetricStatus.EVALUATED


def test_case_level_metrics_not_evaluated_requires_reason_code() -> None:
    with pytest.raises(ValidationError, match="reason_code"):
        CaseLevelMetrics(status=CaseMetricStatus.NOT_EVALUATED)


def test_case_level_metrics_not_evaluated_rejects_partial_values() -> None:
    with pytest.raises(ValidationError, match="NOT_EVALUATED"):
        CaseLevelMetrics(
            status=CaseMetricStatus.NOT_EVALUATED,
            reason_code=CaseMetricReasonCode.CASE_ARTIFACT_TRACEABILITY_UNAVAILABLE,
            evidence_reference_count=1,
        )


def test_case_level_metrics_evaluated_requires_identity() -> None:
    with pytest.raises(ValidationError, match="EVALUATED"):
        CaseLevelMetrics(status=CaseMetricStatus.EVALUATED)


def test_case_level_metrics_evaluated_rejects_reason_code() -> None:
    with pytest.raises(ValidationError, match="reason_code"):
        CaseLevelMetrics(
            status=CaseMetricStatus.EVALUATED,
            reason_code=CaseMetricReasonCode.CASE_ARTIFACT_TRACEABILITY_UNAVAILABLE,
            unified_reference_id="ref-1",
            source_candidate_id="candidate-1",
        )


def test_metrics_precision_formula_enforced() -> None:
    with pytest.raises(ValidationError, match="precision"):
        FunctionalValidationMetrics(
            true_positive_count=1,
            false_negative_count=0,
            false_positive_count=1,
            true_negative_count=0,
            precision=1.0,
            recall=1.0,
            f1_score=1.0,
        )


def test_metrics_undefined_when_denominator_zero() -> None:
    metrics = FunctionalValidationMetrics(
        true_positive_count=0,
        false_negative_count=0,
        false_positive_count=0,
        true_negative_count=1,
        precision=None,
        recall=None,
        f1_score=None,
    )
    assert metrics.precision is None
    assert metrics.f1_score is None


def _zero_metrics() -> FunctionalValidationMetrics:
    return FunctionalValidationMetrics(
        true_positive_count=0,
        false_negative_count=0,
        false_positive_count=0,
        true_negative_count=0,
        precision=None,
        recall=None,
        f1_score=None,
    )


def _build_report(
    case_results: list[GroundTruthCaseResult], **overrides: object
) -> FunctionalValidationReport:
    """Helper de test: autocalcula coverage_status/required_case_count/
    etc. a partir de `case_results`, igual que `validate_ground_truth`
    -- `overrides` permite forzar valores incorrectos deliberadamente
    para probar los validadores."""
    required = [c for c in case_results if c.kind == GroundTruthCaseKind.POSITIVE]
    forbidden = [c for c in case_results if c.kind == GroundTruthCaseKind.NEGATIVE]
    pending = sorted(
        c.case_id for c in case_results if c.applicability == Applicability.NOT_APPLICABLE
    )
    any_applicable = any(c.applicability == Applicability.APPLICABLE for c in case_results)
    dataset_applicability = (
        Applicability.APPLICABLE if any_applicable else Applicability.NOT_APPLICABLE
    )
    if not any_applicable:
        coverage_status = FunctionalDatasetCoverageStatus.NOT_EVALUATED
    elif pending:
        coverage_status = FunctionalDatasetCoverageStatus.PARTIALLY_EVALUATED
    else:
        coverage_status = FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED
    dataset_disposition = (
        FunctionalDatasetDisposition.NOT_EVALUATED
        if coverage_status != FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED
        else FunctionalDatasetDisposition.PASS_ENGINEERING
    )
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": _HASH,
        "ground_truth_catalog_edition": "edition-1",
        "dataset_applicability": dataset_applicability,
        "coverage_status": coverage_status,
        "required_case_count": len(required),
        "evaluated_required_case_count": sum(
            1 for c in required if c.applicability == Applicability.APPLICABLE
        ),
        "forbidden_case_count": len(forbidden),
        "evaluated_forbidden_case_count": sum(
            1 for c in forbidden if c.applicability == Applicability.APPLICABLE
        ),
        "pending_case_ids": pending,
        "dataset_disposition": dataset_disposition,
        "case_results": case_results,
        "metrics": _zero_metrics(),
    }
    defaults.update(overrides)
    return FunctionalValidationReport(**defaults)  # type: ignore[arg-type]


def test_report_case_results_out_of_order_rejected() -> None:
    case_a = GroundTruthCaseResult(
        case_id="z-case", kind=GroundTruthCaseKind.NEGATIVE, program="P",
        applicability=Applicability.APPLICABLE, outcome=MatchOutcome.CONFIRMED_ABSENT,
    )
    case_b = GroundTruthCaseResult(
        case_id="a-case", kind=GroundTruthCaseKind.NEGATIVE, program="P",
        applicability=Applicability.APPLICABLE, outcome=MatchOutcome.CONFIRMED_ABSENT,
    )
    with pytest.raises(ValidationError, match="ordenado"):
        _build_report([case_a, case_b])


def test_report_metrics_must_match_case_results() -> None:
    case = GroundTruthCaseResult(
        case_id="case-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.MATCHED,
        expectation_results=[_matched_result()],
    )
    with pytest.raises(ValidationError, match="metrics no coincide"):
        _build_report([case])


def test_report_valid_round_trips_json() -> None:
    case = GroundTruthCaseResult(
        case_id="case-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.MATCHED,
        expectation_results=[_matched_result()],
    )
    metrics = FunctionalValidationMetrics(
        true_positive_count=1,
        false_negative_count=0,
        false_positive_count=0,
        true_negative_count=0,
        precision=1.0,
        recall=1.0,
        f1_score=1.0,
    )
    report = _build_report([case], metrics=metrics)
    reloaded = FunctionalValidationReport.model_validate_json(report.to_stable_json())
    assert reloaded == report


def test_not_applicable_case_never_counted_in_metrics() -> None:
    """Checkpoint correctivo: un caso NOT_APPLICABLE nunca puede
    respaldar metrics != cero -- ningun sustituto por cero disfrazado,
    simplemente el caso no participa en absoluto."""
    not_applicable_case = GroundTruthCaseResult(
        case_id="case-1",
        kind=GroundTruthCaseKind.NEGATIVE,
        program="PROG1",
        applicability=Applicability.NOT_APPLICABLE,
        outcome=MatchOutcome.NOT_EVALUATED,
    )
    report = _build_report([not_applicable_case])
    assert report.metrics.true_negative_count == 0
    assert report.metrics.precision is None
    assert report.dataset_applicability == Applicability.NOT_APPLICABLE
    assert report.coverage_status == FunctionalDatasetCoverageStatus.NOT_EVALUATED


def test_dataset_applicability_must_match_case_results() -> None:
    applicable_case = GroundTruthCaseResult(
        case_id="case-1",
        kind=GroundTruthCaseKind.NEGATIVE,
        program="PROG1",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.CONFIRMED_ABSENT,
    )
    with pytest.raises(ValidationError, match="dataset_applicability"):
        _build_report(
            [applicable_case],
            dataset_applicability=Applicability.NOT_APPLICABLE,
            coverage_status=FunctionalDatasetCoverageStatus.NOT_EVALUATED,
            dataset_disposition=FunctionalDatasetDisposition.NOT_EVALUATED,
            metrics=FunctionalValidationMetrics(
                true_positive_count=0,
                false_negative_count=0,
                false_positive_count=0,
                true_negative_count=1,
                precision=None,
                recall=None,
                f1_score=None,
            ),
        )


def test_dataset_disposition_not_evaluated_requires_no_applicable_cases() -> None:
    not_applicable_case = GroundTruthCaseResult(
        case_id="case-1",
        kind=GroundTruthCaseKind.NEGATIVE,
        program="PROG1",
        applicability=Applicability.NOT_APPLICABLE,
        outcome=MatchOutcome.NOT_EVALUATED,
    )
    with pytest.raises(ValidationError, match="dataset_disposition"):
        _build_report(
            [not_applicable_case], dataset_disposition=FunctionalDatasetDisposition.PASS_ENGINEERING
        )


# --- Checkpoint correctivo: completitud del dataset --------------------


def test_coverage_status_partially_evaluated_when_case_pending() -> None:
    applicable_case = GroundTruthCaseResult(
        case_id="case-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.MATCHED,
        expectation_results=[_matched_result()],
    )
    pending_case = GroundTruthCaseResult(
        case_id="case-2",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG2",
        applicability=Applicability.NOT_APPLICABLE,
        outcome=MatchOutcome.NOT_EVALUATED,
    )
    report = _build_report(
        [applicable_case, pending_case],
        metrics=FunctionalValidationMetrics(
            true_positive_count=1,
            false_negative_count=0,
            false_positive_count=0,
            true_negative_count=0,
            precision=1.0,
            recall=1.0,
            f1_score=1.0,
        ),
    )
    assert report.coverage_status == FunctionalDatasetCoverageStatus.PARTIALLY_EVALUATED
    assert report.pending_case_ids == ["case-2"]
    assert report.required_case_count == 2
    assert report.evaluated_required_case_count == 1


def test_partial_report_cannot_claim_pass_engineering() -> None:
    """Checkpoint correctivo central: incluso con el unico caso
    evaluado en MATCHED, un reporte PARTIALLY_EVALUATED nunca puede
    declarar dataset_disposition=PASS_ENGINEERING."""
    applicable_case = GroundTruthCaseResult(
        case_id="case-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.MATCHED,
        expectation_results=[_matched_result()],
    )
    pending_case = GroundTruthCaseResult(
        case_id="case-2",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG2",
        applicability=Applicability.NOT_APPLICABLE,
        outcome=MatchOutcome.NOT_EVALUATED,
    )
    with pytest.raises(ValidationError, match="dataset_disposition"):
        _build_report(
            [applicable_case, pending_case],
            dataset_disposition=FunctionalDatasetDisposition.PASS_ENGINEERING,
            metrics=FunctionalValidationMetrics(
                true_positive_count=1,
                false_negative_count=0,
                false_positive_count=0,
                true_negative_count=0,
                precision=1.0,
                recall=1.0,
                f1_score=1.0,
            ),
        )


def test_coverage_status_completely_evaluated_enables_pass_engineering() -> None:
    case = GroundTruthCaseResult(
        case_id="case-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.MATCHED,
        expectation_results=[_matched_result()],
    )
    report = _build_report(
        [case],
        metrics=FunctionalValidationMetrics(
            true_positive_count=1,
            false_negative_count=0,
            false_positive_count=0,
            true_negative_count=0,
            precision=1.0,
            recall=1.0,
            f1_score=1.0,
        ),
    )
    assert report.coverage_status == FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED
    assert report.dataset_disposition == FunctionalDatasetDisposition.PASS_ENGINEERING
    assert report.pending_case_ids == []


def test_pending_case_ids_must_match_not_applicable_cases() -> None:
    case = GroundTruthCaseResult(
        case_id="case-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.MATCHED,
        expectation_results=[_matched_result()],
    )
    with pytest.raises(ValidationError, match="pending_case_ids"):
        _build_report(
            [case],
            pending_case_ids=["case-1"],
            metrics=FunctionalValidationMetrics(
                true_positive_count=1,
                false_negative_count=0,
                false_positive_count=0,
                true_negative_count=0,
                precision=1.0,
                recall=1.0,
                f1_score=1.0,
            ),
        )


def test_required_case_count_must_match_positive_cases() -> None:
    case = GroundTruthCaseResult(
        case_id="case-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.MATCHED,
        expectation_results=[_matched_result()],
    )
    with pytest.raises(ValidationError, match="required_case_count"):
        _build_report(
            [case],
            required_case_count=99,
            metrics=FunctionalValidationMetrics(
                true_positive_count=1,
                false_negative_count=0,
                false_positive_count=0,
                true_negative_count=0,
                precision=1.0,
                recall=1.0,
                f1_score=1.0,
            ),
        )
