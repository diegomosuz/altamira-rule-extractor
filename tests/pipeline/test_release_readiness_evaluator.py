"""Tests del analizador puro de release readiness funcional (Fase
15B2-A, Parte G): `pipeline/release_readiness_evaluator.py`. Incluye el
checkpoint correctivo de aplicabilidad (cierre de Fase 15B2-A): cuando
`dataset_applicability=NOT_APPLICABLE`, los cuatro criterios
dependientes de `FunctionalValidationReport` deben quedar NOT_EVALUATED,
nunca FAILED."""

from __future__ import annotations

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedCandidateReference,
)
from altamira_extractor.contracts.enums import Severity
from altamira_extractor.contracts.functional_dataset_validation import FunctionalDatasetLane
from altamira_extractor.contracts.functional_ground_truth import (
    FunctionalGroundTruthSet,
    FunctionalGroundTruthSummary,
    GroundTruthCase,
    GroundTruthCaseKind,
    GroundTruthExpectedRule,
    GroundTruthFixtureReference,
)
from altamira_extractor.contracts.functional_validation import (
    Applicability,
    ArtifactChainIntegrityReport,
    ExpectedRuleMatchResult,
    FinalRuleLinkageReport,
    FinalRuleLinkageStatus,
    FunctionalDatasetCoverageStatus,
    FunctionalDatasetDisposition,
    FunctionalValidationMetrics,
    FunctionalValidationReport,
    GroundTruthCaseResult,
    MatchOutcome,
    ValidationSource,
)
from altamira_extractor.contracts.release_readiness import (
    DomainFunctionalReadinessStatus,
    ReleaseReadinessCriterion,
    ReleaseReadinessCriterionKind,
    ReleaseReadinessCriterionStatus,
    ReleaseReadinessDisposition,
    ReleaseReadinessPolicy,
    ReleaseReadinessWarningCode,
)
from altamira_extractor.contracts.semantic_coverage import SemanticCoverageIssue
from altamira_extractor.pipeline.functional_validation_aggregator import (
    aggregate_functional_validation_reports,
)
from altamira_extractor.pipeline.functional_validation_matcher import validate_ground_truth
from altamira_extractor.pipeline.release_readiness_evaluator import (
    _format_pending_case_ids,
    evaluate_release_readiness,
    evaluate_release_readiness_for_dataset,
)

_HASH = "a" * 64


def _policy(
    *kinds: tuple[str, ReleaseReadinessCriterionKind, float | None],
) -> ReleaseReadinessPolicy:
    criteria = sorted(
        (
            ReleaseReadinessCriterion(
                criterion_id=criterion_id, kind=kind, description="d", minimum_value=minimum
            )
            for criterion_id, kind, minimum in kinds
        ),
        key=lambda c: c.criterion_id,
    )
    return ReleaseReadinessPolicy(policy_edition="edition-1", criteria=criteria)


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


_DEFAULT_INTEGRITY = ArtifactChainIntegrityReport(
    candidates_checked=0, candidates_missing_context=[]
)
_DEFAULT_LINKAGE = FinalRuleLinkageReport(status=FinalRuleLinkageStatus.NOT_APPLICABLE)


