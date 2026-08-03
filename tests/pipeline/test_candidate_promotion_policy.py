"""Tests de la politica de evaluacion de preparacion para promocion
(Fase 9, `feat/unified-candidate-promotion-assessment`). Items 23-32 de
los 50 tests obligatorios: BASELINE_V1, ALREADY_COVERED,
READY_FOR_CONTROLLED_REVIEW, REVIEW_REQUIRED (incluyendo
interprocedural-only), BLOCKED (parcial/barreras/UNKNOWN), CONFLICTING,
NOT_EVALUATED."""

from __future__ import annotations

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    PromotionDisposition,
    SourceAvailability,
    UnifiedCandidateReference,
    UnifiedRuleFamily,
)
from altamira_extractor.pipeline.candidate_promotion_policy import evaluate_candidate

HASH = "c" * 64

_ALL_AVAILABLE = {
    CandidateSource.V1: SourceAvailability.AVAILABLE,
    CandidateSource.V2: SourceAvailability.AVAILABLE,
    CandidateSource.INTERPROCEDURAL: SourceAvailability.AVAILABLE,
}


def _ref(
    *,
    ref_id: str = "unified::v2::a",
    source: CandidateSource = CandidateSource.V2,
    original_support: str = "DETERMINISTIC",
    target: str | None = "WS-X",
    output_literal: str | None = "R001",
    barrier_codes: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    rule_family: UnifiedRuleFamily = UnifiedRuleFamily.RETURN_CODE,
) -> UnifiedCandidateReference:
    return UnifiedCandidateReference(
        unified_reference_id=ref_id,
        source=source,
        source_candidate_id=ref_id,
        source_artifact_hash=HASH,
        rule_family=rule_family,
        original_support=original_support,
        program="CALLER",
        target=target,
        output_literal=output_literal,
        barrier_codes=sorted(barrier_codes or []),
        evidence_ids=sorted(evidence_ids or ["evidence::1"]),
    )


def test_v1_source_always_produces_baseline_v1() -> None:
    reference = _ref(ref_id="unified::v1::a", source=CandidateSource.V1)
    assessment = evaluate_candidate(
        reference,
        reference_by_id={reference.unified_reference_id: reference},
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=_ALL_AVAILABLE,
    )
    assert assessment.disposition == PromotionDisposition.BASELINE_V1


def test_exact_match_with_v1_produces_already_covered() -> None:
    v1_ref = _ref(ref_id="unified::v1::a", source=CandidateSource.V1)
    v2_ref = _ref(ref_id="unified::v2::a", source=CandidateSource.V2)
    assessment = evaluate_candidate(
        v2_ref,
        reference_by_id={v1_ref.unified_reference_id: v1_ref, v2_ref.unified_reference_id: v2_ref},
        exact_match_reference_ids=[v1_ref.unified_reference_id],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=_ALL_AVAILABLE,
    )
    assert assessment.disposition == PromotionDisposition.ALREADY_COVERED
    assert assessment.exact_match_reference_ids == [v1_ref.unified_reference_id]


def test_v2_interprocedural_corroboration_produces_ready_for_controlled_review() -> None:
    v2_ref = _ref(ref_id="unified::v2::a", source=CandidateSource.V2)
    ip_ref = _ref(ref_id="unified::interprocedural::a", source=CandidateSource.INTERPROCEDURAL)
    assessment = evaluate_candidate(
        v2_ref,
        reference_by_id={v2_ref.unified_reference_id: v2_ref, ip_ref.unified_reference_id: ip_ref},
        exact_match_reference_ids=[ip_ref.unified_reference_id],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=_ALL_AVAILABLE,
    )
    assert assessment.disposition == PromotionDisposition.READY_FOR_CONTROLLED_REVIEW


def test_deterministic_candidate_without_corroboration_is_review_required() -> None:
    v2_ref = _ref(ref_id="unified::v2::a", source=CandidateSource.V2)
    assessment = evaluate_candidate(
        v2_ref,
        reference_by_id={v2_ref.unified_reference_id: v2_ref},
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=_ALL_AVAILABLE,
    )
    assert assessment.disposition == PromotionDisposition.REVIEW_REQUIRED


def test_interprocedural_only_candidate_is_review_required_never_auto_promoted() -> None:
    ip_ref = _ref(ref_id="unified::interprocedural::a", source=CandidateSource.INTERPROCEDURAL)
    availability = {
        CandidateSource.V1: SourceAvailability.AVAILABLE,
        CandidateSource.V2: SourceAvailability.NOT_AVAILABLE,
        CandidateSource.INTERPROCEDURAL: SourceAvailability.AVAILABLE,
    }
    assessment = evaluate_candidate(
        ip_ref,
        reference_by_id={ip_ref.unified_reference_id: ip_ref},
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=availability,
    )
    assert assessment.disposition == PromotionDisposition.REVIEW_REQUIRED


def test_partial_candidate_is_blocked() -> None:
    v2_ref = _ref(ref_id="unified::v2::a", source=CandidateSource.V2, original_support="PARTIAL")
    assessment = evaluate_candidate(
        v2_ref,
        reference_by_id={v2_ref.unified_reference_id: v2_ref},
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=_ALL_AVAILABLE,
    )
    assert assessment.disposition == PromotionDisposition.BLOCKED


