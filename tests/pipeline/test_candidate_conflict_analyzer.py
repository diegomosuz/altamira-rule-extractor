"""Tests del analizador de conflictos (Fase 9, `feat/unified-
candidate-promotion-assessment`). Items 17-22 de los 50 tests
obligatorios: mismo target/decision_id/call_site_id con literal
distinto SI es conflicto; ausencia de literal/decision_id NUNCA es
conflicto."""

from __future__ import annotations

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateConflictType,
    CandidateSource,
    UnifiedCandidateReference,
    UnifiedRuleFamily,
)
from altamira_extractor.pipeline.candidate_conflict_analyzer import (
    analyze_candidate_conflicts,
    conflicting_pairs,
)

HASH = "b" * 64


def _ref(
    *,
    ref_id: str,
    source: CandidateSource = CandidateSource.V1,
    program: str | None = "CALLER",
    decision_id: str | None = None,
    call_site_id: str | None = None,
    target: str | None = None,
    output_literal: str | None = None,
    rule_family: UnifiedRuleFamily = UnifiedRuleFamily.RETURN_CODE,
    original_support: str = "DETERMINISTIC",
    evidence_ids: list[str] | None = None,
) -> UnifiedCandidateReference:
    return UnifiedCandidateReference(
        unified_reference_id=ref_id,
        source=source,
        source_candidate_id=ref_id,
        source_artifact_hash=HASH,
        rule_family=rule_family,
        original_support=original_support,
        program=program,
        decision_id=decision_id,
        call_site_id=call_site_id,
        target=target,
        output_literal=output_literal,
        evidence_ids=sorted(evidence_ids or []),
    )


def test_same_target_different_literal_is_a_conflict() -> None:
    refs = [
        _ref(ref_id="unified::v1::a", target="WS-X", output_literal="R001"),
        _ref(
            ref_id="unified::v2::b",
            source=CandidateSource.V2,
            target="WS-X",
            output_literal="R002",
            evidence_ids=["e1"],
        ),
    ]
    conflicts = analyze_candidate_conflicts(refs)
    assert any(
        c.conflict_type == CandidateConflictType.SAME_TARGET_CONTRADICTORY_OUTPUT
        for c in conflicts
    )


def test_same_decision_different_literal_is_a_conflict() -> None:
    refs = [
        _ref(
            ref_id="unified::v1::a",
            decision_id="decision::1",
            target="WS-X",
            output_literal="R001",
        ),
        _ref(
            ref_id="unified::v2::b",
            source=CandidateSource.V2,
            decision_id="decision::1",
            target="WS-X",
            output_literal="R002",
            evidence_ids=["e1"],
        ),
    ]
    conflicts = analyze_candidate_conflicts(refs)
    assert any(
        c.conflict_type == CandidateConflictType.SAME_DECISION_DIFFERENT_OUTPUT
        for c in conflicts
    )


def test_same_call_site_different_literal_is_a_conflict() -> None:
    refs = [
        _ref(
            ref_id="unified::interprocedural::a",
            source=CandidateSource.INTERPROCEDURAL,
            call_site_id="callsite::1",
            target="WS-X",
            output_literal="R001",
            evidence_ids=["e1"],
        ),
        _ref(
            ref_id="unified::interprocedural::b",
            source=CandidateSource.INTERPROCEDURAL,
            call_site_id="callsite::1",
            target="WS-X",
            output_literal="R002",
            evidence_ids=["e2"],
        ),
    ]
    conflicts = analyze_candidate_conflicts(refs)
    assert any(
        c.conflict_type == CandidateConflictType.SAME_CALL_SITE_DIFFERENT_OUTPUT
        for c in conflicts
    )


def test_same_call_site_different_target_is_never_a_conflict() -> None:
    """Un mismo `call_site_id` puede demostrar legitimamente un valor
    `RETURNING` y un valor `BY REFERENCE` simultaneos sobre DOS targets
    distintos (canales de salida independientes de la misma llamada) --
    nunca una contradiccion, aunque los literales difieran."""
    refs = [
        _ref(
            ref_id="unified::interprocedural::a",
            source=CandidateSource.INTERPROCEDURAL,
            call_site_id="callsite::1",
            target="WS-COD-RETORNO",
            output_literal="R001",
            evidence_ids=["e1"],
        ),
        _ref(
            ref_id="unified::interprocedural::b",
            source=CandidateSource.INTERPROCEDURAL,
            call_site_id="callsite::1",
            target="WS-STATUS",
            output_literal="APPROVED",
            evidence_ids=["e2"],
        ),
    ]
    conflicts = analyze_candidate_conflicts(refs)
    assert conflicts == []


