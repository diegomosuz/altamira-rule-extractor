"""Tests de la politica declarativa de gates de la validacion
diferencial del artefacto unificado en shadow mode (Fase 12 Parte 9,
`feat/unified-shadow-differential-validation`)."""

from __future__ import annotations

from altamira_extractor.contracts.unified_shadow_validation import (
    UnifiedShadowGateStatus,
    UnifiedShadowGroupValidation,
    UnifiedShadowValidationDisposition,
    UnifiedShadowValidationGate,
    UnifiedShadowValidationIssueCode,
    UnifiedShadowValidationIssueSeverity,
)
from altamira_extractor.pipeline.unified_shadow_validation_policy import (
    GATE_BLOCKING,
    GLOBAL_GATES,
    SUMMARY_GATE,
    RawFinding,
    derive_disposition,
    is_group_downstream_eligible,
)

Gate = UnifiedShadowValidationGate
Code = UnifiedShadowValidationIssueCode
Severity = UnifiedShadowValidationIssueSeverity
Status = UnifiedShadowGateStatus
Disposition = UnifiedShadowValidationDisposition


def _group_validation(*, eligible: bool = False) -> UnifiedShadowGroupValidation:
    from altamira_extractor.contracts.unified_candidates_shadow import (
        UnifiedShadowComparisonKind,
        UnifiedShadowGroupStatus,
    )

    return UnifiedShadowGroupValidation(
        group_id="group::1",
        group_status=UnifiedShadowGroupStatus.VALID,
        comparison_to_v1=UnifiedShadowComparisonKind.NOT_IN_BASELINE,
        structurally_valid=True,
        downstream_shadow_eligible=eligible,
        member_ids=["member::1"],
    )


# --- RawFinding.resolved_severity() ---


def test_raw_finding_uses_default_severity_when_none_given() -> None:
    finding = RawFinding(code=Code.GROUP_NOT_IN_BASELINE, gate=Gate.BASELINE_DIFFERENTIAL_SAFETY)
    assert finding.resolved_severity() == Severity.INFO


def test_raw_finding_explicit_severity_overrides_default() -> None:
    finding = RawFinding(
        code=Code.GROUP_NOT_IN_BASELINE,
        gate=Gate.BASELINE_DIFFERENTIAL_SAFETY,
        severity=Severity.ERROR,
    )
    assert finding.resolved_severity() == Severity.ERROR


def test_every_issue_code_has_a_default_severity() -> None:
    for code in UnifiedShadowValidationIssueCode:
        finding = RawFinding(code=code, gate=Gate.SOURCE_INTEGRITY)
        assert isinstance(finding.resolved_severity(), Severity)


def test_blocking_codes_are_the_expected_set() -> None:
    blocking_codes = {
        code
        for code in UnifiedShadowValidationIssueCode
        if RawFinding(code=code, gate=Gate.SOURCE_INTEGRITY).resolved_severity()
        == Severity.BLOCKING
    }
    assert Code.GROUP_DUPLICATES_BASELINE in blocking_codes
    assert Code.GROUP_CONFLICTS_WITH_BASELINE in blocking_codes
    assert Code.GROUP_BASELINE_NOT_EVALUATED in blocking_codes
    assert Code.SOURCE_ARTIFACT_MISSING in blocking_codes
    assert Code.UNIFIED_ARTIFACT_HASH_MISMATCH in blocking_codes
    # Defectos LOCALES a un grupo nunca fuerzan BLOCKED global.
    assert Code.GROUP_MULTIPLE_FAMILIES not in blocking_codes
    assert Code.GROUP_UNKNOWN_FAMILY not in blocking_codes
    assert Code.GROUP_RELATED_TO_BASELINE not in blocking_codes


# --- Gates ---


def test_global_gates_has_11_entries() -> None:
    assert len(GLOBAL_GATES) == 11
    assert SUMMARY_GATE not in GLOBAL_GATES


def test_all_12_gates_covered_by_global_plus_summary() -> None:
    assert set(GLOBAL_GATES) | {SUMMARY_GATE} == set(UnifiedShadowValidationGate)


def test_summary_gate_is_never_blocking() -> None:
    assert GATE_BLOCKING[SUMMARY_GATE] is False


def test_all_global_gates_are_blocking() -> None:
    assert all(GATE_BLOCKING[gate] for gate in GLOBAL_GATES)


# --- is_group_downstream_eligible ---


