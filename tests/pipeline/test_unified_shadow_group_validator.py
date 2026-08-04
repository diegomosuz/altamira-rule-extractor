"""Tests del validador PURO de members y groups del artefacto
unificado (Fase 12 Parte 6, `feat/unified-shadow-differential-validation`)."""

from __future__ import annotations

from altamira_extractor.contracts.candidate_promotion_assessment import UnifiedRuleFamily
from altamira_extractor.contracts.unified_candidates_shadow import UnifiedShadowSupport
from altamira_extractor.contracts.unified_shadow_validation import UnifiedShadowValidationIssueCode
from altamira_extractor.pipeline.unified_shadow_group_validator import validate_group

from ._unified_shadow_validation_fixtures import golden_path, second_member

Code = UnifiedShadowValidationIssueCode


def _group(gp: object):
    return gp.unified_shadow.shadow_groups[0]  # type: ignore[attr-defined]


def _member(gp: object):
    return gp.unified_shadow.shadow_members[0]  # type: ignore[attr-defined]


def _members_by_id(*members: object) -> dict:
    return {m.member_id: m for m in members}  # type: ignore[attr-defined]


def test_member_source_not_found_when_v2_absent() -> None:
    gp = golden_path()
    result = validate_group(
        _group(gp),
        members_by_id=_members_by_id(_member(gp)),
        assessment=gp.assessment,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
        v1_candidates=gp.v1,  # type: ignore[attr-defined]
        v2_candidates=None,
        interprocedural_candidates=None,
    )
    assert result.structurally_valid is False
    assert any(f.code == Code.SHADOW_MEMBER_SOURCE_NOT_FOUND for f in result.findings)
    assert result.member_results[0].source_resolution_complete is False


def test_member_without_approval_when_plan_item_missing() -> None:
    gp = golden_path()
    plan_without_item = gp.plan.model_copy(update={"plan_items": []})  # type: ignore[attr-defined]
    result = validate_group(
        _group(gp),
        members_by_id=_members_by_id(_member(gp)),
        assessment=gp.assessment,  # type: ignore[attr-defined]
        plan=plan_without_item,
        v1_candidates=gp.v1,  # type: ignore[attr-defined]
        v2_candidates=None,
        interprocedural_candidates=None,
    )
    assert any(f.code == Code.SHADOW_MEMBER_WITHOUT_APPROVAL for f in result.findings)


def test_member_without_evidence_flagged_at_group_level_too() -> None:
    gp = golden_path()
    member = _member(gp).model_copy(update={"evidence_ids": []})
    group = _group(gp).model_copy(update={"evidence_ids": []})
    result = validate_group(
        group,
        members_by_id=_members_by_id(member),
        assessment=gp.assessment,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
        v1_candidates=gp.v1,  # type: ignore[attr-defined]
        v2_candidates=None,
        interprocedural_candidates=None,
    )
    assert any(f.code == Code.SHADOW_MEMBER_WITHOUT_EVIDENCE for f in result.findings)


def test_group_member_not_found_when_member_id_dangling() -> None:
    gp = golden_path()
    group = _group(gp).model_copy(update={"member_ids": [_member(gp).member_id, "member::ghost"]})
    result = validate_group(
        group,
        members_by_id=_members_by_id(_member(gp)),
        assessment=gp.assessment,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
        v1_candidates=gp.v1,  # type: ignore[attr-defined]
        v2_candidates=None,
        interprocedural_candidates=None,
    )
    assert any(f.code == Code.GROUP_MEMBER_NOT_FOUND for f in result.findings)


def test_group_blocked_support_flagged() -> None:
    gp = golden_path()
    group = _group(gp).model_copy(update={"support": UnifiedShadowSupport.BLOCKED})
    result = validate_group(
        group,
        members_by_id=_members_by_id(_member(gp)),
        assessment=gp.assessment,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
        v1_candidates=gp.v1,  # type: ignore[attr-defined]
        v2_candidates=None,
        interprocedural_candidates=None,
    )
    assert any(f.code == Code.GROUP_BLOCKED for f in result.findings)