def test_same_decision_different_target_is_never_a_conflict() -> None:
    """Simetrico al fix `(anchor, target)` de `SAME_CALL_SITE_
    DIFFERENT_OUTPUT`: una misma `decision_id` puede legitimamente
    escribir literales distintos en DOS targets distintos (dos
    asignaciones dentro de la misma rama) -- nunca una contradiccion
    por si sola, solo lo es cuando coinciden target Y decision_id."""
    refs = [
        _ref(
            ref_id="unified::v1::a",
            decision_id="decision::1",
            target="WS-COD-RETORNO",
            output_literal="R001",
        ),
        _ref(
            ref_id="unified::v2::b",
            source=CandidateSource.V2,
            decision_id="decision::1",
            target="WS-STATUS",
            output_literal="APPROVED",
            evidence_ids=["e1"],
        ),
    ]
    conflicts = analyze_candidate_conflicts(refs)
    assert conflicts == []


def test_incompatible_rule_family_same_decision_is_a_conflict() -> None:
    refs = [
        _ref(
            ref_id="unified::v1::a",
            decision_id="decision::1",
            rule_family=UnifiedRuleFamily.RETURN_CODE,
        ),
        _ref(
            ref_id="unified::interprocedural::b",
            source=CandidateSource.INTERPROCEDURAL,
            decision_id="decision::1",
            rule_family=UnifiedRuleFamily.BY_REFERENCE_OUTPUT,
            evidence_ids=["e1"],
        ),
    ]
    conflicts = analyze_candidate_conflicts(refs)
    assert any(
        c.conflict_type == CandidateConflictType.INCOMPATIBLE_RULE_FAMILY for c in conflicts
    )


def test_absence_of_literal_is_never_a_conflict() -> None:
    refs = [
        _ref(ref_id="unified::v1::a", target="WS-X", output_literal="R001"),
        _ref(
            ref_id="unified::v2::b",
            source=CandidateSource.V2,
            target="WS-X",
            output_literal=None,
            evidence_ids=["e1"],
        ),
    ]
    conflicts = analyze_candidate_conflicts(refs)
    assert conflicts == []


def test_absence_of_decision_id_is_never_a_conflict() -> None:
    refs = [
        _ref(ref_id="unified::v1::a", decision_id=None, output_literal="R001"),
        _ref(
            ref_id="unified::v2::b",
            source=CandidateSource.V2,
            decision_id=None,
            output_literal="R002",
            evidence_ids=["e1"],
        ),
    ]
    conflicts = analyze_candidate_conflicts(refs)
    assert conflicts == []


def test_invalid_provenance_conflict_for_deterministic_without_evidence() -> None:
    refs = [
        _ref(
            ref_id="unified::v2::a",
            source=CandidateSource.V2,
            original_support="DETERMINISTIC",
            evidence_ids=[],
        )
    ]
    conflicts = analyze_candidate_conflicts(refs)
    assert any(c.conflict_type == CandidateConflictType.INVALID_PROVENANCE for c in conflicts)


def test_v1_deterministic_without_evidence_is_never_invalid_provenance() -> None:
    """V1 nunca expone `evidence_ids` por diseno (Q0 no produce evidencia
    granular) -- ausencia legitima, nunca un conflicto de provenance."""
    refs = [
        _ref(
            ref_id="unified::v1::a",
            source=CandidateSource.V1,
            original_support="DETECTED_CANDIDATE",
            evidence_ids=[],
        )
    ]
    conflicts = analyze_candidate_conflicts(refs)
    assert conflicts == []


def test_conflicting_pairs_extracts_all_pairwise_combinations() -> None:
    refs = [
        _ref(ref_id="unified::v1::a", target="WS-X", output_literal="R001"),
        _ref(
            ref_id="unified::v2::b",
            source=CandidateSource.V2,
            target="WS-X",
            output_literal="R002",
            evidence_ids=["e1"],
        ),
        _ref(
            ref_id="unified::interprocedural::c",
            source=CandidateSource.INTERPROCEDURAL,
            target="WS-X",
            output_literal="R003",
            evidence_ids=["e2"],
        ),
    ]
    conflicts = analyze_candidate_conflicts(refs)
    pairs = conflicting_pairs(conflicts)
    assert frozenset({"unified::v1::a", "unified::v2::b"}) in pairs
    assert frozenset({"unified::v1::a", "unified::interprocedural::c"}) in pairs
    assert frozenset({"unified::v2::b", "unified::interprocedural::c"}) in pairs


def test_conflicts_are_deterministic() -> None:
    refs = [
        _ref(ref_id="unified::v1::a", target="WS-X", output_literal="R001"),
        _ref(
            ref_id="unified::v2::b",
            source=CandidateSource.V2,
            target="WS-X",
            output_literal="R002",
            evidence_ids=["e1"],
        ),
    ]
    conflicts_1 = analyze_candidate_conflicts(refs)
    conflicts_2 = analyze_candidate_conflicts(list(reversed(refs)))
    assert [c.model_dump_json() for c in conflicts_1] == [
        c.model_dump_json() for c in conflicts_2
    ]
