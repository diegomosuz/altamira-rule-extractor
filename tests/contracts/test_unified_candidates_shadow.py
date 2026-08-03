"""Tests de contrato del artefacto unificado de candidatos en shadow
mode (Fase 11 de la ampliacion semantica,
`feat/unified-candidate-artifact-shadow`). Construye instancias MINIMAS
directamente contra el modelo Pydantic (sin pasar por el analizador)
para aislar cada invariante del contrato -- ver
`tests/pipeline/test_unified_candidates_shadow_analyzer.py` para
pruebas de comportamiento end-to-end del analizador real."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.candidate_promotion_plan import (
    PromotionPlanAction,
    PromotionPlanItemStatus,
)
from altamira_extractor.contracts.candidate_promotion_review import (
    DecisionReasonCode,
    ReviewDecision,
)
from altamira_extractor.contracts.unified_candidates_shadow import (
    UnifiedBaselineCandidateReference,
    UnifiedCandidatesShadowArtifact,
    UnifiedCandidatesShadowSummary,
    UnifiedShadowCandidateGroup,
    UnifiedShadowComparisonKind,
    UnifiedShadowExcludedPlanItem,
    UnifiedShadowExclusionReason,
    UnifiedShadowGroupStatus,
    UnifiedShadowSourceMember,
    UnifiedShadowSupport,
)

HASH = "a" * 64


def _baseline(
    *, baseline_reference_id: str = "baseline::1", source_candidate_id: str = "candidate::1"
) -> UnifiedBaselineCandidateReference:
    return UnifiedBaselineCandidateReference(
        baseline_reference_id=baseline_reference_id,
        source_candidate_id=source_candidate_id,
        source_artifact_hash=HASH,
        original_candidate_hash=HASH,
        rule_family=UnifiedRuleFamily.RETURN_CODE,
        original_support="DETERMINISTIC",
        program="CALLER",
        paragraph="MAIN",
        output_literal="R001",
    )


def _member(
    *,
    member_id: str = "member::1",
    source: CandidateSource = CandidateSource.V2,
    source_candidate_id: str = "v2::1",
    plan_item_id: str = "plan::1",
    review_item_id: str = "review::1",
    assessment_id: str = "assessment::1",
    assessment_reference_id: str = "unified::v2::a",
    review_decision_id: str = "decision::1",
    rule_family: UnifiedRuleFamily = UnifiedRuleFamily.RETURN_CODE,
    program: str = "CALLER",
    target: str | None = "WS-X",
    output_literal: str | None = "R001",
    decision: ReviewDecision = ReviewDecision.APPROVE_FOR_SHADOW_PROMOTION,
    reason_code: DecisionReasonCode = DecisionReasonCode.EVIDENCE_CONFIRMED,
) -> UnifiedShadowSourceMember:
    return UnifiedShadowSourceMember(
        member_id=member_id,
        source=source,
        source_candidate_id=source_candidate_id,
        source_artifact_hash=HASH,
        source_candidate_hash=HASH,
        assessment_id=assessment_id,
        assessment_reference_id=assessment_reference_id,
        review_item_id=review_item_id,
        plan_item_id=plan_item_id,
        review_decision_id=review_decision_id,
        decision=decision,
        reason_code=reason_code,
        reviewer_reference="analyst@example.com",
        rule_family=rule_family,
        original_support="DETERMINISTIC",
        program=program,
        paragraph="MAIN",
        target=target,
        output_literal=output_literal,
        evidence_ids=["evidence::1"],
    )


def _group(
    *,
    unified_shadow_candidate_id: str = "group::1",
    status: UnifiedShadowGroupStatus = UnifiedShadowGroupStatus.VALID,
    member_ids: list[str] | None = None,
    rule_family: UnifiedRuleFamily = UnifiedRuleFamily.RETURN_CODE,
    program: str = "CALLER",
    target: str | None = "WS-X",
    output_literal: str | None = "R001",
    comparison_to_v1: UnifiedShadowComparisonKind = UnifiedShadowComparisonKind.NOT_IN_BASELINE,
    exact_baseline_reference_ids: list[str] | None = None,
    conflicting_baseline_reference_ids: list[str] | None = None,
) -> UnifiedShadowCandidateGroup:
    return UnifiedShadowCandidateGroup(
        unified_shadow_candidate_id=unified_shadow_candidate_id,
        status=status,
        rule_family=rule_family,
        program=program,
        target=target,
        output_literal=output_literal,
        support=UnifiedShadowSupport.DETERMINISTIC,
        member_ids=member_ids or ["member::1"],
        comparison_to_v1=comparison_to_v1,
        exact_baseline_reference_ids=sorted(exact_baseline_reference_ids or []),
        conflicting_baseline_reference_ids=sorted(conflicting_baseline_reference_ids or []),
        blocking_reasons=(
            ["EXACT_MATCH_WITH_V1_BASELINE"]
            if status == UnifiedShadowGroupStatus.DUPLICATE_BASELINE_COVERAGE
            else (
                ["CONFLICTS_WITH_V1_BASELINE"] if status == UnifiedShadowGroupStatus.BLOCKED else []
            )
        ),
    )


def _excluded(
    *,
    exclusion_id: str = "exclusion::1",
    plan_item_id: str = "plan::2",
    reason: UnifiedShadowExclusionReason = UnifiedShadowExclusionReason.BASELINE_ITEM,
) -> UnifiedShadowExcludedPlanItem:
    return UnifiedShadowExcludedPlanItem(
        exclusion_id=exclusion_id,
        plan_item_id=plan_item_id,
        review_item_id="review::2",
        assessment_id="assessment::2",
        reference_id="unified::v1::b",
        source=CandidateSource.V1,
        source_candidate_id="candidate::2",
        action=PromotionPlanAction.KEEP_BASELINE,
        status=PromotionPlanItemStatus.VALID,
        reason=reason,
    )


def _summary(
    *,
    v1_baseline_count: int = 0,
    proposed_plan_item_count: int = 1,
    shadow_member_count: int = 1,
    shadow_group_count: int = 1,
    excluded_plan_item_count: int = 0,
    exact_baseline_match_group_count: int = 0,
    related_to_baseline_group_count: int = 0,
    not_in_baseline_group_count: int = 1,
    conflicting_with_baseline_group_count: int = 0,
    not_evaluated_group_count: int = 0,
    valid_group_count: int = 1,
    invalid_group_count: int = 0,
    counts_by_source: dict[CandidateSource, int] | None = None,
    counts_by_rule_family: dict[UnifiedRuleFamily, int] | None = None,
    counts_by_group_status: dict[UnifiedShadowGroupStatus, int] | None = None,
    counts_by_baseline_comparison: dict[UnifiedShadowComparisonKind, int] | None = None,
    counts_by_exclusion_reason: dict[UnifiedShadowExclusionReason, int] | None = None,
) -> UnifiedCandidatesShadowSummary:
    return UnifiedCandidatesShadowSummary(
        v1_baseline_count=v1_baseline_count,
        proposed_plan_item_count=proposed_plan_item_count,
        shadow_member_count=shadow_member_count,
        shadow_group_count=shadow_group_count,
        excluded_plan_item_count=excluded_plan_item_count,
        exact_baseline_match_group_count=exact_baseline_match_group_count,
        related_to_baseline_group_count=related_to_baseline_group_count,
        not_in_baseline_group_count=not_in_baseline_group_count,
        conflicting_with_baseline_group_count=conflicting_with_baseline_group_count,
        not_evaluated_group_count=not_evaluated_group_count,
        valid_group_count=valid_group_count,
        invalid_group_count=invalid_group_count,
        counts_by_source=(
            counts_by_source if counts_by_source is not None else {CandidateSource.V2: 1}
        ),
        counts_by_rule_family=(
            counts_by_rule_family
            if counts_by_rule_family is not None
            else {UnifiedRuleFamily.RETURN_CODE: 1}
        ),
        counts_by_group_status=(
            counts_by_group_status
            if counts_by_group_status is not None
            else {UnifiedShadowGroupStatus.VALID: 1}
        ),
        counts_by_baseline_comparison=(
            counts_by_baseline_comparison
            if counts_by_baseline_comparison is not None
            else {UnifiedShadowComparisonKind.NOT_IN_BASELINE: 1}
        ),
        counts_by_exclusion_reason=(
            counts_by_exclusion_reason if counts_by_exclusion_reason is not None else {}
        ),
    )


def _minimal_artifact(**overrides: object) -> UnifiedCandidatesShadowArtifact:
    fields: dict[str, object] = dict(
        run_id="run1",
        source_package_hash=HASH,
        candidate_v1_artifact_hash=HASH,
        assessment_artifact_hash=HASH,
        review_package_hash=HASH,
        promotion_plan_hash=HASH,
        summary=_summary(),
        baseline_candidates=[],
        shadow_members=[_member()],
        shadow_groups=[_group()],
        excluded_plan_items=[],
    )
    fields.update(overrides)
    return UnifiedCandidatesShadowArtifact(**fields)  # type: ignore[arg-type]


def test_minimal_valid_artifact_constructs() -> None:
    artifact = _minimal_artifact()
    assert artifact.shadow_members[0].member_id == "member::1"
    assert artifact.shadow_groups[0].status == UnifiedShadowGroupStatus.VALID


def test_round_trips_through_json() -> None:
    artifact = _minimal_artifact()
    reloaded = UnifiedCandidatesShadowArtifact.model_validate_json(artifact.to_stable_json())
    assert reloaded == artifact


def test_baseline_candidates_must_be_sorted() -> None:
    b1 = _baseline(baseline_reference_id="baseline::2", source_candidate_id="candidate::2")
    b2 = _baseline(baseline_reference_id="baseline::1", source_candidate_id="candidate::1")
    with pytest.raises(ValidationError, match="ordenado"):
        _minimal_artifact(
            baseline_candidates=[b1, b2],
            summary=_summary(v1_baseline_count=2),
        )


def test_baseline_candidates_duplicate_reference_id_rejected() -> None:
    b1 = _baseline(baseline_reference_id="baseline::1", source_candidate_id="candidate::1")
    b2 = _baseline(baseline_reference_id="baseline::1", source_candidate_id="candidate::2")
    with pytest.raises(ValidationError, match="duplicado"):
        _minimal_artifact(
            baseline_candidates=[b1, b2],
            summary=_summary(v1_baseline_count=2),
        )


def test_member_id_duplicate_rejected() -> None:
    m1 = _member(member_id="member::1", source_candidate_id="v2::1")
    m2 = _member(member_id="member::1", source_candidate_id="v2::2")
    with pytest.raises(ValidationError, match="duplicado"):
        _minimal_artifact(
            shadow_members=[m1, m2],
            shadow_groups=[_group(member_ids=["member::1"])],
        )


def test_same_source_candidate_id_in_two_members_rejected() -> None:
    """Invariante 5: ni siquiera con `member_id` distinto puede
    repetirse (source, source_candidate_id) entre dos miembros."""
    m1 = _member(member_id="member::1", source_candidate_id="v2::same")
    m2 = _member(
        member_id="member::2",
        source_candidate_id="v2::same",
        plan_item_id="plan::2",
        review_item_id="review::2",
        assessment_id="assessment::2",
        assessment_reference_id="unified::v2::b",
    )
    with pytest.raises(ValidationError, match="invariante 5"):
        _minimal_artifact(
            shadow_members=sorted([m1, m2], key=lambda m: m.member_id),
            shadow_groups=[_group(member_ids=["member::1", "member::2"])],
        )


def test_group_id_duplicate_rejected() -> None:
    g1 = _group(unified_shadow_candidate_id="group::1", member_ids=["member::1"])
    g2 = _group(unified_shadow_candidate_id="group::1", member_ids=["member::1"])
    with pytest.raises(ValidationError, match="duplicado"):
        _minimal_artifact(shadow_groups=[g1, g2])


def test_exclusion_id_duplicate_rejected() -> None:
    e1 = _excluded(exclusion_id="exclusion::1", plan_item_id="plan::a")
    e2 = _excluded(exclusion_id="exclusion::1", plan_item_id="plan::b")
    with pytest.raises(ValidationError, match="duplicado"):
        _minimal_artifact(
            shadow_members=[],
            shadow_groups=[],
            excluded_plan_items=[e1, e2],
            summary=_summary(
                shadow_member_count=0,
                shadow_group_count=0,
                valid_group_count=0,
                not_in_baseline_group_count=0,
                excluded_plan_item_count=2,
                proposed_plan_item_count=2,
                counts_by_source={},
                counts_by_rule_family={},
                counts_by_group_status={},
                counts_by_baseline_comparison={},
                counts_by_exclusion_reason={UnifiedShadowExclusionReason.BASELINE_ITEM: 2},
            ),
        )


def test_group_referencing_nonexistent_member_rejected() -> None:
    with pytest.raises(ValidationError, match="member_id inexistente"):
        _minimal_artifact(
            shadow_groups=[_group(member_ids=["member::nonexistent"])],
        )


def test_member_not_assigned_to_any_group_rejected() -> None:
    m1 = _member(member_id="member::1")
    m2 = _member(
        member_id="member::2",
        source_candidate_id="v2::2",
        plan_item_id="plan::2",
        review_item_id="review::2",
        assessment_id="assessment::2",
        assessment_reference_id="unified::v2::b",
    )
    with pytest.raises(ValidationError, match="sin grupo"):
        _minimal_artifact(
            shadow_members=sorted([m1, m2], key=lambda m: m.member_id),
            shadow_groups=[_group(member_ids=["member::1"])],
        )


def test_member_in_two_groups_rejected() -> None:
    with pytest.raises(ValidationError, match="mas de un grupo"):
        _minimal_artifact(
            shadow_groups=[
                _group(unified_shadow_candidate_id="group::1", member_ids=["member::1"]),
                _group(unified_shadow_candidate_id="group::2", member_ids=["member::1"]),
            ],
        )


def test_baseline_match_id_must_exist() -> None:
    with pytest.raises(ValidationError, match="baseline_reference_id inexistente"):
        _minimal_artifact(
            shadow_groups=[_group(exact_baseline_reference_ids=["baseline::nonexistent"])],
        )


def test_plan_item_in_both_member_and_excluded_rejected() -> None:
    m1 = _member(member_id="member::1", plan_item_id="plan::shared")
    e1 = _excluded(exclusion_id="exclusion::1", plan_item_id="plan::shared")
    with pytest.raises(ValidationError, match="invariante 9"):
        _minimal_artifact(
            shadow_members=[m1],
            excluded_plan_items=[e1],
            summary=_summary(
                excluded_plan_item_count=1,
                proposed_plan_item_count=2,
                counts_by_exclusion_reason={UnifiedShadowExclusionReason.BASELINE_ITEM: 1},
            ),
        )


def test_group_mixed_rule_family_rejected() -> None:
    m1 = _member(member_id="member::1", rule_family=UnifiedRuleFamily.RETURN_CODE)
    m2 = _member(
        member_id="member::2",
        source_candidate_id="v2::2",
        plan_item_id="plan::2",
        review_item_id="review::2",
        assessment_id="assessment::2",
        assessment_reference_id="unified::v2::b",
        rule_family=UnifiedRuleFamily.STATE_TRANSITION,
    )
    with pytest.raises(ValidationError, match="invariante 18"):
        _minimal_artifact(
            shadow_members=sorted([m1, m2], key=lambda m: m.member_id),
            shadow_groups=[_group(member_ids=["member::1", "member::2"])],
        )


def test_group_mixed_target_rejected() -> None:
    m1 = _member(member_id="member::1", target="WS-X")
    m2 = _member(
        member_id="member::2",
        source_candidate_id="v2::2",
        plan_item_id="plan::2",
        review_item_id="review::2",
        assessment_id="assessment::2",
        assessment_reference_id="unified::v2::b",
        target="WS-Y",
    )
    with pytest.raises(ValidationError, match="invariante 19"):
        _minimal_artifact(
            shadow_members=sorted([m1, m2], key=lambda m: m.member_id),
            shadow_groups=[_group(member_ids=["member::1", "member::2"])],
        )


def test_group_mixed_output_literal_rejected() -> None:
    m1 = _member(member_id="member::1", output_literal="R001")
    m2 = _member(
        member_id="member::2",
        source_candidate_id="v2::2",
        plan_item_id="plan::2",
        review_item_id="review::2",
        assessment_id="assessment::2",
        assessment_reference_id="unified::v2::b",
        output_literal="R002",
    )
    with pytest.raises(ValidationError, match="invariante 20"):
        _minimal_artifact(
            shadow_members=sorted([m1, m2], key=lambda m: m.member_id),
            shadow_groups=[_group(member_ids=["member::1", "member::2"])],
        )


def test_group_mixed_program_rejected() -> None:
    m1 = _member(member_id="member::1", program="CALLER")
    m2 = _member(
        member_id="member::2",
        source_candidate_id="v2::2",
        plan_item_id="plan::2",
        review_item_id="review::2",
        assessment_id="assessment::2",
        assessment_reference_id="unified::v2::b",
        program="OTHER",
    )
    with pytest.raises(ValidationError, match="invariante 21"):
        _minimal_artifact(
            shadow_members=sorted([m1, m2], key=lambda m: m.member_id),
            shadow_groups=[_group(member_ids=["member::1", "member::2"])],
        )


def test_exact_baseline_match_requires_duplicate_status() -> None:
    """Invariante 23: comparison_to_v1=EXACT_BASELINE_MATCH nunca puede
    quedar status=VALID silenciosamente."""
    with pytest.raises(ValidationError, match="invariante 23"):
        _group(
            status=UnifiedShadowGroupStatus.VALID,
            comparison_to_v1=UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH,
            exact_baseline_reference_ids=["baseline::1"],
        )


def test_duplicate_baseline_coverage_requires_exact_baseline_ids() -> None:
    with pytest.raises(ValidationError, match="al menos un"):
        _group(
            status=UnifiedShadowGroupStatus.DUPLICATE_BASELINE_COVERAGE,
            comparison_to_v1=UnifiedShadowComparisonKind.EXACT_BASELINE_MATCH,
            exact_baseline_reference_ids=[],
        )


def test_conflicts_with_baseline_requires_blocked_status() -> None:
    """Invariante 24."""
    with pytest.raises(ValidationError, match="invariante 24"):
        _group(
            status=UnifiedShadowGroupStatus.VALID,
            comparison_to_v1=UnifiedShadowComparisonKind.CONFLICTS_WITH_BASELINE,
            conflicting_baseline_reference_ids=["baseline::1"],
        )


def test_member_source_v1_rejected() -> None:
    """Invariante 11: V1 nunca produce un shadow member."""
    with pytest.raises(ValidationError, match="invariante 11"):
        _member(source=CandidateSource.V1, source_candidate_id="candidate::1")


def test_member_decision_must_be_approve() -> None:
    """Invariante 13."""
    with pytest.raises(ValidationError, match="invariante 13"):
        _member(decision=ReviewDecision.DEFER)


def test_member_reason_code_must_be_compatible_with_approval() -> None:
    """Invariante 14."""
    with pytest.raises(ValidationError, match="invariante 14"):
        _member(reason_code=DecisionReasonCode.DUPLICATE_RULE)


def test_summary_comparison_counts_must_sum_to_group_count() -> None:
    with pytest.raises(ValidationError, match="no coincide con shadow_group_count"):
        _summary(shadow_group_count=2)


def test_summary_valid_invalid_must_sum_to_group_count() -> None:
    with pytest.raises(ValidationError, match="valid_group_count \\+ invalid_group_count"):
        _summary(valid_group_count=0, invalid_group_count=0, shadow_group_count=1)


def test_summary_proposed_plan_item_count_reconciliation() -> None:
    """Parte 9: proposed_plan_item_count == shadow_member_count +
    excluded_plan_item_count."""
    with pytest.raises(ValidationError, match="Parte 9"):
        _summary(
            proposed_plan_item_count=5,
            shadow_member_count=1,
            excluded_plan_item_count=1,
            counts_by_exclusion_reason={UnifiedShadowExclusionReason.BASELINE_ITEM: 1},
        )


def test_summary_matches_content_counts_by_source() -> None:
    with pytest.raises(ValidationError, match="counts_by_source"):
        _minimal_artifact(summary=_summary(counts_by_source={CandidateSource.INTERPROCEDURAL: 1}))


def test_summary_matches_content_counts_by_exclusion_reason() -> None:
    e1 = _excluded(reason=UnifiedShadowExclusionReason.REJECTED)
    with pytest.raises(ValidationError, match="counts_by_exclusion_reason"):
        _minimal_artifact(
            excluded_plan_items=[e1],
            summary=_summary(
                excluded_plan_item_count=1,
                proposed_plan_item_count=2,
                counts_by_exclusion_reason={UnifiedShadowExclusionReason.BASELINE_ITEM: 1},
            ),
        )


def test_diagnostics_must_be_sorted_and_unique() -> None:
    with pytest.raises(ValidationError, match="ordenado alfabeticamente"):
        _minimal_artifact(diagnostics=["b", "a"])


def test_member_evidence_ids_must_be_sorted_and_unique() -> None:
    with pytest.raises(ValidationError, match="ordenado y sin duplicados"):
        UnifiedShadowSourceMember(**{**_member().model_dump(), "evidence_ids": ["b", "a"]})
