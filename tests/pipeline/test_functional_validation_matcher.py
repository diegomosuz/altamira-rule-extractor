"""Tests del analizador puro de validacion funcional (Fase 15B2-A, Parte
F): `pipeline/functional_validation_matcher.py`. Incluye el checkpoint
correctivo de aplicabilidad (cierre de Fase 15B2-A): un caso cuyo
fixture set no esta presente en `run_fixture_hashes` nunca se evalua
contra `candidate_references`, sin importar cuantos candidatos reales
existan."""

from __future__ import annotations

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedCandidateReference,
)
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
    CaseMetricReasonCode,
    CaseMetricStatus,
    FunctionalDatasetCoverageStatus,
    FunctionalDatasetDisposition,
    MatchOutcome,
)
from altamira_extractor.pipeline.functional_validation_matcher import (
    GuardrailLookupEntry,
    compute_case_applicability,
    validate_ground_truth,
)

_HASH = "a" * 64
_FIXTURE_SHA = "b" * 64
_OTHER_FIXTURE_SHA = "c" * 64
_APPLICABLE_HASHES = frozenset({_FIXTURE_SHA})
_INAPPLICABLE_HASHES = frozenset({_OTHER_FIXTURE_SHA})


def _ref(
    ref_id: str,
    rule_family: str,
    program: str,
    paragraph: str | None,
    *,
    output_literal: str | None = None,
    condition: str | None = None,
) -> UnifiedCandidateReference:
    return UnifiedCandidateReference(
        unified_reference_id=ref_id,
        source=CandidateSource.V1,
        source_candidate_id=ref_id,
        source_artifact_hash=_HASH,
        rule_family=rule_family,
        original_support="DETERMINISTIC",
        program=program,
        paragraph=paragraph,
        output_literal=output_literal,
        condition=condition,
    )


def _positive_case() -> GroundTruthCase:
    return GroundTruthCase(
        case_id="pos-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        fixtures=[
            GroundTruthFixtureReference(
                relative_path="config/ground_truth/fixtures/x.cbl", sha256=_FIXTURE_SHA
            )
        ],
        description="caso positivo",
        expected_rules=[
            GroundTruthExpectedRule(
                expectation_id="pos-1::e1",
                rule_family="RETURN_CODE",
                paragraph="MAIN-PARA",
                minimum_count=1,
                derivation_notes="nota de prueba",
            )
        ],
    )


def _negative_case() -> GroundTruthCase:
    return GroundTruthCase(
        case_id="neg-1",
        kind=GroundTruthCaseKind.NEGATIVE,
        program="PROG2",
        fixtures=[
            GroundTruthFixtureReference(
                relative_path="config/ground_truth/fixtures/y.cbl", sha256=_FIXTURE_SHA
            )
        ],
        description="caso negativo",
        expected_rules=[],
    )


def _ground_truth(cases: list[GroundTruthCase]) -> FunctionalGroundTruthSet:
    cases = sorted(cases, key=lambda c: c.case_id)
    positive = sum(1 for c in cases if c.kind == GroundTruthCaseKind.POSITIVE)
    negative = sum(1 for c in cases if c.kind == GroundTruthCaseKind.NEGATIVE)
    rules = sum(len(c.expected_rules) for c in cases)
    return FunctionalGroundTruthSet(
        catalog_edition="edition-1",
        cases=cases,
        summary=FunctionalGroundTruthSummary(
            case_count=len(cases),
            positive_case_count=positive,
            negative_case_count=negative,
            expected_rule_count=rules,
        ),
    )


def _validate(gt: FunctionalGroundTruthSet, refs: list[UnifiedCandidateReference], **kwargs):
    kwargs.setdefault("run_id", "run-1")
    kwargs.setdefault("source_package_hash", _HASH)
    kwargs.setdefault("run_fixture_hashes", _APPLICABLE_HASHES)
    return validate_ground_truth(gt, refs, **kwargs)


def test_positive_case_matched_when_reference_present() -> None:
    gt = _ground_truth([_positive_case()])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA")]
    report = _validate(gt, refs)
    case = report.case_results[0]
    assert case.outcome == MatchOutcome.MATCHED
    assert report.metrics.true_positive_count == 1


