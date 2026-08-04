"""Tests del adaptador PURO de grupo (Fase 13 Parte 5,
`feat/unified-shadow-downstream-pipeline`)."""

from __future__ import annotations

from altamira_extractor.pipeline.unified_shadow_context_adapter import (
    adapt_group_to_context_view,
)

from ._unified_shadow_downstream_fixtures import downstream_golden_path
from ._unified_shadow_validation_fixtures import GROUP_ID, MEMBER_ID, second_member


def test_view_preserves_group_identity_as_primary() -> None:
    dgp = downstream_golden_path()
    group = dgp.unified_shadow.shadow_groups[0]
    members_by_id = {m.member_id: m for m in dgp.unified_shadow.shadow_members}

    view = adapt_group_to_context_view(group, members_by_id=members_by_id)

    assert view.group_id == GROUP_ID == group.unified_shadow_candidate_id
    assert view.program == group.program
    assert view.target == group.target
    assert view.output_literal == group.output_literal


def test_view_preserves_all_member_ids_no_winner_chosen() -> None:
    dgp = downstream_golden_path()
    group = dgp.unified_shadow.shadow_groups[0]
    member_2 = second_member()
    group_with_two_members = group.model_copy(
        update={
            "member_ids": sorted([MEMBER_ID, member_2.member_id]),
            "evidence_ids": sorted({*group.evidence_ids, *member_2.evidence_ids}),
            "provenance_references": sorted(
                {*group.provenance_references, *member_2.provenance_references}
            ),
        }
    )
    members_by_id = {m.member_id: m for m in [*dgp.unified_shadow.shadow_members, member_2]}

    view = adapt_group_to_context_view(group_with_two_members, members_by_id=members_by_id)

    assert set(view.member_ids) == {MEMBER_ID, member_2.member_id}
    assert len(view.members) == 2
    assert {m.member_id for m in view.members} == {MEMBER_ID, member_2.member_id}


def test_view_source_candidate_ids_and_review_decision_ids_from_all_members() -> None:
    dgp = downstream_golden_path()
    group = dgp.unified_shadow.shadow_groups[0]
    members_by_id = {m.member_id: m for m in dgp.unified_shadow.shadow_members}

    view = adapt_group_to_context_view(group, members_by_id=members_by_id)

    member = members_by_id[MEMBER_ID]
    assert view.source_candidate_ids == (member.source_candidate_id,)
    assert view.review_decision_ids == (member.review_decision_id,)


def test_view_paragraphs_derived_from_unique_member_paragraphs() -> None:
    dgp = downstream_golden_path()
    group = dgp.unified_shadow.shadow_groups[0]
    members_by_id = {m.member_id: m for m in dgp.unified_shadow.shadow_members}

    view = adapt_group_to_context_view(group, members_by_id=members_by_id)

    assert view.paragraphs == (members_by_id[MEMBER_ID].paragraph,)


def test_view_evidence_and_provenance_are_group_union_sorted_and_deduplicated() -> None:
    dgp = downstream_golden_path()
    group = dgp.unified_shadow.shadow_groups[0]
    members_by_id = {m.member_id: m for m in dgp.unified_shadow.shadow_members}

    view = adapt_group_to_context_view(group, members_by_id=members_by_id)

    assert view.evidence_ids == tuple(sorted(set(group.evidence_ids)))
    assert view.provenance_references == tuple(sorted(set(group.provenance_references)))


def test_view_rule_family_copied_verbatim_never_reinterpreted() -> None:
    dgp = downstream_golden_path()
    group = dgp.unified_shadow.shadow_groups[0]
    members_by_id = {m.member_id: m for m in dgp.unified_shadow.shadow_members}

    view = adapt_group_to_context_view(group, members_by_id=members_by_id)

    assert view.rule_family == group.rule_family


def test_adapter_does_not_mutate_inputs() -> None:
    dgp = downstream_golden_path()
    group = dgp.unified_shadow.shadow_groups[0]
    members_by_id = {m.member_id: m for m in dgp.unified_shadow.shadow_members}
    group_snapshot = group.model_copy(deep=True)
    members_snapshot = {k: v.model_copy(deep=True) for k, v in members_by_id.items()}

    adapt_group_to_context_view(group, members_by_id=members_by_id)

    assert group == group_snapshot
    assert members_by_id == members_snapshot
