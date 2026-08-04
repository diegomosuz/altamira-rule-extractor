"""Tests del validador PURO de evidence, provenance y trazabilidad de
decision (Fase 12 Parte 8, `feat/unified-shadow-differential-validation`)."""

from __future__ import annotations

from altamira_extractor.contracts.unified_shadow_validation import UnifiedShadowValidationIssueCode
from altamira_extractor.pipeline.unified_shadow_evidence_validator import (
    validate_group_traceability,
)

from ._unified_shadow_validation_fixtures import golden_path

Code = UnifiedShadowValidationIssueCode


def _member(gp: object):
    return gp.unified_shadow.shadow_members[0]  # type: ignore[attr-defined]


def _group(gp: object):
    return gp.unified_shadow.shadow_groups[0]  # type: ignore[attr-defined]


def test_golden_path_group_is_fully_traceable() -> None:
    gp = golden_path()
    result = validate_group_traceability(
        _group(gp),
        members=[_member(gp)],
        assessment=gp.assessment,  # type: ignore[attr-defined]
        review_package=gp.review_package,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
    )
    assert result.evidence_complete is True
    assert result.provenance_complete is True
    assert result.decision_trace_complete is True
    assert result.findings == ()


def test_member_without_evidence_flags_incomplete() -> None:
    gp = golden_path()
    member = _member(gp).model_copy(update={"evidence_ids": []})
    result = validate_group_traceability(
        _group(gp),
        members=[member],
        assessment=gp.assessment,  # type: ignore[attr-defined]
        review_package=gp.review_package,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
    )
    assert result.evidence_complete is False
    assert any(f.code == Code.SHADOW_MEMBER_WITHOUT_EVIDENCE for f in result.findings)


def test_member_without_provenance_flags_incomplete() -> None:
    gp = golden_path()
    member = _member(gp).model_copy(update={"provenance_references": []})
    result = validate_group_traceability(
        _group(gp),
        members=[member],
        assessment=gp.assessment,  # type: ignore[attr-defined]
        review_package=gp.review_package,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
    )
    assert result.provenance_complete is False
    assert any(f.code == Code.SHADOW_MEMBER_WITHOUT_PROVENANCE for f in result.findings)


def test_member_with_unknown_assessment_id_breaks_decision_trace() -> None:
    gp = golden_path()
    member = _member(gp).model_copy(update={"assessment_id": "assessment::ghost"})
    result = validate_group_traceability(
        _group(gp),
        members=[member],
        assessment=gp.assessment,  # type: ignore[attr-defined]
        review_package=gp.review_package,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
    )
    assert result.decision_trace_complete is False
    assert any(f.code == Code.PLAN_BINDING_MISMATCH for f in result.findings)


def test_member_with_review_item_assessment_mismatch_breaks_decision_trace() -> None:
    gp = golden_path()
    mismatched_review_item = gp.review_package.review_items[0].model_copy(  # type: ignore[attr-defined]
        update={"assessment_id": "assessment::different"}
    )
    review_package = gp.review_package.model_copy(  # type: ignore[attr-defined]
        update={"review_items": [mismatched_review_item]}
    )
    result = validate_group_traceability(
        _group(gp),
        members=[_member(gp)],
        assessment=gp.assessment,  # type: ignore[attr-defined]
        review_package=review_package,
        plan=gp.plan,  # type: ignore[attr-defined]
    )
    assert result.decision_trace_complete is False


def test_member_with_unknown_plan_item_breaks_decision_trace() -> None:
    gp = golden_path()
    member = _member(gp).model_copy(update={"plan_item_id": "plan::ghost"})
    result = validate_group_traceability(
        _group(gp),
        members=[member],
        assessment=gp.assessment,  # type: ignore[attr-defined]
        review_package=gp.review_package,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
    )
    assert result.decision_trace_complete is False


def test_group_traceability_requires_at_least_one_member() -> None:
    gp = golden_path()
    result = validate_group_traceability(
        _group(gp),
        members=[],
        assessment=gp.assessment,  # type: ignore[attr-defined]
        review_package=gp.review_package,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
    )
    assert result.evidence_complete is False
    assert result.provenance_complete is False
    assert result.decision_trace_complete is False


def test_never_mutates_member_or_group() -> None:
    gp = golden_path()
    member_before = _member(gp).model_copy(deep=True)
    group_before = _group(gp).model_copy(deep=True)
    validate_group_traceability(
        _group(gp),
        members=[_member(gp)],
        assessment=gp.assessment,  # type: ignore[attr-defined]
        review_package=gp.review_package,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
    )
    assert _member(gp) == member_before
    assert _group(gp) == group_before