def test_positive_case_missing_when_reference_absent() -> None:
    gt = _ground_truth([_positive_case()])
    report = _validate(gt, [])
    assert report.case_results[0].outcome == MatchOutcome.MISSING
    assert report.metrics.false_negative_count == 1


def test_positive_case_ignores_wrong_program() -> None:
    gt = _ground_truth([_positive_case()])
    refs = [_ref("r1", "RETURN_CODE", "OTHER_PROGRAM", "MAIN-PARA")]
    report = _validate(gt, refs)
    assert report.case_results[0].outcome == MatchOutcome.MISSING


def test_positive_case_ignores_wrong_rule_family() -> None:
    gt = _ground_truth([_positive_case()])
    refs = [_ref("r1", "BY_REFERENCE_OUTPUT", "PROG1", "MAIN-PARA")]
    report = _validate(gt, refs)
    assert report.case_results[0].outcome == MatchOutcome.MISSING


def test_positive_case_ignores_wrong_paragraph() -> None:
    gt = _ground_truth([_positive_case()])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "OTHER-PARA")]
    report = _validate(gt, refs)
    assert report.case_results[0].outcome == MatchOutcome.MISSING


# --- 5D-SAFETY-2 (cierre de BRANCH_EXPECTATION_NOT_EXACT): ------------------
# expected_output_literal discrimina candidatos con la misma
# (rule_family, program, paragraph) pero un literal distinto -- un
# candidato de literal equivocado ya NUNCA satisface silenciosamente una
# expectation de un hecho funcional especifico.


def _branch_case(*, expected_a: str, expected_b: str) -> GroundTruthCase:
    return GroundTruthCase(
        case_id="branch-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        fixtures=[
            GroundTruthFixtureReference(
                relative_path="config/ground_truth/fixtures/x.cbl", sha256=_FIXTURE_SHA
            )
        ],
        description="caso multi-branch (IF/ELSE) con dos hechos exactos",
        expected_rules=[
            GroundTruthExpectedRule(
                expectation_id="branch-1::a",
                rule_family="RETURN_CODE",
                paragraph="MAIN-PARA",
                expected_output_literal=expected_a,
                minimum_count=1,
                derivation_notes="rama A",
            ),
            GroundTruthExpectedRule(
                expectation_id="branch-1::b",
                rule_family="RETURN_CODE",
                paragraph="MAIN-PARA",
                expected_output_literal=expected_b,
                minimum_count=1,
                derivation_notes="rama B",
            ),
        ],
    )


def test_positive_case_ignores_wrong_output_literal_when_declared() -> None:
    gt = _ground_truth([_branch_case(expected_a="0", expected_b="99")])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA", output_literal="77")]
    report = _validate(gt, refs)
    for expectation in report.case_results[0].expectation_results:
        assert expectation.outcome == MatchOutcome.MISSING, expectation.expectation_id


def test_positive_case_matches_correct_output_literal_when_declared() -> None:
    gt = _ground_truth([_branch_case(expected_a="0", expected_b="99")])
    refs = [
        _ref("r-else", "RETURN_CODE", "PROG1", "MAIN-PARA", output_literal="0"),
        _ref("r-then", "RETURN_CODE", "PROG1", "MAIN-PARA", output_literal="99"),
    ]
    report = _validate(gt, refs)
    assert report.case_results[0].outcome == MatchOutcome.MATCHED
    assert report.metrics.true_positive_count == 2


def test_multi_branch_wrong_outcome_never_counted_as_perfect_tp() -> None:
    """Seccion 9 (5D-SAFETY-2), test obligatorio: correct family, correct
    paragraph, correct RAW count (2 candidatos presentes) pero UN literal
    equivocado (77 en vez de 0) -- nunca debe pasar como un caso MATCHED
    perfecto. Reproduce exactamente el probe empirico de la Seccion 2."""
    gt = _ground_truth([_branch_case(expected_a="0", expected_b="99")])
    refs = [
        _ref("r-then", "RETURN_CODE", "PROG1", "MAIN-PARA", output_literal="99"),
        _ref("r-wrong", "RETURN_CODE", "PROG1", "MAIN-PARA", output_literal="77"),
    ]
    report = _validate(gt, refs)
    case = report.case_results[0]
    assert case.outcome == MatchOutcome.MISSING
    assert len(refs) == 2  # el RAW count "parece" correcto -- pero el caso no pasa
    outcomes = {e.expectation_id: e.outcome for e in case.expectation_results}
    assert outcomes["branch-1::a"] == MatchOutcome.MISSING  # ''0'' nunca aparecio
    assert outcomes["branch-1::b"] == MatchOutcome.MATCHED  # ''99'' si aparecio
    assert report.metrics.true_positive_count == 1
    assert report.metrics.false_negative_count == 1