def _report(
    *, matched: bool, unexpected: bool, precision: float | None, recall: float | None
) -> FunctionalValidationReport:
    positive = GroundTruthCaseResult(
        case_id="pos-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="P1",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.MATCHED if matched else MatchOutcome.MISSING,
        expectation_results=[
            ExpectedRuleMatchResult(
                expectation_id="pos-1::e1",
                rule_family="RETURN_CODE",
                minimum_count=1,
                matched_count=1 if matched else 0,
                outcome=MatchOutcome.MATCHED if matched else MatchOutcome.MISSING,
                matched_unified_reference_ids=["r1"] if matched else [],
            )
        ],
    )
    negative_outcome = (
        MatchOutcome.UNEXPECTED_CANDIDATES if unexpected else MatchOutcome.CONFIRMED_ABSENT
    )
    negative = GroundTruthCaseResult(
        case_id="neg-1",
        kind=GroundTruthCaseKind.NEGATIVE,
        program="P2",
        applicability=Applicability.APPLICABLE,
        outcome=negative_outcome,
        unexpected_candidate_reference_ids=["r9"] if unexpected else [],
    )
    metrics = FunctionalValidationMetrics(
        true_positive_count=1 if matched else 0,
        false_negative_count=0 if matched else 1,
        false_positive_count=1 if unexpected else 0,
        true_negative_count=0 if unexpected else 1,
        precision=precision,
        recall=recall,
        f1_score=_f1(precision, recall),
    )
    dataset_disposition = (
        FunctionalDatasetDisposition.PASS_ENGINEERING
        if matched and not unexpected
        else FunctionalDatasetDisposition.FAIL_ENGINEERING
    )
    return FunctionalValidationReport(
        run_id="run-1",
        source_package_hash=_HASH,
        ground_truth_catalog_edition="edition-1",
        validation_source=ValidationSource.PRODUCTIVE_ARTIFACT,
        productive_candidate_count=0,
        dataset_applicability=Applicability.APPLICABLE,
        coverage_status=FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED,
        required_case_count=1,
        evaluated_required_case_count=1,
        forbidden_case_count=1,
        evaluated_forbidden_case_count=1,
        pending_case_ids=[],
        dataset_disposition=dataset_disposition,
        case_results=[negative, positive],
        metrics=metrics,
        artifact_chain_integrity=_DEFAULT_INTEGRITY,
        final_rule_linkage=_DEFAULT_LINKAGE,
    )


def _not_applicable_report() -> FunctionalValidationReport:
    positive = GroundTruthCaseResult(
        case_id="pos-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="P1",
        applicability=Applicability.NOT_APPLICABLE,
        outcome=MatchOutcome.NOT_EVALUATED,
    )
    negative = GroundTruthCaseResult(
        case_id="neg-1",
        kind=GroundTruthCaseKind.NEGATIVE,
        program="P2",
        applicability=Applicability.NOT_APPLICABLE,
        outcome=MatchOutcome.NOT_EVALUATED,
    )
    return FunctionalValidationReport(
        run_id="run-1",
        source_package_hash=_HASH,
        ground_truth_catalog_edition="edition-1",
        validation_source=ValidationSource.PRODUCTIVE_ARTIFACT,
        productive_candidate_count=0,
        dataset_applicability=Applicability.NOT_APPLICABLE,
        coverage_status=FunctionalDatasetCoverageStatus.NOT_EVALUATED,
        required_case_count=1,
        evaluated_required_case_count=0,
        forbidden_case_count=1,
        evaluated_forbidden_case_count=0,
        pending_case_ids=["neg-1", "pos-1"],
        dataset_disposition=FunctionalDatasetDisposition.NOT_EVALUATED,
        case_results=[negative, positive],
        metrics=FunctionalValidationMetrics(
            true_positive_count=0,
            false_negative_count=0,
            false_positive_count=0,
            true_negative_count=0,
            precision=None,
            recall=None,
            f1_score=None,
        ),
        artifact_chain_integrity=_DEFAULT_INTEGRITY,
        final_rule_linkage=_DEFAULT_LINKAGE,
    )


