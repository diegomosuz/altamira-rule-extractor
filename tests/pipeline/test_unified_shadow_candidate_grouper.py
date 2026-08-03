"""Tests del agrupador puro de propuestas shadow EXACTAMENTE
equivalentes (Fase 11 Parte 6,
`pipeline/unified_shadow_candidate_grouper.py`)."""

from __future__ import annotations

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.candidate_promotion_review import (
    DecisionReasonCode,
    ReviewDecision,
)
from altamira_extractor.contracts.unified_candidates_shadow import UnifiedShadowSourceMember
from altamira_extractor.pipeline.unified_shadow_candidate_grouper import (
    group_id_for,
    group_shadow_members,
)

HASH = "a" * 64


def _member(
    *,
    member_id: str,
    assessment_reference_id: str,
    rule_family: UnifiedRuleFamily = UnifiedRuleFamily.RETURN_CODE,
    program: str = "CALLER",
    target: str | None = "WS-X",
    output_literal: str | None = "R001",
) -> UnifiedShadowSourceMember:
    return UnifiedShadowSourceMember(
        member_id=member_id,
        source=CandidateSource.V2,
        source_candidate_id=f"v2::{member_id}",
        source_artifact_hash=HASH,
        source_candidate_hash=HASH,
        assessment_id=f"assessment::{assessment_reference_id}",
        assessment_reference_id=assessment_reference_id,
        review_item_id=f"review::{assessment_reference_id}",
        plan_item_id=f"plan::{assessment_reference_id}",
        review_decision_id=f"decision::{assessment_reference_id}",
        decision=ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
        reason_code=DecisionReasonCode.EVIDENCE_CONFIRMED,
        reviewer_reference="analyst@example.com",
        rule_family=rule_family,
        original_support="DETERMINISTIC",
        program=program,
        target=target,
        output_literal=output_literal,
    )


def test_no_exact_match_pairs_each_member_is_own_component() -> None:
    m1 = _member(member_id="member::1", assessment_reference_id="ref::1")
    m2 = _member(member_id="member::2", assessment_reference_id="ref::2")
    components = group_shadow_members([m1, m2], exact_match_reference_pairs=frozenset())
    assert len(components) == 2
    assert all(len(c.member_ids) == 1 for c in components)


def test_one_exact_match_pair_merges_into_single_component() -> None:
    m1 = _member(member_id="member::1", assessment_reference_id="ref::1")
    m2 = _member(member_id="member::2", assessment_reference_id="ref::2")
    pairs = frozenset([frozenset({"ref::1", "ref::2"})])
    components = group_shadow_members([m1, m2], exact_match_reference_pairs=pairs)
    assert len(components) == 1
    assert components[0].member_ids == ["member::1", "member::2"]
    assert components[0].is_consistent


def test_transitive_exact_match_merges_three_members() -> None:
    m1 = _member(member_id="member::1", assessment_reference_id="ref::1")
    m2 = _member(member_id="member::2", assessment_reference_id="ref::2")
    m3 = _member(member_id="member::3", assessment_reference_id="ref::3")
    pairs = frozenset(
        [
            frozenset({"ref::1", "ref::2"}),
            frozenset({"ref::2", "ref::3"}),
        ]
    )
    components = group_shadow_members([m1, m2, m3], exact_match_reference_pairs=pairs)
    assert len(components) == 1
    assert components[0].member_ids == ["member::1", "member::2", "member::3"]
    assert components[0].is_consistent


def test_exact_match_pair_not_touching_any_member_ignored() -> None:
    m1 = _member(member_id="member::1", assessment_reference_id="ref::1")
    pairs = frozenset([frozenset({"ref::not-a-member", "ref::also-not"})])
    components = group_shadow_members([m1], exact_match_reference_pairs=pairs)
    assert len(components) == 1
    assert components[0].member_ids == ["member::1"]


def test_inconsistent_rule_family_flagged() -> None:
    m1 = _member(
        member_id="member::1",
        assessment_reference_id="ref::1",
        rule_family=UnifiedRuleFamily.RETURN_CODE,
    )
    m2 = _member(
        member_id="member::2",
        assessment_reference_id="ref::2",
        rule_family=UnifiedRuleFamily.STATE_TRANSITION,
    )
    pairs = frozenset([frozenset({"ref::1", "ref::2"})])
    [component] = group_shadow_members([m1, m2], exact_match_reference_pairs=pairs)
    assert not component.is_consistent
    assert any("INCONSISTENT_RULE_FAMILY" in reason for reason in component.inconsistency_reasons)


def test_inconsistent_target_flagged() -> None:
    m1 = _member(member_id="member::1", assessment_reference_id="ref::1", target="WS-X")
    m2 = _member(member_id="member::2", assessment_reference_id="ref::2", target="WS-Y")
    pairs = frozenset([frozenset({"ref::1", "ref::2"})])
    [component] = group_shadow_members([m1, m2], exact_match_reference_pairs=pairs)
    assert not component.is_consistent
    assert any("INCONSISTENT_TARGET" in reason for reason in component.inconsistency_reasons)


def test_inconsistent_output_literal_flagged() -> None:
    m1 = _member(member_id="member::1", assessment_reference_id="ref::1", output_literal="R001")
    m2 = _member(member_id="member::2", assessment_reference_id="ref::2", output_literal="R002")
    pairs = frozenset([frozenset({"ref::1", "ref::2"})])
    [component] = group_shadow_members([m1, m2], exact_match_reference_pairs=pairs)
    assert not component.is_consistent
    assert any(
        "INCONSISTENT_OUTPUT_LITERAL" in reason for reason in component.inconsistency_reasons
    )


def test_inconsistent_program_flagged() -> None:
    m1 = _member(member_id="member::1", assessment_reference_id="ref::1", program="CALLER")
    m2 = _member(member_id="member::2", assessment_reference_id="ref::2", program="OTHER")
    pairs = frozenset([frozenset({"ref::1", "ref::2"})])
    [component] = group_shadow_members([m1, m2], exact_match_reference_pairs=pairs)
    assert not component.is_consistent
    assert any("INCONSISTENT_PROGRAM" in reason for reason in component.inconsistency_reasons)


def test_group_id_for_is_deterministic() -> None:
    def _make() -> str:
        return group_id_for(
            member_ids=["member::1", "member::2"],
            rule_family="RETURN_CODE",
            program="CALLER",
            target="WS-X",
            output_literal="R001",
        )

    assert _make() == _make()


def test_group_id_for_changes_with_member_set() -> None:
    id_a = group_id_for(
        member_ids=["member::1"],
        rule_family="RETURN_CODE",
        program="CALLER",
        target="WS-X",
        output_literal="R001",
    )
    id_b = group_id_for(
        member_ids=["member::1", "member::2"],
        rule_family="RETURN_CODE",
        program="CALLER",
        target="WS-X",
        output_literal="R001",
    )
    assert id_a != id_b