def test_output_literal_none_preserves_backward_compatibility() -> None:
    """expected_output_literal ausente (None) preserva el comportamiento
    historico: family+paragraph+count, sin discriminar por literal --
    necesario para toda regla de un solo hecho (RETURN_CODE simple,
    LEVEL_88, STATE_TRANSITION, CALCULATION)."""
    gt = _ground_truth([_positive_case()])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA", output_literal="cualquier-cosa")]
    report = _validate(gt, refs)
    assert report.case_results[0].outcome == MatchOutcome.MATCHED


# --- 5D-SAFETY-3 seccion 4: expected_condition --------------------------------


def _condition_case() -> GroundTruthCase:
    return GroundTruthCase(
        case_id="cond-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        fixtures=[
            GroundTruthFixtureReference(
                relative_path="config/ground_truth/fixtures/x.cbl", sha256=_FIXTURE_SHA
            )
        ],
        description="caso con expected_condition declarado",
        expected_rules=[
            GroundTruthExpectedRule(
                expectation_id="cond-1::e1",
                rule_family="RETURN_CODE",
                paragraph="MAIN-PARA",
                expected_output_literal="99",
                expected_condition="WS-MONTO>1000",
                minimum_count=1,
                derivation_notes="nota de prueba",
            )
        ],
    )


def test_positive_case_ignores_wrong_condition_when_declared() -> None:
    """Seccion 3 (5D-SAFETY-3), probe obligatorio: family/program/
    paragraph/output_literal correctos, pero condition equivocada --
    NUNCA un match perfecto. Debe aparecer expected fact missing (fn=1)
    + actual fact unexpected (fp=1), nunca un MATCHED silencioso."""
    gt = _ground_truth([_condition_case()])
    refs = [
        _ref(
            "r1",
            "RETURN_CODE",
            "PROG1",
            "MAIN-PARA",
            output_literal="99",
            condition="WS-OTRA-COSA<>0",
        )
    ]
    report = _validate(gt, refs)
    assert report.case_results[0].outcome == MatchOutcome.MISSING
    assert report.metrics.false_negative_count == 1
    assert report.metrics.false_positive_count == 1


def test_positive_case_matches_correct_condition_when_declared() -> None:
    gt = _ground_truth([_condition_case()])
    refs = [
        _ref(
            "r1",
            "RETURN_CODE",
            "PROG1",
            "MAIN-PARA",
            output_literal="99",
            condition="WS-MONTO>1000",
        )
    ]
    report = _validate(gt, refs)
    assert report.case_results[0].outcome == MatchOutcome.MATCHED
    assert report.metrics.false_positive_count == 0


def test_expected_condition_none_preserves_backward_compatibility() -> None:
    gt = _ground_truth([_positive_case()])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA", condition="cualquier-condicion")]
    report = _validate(gt, refs)
    assert report.case_results[0].outcome == MatchOutcome.MATCHED


# --- 5D-SAFETY-3 secciones 6-8: actual no emparejado en scope -> FP ----------


def test_unmatched_actual_within_scope_counts_as_fp() -> None:
    """Seccion 6, probe obligatorio: 2 expectations exactas, candidate A
    correcto + candidate B que no satisface ninguna -- TP=1 FN=1 FP=1."""
    gt = _ground_truth([_branch_case(expected_a="0", expected_b="99")])
    refs = [
        _ref("r-a", "RETURN_CODE", "PROG1", "MAIN-PARA", output_literal="0"),
        _ref("r-unexpected", "RETURN_CODE", "PROG1", "MAIN-PARA", output_literal="55"),
    ]
    report = _validate(gt, refs)
    assert report.metrics.true_positive_count == 1
    assert report.metrics.false_negative_count == 1
    assert report.metrics.false_positive_count == 1