def test_group_unknown_family_flagged() -> None:
    gp = golden_path()
    group = _group(gp).model_copy(update={"rule_family": UnifiedRuleFamily.UNKNOWN})
    result = validate_group(
        group,
        members_by_id=_members_by_id(_member(gp)),
        assessment=gp.assessment,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
        v1_candidates=gp.v1,  # type: ignore[attr-defined]
        v2_candidates=None,
        interprocedural_candidates=None,
    )
    assert any(f.code == Code.GROUP_UNKNOWN_FAMILY for f in result.findings)


def test_group_with_two_members_multiple_families_flagged() -> None:
    gp = golden_path()
    other = second_member(rule_family=UnifiedRuleFamily.BY_REFERENCE_OUTPUT)
    group = _group(gp).model_copy(
        update={
            "member_ids": [_member(gp).member_id, other.member_id],
            "evidence_ids": sorted(set(_member(gp).evidence_ids) | set(other.evidence_ids)),
            "provenance_references": sorted(
                set(_member(gp).provenance_references) | set(other.provenance_references)
            ),
        }
    )
    result = validate_group(
        group,
        members_by_id=_members_by_id(_member(gp), other),
        assessment=gp.assessment,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
        v1_candidates=gp.v1,  # type: ignore[attr-defined]
        v2_candidates=None,
        interprocedural_candidates=None,
    )
    assert any(f.code == Code.GROUP_MULTIPLE_FAMILIES for f in result.findings)


def test_group_with_two_members_multiple_targets_flagged() -> None:
    gp = golden_path()
    other = second_member(target="WS-OTHER-TARGET")
    group = _group(gp).model_copy(
        update={
            "member_ids": [_member(gp).member_id, other.member_id],
            "evidence_ids": sorted(set(_member(gp).evidence_ids) | set(other.evidence_ids)),
            "provenance_references": sorted(
                set(_member(gp).provenance_references) | set(other.provenance_references)
            ),
        }
    )
    result = validate_group(
        group,
        members_by_id=_members_by_id(_member(gp), other),
        assessment=gp.assessment,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
        v1_candidates=gp.v1,  # type: ignore[attr-defined]
        v2_candidates=None,
        interprocedural_candidates=None,
    )
    assert any(f.code == Code.GROUP_MULTIPLE_TARGETS for f in result.findings)


def test_group_with_two_members_multiple_outputs_flagged() -> None:
    gp = golden_path()
    other = second_member(output_literal="R999")
    group = _group(gp).model_copy(
        update={
            "member_ids": [_member(gp).member_id, other.member_id],
            "evidence_ids": sorted(set(_member(gp).evidence_ids) | set(other.evidence_ids)),
            "provenance_references": sorted(
                set(_member(gp).provenance_references) | set(other.provenance_references)
            ),
        }
    )
    result = validate_group(
        group,
        members_by_id=_members_by_id(_member(gp), other),
        assessment=gp.assessment,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
        v1_candidates=gp.v1,  # type: ignore[attr-defined]
        v2_candidates=None,
        interprocedural_candidates=None,
    )
    assert any(f.code == Code.GROUP_MULTIPLE_OUTPUTS for f in result.findings)


def test_group_evidence_union_mismatch_flagged() -> None:
    gp = golden_path()
    group = _group(gp).model_copy(update={"evidence_ids": ["evidence::not-from-any-member"]})
    result = validate_group(
        group,
        members_by_id=_members_by_id(_member(gp)),
        assessment=gp.assessment,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
        v1_candidates=gp.v1,  # type: ignore[attr-defined]
        v2_candidates=None,
        interprocedural_candidates=None,
    )
    assert any(
        f.code == Code.GROUP_INCONSISTENT_SCOPE and "evidence_union_mismatch" in f.diagnostics
        for f in result.findings
    )


def test_never_mutates_group_or_members() -> None:
    gp = golden_path()
    group_before = _group(gp).model_copy(deep=True)
    member_before = _member(gp).model_copy(deep=True)
    validate_group(
        _group(gp),
        members_by_id=_members_by_id(_member(gp)),
        assessment=gp.assessment,  # type: ignore[attr-defined]
        plan=gp.plan,  # type: ignore[attr-defined]
        v1_candidates=gp.v1,  # type: ignore[attr-defined]
        v2_candidates=None,
        interprocedural_candidates=None,
    )
    assert _group(gp) == group_before
    assert _member(gp) == member_before
