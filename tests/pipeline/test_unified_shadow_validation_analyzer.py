"""Tests del analizador PURO principal (Fase 12 Parte 10,
`feat/unified-shadow-differential-validation`). El escenario real
JAR+Neo4j (grupo VALID/NOT_IN_BASELINE elegible, disposition
QUALIFIED_FOR_DOWNSTREAM_SHADOW) se cubre en
`tests/parser_integration/test_unified_shadow_validation_integration.py`
-- aqui se aislan las rutas NOT_EVALUATED/BLOCKED/REVIEW_REQUIRED y el
determinismo, con fuentes sinteticas."""

from __future__ import annotations

import pytest

from altamira_extractor.contracts.candidate import CandidateStatus, RuleCandidate
from altamira_extractor.contracts.unified_shadow_validation import (
    UnifiedShadowGateStatus,
    UnifiedShadowValidationDisposition,
    UnifiedShadowValidationIssueCode,
)
from altamira_extractor.pipeline.errors import UnifiedShadowValidationError

from ._unified_shadow_validation_fixtures import (
    HASH,
    analyze_golden_path,
    golden_path,
    golden_path_with_working_v2_resolution,
)

Code = UnifiedShadowValidationIssueCode
Disposition = UnifiedShadowValidationDisposition
Status = UnifiedShadowGateStatus


def test_missing_required_source_produces_not_evaluated() -> None:
    gp = golden_path()
    report = analyze_golden_path(gp, unified_shadow_override=None)
    assert report.disposition == Disposition.NOT_EVALUATED
    assert report.group_validations == []
    non_source_gates = [g for g in report.gate_results if g.gate.value != "SOURCE_INTEGRITY"]
    assert all(g.status == Status.NOT_EVALUATED for g in non_source_gates)


def test_v2_absent_but_member_sourced_from_v2_produces_blocked() -> None:
    gp = golden_path()
    report = analyze_golden_path(gp)
    assert report.disposition == Disposition.BLOCKED
    assert any(issue.code == Code.SHADOW_MEMBER_SOURCE_NOT_FOUND for issue in report.issues)
    assert any(issue.severity.value == "BLOCKING" for issue in report.issues)


def test_functional_validation_required_issue_always_present_when_evaluated() -> None:
    gp = golden_path()
    report = analyze_golden_path(gp)
    assert any(issue.code == Code.FUNCTIONAL_VALIDATION_REQUIRED for issue in report.issues)


def test_summary_reconciles_with_group_validations() -> None:
    gp = golden_path()
    report = analyze_golden_path(gp)
    assert report.summary.shadow_group_count == len(report.group_validations)
    assert report.summary.downstream_eligible_group_count == sum(
        1 for gv in report.group_validations if gv.downstream_shadow_eligible
    )


def test_every_gate_appears_exactly_once() -> None:
    gp = golden_path()
    report = analyze_golden_path(gp)
    gates = [g.gate for g in report.gate_results]
    assert len(gates) == 12
    assert len(set(gates)) == 12


def test_deterministic_across_two_independent_calls() -> None:
    gp = golden_path()
    report_a = analyze_golden_path(gp)
    report_b = analyze_golden_path(gp)
    assert report_a.to_stable_json() == report_b.to_stable_json()


def test_deterministic_issue_ids_stable_across_calls() -> None:
    gp = golden_path()
    report_a = analyze_golden_path(gp)
    report_b = analyze_golden_path(gp)
    ids_a = sorted(issue.issue_id for issue in report_a.issues)
    ids_b = sorted(issue.issue_id for issue in report_b.issues)
    assert ids_a == ids_b


def test_result_is_a_frozen_snapshot_never_mutates_golden_path() -> None:
    gp = golden_path()
    unified_shadow_before = gp.unified_shadow.model_copy(deep=True)
    assessment_before = gp.assessment.model_copy(deep=True)
    analyze_golden_path(gp)
    assert gp.unified_shadow == unified_shadow_before
    assert gp.assessment == assessment_before


def test_disequal_list_ordering_of_equivalent_inputs_still_produces_identical_bytes() -> None:
    gp = golden_path()
    reordered_unified_shadow = gp.unified_shadow.model_copy(
        update={
            "shadow_members": list(reversed(gp.unified_shadow.shadow_members)),
            "shadow_groups": list(reversed(gp.unified_shadow.shadow_groups)),
        }
    )
    report_a = analyze_golden_path(gp)
    report_b = analyze_golden_path(gp, unified_shadow_override=reordered_unified_shadow)
    assert report_a.to_stable_json() == report_b.to_stable_json()