def test_extra_branch_counts_as_fp_never_hidden() -> None:
    """Seccion 7, probe obligatorio: GT espera 2 branches, actual produce
    3 (2 correctos + 1 adicional espurio) -- TP=2 FN=0 FP=1. Protege
    contra reaparicion de duplicados/branches espurios."""
    gt = _ground_truth([_branch_case(expected_a="0", expected_b="99")])
    refs = [
        _ref("r-a", "RETURN_CODE", "PROG1", "MAIN-PARA", output_literal="0"),
        _ref("r-b", "RETURN_CODE", "PROG1", "MAIN-PARA", output_literal="99"),
        _ref("r-extra", "RETURN_CODE", "PROG1", "MAIN-PARA", output_literal="55"),
    ]
    report = _validate(gt, refs)
    assert report.metrics.true_positive_count == 2
    assert report.metrics.false_negative_count == 0
    assert report.metrics.false_positive_count == 1


def test_missing_branch_counts_as_fn_never_fp() -> None:
    """Seccion 8: GT espera 2, actual solo produce 1 -- TP=1 FN=1 FP=0
    (una branch ausente nunca es un FP)."""
    gt = _ground_truth([_branch_case(expected_a="0", expected_b="99")])
    refs = [_ref("r-a", "RETURN_CODE", "PROG1", "MAIN-PARA", output_literal="0")]
    report = _validate(gt, refs)
    assert report.metrics.true_positive_count == 1
    assert report.metrics.false_negative_count == 1
    assert report.metrics.false_positive_count == 0


def test_unmatched_actual_never_leaks_into_unexpected_for_simple_case() -> None:
    """Seccion 9 (backward compat): un caso simple (sin expected_output_
    literal/expected_condition, una unica expectation) nunca produce FP
    por candidatos "extra" -- la unica expectation, sin filtro, ya
    reclama todo el scope (mismo criterio que 5C: duplicados visibles
    via matched_count, nunca ocultos, pero tampoco FP fabricado)."""
    gt = _ground_truth([_positive_case()])
    refs = [
        _ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA"),
        _ref("r2", "RETURN_CODE", "PROG1", "MAIN-PARA"),
    ]
    report = _validate(gt, refs)
    assert report.case_results[0].outcome == MatchOutcome.MATCHED
    assert report.case_results[0].expectation_results[0].matched_count == 2
    assert report.metrics.false_positive_count == 0


def test_negative_case_confirmed_absent_when_no_candidates() -> None:
    gt = _ground_truth([_negative_case()])
    report = _validate(gt, [])
    assert report.case_results[0].outcome == MatchOutcome.CONFIRMED_ABSENT
    assert report.metrics.true_negative_count == 1


def test_negative_case_unexpected_when_candidate_present() -> None:
    gt = _ground_truth([_negative_case()])
    refs = [_ref("r9", "RETURN_CODE", "PROG2", "ANY-PARA")]
    report = _validate(gt, refs)
    case = report.case_results[0]
    assert case.outcome == MatchOutcome.UNEXPECTED_CANDIDATES
    assert case.unexpected_candidate_reference_ids == ["r9"]
    assert report.metrics.false_positive_count == 1


def test_negative_case_ignores_unknown_rule_family() -> None:
    gt = _ground_truth([_negative_case()])
    refs = [_ref("r9", "UNKNOWN", "PROG2", "ANY-PARA")]
    report = _validate(gt, refs)
    assert report.case_results[0].outcome == MatchOutcome.CONFIRMED_ABSENT


def test_case_results_sorted_by_case_id_regardless_of_input_order() -> None:
    gt = _ground_truth([_negative_case(), _positive_case()])
    report = _validate(gt, [])
    ids = [c.case_id for c in report.case_results]
    assert ids == sorted(ids)


def test_perfect_run_yields_precision_and_recall_of_one() -> None:
    gt = _ground_truth([_negative_case(), _positive_case()])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA")]
    report = _validate(gt, refs)
    assert report.metrics.precision == 1.0
    assert report.metrics.recall == 1.0
    assert report.metrics.f1_score == 1.0


def test_metrics_undefined_without_positive_or_negative_cases() -> None:
    gt = _ground_truth([])
    report = _validate(gt, [])
    assert report.metrics.precision is None
    assert report.metrics.recall is None
    assert report.metrics.f1_score is None


