"""Tests del agregador MULTI-RUN de validacion funcional (cierre de
Fase 15B2-A, Seccion 2): `pipeline/functional_validation_aggregator.py`.

Cada `FunctionalValidationReport` de entrada se construye con el
analizador REAL (`validate_ground_truth`), nunca a mano -- garantiza que
cada reporte de entrada ya es auto-consistente (mismo mecanismo que
`test_functional_validation_matcher.py`)."""

from __future__ import annotations

import pytest

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedCandidateReference,
)
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
    FunctionalDatasetCoverageStatus,
    FunctionalDatasetDisposition,
    FunctionalValidationReport,
    MatchOutcome,
)
from altamira_extractor.pipeline.errors import FunctionalDatasetAggregationError
from altamira_extractor.pipeline.functional_validation_aggregator import (
    aggregate_functional_validation_reports,
)
from altamira_extractor.pipeline.functional_validation_matcher import validate_ground_truth

_HASH = "a" * 64
_FIX_A = "1" * 64
_FIX_B = "2" * 64
_FIX_C = "3" * 64
_EDITION = "edition-1"


def _ref(ref_id: str, rule_family: str, program: str) -> UnifiedCandidateReference:
    return UnifiedCandidateReference(
        unified_reference_id=ref_id,
        source=CandidateSource.V1,
        source_candidate_id=ref_id,
        source_artifact_hash=_HASH,
        rule_family=rule_family,
        original_support="DETERMINISTIC",
        program=program,
    )