def _partially_evaluated_report() -> FunctionalValidationReport:
    """Un caso APPLICABLE+MATCHED, pero el otro caso REQUIRED del
    catalogo sigue pendiente en otro run -- coverage_status=
    PARTIALLY_EVALUATED (checkpoint correctivo: distinto de
    `_not_applicable_report`, que no tiene NINGUN caso aplicable)."""
    positive = GroundTruthCaseResult(
        case_id="pos-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="P1",
        applicability=Applicability.APPLICABLE,
        outcome=MatchOutcome.MATCHED,
        expectation_results=[
            ExpectedRuleMatchResult(
                expectation_id="pos-1::e1",
                rule_family="RETURN_CODE",
                minimum_count=1,
                matched_count=1,
                outcome=MatchOutcome.MATCHED,
                matched_unified_reference_ids=["r1"],
            )
        ],
    )
    pending = GroundTruthCaseResult(
        case_id="pos-2",
        kind=GroundTruthCaseKind.POSITIVE,
        program="P3",
        applicability=Applicability.NOT_APPLICABLE,
        outcome=MatchOutcome.NOT_EVALUATED,
    )
    return FunctionalValidationReport(
        run_id="run-1",
        source_package_hash=_HASH,
        ground_truth_catalog_edition="edition-1",
        validation_source=ValidationSource.PRODUCTIVE_ARTIFACT,
        productive_candidate_count=0,
        dataset_applicability=Applicability.APPLICABLE,
        coverage_status=FunctionalDatasetCoverageStatus.PARTIALLY_EVALUATED,
        required_case_count=2,
        evaluated_required_case_count=1,
        forbidden_case_count=0,
        evaluated_forbidden_case_count=0,
        pending_case_ids=["pos-2"],
        dataset_disposition=FunctionalDatasetDisposition.NOT_EVALUATED,
        case_results=[positive, pending],
        metrics=FunctionalValidationMetrics(
            true_positive_count=1,
            false_negative_count=0,
            false_positive_count=0,
            true_negative_count=0,
            precision=1.0,
            recall=1.0,
            f1_score=1.0,
        ),
        artifact_chain_integrity=_DEFAULT_INTEGRITY,
        final_rule_linkage=_DEFAULT_LINKAGE,
    )


def test_no_error_coverage_issues_passes_with_empty_issues() -> None:
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_ERROR_SEVERITY_COVERAGE_ISSUES, None))
    report = _report(matched=True, unexpected=False, precision=1.0, recall=1.0)
    assessment = evaluate_release_readiness(policy, [], report)
    assert assessment.criteria_results[0].status == ReleaseReadinessCriterionStatus.PASSED


def test_no_error_coverage_issues_fails_with_error_severity_issue() -> None:
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_ERROR_SEVERITY_COVERAGE_ISSUES, None))
    issue = SemanticCoverageIssue(
        issue_id="X::1", construct_id=None, severity=Severity.ERROR,
        reason_code="UNKNOWN_DETECTOR_ID", message="m",
    )
    report = _report(matched=True, unexpected=False, precision=1.0, recall=1.0)
    assessment = evaluate_release_readiness(policy, [issue], report)
    assert assessment.criteria_results[0].status == ReleaseReadinessCriterionStatus.FAILED


def test_no_error_coverage_issues_passes_with_only_warning_issue() -> None:
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_ERROR_SEVERITY_COVERAGE_ISSUES, None))
    issue = SemanticCoverageIssue(
        issue_id="X::1", construct_id=None, severity=Severity.WARNING,
        reason_code="UNDOCUMENTED_DETECTOR", message="m",
    )
    report = _report(matched=True, unexpected=False, precision=1.0, recall=1.0)
    assessment = evaluate_release_readiness(policy, [issue], report)
    assert assessment.criteria_results[0].status == ReleaseReadinessCriterionStatus.PASSED


def test_no_missing_positive_cases_fails_when_missing() -> None:
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None))
    report = _report(matched=False, unexpected=False, precision=None, recall=0.0)
    assessment = evaluate_release_readiness(policy, [], report)
    assert assessment.criteria_results[0].status == ReleaseReadinessCriterionStatus.FAILED


def test_no_unexpected_negative_cases_fails_when_unexpected() -> None:
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_UNEXPECTED_NEGATIVE_CASES, None))
    report = _report(matched=True, unexpected=True, precision=0.5, recall=1.0)
    assessment = evaluate_release_readiness(policy, [], report)
    assert assessment.criteria_results[0].status == ReleaseReadinessCriterionStatus.FAILED


def test_minimum_recall_passes_when_at_threshold() -> None:
    policy = _policy(("c1", ReleaseReadinessCriterionKind.MINIMUM_RECALL, 1.0))
    report = _report(matched=True, unexpected=False, precision=1.0, recall=1.0)
    assessment = evaluate_release_readiness(policy, [], report)
    assert assessment.criteria_results[0].status == ReleaseReadinessCriterionStatus.PASSED


def test_minimum_recall_fails_below_threshold() -> None:
    policy = _policy(("c1", ReleaseReadinessCriterionKind.MINIMUM_RECALL, 1.0))
    report = _report(matched=False, unexpected=False, precision=None, recall=0.0)
    assessment = evaluate_release_readiness(policy, [], report)
    assert assessment.criteria_results[0].status == ReleaseReadinessCriterionStatus.FAILED