# --- Checkpoint correctivo: aplicabilidad (cierre de Fase 15B2-A) -----


def test_compute_case_applicability_applicable_when_fixture_present() -> None:
    assert compute_case_applicability(_positive_case(), _APPLICABLE_HASHES) == (
        Applicability.APPLICABLE
    )


def test_compute_case_applicability_not_applicable_when_fixture_absent() -> None:
    assert compute_case_applicability(_positive_case(), _INAPPLICABLE_HASHES) == (
        Applicability.NOT_APPLICABLE
    )


def test_compute_case_applicability_requires_all_fixtures_present() -> None:
    """Un caso interprocedural (caller+callee) exige AMBAS fixtures en
    el mismo run -- una sola presente no basta."""
    case = GroundTruthCase(
        case_id="interproc-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="CALLER1",
        fixtures=[
            GroundTruthFixtureReference(
                relative_path="config/ground_truth/fixtures/callee.cbl",
                sha256=_OTHER_FIXTURE_SHA,
            ),
            GroundTruthFixtureReference(
                relative_path="config/ground_truth/fixtures/caller.cbl", sha256=_FIXTURE_SHA
            ),
        ],
        description="caso interprocedural",
        expected_rules=[
            GroundTruthExpectedRule(
                expectation_id="interproc-1::e1",
                rule_family="BY_REFERENCE_OUTPUT",
                minimum_count=1,
                derivation_notes="nota de prueba",
            )
        ],
    )
    assert compute_case_applicability(case, _APPLICABLE_HASHES) == Applicability.NOT_APPLICABLE
    assert compute_case_applicability(case, _INAPPLICABLE_HASHES) == Applicability.NOT_APPLICABLE
    assert compute_case_applicability(
        case, frozenset({_FIXTURE_SHA, _OTHER_FIXTURE_SHA})
    ) == Applicability.APPLICABLE


def test_not_applicable_positive_case_is_not_evaluated() -> None:
    gt = _ground_truth([_positive_case()])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA")]
    report = _validate(gt, refs, run_fixture_hashes=_INAPPLICABLE_HASHES)
    case = report.case_results[0]
    assert case.applicability == Applicability.NOT_APPLICABLE
    assert case.outcome == MatchOutcome.NOT_EVALUATED
    assert case.expectation_results == []


def test_different_package_does_not_generate_false_negative() -> None:
    """Un run de un paquete distinto (sin la fixture del caso), incluso
    SIN ningun candidato real, nunca produce un FN -- el caso queda
    NOT_EVALUATED, no MISSING."""
    gt = _ground_truth([_positive_case()])
    report = _validate(gt, [], run_fixture_hashes=_INAPPLICABLE_HASHES)
    assert report.case_results[0].outcome == MatchOutcome.NOT_EVALUATED
    assert report.metrics.false_negative_count == 0


def test_different_package_does_not_reduce_recall() -> None:
    """Mezclando un caso aplicable (MATCHED) con uno no aplicable: el
    recall se calcula UNICAMENTE sobre el caso aplicable -- el
    inaplicable ni siquiera participa en el denominador."""
    applicable_case = _positive_case()
    inapplicable_case = GroundTruthCase(
        case_id="pos-2",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG9",
        fixtures=[
            GroundTruthFixtureReference(
                relative_path="config/ground_truth/fixtures/z.cbl", sha256=_OTHER_FIXTURE_SHA
            )
        ],
        description="caso de otro paquete",
        expected_rules=[
            GroundTruthExpectedRule(
                expectation_id="pos-2::e1",
                rule_family="RETURN_CODE",
                minimum_count=1,
                derivation_notes="nota de prueba",
            )
        ],
    )
    gt = _ground_truth([applicable_case, inapplicable_case])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA")]
    report = _validate(gt, refs, run_fixture_hashes=_APPLICABLE_HASHES)
    outcomes = {c.case_id: c.outcome for c in report.case_results}
    assert outcomes["pos-1"] == MatchOutcome.MATCHED
    assert outcomes["pos-2"] == MatchOutcome.NOT_EVALUATED
    assert report.metrics.recall == 1.0
    assert report.metrics.true_positive_count == 1
    assert report.metrics.false_negative_count == 0