# --- Baseline validator / analizador puro: robustez ante entradas
# --- adversas (cierre de seguridad, Parte 4) ---


def _malformed_v1() -> object:
    gp = golden_path()
    return gp.v1.model_copy(
        update={
            "candidates": [
                RuleCandidate(
                    candidate_id="candidate::malformed::1",
                    paragraph_id="not-a-well-formed-paragraph-id",
                    paragraph_name="MAIN",
                    decision_id="decision::1",
                    detector_id="q0-return-code-decision",
                    detector_version="1.0",
                    detector_score=1.0,
                    condition="RETURN-CODE = 0",
                    line_start=1,
                    source_file="src/PROG.cbl",
                    source_package_hash=HASH,
                    status=CandidateStatus.DETECTED_CANDIDATE,
                )
            ]
        }
    )


def test_analyzer_raises_typed_error_for_malformed_v1_paragraph_id() -> None:
    """Un candidato V1 con `paragraph_id` de formato inesperado hace
    que `adapt_v1_baseline_candidates` (Fase 11) no pueda derivar
    `program` -- el analizador puro debe relanzar
    `UnifiedShadowValidationError` (Fase 12, tipado), NUNCA dejar
    escapar el `UnifiedCandidatesShadowError` crudo de Fase 11 ni
    ignorar la excepcion silenciosamente."""
    gp, v2 = golden_path_with_working_v2_resolution()
    with pytest.raises(UnifiedShadowValidationError):
        analyze_golden_path(gp, v2=v2, v1_override=_malformed_v1())


def test_analyzer_baseline_hash_inconsistent_produces_not_evaluated() -> None:
    """Un V1 cuyo `source_package_hash` cambio desde que el assessment
    lo registro se intercepta en SOURCE_INTEGRITY (antes de llegar al
    adaptador de baseline) -- disposition NOT_EVALUATED, nunca una
    excepcion no controlada."""
    gp, v2 = golden_path_with_working_v2_resolution()
    inconsistent_v1 = gp.v1.model_copy(update={"source_package_hash": "b" * 64})
    report = analyze_golden_path(gp, v2=v2, v1_override=inconsistent_v1)
    assert report.disposition == Disposition.NOT_EVALUATED
    source_gate = next(g for g in report.gate_results if g.gate.value == "SOURCE_INTEGRITY")
    assert source_gate.status == Status.FAIL


def test_analyzer_baseline_reference_incomplete_produces_missing_finding() -> None:
    """Un V1 con un candidato real que NUNCA se reflejo en
    `unified_shadow.baseline_candidates` produce
    BASELINE_CANDIDATE_MISSING -- representable en el reporte, nunca
    una excepcion."""
    gp, v2 = golden_path_with_working_v2_resolution()
    v1_with_extra_candidate = gp.v1.model_copy(
        update={
            "candidates": [
                RuleCandidate(
                    candidate_id="candidate::v1::extra",
                    paragraph_id=(
                        "program::CO::CALLER10::CALLER10::1::abc123456789::paragraph::MAIN"
                    ),
                    paragraph_name="MAIN",
                    decision_id="decision::extra::1",
                    detector_id="q0-return-code-decision",
                    detector_version="1.0",
                    detector_score=1.0,
                    condition="RETURN-CODE = 0",
                    line_start=10,
                    source_file="src/CALLER10.cbl",
                    source_package_hash=HASH,
                    status=CandidateStatus.DETECTED_CANDIDATE,
                )
            ]
        }
    )
    report = analyze_golden_path(gp, v2=v2, v1_override=v1_with_extra_candidate)
    codes = {issue.code.value for issue in report.issues}
    assert "BASELINE_CANDIDATE_MISSING" in codes
    baseline_gate = next(g for g in report.gate_results if g.gate.value == "BASELINE_COMPLETENESS")
    assert baseline_gate.status == Status.FAIL


def test_analyzer_valid_control_input_produces_no_baseline_findings() -> None:
    """Control: `golden_path_with_working_v2_resolution()` sin mutar
    (V1 vacio, baseline vacio, reconciliado) nunca produce un hallazgo
    de baseline ni una excepcion -- BASELINE_COMPLETENESS PASS."""
    gp, v2 = golden_path_with_working_v2_resolution()
    report = analyze_golden_path(gp, v2=v2)
    baseline_gate = next(g for g in report.gate_results if g.gate.value == "BASELINE_COMPLETENESS")
    assert baseline_gate.status == Status.PASS
    codes = {issue.code.value for issue in report.issues}
    assert "BASELINE_CANDIDATE_MISSING" not in codes
    assert "BASELINE_COUNT_MISMATCH" not in codes
