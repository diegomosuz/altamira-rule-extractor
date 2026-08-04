"""Tests de contrato del reporte de validacion diferencial del
artefacto unificado de candidatos en shadow mode (Fase 12 de la
ampliacion semantica, `feat/unified-shadow-differential-validation`).
Construye instancias MINIMAS directamente contra el modelo Pydantic
(sin pasar por el analizador) para aislar cada invariante del
contrato -- ver `tests/pipeline/test_unified_shadow_validation_analyzer.py`
para pruebas de comportamiento end-to-end del analizador real."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.unified_candidates_shadow import (
    UnifiedShadowComparisonKind,
    UnifiedShadowGroupStatus,
)
from altamira_extractor.contracts.unified_shadow_validation import (
    UnifiedShadowGateStatus,
    UnifiedShadowGroupValidation,
    UnifiedShadowValidationDisposition,
    UnifiedShadowValidationGate,
    UnifiedShadowValidationGateResult,
    UnifiedShadowValidationIssue,
    UnifiedShadowValidationIssueCode,
    UnifiedShadowValidationIssueSeverity,
    UnifiedShadowValidationReport,
    UnifiedShadowValidationSummary,
)

HASH = "a" * 64
Code = UnifiedShadowValidationIssueCode
Gate = UnifiedShadowValidationGate
Severity = UnifiedShadowValidationIssueSeverity
Status = UnifiedShadowGateStatus
Disposition = UnifiedShadowValidationDisposition


def _issue(
    *,
    issue_id: str = "issue::1",
    code: Code = Code.GROUP_NOT_IN_BASELINE,
    severity: Severity = Severity.INFO,
    gate: Gate = Gate.BASELINE_DIFFERENTIAL_SAFETY,
    shadow_group_ids: list[str] | None = None,
    shadow_member_ids: list[str] | None = None,
) -> UnifiedShadowValidationIssue:
    return UnifiedShadowValidationIssue(
        issue_id=issue_id,
        code=code,
        severity=severity,
        gate=gate,
        message_code=f"MSG_{code.value}",
        shadow_group_ids=sorted(shadow_group_ids or []),
        shadow_member_ids=sorted(shadow_member_ids or []),
    )


def _gate_result(
    *,
    gate: Gate = Gate.SOURCE_INTEGRITY,
    status: Status = Status.PASS,
    required: bool = True,
    blocking: bool = True,
    issue_ids: list[str] | None = None,
    checked_group_ids: list[str] | None = None,
    checked_member_ids: list[str] | None = None,
) -> UnifiedShadowValidationGateResult:
    return UnifiedShadowValidationGateResult(
        gate=gate,
        status=status,
        required=required,
        blocking=blocking,
        issue_ids=sorted(issue_ids or []),
        checked_group_ids=sorted(checked_group_ids or []),
        checked_member_ids=sorted(checked_member_ids or []),
    )


def _all_gate_results(
    *, issue_ids: list[str] | None = None
) -> list[UnifiedShadowValidationGateResult]:
    return [
        _gate_result(
            gate=gate,
            issue_ids=issue_ids if gate == Gate.SOURCE_INTEGRITY else [],
            blocking=gate != Gate.DOWNSTREAM_SHADOW_ELIGIBILITY,
        )
        for gate in UnifiedShadowValidationGate
    ]


def _group_validation(
    *,
    group_id: str = "group::1",
    group_status: UnifiedShadowGroupStatus = UnifiedShadowGroupStatus.VALID,
    comparison_to_v1: UnifiedShadowComparisonKind = UnifiedShadowComparisonKind.NOT_IN_BASELINE,
    structurally_valid: bool = True,
    downstream_shadow_eligible: bool = True,
    issue_ids: list[str] | None = None,
    member_ids: list[str] | None = None,
) -> UnifiedShadowGroupValidation:
    return UnifiedShadowGroupValidation(
        group_id=group_id,
        group_status=group_status,
        comparison_to_v1=comparison_to_v1,
        structurally_valid=structurally_valid,
        downstream_shadow_eligible=downstream_shadow_eligible,
        issue_ids=sorted(issue_ids or []),
        member_ids=sorted(member_ids or ["member::1"]),
    )


def _summary(**overrides: object) -> UnifiedShadowValidationSummary:
    base = dict(
        baseline_candidate_count=0,
        shadow_member_count=1,
        shadow_group_count=1,
        valid_shadow_group_count=1,
        invalid_shadow_group_count=0,
        downstream_eligible_group_count=1,
        exact_baseline_match_group_count=0,
        related_to_baseline_group_count=0,
        not_in_baseline_group_count=1,
        conflicting_with_baseline_group_count=0,
        not_evaluated_group_count=0,
        groups_with_complete_evidence_count=1,
        groups_with_complete_provenance_count=1,
        groups_with_complete_decision_trace_count=1,
        error_count=0,
        warning_count=0,
        blocking_issue_count=0,
        counts_by_gate_status={Status.PASS: 12},
        counts_by_issue_severity={},
        counts_by_issue_code={},
        counts_by_group_status={UnifiedShadowGroupStatus.VALID: 1},
        counts_by_baseline_comparison={UnifiedShadowComparisonKind.NOT_IN_BASELINE: 1},
    )
    base.update(overrides)
    return UnifiedShadowValidationSummary(**base)  # type: ignore[arg-type]


def _report(**overrides: object) -> UnifiedShadowValidationReport:
    base = dict(
        run_id="20260101T000000000000-aaaaaaaa",
        source_package_hash=HASH,
        unified_candidates_shadow_hash=HASH,
        candidate_v1_artifact_hash=HASH,
        assessment_artifact_hash=HASH,
        review_package_hash=HASH,
        promotion_plan_hash=HASH,
        disposition=Disposition.QUALIFIED_FOR_DOWNSTREAM_SHADOW,
        gate_results=_all_gate_results(),
        group_validations=[_group_validation()],
        issues=[],
        summary=_summary(),
    )
    base.update(overrides)
    return UnifiedShadowValidationReport(**base)  # type: ignore[arg-type]


# --- Catalogo / enums ---


def test_all_12_gates_are_distinct() -> None:
    assert len(set(UnifiedShadowValidationGate)) == 12


def test_message_code_must_match_catalog() -> None:
    with pytest.raises(ValidationError):
        UnifiedShadowValidationIssue(
            issue_id="issue::1",
            code=Code.GROUP_NOT_IN_BASELINE,
            severity=Severity.INFO,
            gate=Gate.BASELINE_DIFFERENTIAL_SAFETY,
            message_code="free text message",
        )


def test_message_code_registered_for_every_code() -> None:
    for code in UnifiedShadowValidationIssueCode:
        issue = UnifiedShadowValidationIssue(
            issue_id=f"issue::{code.value}",
            code=code,
            severity=Severity.INFO,
            gate=Gate.SOURCE_INTEGRITY,
            message_code=f"MSG_{code.value}",
        )
        assert issue.message_code == f"MSG_{code.value}"


def test_issue_lists_must_be_sorted_and_unique() -> None:
    with pytest.raises(ValidationError):
        UnifiedShadowValidationIssue(
            issue_id="issue::1",
            code=Code.GROUP_NOT_IN_BASELINE,
            severity=Severity.INFO,
            gate=Gate.BASELINE_DIFFERENTIAL_SAFETY,
            message_code=f"MSG_{Code.GROUP_NOT_IN_BASELINE.value}",
            shadow_group_ids=["b", "a"],
        )


def test_gate_result_lists_must_be_sorted_and_unique() -> None:
    with pytest.raises(ValidationError):
        UnifiedShadowValidationGateResult(
            gate=Gate.SOURCE_INTEGRITY,
            status=Status.PASS,
            required=True,
            blocking=True,
            checked_group_ids=["group::b", "group::a"],
        )


# --- UnifiedShadowGroupValidation: invariantes 13-16 ---


def test_group_validation_eligibility_implies_structurally_valid() -> None:
    with pytest.raises(ValidationError):
        _group_validation(structurally_valid=False, downstream_shadow_eligible=True)


def test_blocked_group_never_eligible_invariant_13() -> None:
    with pytest.raises(ValidationError):
        _group_validation(
            group_status=UnifiedShadowGroupStatus.BLOCKED, downstream_shadow_eligible=True
        )


def test_duplicate_baseline_group_never_eligible_invariant_14() -> None:
    with pytest.raises(ValidationError):
        _group_validation(
            group_status=UnifiedShadowGroupStatus.DUPLICATE_BASELINE_COVERAGE,
            downstream_shadow_eligible=True,
        )


def test_conflicts_with_baseline_never_eligible_invariant_15() -> None:
    with pytest.raises(ValidationError):
        _group_validation(
            comparison_to_v1=UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE,
            downstream_shadow_eligible=True,
        )


def test_exact_baseline_match_never_eligible_invariant_16() -> None:
    with pytest.raises(ValidationError):
        _group_validation(
            comparison_to_v1=UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH,
            downstream_shadow_eligible=True,
        )


def test_group_validation_requires_at_least_one_member() -> None:
    with pytest.raises(ValidationError):
        UnifiedShadowGroupValidation(
            group_id="group::1",
            group_status=UnifiedShadowGroupStatus.VALID,
            comparison_to_v1=UnifiedShadowComparisonKind.NOT_IN_BASELINE,
            structurally_valid=True,
            downstream_shadow_eligible=False,
            member_ids=[],
        )


# --- UnifiedShadowValidationSummary ---


def test_summary_group_counts_must_reconcile() -> None:
    with pytest.raises(ValidationError):
        _summary(shadow_group_count=2)


def test_summary_issue_severity_counts_must_reconcile() -> None:
    with pytest.raises(ValidationError):
        _summary(error_count=1)


def test_summary_gate_status_counts_must_cover_all_12_gates() -> None:
    with pytest.raises(ValidationError):
        _summary(counts_by_gate_status={Status.PASS: 5})


def test_summary_downstream_eligible_cannot_exceed_group_count() -> None:
    with pytest.raises(ValidationError):
        _summary(downstream_eligible_group_count=2)


# --- UnifiedShadowValidationReport: invariantes 1-6, 8-12, 21-22 ---


def test_report_issue_id_must_be_unique_invariant_1() -> None:
    issue = _issue(issue_id="issue::dup")
    with pytest.raises(ValidationError):
        _report(issues=[issue, issue])


def test_report_issues_must_be_ordered_by_id_invariant_22() -> None:
    with pytest.raises(ValidationError):
        _report(issues=[_issue(issue_id="issue::b"), _issue(issue_id="issue::a")])


def test_report_every_gate_must_appear_exactly_once_invariant_2() -> None:
    incomplete = _all_gate_results()[:-1]
    with pytest.raises(ValidationError):
        _report(gate_results=incomplete)


def test_report_gate_cannot_be_duplicated_invariant_2() -> None:
    duplicated = _all_gate_results() + [_gate_result(gate=Gate.SOURCE_INTEGRITY)]
    with pytest.raises(ValidationError):
        _report(gate_results=duplicated)


def test_report_group_validation_ids_must_be_unique_invariant_3() -> None:
    gv = _group_validation()
    with pytest.raises(ValidationError):
        _report(group_validations=[gv, gv])


def test_report_gate_result_cannot_reference_unknown_issue_invariant_4() -> None:
    bad_results = [
        _gate_result(gate=g, issue_ids=["issue::ghost"] if g == Gate.SOURCE_INTEGRITY else [])
        for g in UnifiedShadowValidationGate
    ]
    with pytest.raises(ValidationError):
        _report(gate_results=bad_results, issues=[])


def test_report_issue_cannot_reference_unknown_group_invariant_5() -> None:
    issue = _issue(shadow_group_ids=["group::ghost"])
    with pytest.raises(ValidationError):
        _report(issues=[issue], gate_results=_all_gate_results(issue_ids=[issue.issue_id]))


def test_report_issue_cannot_reference_unknown_member_invariant_6() -> None:
    issue = _issue(shadow_member_ids=["member::ghost"], shadow_group_ids=["group::1"])
    with pytest.raises(ValidationError):
        _report(issues=[issue], gate_results=_all_gate_results(issue_ids=[issue.issue_id]))


def test_report_blocked_requires_blocking_issue_invariant_8() -> None:
    with pytest.raises(ValidationError):
        _report(disposition=Disposition.BLOCKED, issues=[])


def test_report_blocked_accepts_with_blocking_issue() -> None:
    issue = _issue(code=Code.GROUP_CONFLICTS_WITH_BASELINE, severity=Severity.BLOCKING)
    report = _report(
        disposition=Disposition.BLOCKED,
        issues=[issue],
        gate_results=_all_gate_results(issue_ids=[issue.issue_id]),
        group_validations=[
            _group_validation(
                downstream_shadow_eligible=False,
                structurally_valid=False,
                comparison_to_v1=UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE,
            )
        ],
        summary=_summary(
            downstream_eligible_group_count=0,
            not_in_baseline_group_count=0,
            conflicting_with_baseline_group_count=1,
            counts_by_baseline_comparison={UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE: 1},
            counts_by_issue_severity={Severity.BLOCKING: 1},
            counts_by_issue_code={Code.GROUP_CONFLICTS_WITH_BASELINE: 1},
            blocking_issue_count=1,
            groups_with_complete_evidence_count=0,
            groups_with_complete_provenance_count=0,
            groups_with_complete_decision_trace_count=0,
            valid_shadow_group_count=0,
            invalid_shadow_group_count=1,
            counts_by_group_status={UnifiedShadowGroupStatus.VALID: 1},
        ),
    )
    assert report.disposition == Disposition.BLOCKED


def test_report_qualified_requires_all_required_gates_pass_invariant_9() -> None:
    failing_gates = [
        _gate_result(gate=g, status=Status.FAIL if g == Gate.BASELINE_COMPLETENESS else Status.PASS)
        for g in UnifiedShadowValidationGate
    ]
    with pytest.raises(ValidationError):
        _report(disposition=Disposition.QUALIFIED_FOR_DOWNSTREAM_SHADOW, gate_results=failing_gates)


def test_report_qualified_requires_zero_blocking_invariant_9() -> None:
    issue = _issue(code=Code.GROUP_CONFLICTS_WITH_BASELINE, severity=Severity.BLOCKING)
    with pytest.raises(ValidationError):
        _report(
            disposition=Disposition.QUALIFIED_FOR_DOWNSTREAM_SHADOW,
            issues=[issue],
            gate_results=_all_gate_results(issue_ids=[issue.issue_id]),
        )


def test_report_qualified_requires_at_least_one_eligible_group_invariant_9() -> None:
    with pytest.raises(ValidationError):
        _report(
            disposition=Disposition.QUALIFIED_FOR_DOWNSTREAM_SHADOW,
            group_validations=[_group_validation(downstream_shadow_eligible=False)],
            summary=_summary(downstream_eligible_group_count=0),
        )


def test_report_qualified_with_warnings_requires_warning_invariant_10() -> None:
    with pytest.raises(ValidationError):
        _report(disposition=Disposition.QUALIFIED_WITH_WARNINGS, issues=[])


def test_report_qualified_with_warnings_accepts_with_warning() -> None:
    issue = _issue(code=Code.GROUP_RELATED_TO_BASELINE, severity=Severity.WARNING)
    report = _report(
        disposition=Disposition.QUALIFIED_WITH_WARNINGS,
        issues=[issue],
        gate_results=_all_gate_results(issue_ids=[issue.issue_id]),
        summary=_summary(
            counts_by_issue_severity={Severity.WARNING: 1},
            counts_by_issue_code={Code.GROUP_RELATED_TO_BASELINE: 1},
            warning_count=1,
        ),
    )
    assert report.disposition == Disposition.QUALIFIED_WITH_WARNINGS


def test_report_not_evaluated_requires_missing_source_invariant_12() -> None:
    with pytest.raises(ValidationError):
        _report(
            disposition=Disposition.NOT_EVALUATED,
            group_validations=[],
            summary=_summary(
                shadow_group_count=0,
                valid_shadow_group_count=0,
                downstream_eligible_group_count=0,
                not_in_baseline_group_count=0,
                groups_with_complete_evidence_count=0,
                groups_with_complete_provenance_count=0,
                groups_with_complete_decision_trace_count=0,
                counts_by_group_status={},
                counts_by_baseline_comparison={},
            ),
        )


def test_report_not_evaluated_accepts_with_missing_source_issue() -> None:
    issue = _issue(
        code=Code.SOURCE_ARTIFACT_MISSING,
        severity=Severity.BLOCKING,
        gate=Gate.SOURCE_INTEGRITY,
        shadow_group_ids=[],
    )
    gate_results = [
        _gate_result(
            gate=g,
            status=Status.FAIL if g == Gate.SOURCE_INTEGRITY else Status.NOT_EVALUATED,
            issue_ids=[issue.issue_id] if g == Gate.SOURCE_INTEGRITY else [],
        )
        for g in UnifiedShadowValidationGate
    ]
    report = _report(
        disposition=Disposition.NOT_EVALUATED,
        issues=[issue],
        gate_results=gate_results,
        group_validations=[],
        summary=_summary(
            shadow_group_count=0,
            valid_shadow_group_count=0,
            invalid_shadow_group_count=0,
            downstream_eligible_group_count=0,
            not_in_baseline_group_count=0,
            counts_by_issue_severity={Severity.BLOCKING: 1},
            counts_by_issue_code={Code.SOURCE_ARTIFACT_MISSING: 1},
            blocking_issue_count=1,
            groups_with_complete_evidence_count=0,
            groups_with_complete_provenance_count=0,
            groups_with_complete_decision_trace_count=0,
            counts_by_group_status={},
            counts_by_baseline_comparison={},
            counts_by_gate_status={Status.FAIL: 1, Status.NOT_EVALUATED: 11},
        ),
    )
    assert report.disposition == Disposition.NOT_EVALUATED


def test_report_summary_shadow_group_count_reconciles_invariant_21() -> None:
    with pytest.raises(ValidationError):
        _report(group_validations=[], summary=_summary(shadow_group_count=1))


def test_report_diagnostics_must_be_sorted_and_unique() -> None:
    with pytest.raises(ValidationError):
        _report(diagnostics=["b", "a"])


def test_report_roundtrip_via_stable_json() -> None:
    report = _report()
    dumped = report.to_stable_json()
    reloaded = UnifiedShadowValidationReport.model_validate_json(dumped)
    assert reloaded.to_stable_json() == dumped