def test_dataset_not_applicable_when_no_case_matches_run() -> None:
    gt = _ground_truth([_positive_case(), _negative_case()])
    report = _validate(gt, [], run_fixture_hashes=_INAPPLICABLE_HASHES)
    assert report.dataset_applicability == Applicability.NOT_APPLICABLE
    assert report.dataset_disposition == FunctionalDatasetDisposition.NOT_EVALUATED
    assert all(c.outcome == MatchOutcome.NOT_EVALUATED for c in report.case_results)
    assert report.metrics.precision is None
    assert report.metrics.recall is None
    assert report.metrics.true_positive_count == 0
    assert report.metrics.false_negative_count == 0
    assert report.metrics.false_positive_count == 0
    assert report.metrics.true_negative_count == 0


def test_dataset_pass_engineering_when_all_applicable_cases_satisfied() -> None:
    gt = _ground_truth([_positive_case(), _negative_case()])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA")]
    report = _validate(gt, refs, run_fixture_hashes=_APPLICABLE_HASHES)
    assert report.dataset_applicability == Applicability.APPLICABLE
    assert report.dataset_disposition == FunctionalDatasetDisposition.PASS_ENGINEERING


def test_dataset_fail_engineering_when_an_applicable_case_missing() -> None:
    gt = _ground_truth([_positive_case()])
    report = _validate(gt, [], run_fixture_hashes=_APPLICABLE_HASHES)
    assert report.dataset_applicability == Applicability.APPLICABLE
    assert report.dataset_disposition == FunctionalDatasetDisposition.FAIL_ENGINEERING


def test_not_applicable_metrics_serialize_as_none_never_zero_substitute() -> None:
    gt = _ground_truth([_negative_case()])
    report = _validate(gt, [], run_fixture_hashes=_INAPPLICABLE_HASHES)
    payload = report.model_dump(mode="json")
    assert payload["metrics"]["precision"] is None
    assert payload["metrics"]["recall"] is None
    assert payload["metrics"]["f1_score"] is None
    assert payload["dataset_applicability"] == "NOT_APPLICABLE"
    reloaded = type(report).model_validate_json(report.to_stable_json())
    assert reloaded == report


# --- Checkpoint correctivo: completitud del dataset (cierre Fase 15B2-A) --


def _second_positive_case(*, sha256: str) -> GroundTruthCase:
    return GroundTruthCase(
        case_id="pos-2",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG9",
        fixtures=[
            GroundTruthFixtureReference(
                relative_path="config/ground_truth/fixtures/z.cbl", sha256=sha256
            )
        ],
        description="segundo caso positivo",
        expected_rules=[
            GroundTruthExpectedRule(
                expectation_id="pos-2::e1",
                rule_family="RETURN_CODE",
                minimum_count=1,
                derivation_notes="nota de prueba",
            )
        ],
    )


def test_coverage_partially_evaluated_when_a_required_case_is_pending() -> None:
    gt = _ground_truth([_positive_case(), _second_positive_case(sha256=_OTHER_FIXTURE_SHA)])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA")]
    report = _validate(gt, refs, run_fixture_hashes=_APPLICABLE_HASHES)
    assert report.coverage_status == FunctionalDatasetCoverageStatus.PARTIALLY_EVALUATED
    assert report.pending_case_ids == ["pos-2"]
    assert report.required_case_count == 2
    assert report.evaluated_required_case_count == 1


def test_partial_coverage_never_reaches_pass_engineering_even_when_matched() -> None:
    """Checkpoint correctivo central: el unico caso aplicable esta
    MATCHED, pero el segundo caso REQUIRED del catalogo sigue pendiente
    en otro run/paquete -- dataset_disposition debe ser NOT_EVALUATED,
    nunca PASS_ENGINEERING."""
    gt = _ground_truth([_positive_case(), _second_positive_case(sha256=_OTHER_FIXTURE_SHA)])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA")]
    report = _validate(gt, refs, run_fixture_hashes=_APPLICABLE_HASHES)
    outcomes = {c.case_id: c.outcome for c in report.case_results}
    assert outcomes["pos-1"] == MatchOutcome.MATCHED
    assert outcomes["pos-2"] == MatchOutcome.NOT_EVALUATED
    assert report.dataset_disposition == FunctionalDatasetDisposition.NOT_EVALUATED
    assert report.dataset_disposition != FunctionalDatasetDisposition.PASS_ENGINEERING