def _eligible_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        group_status="VALID",
        comparison_to_v1="NOT_IN_BASELINE",
        rule_family_is_unknown=False,
        support_is_blocked=False,
        member_source_resolution_complete=True,
        evidence_complete=True,
        provenance_complete=True,
        decision_trace_complete=True,
        has_error_or_blocking_issue=False,
    )
    base.update(overrides)
    return base


def test_eligible_when_all_conditions_hold() -> None:
    assert is_group_downstream_eligible(**_eligible_kwargs()) is True


def test_not_eligible_when_group_status_not_valid() -> None:
    assert is_group_downstream_eligible(**_eligible_kwargs(group_status="BLOCKED")) is False


def test_not_eligible_when_comparison_not_not_in_baseline() -> None:
    assert (
        is_group_downstream_eligible(**_eligible_kwargs(comparison_to_v1="RELATED_TO_BASELINE"))
        is False
    )


def test_not_eligible_when_family_unknown() -> None:
    assert is_group_downstream_eligible(**_eligible_kwargs(rule_family_is_unknown=True)) is False


def test_not_eligible_when_support_blocked() -> None:
    assert is_group_downstream_eligible(**_eligible_kwargs(support_is_blocked=True)) is False


def test_not_eligible_when_source_resolution_incomplete() -> None:
    assert (
        is_group_downstream_eligible(**_eligible_kwargs(member_source_resolution_complete=False))
        is False
    )


def test_not_eligible_when_evidence_incomplete() -> None:
    assert is_group_downstream_eligible(**_eligible_kwargs(evidence_complete=False)) is False


def test_not_eligible_when_provenance_incomplete() -> None:
    assert is_group_downstream_eligible(**_eligible_kwargs(provenance_complete=False)) is False


def test_not_eligible_when_decision_trace_incomplete() -> None:
    assert is_group_downstream_eligible(**_eligible_kwargs(decision_trace_complete=False)) is False


def test_not_eligible_when_has_error_or_blocking_issue() -> None:
    assert (
        is_group_downstream_eligible(**_eligible_kwargs(has_error_or_blocking_issue=True)) is False
    )


# --- derive_disposition ---


def _all_pass_statuses() -> dict[Gate, Status]:
    return dict.fromkeys((*GLOBAL_GATES, SUMMARY_GATE), Status.PASS)


def test_disposition_not_evaluated_takes_priority() -> None:
    disposition = derive_disposition(
        required_source_missing=True,
        gate_statuses={},
        has_blocking_issue=True,
        has_warning_issue=True,
        group_validations=[],
    )
    assert disposition == Disposition.NOT_EVALUATED


def test_disposition_blocked_when_blocking_issue_present() -> None:
    disposition = derive_disposition(
        required_source_missing=False,
        gate_statuses=_all_pass_statuses(),
        has_blocking_issue=True,
        has_warning_issue=False,
        group_validations=[_group_validation(eligible=True)],
    )
    assert disposition == Disposition.BLOCKED


def test_disposition_qualified_for_downstream_shadow() -> None:
    disposition = derive_disposition(
        required_source_missing=False,
        gate_statuses=_all_pass_statuses(),
        has_blocking_issue=False,
        has_warning_issue=False,
        group_validations=[_group_validation(eligible=True)],
    )
    assert disposition == Disposition.QUALIFIED_FOR_DOWNSTREAM_SHADOW


def test_disposition_qualified_with_warnings() -> None:
    disposition = derive_disposition(
        required_source_missing=False,
        gate_statuses=_all_pass_statuses(),
        has_blocking_issue=False,
        has_warning_issue=True,
        group_validations=[_group_validation(eligible=True)],
    )
    assert disposition == Disposition.QUALIFIED_WITH_WARNINGS


def test_disposition_review_required_when_no_group_eligible() -> None:
    disposition = derive_disposition(
        required_source_missing=False,
        gate_statuses=_all_pass_statuses(),
        has_blocking_issue=False,
        has_warning_issue=False,
        group_validations=[_group_validation(eligible=False)],
    )
    assert disposition == Disposition.REVIEW_REQUIRED


def test_disposition_review_required_when_a_required_gate_fails_without_blocking() -> None:
    statuses = _all_pass_statuses()
    statuses[Gate.EVIDENCE_COMPLETENESS] = Status.FAIL
    disposition = derive_disposition(
        required_source_missing=False,
        gate_statuses=statuses,
        has_blocking_issue=False,
        has_warning_issue=False,
        group_validations=[_group_validation(eligible=True)],
    )
    assert disposition == Disposition.REVIEW_REQUIRED