def test_minimum_precision_fails_when_undefined() -> None:
    policy = _policy(("c1", ReleaseReadinessCriterionKind.MINIMUM_PRECISION, 1.0))
    report = _report(matched=False, unexpected=False, precision=None, recall=0.0)
    assessment = evaluate_release_readiness(policy, [], report)
    result = assessment.criteria_results[0]
    assert result.status == ReleaseReadinessCriterionStatus.FAILED
    assert result.actual_value is None


def test_disposition_met_when_all_criteria_pass() -> None:
    policy = _policy(
        ("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None),
        ("c2", ReleaseReadinessCriterionKind.NO_UNEXPECTED_NEGATIVE_CASES, None),
    )
    report = _report(matched=True, unexpected=False, precision=1.0, recall=1.0)
    assessment = evaluate_release_readiness(policy, [], report)
    assert assessment.disposition == ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_MET


def test_disposition_not_met_when_any_criterion_fails() -> None:
    policy = _policy(
        ("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None),
        ("c2", ReleaseReadinessCriterionKind.NO_UNEXPECTED_NEGATIVE_CASES, None),
    )
    report = _report(matched=False, unexpected=False, precision=None, recall=0.0)
    assessment = evaluate_release_readiness(policy, [], report)
    assert assessment.disposition == ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_NOT_MET


def test_criteria_results_are_sorted_by_criterion_id() -> None:
    policy = _policy(
        ("a-criterion", ReleaseReadinessCriterionKind.NO_UNEXPECTED_NEGATIVE_CASES, None),
        ("z-criterion", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None),
    )
    report = _report(matched=True, unexpected=False, precision=1.0, recall=1.0)
    assessment = evaluate_release_readiness(policy, [], report)
    ids = [r.criterion_id for r in assessment.criteria_results]
    assert ids == sorted(ids)


# --- Checkpoint correctivo: readiness cuando ground truth no aplica ----


def test_not_applicable_ground_truth_marks_functional_criteria_not_evaluated() -> None:
    policy = _policy(
        ("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None),
        ("c2", ReleaseReadinessCriterionKind.NO_UNEXPECTED_NEGATIVE_CASES, None),
        ("c3", ReleaseReadinessCriterionKind.MINIMUM_RECALL, 1.0),
        ("c4", ReleaseReadinessCriterionKind.MINIMUM_PRECISION, 1.0),
    )
    report = _not_applicable_report()
    assessment = evaluate_release_readiness(policy, [], report)
    assert all(
        r.status == ReleaseReadinessCriterionStatus.NOT_EVALUATED
        for r in assessment.criteria_results
    )
    assert all(r.actual_value is None for r in assessment.criteria_results)


def test_not_applicable_ground_truth_never_produces_functional_criteria_not_met() -> None:
    """Checkpoint correctivo central: un paquete sin ground truth
    aplicable NUNCA queda en FUNCTIONAL_CRITERIA_NOT_MET -- ausencia de
    senal no es una regresion."""
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None))
    report = _not_applicable_report()
    assessment = evaluate_release_readiness(policy, [], report)
    assert assessment.disposition == ReleaseReadinessDisposition.NOT_EVALUATED
    assert assessment.disposition != ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_NOT_MET


def test_not_applicable_ground_truth_sets_engineering_and_domain_readiness_not_evaluated() -> None:
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None))
    report = _not_applicable_report()
    assessment = evaluate_release_readiness(policy, [], report)
    assert (
        assessment.engineering_functional_readiness == ReleaseReadinessCriterionStatus.NOT_EVALUATED
    )
    assert assessment.domain_functional_readiness == DomainFunctionalReadinessStatus.NOT_EVALUATED


def test_not_applicable_ground_truth_adds_typed_warning() -> None:
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None))
    report = _not_applicable_report()
    assessment = evaluate_release_readiness(policy, [], report)
    assert len(assessment.warnings) == 1
    assert assessment.warnings[0].code == ReleaseReadinessWarningCode.GROUND_TRUTH_NOT_AVAILABLE