def test_candidate_with_barrier_is_blocked() -> None:
    ip_ref = _ref(
        ref_id="unified::interprocedural::a",
        source=CandidateSource.INTERPROCEDURAL,
        barrier_codes=["DYNAMIC_CALL"],
        output_literal=None,
        original_support="BLOCKED",
    )
    assessment = evaluate_candidate(
        ip_ref,
        reference_by_id={ip_ref.unified_reference_id: ip_ref},
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=_ALL_AVAILABLE,
    )
    assert assessment.disposition == PromotionDisposition.BLOCKED


def test_unknown_rule_family_is_blocked() -> None:
    v2_ref = _ref(
        ref_id="unified::v2::a", source=CandidateSource.V2, rule_family=UnifiedRuleFamily.UNKNOWN
    )
    assessment = evaluate_candidate(
        v2_ref,
        reference_by_id={v2_ref.unified_reference_id: v2_ref},
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=_ALL_AVAILABLE,
    )
    assert assessment.disposition == PromotionDisposition.BLOCKED


def test_contradictory_candidate_is_conflicting() -> None:
    v2_ref = _ref(ref_id="unified::v2::a", source=CandidateSource.V2)
    assessment = evaluate_candidate(
        v2_ref,
        reference_by_id={v2_ref.unified_reference_id: v2_ref},
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=["conflict::1"],
        source_availability=_ALL_AVAILABLE,
    )
    assert assessment.disposition == PromotionDisposition.CONFLICTING
    assert assessment.conflict_ids == ["conflict::1"]


def test_necessary_source_absent_is_not_evaluated() -> None:
    v2_ref = _ref(ref_id="unified::v2::a", source=CandidateSource.V2)
    availability = {
        CandidateSource.V1: SourceAvailability.NOT_AVAILABLE,
        CandidateSource.V2: SourceAvailability.AVAILABLE,
        CandidateSource.INTERPROCEDURAL: SourceAvailability.NOT_AVAILABLE,
    }
    assessment = evaluate_candidate(
        v2_ref,
        reference_by_id={v2_ref.unified_reference_id: v2_ref},
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=availability,
    )
    assert assessment.disposition == PromotionDisposition.NOT_EVALUATED


def test_not_evaluated_never_confused_with_review_required_or_blocked() -> None:
    """V1 ausente produce NOT_EVALUATED incluso cuando el candidato en si
    mismo cumple todos los criterios de calidad (nunca se confunde con
    REVIEW_REQUIRED/BLOCKED, ver docstring de la politica)."""
    v2_ref = _ref(ref_id="unified::v2::a", source=CandidateSource.V2)
    ip_ref = _ref(ref_id="unified::interprocedural::a", source=CandidateSource.INTERPROCEDURAL)
    availability = {
        CandidateSource.V1: SourceAvailability.NOT_AVAILABLE,
        CandidateSource.V2: SourceAvailability.AVAILABLE,
        CandidateSource.INTERPROCEDURAL: SourceAvailability.AVAILABLE,
    }
    assessment = evaluate_candidate(
        v2_ref,
        reference_by_id={v2_ref.unified_reference_id: v2_ref, ip_ref.unified_reference_id: ip_ref},
        exact_match_reference_ids=[ip_ref.unified_reference_id],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=availability,
    )
    assert assessment.disposition not in (
        PromotionDisposition.REVIEW_REQUIRED,
        PromotionDisposition.BLOCKED,
    )
    assert assessment.disposition == PromotionDisposition.NOT_EVALUATED


def test_never_simultaneously_ready_and_conflicting() -> None:
    v2_ref = _ref(ref_id="unified::v2::a", source=CandidateSource.V2)
    ip_ref = _ref(ref_id="unified::interprocedural::a", source=CandidateSource.INTERPROCEDURAL)
    assessment = evaluate_candidate(
        v2_ref,
        reference_by_id={v2_ref.unified_reference_id: v2_ref, ip_ref.unified_reference_id: ip_ref},
        exact_match_reference_ids=[ip_ref.unified_reference_id],
        related_reference_ids=[],
        conflict_ids=["conflict::1"],
        source_availability=_ALL_AVAILABLE,
    )
    assert assessment.disposition == PromotionDisposition.CONFLICTING


def test_invalid_source_artifact_blocks_the_candidate() -> None:
    v2_ref = _ref(ref_id="unified::v2::a", source=CandidateSource.V2)
    availability = {
        CandidateSource.V1: SourceAvailability.AVAILABLE,
        CandidateSource.V2: SourceAvailability.INVALID,
        CandidateSource.INTERPROCEDURAL: SourceAvailability.AVAILABLE,
    }
    assessment = evaluate_candidate(
        v2_ref,
        reference_by_id={v2_ref.unified_reference_id: v2_ref},
        exact_match_reference_ids=[],
        related_reference_ids=[],
        conflict_ids=[],
        source_availability=availability,
    )
    assert assessment.disposition == PromotionDisposition.BLOCKED
