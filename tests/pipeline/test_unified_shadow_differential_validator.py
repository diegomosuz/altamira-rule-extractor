"""Tests del validador PURO diferencial contra el baseline V1 (Fase 12
Parte 7, `feat/unified-shadow-differential-validation`)."""

from __future__ import annotations

from altamira_extractor.contracts.unified_candidates_shadow import (
    UnifiedShadowComparisonKind,
    UnifiedShadowGroupStatus,
)
from altamira_extractor.contracts.unified_shadow_validation import (
    UnifiedShadowValidationIssueCode,
    UnifiedShadowValidationIssueSeverity,
)
from altamira_extractor.pipeline.unified_shadow_differential_validator import (
    validate_baseline_differential,
)

from ._unified_shadow_validation_fixtures import golden_path

Code = UnifiedShadowValidationIssueCode
Severity = UnifiedShadowValidationIssueSeverity
Comparison = UnifiedShadowComparisonKind
Status = UnifiedShadowGroupStatus


def _group(gp: object):
    return gp.unified_shadow.shadow_groups[0]  # type: ignore[attr-defined]


def test_not_in_baseline_is_informational_and_baseline_safe() -> None:
    gp = golden_path()
    group = _group(gp)
    assert group.comparison_to_v1 == Comparison.NOT_IN_BASELINE
    result = validate_baseline_differential(group)
    assert result.baseline_safe is True
    codes = {f.code: f.resolved_severity() for f in result.findings}
    assert codes == {Code.GROUP_NOT_IN_BASELINE: Severity.INFO}


def test_exact_baseline_match_produces_blocking_duplicate_finding() -> None:
    gp = golden_path()
    group = _group(gp).model_copy(
        update={
            "comparison_to_v1": Comparison.EXACT_BASELINE_MATCH,
            "status": Status.DUPLICATE_BASELINE_COVERAGE,
            "exact_baseline_reference_ids": ["baseline::1"],
        }
    )
    result = validate_baseline_differential(group)
    assert result.baseline_safe is False
    primary = next(f for f in result.findings if f.code == Code.GROUP_DUPLICATES_BASELINE)
    assert primary.resolved_severity() == Severity.BLOCKING
    assert primary.baseline_reference_ids == ("baseline::1",)


def test_conflicts_with_baseline_produces_blocking_finding() -> None:
    gp = golden_path()
    group = _group(gp).model_copy(
        update={
            "comparison_to_v1": Comparison.CONFLICTS_WITH_BASELINE,
            "status": Status.BLOCKED,
            "conflicting_baseline_reference_ids": ["baseline::conflict::1"],
        }
    )
    result = validate_baseline_differential(group)
    assert result.baseline_safe is False
    primary = next(f for f in result.findings if f.code == Code.GROUP_CONFLICTS_WITH_BASELINE)
    assert primary.resolved_severity() == Severity.BLOCKING


def test_related_to_baseline_produces_warning_not_blocking() -> None:
    gp = golden_path()
    group = _group(gp).model_copy(
        update={
            "comparison_to_v1": Comparison.RELATED_TO_BASELINE,
            "related_baseline_reference_ids": ["baseline::related::1"],
        }
    )
    result = validate_baseline_differential(group)
    assert result.baseline_safe is False
    primary = next(f for f in result.findings if f.code == Code.GROUP_RELATED_TO_BASELINE)
    assert primary.resolved_severity() == Severity.WARNING


def test_not_evaluated_produces_blocking_never_confused_with_not_in_baseline() -> None:
    gp = golden_path()
    group = _group(gp).model_copy(update={"comparison_to_v1": Comparison.NOT_EVALUATED})
    result = validate_baseline_differential(group)
    codes = {f.code for f in result.findings}
    assert Code.GROUP_BASELINE_NOT_EVALUATED in codes
    assert Code.GROUP_NOT_IN_BASELINE not in codes
    assert result.baseline_safe is False


def test_exact_match_without_references_flags_inconsistent_scope() -> None:
    gp = golden_path()
    group = _group(gp).model_copy(
        update={
            "comparison_to_v1": Comparison.EXACT_BASELINE_MATCH,
            "status": Status.DUPLICATE_BASELINE_COVERAGE,
            "exact_baseline_reference_ids": [],
        }
    )
    result = validate_baseline_differential(group)
    assert any(f.code == Code.GROUP_INCONSISTENT_SCOPE for f in result.findings)


def test_not_in_baseline_with_stray_references_flags_inconsistent_scope() -> None:
    gp = golden_path()
    group = _group(gp).model_copy(
        update={
            "comparison_to_v1": Comparison.NOT_IN_BASELINE,
            "related_baseline_reference_ids": ["baseline::stray::1"],
        }
    )
    result = validate_baseline_differential(group)
    assert any(f.code == Code.GROUP_INCONSISTENT_SCOPE for f in result.findings)


def test_never_mutates_group() -> None:
    gp = golden_path()
    group = _group(gp)
    before = group.model_copy(deep=True)
    validate_baseline_differential(group)
    assert group == before