def test_not_applicable_ground_truth_keeps_structural_readiness_visible() -> None:
    """La aplicabilidad del ground truth nunca oculta la senal
    estructural (Parte C): un problema real de cobertura semantica sigue
    bloqueando, incluso sin ground truth aplicable."""
    policy = _policy(
        ("coverage", ReleaseReadinessCriterionKind.NO_ERROR_SEVERITY_COVERAGE_ISSUES, None),
        ("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None),
    )
    issue = SemanticCoverageIssue(
        issue_id="X::1", construct_id=None, severity=Severity.ERROR,
        reason_code="UNKNOWN_DETECTOR_ID", message="m",
    )
    report = _not_applicable_report()
    assessment = evaluate_release_readiness(policy, [issue], report)
    assert assessment.structural_readiness == ReleaseReadinessCriterionStatus.FAILED
    coverage_result = next(r for r in assessment.criteria_results if r.criterion_id == "coverage")
    assert coverage_result.status == ReleaseReadinessCriterionStatus.FAILED
    functional_result = next(r for r in assessment.criteria_results if r.criterion_id == "c1")
    assert functional_result.status == ReleaseReadinessCriterionStatus.NOT_EVALUATED
    # La disposicion global sigue siendo NOT_EVALUATED (ground truth no
    # aplicable domina la lectura global), pero structural_readiness
    # deja constancia explicita del problema real.
    assert assessment.disposition == ReleaseReadinessDisposition.NOT_EVALUATED


def test_applicable_ground_truth_unaffected_by_not_evaluated_logic() -> None:
    """Reconciliacion: cuando el ground truth SI es aplicable, el
    comportamiento previo (PASSED/FAILED reales) permanece intacto."""
    policy = _policy(
        ("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None),
        ("c2", ReleaseReadinessCriterionKind.NO_UNEXPECTED_NEGATIVE_CASES, None),
    )
    report = _report(matched=True, unexpected=False, precision=1.0, recall=1.0)
    assessment = evaluate_release_readiness(policy, [], report)
    assert assessment.engineering_functional_readiness == ReleaseReadinessCriterionStatus.PASSED
    assert (
        assessment.domain_functional_readiness
        == DomainFunctionalReadinessStatus.PENDING_DOMAIN_REVIEW
    )
    assert assessment.disposition == ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_MET
    assert assessment.warnings == []


# --- Checkpoint correctivo: completitud del dataset (cierre Fase 15B2-A) --


def test_partially_evaluated_report_never_reaches_engineering_passed() -> None:
    """Un caso APPLICABLE+MATCHED con OTRO caso REQUIRED pendiente en
    otro run: engineering_functional_readiness NUNCA es PASSED, sin
    importar que el caso evaluado haya resultado correcto."""
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None))
    report = _partially_evaluated_report()
    assessment = evaluate_release_readiness(policy, [], report)
    assert (
        assessment.engineering_functional_readiness == ReleaseReadinessCriterionStatus.NOT_EVALUATED
    )
    assert assessment.disposition == ReleaseReadinessDisposition.NOT_EVALUATED
    assert assessment.disposition != ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_MET


def test_partially_evaluated_report_uses_distinct_warning_code() -> None:
    """Distingue de `GROUND_TRUTH_NOT_AVAILABLE` (cero senal): aqui SI
    hubo un caso real evaluado, solo que el dataset sigue incompleto."""
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None))
    report = _partially_evaluated_report()
    assessment = evaluate_release_readiness(policy, [], report)
    assert len(assessment.warnings) == 1
    assert assessment.warnings[0].code == (
        ReleaseReadinessWarningCode.REQUIRED_GROUND_TRUTH_CASES_NOT_EXECUTED
    )
    assert "pos-2" in assessment.warnings[0].message


# --- Checkpoint correctivo: readiness sobre el reporte AGREGADO ---------