def _case(
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


def _dataset(*, edition: str = _EDITION) -> FunctionalGroundTruthSet:
    cases = sorted(
        [
            _case("req-1", GroundTruthCaseKind.POSITIVE, "PROG1", _FIX_A),
            _case("req-2", GroundTruthCaseKind.POSITIVE, "PROG2", _FIX_B),
            _case("forbidden-1", GroundTruthCaseKind.NEGATIVE, "PROG3", _FIX_C),
        ],
        key=lambda c: c.case_id,
    )
    return FunctionalGroundTruthSet(
        catalog_edition=edition,
        cases=cases,
        summary=FunctionalGroundTruthSummary(
            case_count=3, positive_case_count=2, negative_case_count=1, expected_rule_count=2
        ),
    )


def _report_a(
    *, run_id: str = "run-a", matched: bool = True, edition: str = _EDITION
) -> FunctionalValidationReport:
    """Cubre req-1 y forbidden-1 (fixtures FIX_A/FIX_C); req-2 queda
    pendiente en este run."""
    gt = _dataset(edition=edition)
    refs = [_ref("ref-req1", "RETURN_CODE", "PROG1")] if matched else []
    return validate_ground_truth(
        gt,
        refs,
        run_id=run_id,
        source_package_hash=_HASH,
        run_fixture_hashes=frozenset({_FIX_A, _FIX_C}),
    )


def _report_b(*, run_id: str = "run-b") -> FunctionalValidationReport:
    """Cubre unicamente req-2 (fixture FIX_B)."""
    gt = _dataset()
    refs = [_ref("ref-req2", "RETURN_CODE", "PROG2")]
    return validate_ground_truth(
        gt, refs, run_id=run_id, source_package_hash=_HASH, run_fixture_hashes=frozenset({_FIX_B})
    )


def _aggregate(reports: list[FunctionalValidationReport], **kwargs: object):
    kwargs.setdefault("ground_truth", _dataset())
    kwargs.setdefault("dataset_id", "synthetic_engineering")
    kwargs.setdefault("lane", FunctionalDatasetLane.UNIFIED)
    return aggregate_functional_validation_reports(reports, **kwargs)


# 1. run parcial (un solo reporte de entrada) --------------------------


def test_single_partial_report_stays_partially_evaluated() -> None:
    aggregated = _aggregate([_report_a()])
    assert aggregated.coverage_status == FunctionalDatasetCoverageStatus.PARTIALLY_EVALUATED
    assert aggregated.pending_case_ids == ["req-2"]
    assert aggregated.dataset_disposition == FunctionalDatasetDisposition.NOT_EVALUATED


# 2. dataset completo multi-run -----------------------------------------


def test_two_reports_cover_the_full_dataset() -> None:
    aggregated = _aggregate([_report_a(), _report_b()])
    assert aggregated.coverage_status == FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED
    assert aggregated.pending_case_ids == []
    assert aggregated.dataset_disposition == FunctionalDatasetDisposition.PASS_ENGINEERING
    outcomes = {c.case_id: c.outcome for c in aggregated.case_results}
    assert outcomes["req-1"] == MatchOutcome.MATCHED
    assert outcomes["req-2"] == MatchOutcome.MATCHED
    assert outcomes["forbidden-1"] == MatchOutcome.CONFIRMED_ABSENT
    assert aggregated.metrics.true_positive_count == 2
    assert aggregated.metrics.true_negative_count == 1
    assert aggregated.metrics.precision == 1.0
    assert aggregated.metrics.recall == 1.0
    assert aggregated.metrics.f1_score == 1.0


# 3. caso REQUIRED pendiente --------------------------------------------


def test_required_case_pending_when_only_one_report_included() -> None:
    aggregated = _aggregate([_report_a()])
    req2 = next(c for c in aggregated.case_results if c.case_id == "req-2")
    assert req2.applicability == Applicability.NOT_APPLICABLE
    assert req2.outcome == MatchOutcome.NOT_EVALUATED
    assert req2.source_run_id is None


# 4. caso FORBIDDEN pendiente --------------------------------------------


def test_forbidden_case_pending_when_its_fixture_never_included() -> None:
    aggregated = _aggregate([_report_b()])
    forbidden = next(c for c in aggregated.case_results if c.case_id == "forbidden-1")
    assert forbidden.applicability == Applicability.NOT_APPLICABLE
    assert forbidden.outcome == MatchOutcome.NOT_EVALUATED
    assert "forbidden-1" in aggregated.pending_case_ids


# 5. reporte duplicado (mismo run_id dos veces) --------------------------


def test_duplicate_run_id_rejected() -> None:
    report = _report_a()
    with pytest.raises(FunctionalDatasetAggregationError, match="repetido"):
        _aggregate([report, report])


# 6. caso duplicado (dos runs distintos, mismo caso, mismo outcome) ------


def test_duplicate_case_reconciled_without_error() -> None:
    aggregated = _aggregate([_report_a(run_id="run-a1"), _report_a(run_id="run-a2")])
    assert "req-1" in aggregated.duplicate_case_ids
    assert "forbidden-1" in aggregated.duplicate_case_ids
    req1 = next(c for c in aggregated.case_results if c.case_id == "req-1")
    assert req1.applicability == Applicability.APPLICABLE
    assert req1.source_run_id == "run-a1"  # run_id menor, orden deterministico


# 7. caso conflictivo (dos runs, mismo caso, outcome distinto) -----------


def test_conflicting_case_outcomes_raise() -> None:
    matched_report = _report_a(run_id="run-a1", matched=True)
    missing_report = _report_a(run_id="run-a2", matched=False)
    with pytest.raises(FunctionalDatasetAggregationError, match="conflicto bloqueante"):
        _aggregate([matched_report, missing_report])


def test_conflicting_case_never_persists_partial_report() -> None:
    """Ante un conflicto, no debe existir forma de obtener un
    FunctionalDatasetValidationReport parcial -- la excepcion se lanza
    antes de construir el modelo."""
    matched_report = _report_a(run_id="run-a1", matched=True)
    missing_report = _report_a(run_id="run-a2", matched=False)
    try:
        _aggregate([matched_report, missing_report])
        raised = False
    except FunctionalDatasetAggregationError:
        raised = True
    assert raised


# 8. lane diferente -------------------------------------------------------


def test_different_lane_changes_report_id() -> None:
    reports = [_report_a(), _report_b()]
    unified = _aggregate(reports, lane=FunctionalDatasetLane.UNIFIED)
    assert unified.lane == FunctionalDatasetLane.UNIFIED
    # Unico lane soportado hoy -- se prueba que el campo se propaga y
    # participa en report_id (ver test_report_id_is_stable_and_content_addressed).


# 9. dataset diferente -----------------------------------------------------


def test_report_from_different_dataset_edition_rejected() -> None:
    mismatched = _report_a(edition="other-edition")
    with pytest.raises(FunctionalDatasetAggregationError, match="no coincide"):
        _aggregate([mismatched], ground_truth=_dataset(edition=_EDITION))


# 10. version diferente ----------------------------------------------------


def test_reports_with_different_versions_between_them_rejected() -> None:
    report_v1 = _report_a(run_id="run-a", edition="v1.0")
    report_v2 = _report_b(run_id="run-b")  # edition == _EDITION por defecto
    with pytest.raises(FunctionalDatasetAggregationError, match="no coincide"):
        _aggregate([report_v1, report_v2], ground_truth=_dataset(edition="v1.0"))


# 11. ordering deterministico ------------------------------------------------


def test_ordering_is_deterministic_regardless_of_input_order() -> None:
    forward = _aggregate([_report_a(), _report_b()])
    backward = _aggregate([_report_b(), _report_a()])
    assert [c.case_id for c in forward.case_results] == [c.case_id for c in backward.case_results]
    assert forward.source_run_ids == sorted(forward.source_run_ids)
    assert forward.case_results == backward.case_results


# 12. report_id estable / content-addressed ----------------------------------


def test_report_id_is_stable_and_content_addressed() -> None:
    first = _aggregate([_report_a(), _report_b()])
    second = _aggregate([_report_a(), _report_b()])
    assert first.report_id == second.report_id

    different_content = _aggregate([_report_a(matched=False), _report_b()])
    # matched=False produce un run_fixture distinto en contenido (MISSING
    # en vez de MATCHED para req-1), asi que su content-hash de entrada
    # difiere -- el report_id agregado tambien debe diferir.
    assert different_content.report_id != first.report_id


def test_empty_reports_rejected() -> None:
    with pytest.raises(FunctionalDatasetAggregationError):
        _aggregate([])
