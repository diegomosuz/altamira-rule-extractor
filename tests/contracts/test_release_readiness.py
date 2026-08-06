"""Tests contractuales de release readiness funcional (Fase 15B2-A,
Parte G): `contracts/release_readiness.py`. Incluye el checkpoint
correctivo de aplicabilidad (cierre de Fase 15B2-A): readiness
dimensions (`structural_readiness`/`engineering_functional_readiness`/
`domain_functional_readiness`) y `warnings` tipados."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.release_readiness import (
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

_HASH = "a" * 64


def _binary_criterion(**overrides: object) -> ReleaseReadinessCriterion:
    defaults: dict[str, object] = {
        "criterion_id": "c1",
        "kind": ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES,
        "description": "descripcion",
    }
    defaults.update(overrides)
    return ReleaseReadinessCriterion(**defaults)  # type: ignore[arg-type]


def test_binary_criterion_rejects_minimum_value() -> None:
    with pytest.raises(ValidationError, match="minimum_value"):
        _binary_criterion(minimum_value=1.0)


def test_threshold_criterion_requires_minimum_value() -> None:
    with pytest.raises(ValidationError, match="minimum_value"):
        _binary_criterion(kind=ReleaseReadinessCriterionKind.MINIMUM_RECALL)


def test_threshold_criterion_with_minimum_value_accepted() -> None:
    criterion = _binary_criterion(
        kind=ReleaseReadinessCriterionKind.MINIMUM_RECALL, minimum_value=1.0
    )
    assert criterion.minimum_value == 1.0


def test_policy_rejects_duplicate_criterion_ids() -> None:
    criterion = _binary_criterion()
    with pytest.raises(ValidationError, match="duplicado"):
        ReleaseReadinessPolicy(policy_edition="edition-1", criteria=[criterion, criterion])


def test_policy_requires_at_least_one_criterion() -> None:
    with pytest.raises(ValidationError):
        ReleaseReadinessPolicy(policy_edition="edition-1", criteria=[])


def _result(**overrides: object) -> ReleaseReadinessCriterionResult:
    defaults: dict[str, object] = {
        "criterion_id": "c1",
        "kind": ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES,
        "status": ReleaseReadinessCriterionStatus.PASSED,
        "actual_value": 0.0,
        "message": "0 casos MISSING",
    }
    defaults.update(overrides)
    return ReleaseReadinessCriterionResult(**defaults)  # type: ignore[arg-type]


def _assessment(**overrides: object) -> ReleaseReadinessAssessment:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": _HASH,
        "policy_edition": "edition-1",
        "disposition": ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_MET,
        "structural_readiness": ReleaseReadinessCriterionStatus.PASSED,
        "engineering_functional_readiness": ReleaseReadinessCriterionStatus.PASSED,
        "domain_functional_readiness": DomainFunctionalReadinessStatus.PENDING_DOMAIN_REVIEW,
        "criteria_results": [_result()],
        "summary": ReleaseReadinessSummary(criterion_count=1, passed_count=1, failed_count=0),
    }
    defaults.update(overrides)
    return ReleaseReadinessAssessment(**defaults)  # type: ignore[arg-type]


def test_result_not_evaluated_cannot_declare_actual_value() -> None:
    with pytest.raises(ValidationError, match="NOT_EVALUATED"):
        _result(status=ReleaseReadinessCriterionStatus.NOT_EVALUATED, actual_value=0.0)


def test_result_not_evaluated_without_actual_value_accepted() -> None:
    result = _result(status=ReleaseReadinessCriterionStatus.NOT_EVALUATED, actual_value=None)
    assert result.status == ReleaseReadinessCriterionStatus.NOT_EVALUATED


def test_assessment_disposition_met_when_all_passed() -> None:
    assessment = _assessment()
    assert assessment.disposition == ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_MET


def test_assessment_disposition_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="disposition"):
        _assessment(
            disposition=ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_MET,
            engineering_functional_readiness=ReleaseReadinessCriterionStatus.FAILED,
            domain_functional_readiness=DomainFunctionalReadinessStatus.NOT_EVALUATED,
            criteria_results=[_result(status=ReleaseReadinessCriterionStatus.FAILED)],
            summary=ReleaseReadinessSummary(criterion_count=1, passed_count=0, failed_count=1),
        )


def test_assessment_disposition_not_met_with_empty_results() -> None:
    assessment = _assessment(
        disposition=ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_NOT_MET,
        engineering_functional_readiness=ReleaseReadinessCriterionStatus.FAILED,
        domain_functional_readiness=DomainFunctionalReadinessStatus.NOT_EVALUATED,
        criteria_results=[],
        summary=ReleaseReadinessSummary(criterion_count=0, passed_count=0, failed_count=0),
    )
    assert assessment.criteria_results == []


def test_assessment_summary_must_match_results() -> None:
    bad_summary = ReleaseReadinessSummary(criterion_count=1, passed_count=0, failed_count=0)
    with pytest.raises(ValidationError, match="passed_count"):
        _assessment(summary=bad_summary)


def test_assessment_valid_round_trips_json() -> None:
    assessment = _assessment()
    reloaded = ReleaseReadinessAssessment.model_validate_json(assessment.to_stable_json())
    assert reloaded == assessment


# --- Checkpoint correctivo: readiness dimensions / NOT_EVALUATED -------


def test_engineering_functional_readiness_not_evaluated_when_all_criteria_not_evaluated() -> None:
    assessment = _assessment(
        disposition=ReleaseReadinessDisposition.NOT_EVALUATED,
        engineering_functional_readiness=ReleaseReadinessCriterionStatus.NOT_EVALUATED,
        domain_functional_readiness=DomainFunctionalReadinessStatus.NOT_EVALUATED,
        criteria_results=[
            _result(status=ReleaseReadinessCriterionStatus.NOT_EVALUATED, actual_value=None)
        ],
        summary=ReleaseReadinessSummary(
            criterion_count=1, passed_count=0, failed_count=0, not_evaluated_count=1
        ),
        warnings=[
            ReleaseReadinessWarning(
                code=ReleaseReadinessWarningCode.GROUND_TRUTH_NOT_AVAILABLE,
                message="ground truth no aplicable a este run",
            )
        ],
    )
    assert assessment.disposition == ReleaseReadinessDisposition.NOT_EVALUATED
    assert assessment.domain_functional_readiness == DomainFunctionalReadinessStatus.NOT_EVALUATED
    assert assessment.warnings[0].code == ReleaseReadinessWarningCode.GROUND_TRUTH_NOT_AVAILABLE


def test_engineering_functional_readiness_not_evaluated_never_produces_functional_criteria_not_met() -> (  # noqa: E501
    None
):
    """Checkpoint correctivo explicito: NOT_EVALUATED nunca puede
    coexistir con disposition=FUNCTIONAL_CRITERIA_NOT_MET (eso
    penalizaria un paquete sin ground truth aplicable como si hubiese
    fallado)."""
    with pytest.raises(ValidationError, match="disposition"):
        _assessment(
            disposition=ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_NOT_MET,
            engineering_functional_readiness=ReleaseReadinessCriterionStatus.NOT_EVALUATED,
            domain_functional_readiness=DomainFunctionalReadinessStatus.NOT_EVALUATED,
            criteria_results=[
                _result(status=ReleaseReadinessCriterionStatus.NOT_EVALUATED, actual_value=None)
            ],
            summary=ReleaseReadinessSummary(
                criterion_count=1, passed_count=0, failed_count=0, not_evaluated_count=1
            ),
        )


def test_domain_functional_readiness_cannot_be_pending_when_engineering_not_passed() -> None:
    with pytest.raises(ValidationError, match="domain_functional_readiness"):
        _assessment(
            engineering_functional_readiness=ReleaseReadinessCriterionStatus.FAILED,
            domain_functional_readiness=DomainFunctionalReadinessStatus.PENDING_DOMAIN_REVIEW,
            criteria_results=[_result(status=ReleaseReadinessCriterionStatus.FAILED)],
            summary=ReleaseReadinessSummary(criterion_count=1, passed_count=0, failed_count=1),
        )


def test_domain_functional_readiness_never_reaches_client_approved() -> None:
    """No existe ningun valor de aprobacion de cliente en el enum --
    documentado aqui para que una futura extension accidental del enum
    quede visible en un diff de test."""
    assert set(DomainFunctionalReadinessStatus) == {
        DomainFunctionalReadinessStatus.NOT_EVALUATED,
        DomainFunctionalReadinessStatus.PENDING_DOMAIN_REVIEW,
    }


def test_structural_readiness_evaluated_independently_of_functional() -> None:
    """Un criterio estructural FAILED junto a criterios funcionales
    NOT_EVALUATED: structural_readiness=FAILED permanece visible, nunca
    se oculta detras de NOT_EVALUATED."""
    structural_result = ReleaseReadinessCriterionResult(
        criterion_id="coverage",
        kind=ReleaseReadinessCriterionKind.NO_ERROR_SEVERITY_COVERAGE_ISSUES,
        status=ReleaseReadinessCriterionStatus.FAILED,
        actual_value=2.0,
        message="2 issues ERROR",
    )
    functional_result = _result(
        status=ReleaseReadinessCriterionStatus.NOT_EVALUATED, actual_value=None
    )
    assessment = _assessment(
        disposition=ReleaseReadinessDisposition.NOT_EVALUATED,
        structural_readiness=ReleaseReadinessCriterionStatus.FAILED,
        engineering_functional_readiness=ReleaseReadinessCriterionStatus.NOT_EVALUATED,
        domain_functional_readiness=DomainFunctionalReadinessStatus.NOT_EVALUATED,
        criteria_results=[functional_result, structural_result],
        summary=ReleaseReadinessSummary(
            criterion_count=2, passed_count=0, failed_count=1, not_evaluated_count=1
        ),
    )
    assert assessment.structural_readiness == ReleaseReadinessCriterionStatus.FAILED
    assert assessment.disposition == ReleaseReadinessDisposition.NOT_EVALUATED


def test_summary_not_evaluated_count_must_match_results() -> None:
    bad_summary = ReleaseReadinessSummary(
        criterion_count=1, passed_count=0, failed_count=0, not_evaluated_count=0
    )
    with pytest.raises(ValidationError, match="not_evaluated_count"):
        _assessment(
            disposition=ReleaseReadinessDisposition.NOT_EVALUATED,
            engineering_functional_readiness=ReleaseReadinessCriterionStatus.NOT_EVALUATED,
            domain_functional_readiness=DomainFunctionalReadinessStatus.NOT_EVALUATED,
            criteria_results=[
                _result(status=ReleaseReadinessCriterionStatus.NOT_EVALUATED, actual_value=None)
            ],
            summary=bad_summary,
        )