def _dataset_case(
    case_id: str, kind: GroundTruthCaseKind, program: str, fixture_sha: str
) -> GroundTruthCase:
    expected_rules = (
        [
            GroundTruthExpectedRule(
                expectation_id=f"{case_id}::e1",
                rule_family="RETURN_CODE",
                minimum_count=1,
                derivation_notes="nota de prueba",
            )
        ]
        if kind == GroundTruthCaseKind.POSITIVE
        else []
    )
    return GroundTruthCase(
        case_id=case_id,
        kind=kind,
        program=program,
        fixtures=[
            GroundTruthFixtureReference(
                relative_path=f"config/ground_truth/fixtures/{case_id}.cbl", sha256=fixture_sha
            )
        ],
        description="caso de prueba",
        expected_rules=expected_rules,
    )


_FIX_X = "4" * 64
_FIX_Y = "5" * 64


def _two_case_dataset() -> FunctionalGroundTruthSet:
    cases = [
        _dataset_case("req-a", GroundTruthCaseKind.POSITIVE, "PROGA", _FIX_X),
        _dataset_case("req-b", GroundTruthCaseKind.POSITIVE, "PROGB", _FIX_Y),
    ]
    return FunctionalGroundTruthSet(
        catalog_edition="edition-1",
        cases=cases,
        summary=FunctionalGroundTruthSummary(
            case_count=2, positive_case_count=2, negative_case_count=0, expected_rule_count=2
        ),
    )


def _ref(ref_id: str, program: str) -> UnifiedCandidateReference:
    return UnifiedCandidateReference(
        unified_reference_id=ref_id,
        source=CandidateSource.V1,
        source_candidate_id=ref_id,
        source_artifact_hash=_HASH,
        rule_family="RETURN_CODE",
        original_support="DETERMINISTIC",
        program=program,
    )


def test_dataset_readiness_partial_never_approved() -> None:
    """13. readiness parcial no aprobado (agregado con un solo run)."""
    gt = _two_case_dataset()
    report_a = validate_ground_truth(
        gt,
        [_ref("ra", "PROGA")],
        run_id="run-a",
        source_package_hash=_HASH,
        run_fixture_hashes=frozenset({_FIX_X}),
    )
    dataset_report = aggregate_functional_validation_reports(
        [report_a],
        ground_truth=gt,
        dataset_id="synthetic_engineering",
        lane=FunctionalDatasetLane.UNIFIED,
    )
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None))
    assessment = evaluate_release_readiness_for_dataset(policy, [], dataset_report)
    assert assessment.disposition == ReleaseReadinessDisposition.NOT_EVALUATED
    assert (
        assessment.engineering_functional_readiness == ReleaseReadinessCriterionStatus.NOT_EVALUATED
    )
    assert assessment.domain_functional_readiness == DomainFunctionalReadinessStatus.NOT_EVALUATED


def test_dataset_readiness_complete_approved_at_engineering_level() -> None:
    """14. readiness completo aprobado a nivel engineering (agregado de
    dos runs que cubren el catalogo completo)."""
    gt = _two_case_dataset()
    report_a = validate_ground_truth(
        gt,
        [_ref("ra", "PROGA")],
        run_id="run-a",
        source_package_hash=_HASH,
        run_fixture_hashes=frozenset({_FIX_X}),
    )
    report_b = validate_ground_truth(
        gt,
        [_ref("rb", "PROGB")],
        run_id="run-b",
        source_package_hash=_HASH,
        run_fixture_hashes=frozenset({_FIX_Y}),
    )
    dataset_report = aggregate_functional_validation_reports(
        [report_a, report_b],
        ground_truth=gt,
        dataset_id="synthetic_engineering",
        lane=FunctionalDatasetLane.UNIFIED,
    )
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None))
    assessment = evaluate_release_readiness_for_dataset(policy, [], dataset_report)
    assert assessment.engineering_functional_readiness == ReleaseReadinessCriterionStatus.PASSED
    assert assessment.disposition == ReleaseReadinessDisposition.FUNCTIONAL_CRITERIA_MET