def test_coverage_completely_evaluated_when_all_cases_applicable() -> None:
    gt = _ground_truth([_positive_case(), _negative_case()])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA")]
    report = _validate(gt, refs, run_fixture_hashes=_APPLICABLE_HASHES)
    assert report.coverage_status == FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED
    assert report.pending_case_ids == []


# --- Checkpoint correctivo: metricas por caso (Seccion 5) -----------------


def test_case_metrics_evaluated_for_unambiguous_single_match() -> None:
    gt = _ground_truth([_positive_case()])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA")]
    report = _validate(gt, refs)
    case = report.case_results[0]
    assert case.outcome == MatchOutcome.MATCHED
    assert case.case_metrics is not None
    assert case.case_metrics.status == CaseMetricStatus.EVALUATED
    assert case.case_metrics.unified_reference_id == "r1"
    assert case.case_metrics.source_candidate_id == "r1"
    assert case.case_metrics.evidence_reference_count == 0
    assert case.case_metrics.provenance_reference_count == 0
    assert case.case_metrics.guardrail_verdict is None


def test_case_metrics_includes_guardrail_info_for_v1_source() -> None:
    gt = _ground_truth([_positive_case()])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA")]
    guardrail_lookup = {"r1": GuardrailLookupEntry(verdict="EVIDENCE_VALIDATED", repair_attempts=1)}
    report = _validate(gt, refs, guardrail_by_candidate_id=guardrail_lookup)
    case_metrics = report.case_results[0].case_metrics
    assert case_metrics is not None
    assert case_metrics.guardrail_verdict == "EVIDENCE_VALIDATED"
    assert case_metrics.guardrail_repair_attempts == 1


def test_case_metrics_not_evaluated_when_multiple_candidates_match() -> None:
    """Mas de un candidato satisfaciendo la misma expectation nunca
    produce una asociacion inequivoca -- NOT_EVALUATED explicito, nunca
    se elige arbitrariamente cual "representa" el caso."""
    case = GroundTruthCase(
        case_id="pos-1",
        kind=GroundTruthCaseKind.POSITIVE,
        program="PROG1",
        fixtures=[
            GroundTruthFixtureReference(
                relative_path="config/ground_truth/fixtures/x.cbl", sha256=_FIXTURE_SHA
            )
        ],
        description="caso positivo con minimo 2",
        expected_rules=[
            GroundTruthExpectedRule(
                expectation_id="pos-1::e1",
                rule_family="RETURN_CODE",
                paragraph="MAIN-PARA",
                minimum_count=2,
                derivation_notes="nota de prueba",
            )
        ],
    )
    gt = _ground_truth([case])
    refs = [
        _ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA"),
        _ref("r2", "RETURN_CODE", "PROG1", "MAIN-PARA"),
    ]
    report = _validate(gt, refs)
    case_result = report.case_results[0]
    assert case_result.outcome == MatchOutcome.MATCHED
    assert case_result.case_metrics is not None
    assert case_result.case_metrics.status == CaseMetricStatus.NOT_EVALUATED
    assert (
        case_result.case_metrics.reason_code
        == CaseMetricReasonCode.CASE_ARTIFACT_TRACEABILITY_UNAVAILABLE
    )


def test_case_metrics_absent_for_non_matched_outcomes() -> None:
    gt = _ground_truth([_positive_case()])
    report = _validate(gt, [])
    assert report.case_results[0].outcome == MatchOutcome.MISSING
    assert report.case_results[0].case_metrics is None


def test_run_level_metrics_never_copied_into_case_metrics() -> None:
    """El run tiene tp=1, pero `case_metrics` (cuando EVALUATED) nunca
    expone true_positive_count/false_negative_count/etc -- esos campos
    simplemente no existen en CaseLevelMetrics."""
    gt = _ground_truth([_positive_case()])
    refs = [_ref("r1", "RETURN_CODE", "PROG1", "MAIN-PARA")]
    report = _validate(gt, refs)
    case_metrics = report.case_results[0].case_metrics
    assert case_metrics is not None
    assert not hasattr(case_metrics, "true_positive_count")
    assert not hasattr(case_metrics, "recall")