def test_dataset_readiness_domain_review_never_inferred_beyond_pending() -> None:
    """15. domain review nunca inferida mas alla de PENDING_DOMAIN_REVIEW
    -- ni siquiera cuando engineering_functional_readiness=PASSED."""
    gt = _two_case_dataset()
    report_a = validate_ground_truth(
        gt,
        [_ref("ra", "PROGA")],
        run_id="run-a",
        source_package_hash=_HASH,
        run_fixture_hashes=frozenset({_FIX_X}),
    )
    report_b = validate_ground_truth(
        gt,
        [_ref("rb", "PROGB")],
        run_id="run-b",
        source_package_hash=_HASH,
        run_fixture_hashes=frozenset({_FIX_Y}),
    )
    dataset_report = aggregate_functional_validation_reports(
        [report_a, report_b],
        ground_truth=gt,
        dataset_id="synthetic_engineering",
        lane=FunctionalDatasetLane.UNIFIED,
    )
    policy = _policy(("c1", ReleaseReadinessCriterionKind.NO_MISSING_POSITIVE_CASES, None))
    assessment = evaluate_release_readiness_for_dataset(policy, [], dataset_report)
    assert (
        assessment.domain_functional_readiness
        == DomainFunctionalReadinessStatus.PENDING_DOMAIN_REVIEW
    )
    assert assessment.domain_functional_readiness != "CLIENT_APPROVED"
    assert set(DomainFunctionalReadinessStatus) == {
        DomainFunctionalReadinessStatus.NOT_EVALUATED,
        DomainFunctionalReadinessStatus.PENDING_DOMAIN_REVIEW,
    }


# ---------------------------------------------------------------------------
# Correccion pre-commit 15B3-C2-B2, seccion 4: _format_pending_case_ids
# ---------------------------------------------------------------------------


def test_format_pending_case_ids_short_list_is_unmodified() -> None:
    """Una lista corta (caso comun) se conserva integra, sin marcador de
    truncamiento."""
    result = _format_pending_case_ids(["pos-2", "neg-1"])
    assert result == "neg-1, pos-2"
    assert "mas" not in result


def test_format_pending_case_ids_empty_list_is_empty_string() -> None:
    assert _format_pending_case_ids([]) == ""


def test_format_pending_case_ids_long_list_stays_within_contractual_limit() -> None:
    """El limite contractual real es `ReleaseReadinessCriterionResult.
    message`/`ReleaseReadinessWarning.message` (max_length=500, ver
    contracts/release_readiness.py); el texto fijo alrededor del listado
    (en ambos call sites de release_readiness_evaluator.py) mide bien
    menos de 300 caracteres, asi que el listado en si debe quedar
    comodamente por debajo de 200 para que la suma nunca exceda 500."""
    many_case_ids = [f"gt-positive-calculation-case-{i:04d}" for i in range(200)]
    result = _format_pending_case_ids(many_case_ids)
    assert len(result) <= 200, f"listado demasiado largo ({len(result)} chars): {result!r}"


def test_format_pending_case_ids_is_deterministic() -> None:
    case_ids = [f"case-{i}" for i in range(50)]
    assert _format_pending_case_ids(case_ids) == _format_pending_case_ids(case_ids)
    # Orden de entrada nunca importa: internamente siempre ordena antes de truncar.
    assert _format_pending_case_ids(case_ids) == _format_pending_case_ids(list(reversed(case_ids)))


def test_format_pending_case_ids_never_splits_an_individual_case_id() -> None:
    """Ningun `case_id` incluido en el resultado puede quedar cortado a
    la mitad -- cada token separado por ', ' (ignorando el sufijo
    `(+N mas)`) debe ser EXACTAMENTE uno de los case_id originales."""
    many_case_ids = [f"gt-positive-calculation-case-{i:04d}" for i in range(200)]
    result = _format_pending_case_ids(many_case_ids)
    body = result.split(" (+")[0]
    included = body.split(", ") if body else []
    for case_id in included:
        assert case_id in many_case_ids, f"token {case_id!r} no es un case_id real completo"


def test_format_pending_case_ids_truncated_list_signals_remaining_count() -> None:
    """El resultado truncado debe permitir entender que existen casos
    adicionales no listados, con el conteo exacto de cuantos quedan
    fuera."""
    many_case_ids = [f"gt-positive-calculation-case-{i:04d}" for i in range(200)]
    result = _format_pending_case_ids(many_case_ids)
    assert "(+" in result and " mas)" in result
    included_count = len(result.split(" (+")[0].split(", "))
    remaining = len(many_case_ids) - included_count
    assert f"(+{remaining} mas)" in result
    assert remaining > 0
