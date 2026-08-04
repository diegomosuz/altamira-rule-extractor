"""Tests del comparador diferencial (Fase 14A Parte 6,
`feat/controlled-unified-activation`)."""

from __future__ import annotations

from altamira_extractor.contracts.unified_activation_evaluation import (
    UnifiedActivationComparisonKind,
    UnifiedActivationComparisonLevel,
    UnifiedActivationUnifiedReference,
    UnifiedActivationV1Reference,
)
from altamira_extractor.pipeline.unified_activation_comparator import compare_references


def _v1(
    rid: str,
    *,
    program: str = "PROG",
    output: str | None = "R001",
    level: UnifiedActivationComparisonLevel = UnifiedActivationComparisonLevel.CANDIDATE,
    statement: str | None = None,
    target: str | None = None,
    family: str | None = "RETURN_CODE",
) -> UnifiedActivationV1Reference:
    return UnifiedActivationV1Reference(
        reference_id=rid,
        source_candidate_id=rid,
        level=level,
        program=program,
        output_literal=output,
        statement=statement,
        target=target,
        rule_family=family,
        rule_draft_id=(rid if level == UnifiedActivationComparisonLevel.RULE else None),
    )


def _unified(
    rid: str,
    gid: str,
    *,
    program: str = "PROG",
    output: str | None = "R001",
    level: UnifiedActivationComparisonLevel = UnifiedActivationComparisonLevel.CANDIDATE,
    statement: str | None = None,
    target: str | None = None,
    family: str = "RETURN_CODE",
) -> UnifiedActivationUnifiedReference:
    return UnifiedActivationUnifiedReference(
        reference_id=rid,
        group_id=gid,
        level=level,
        program=program,
        output_literal=output,
        statement=statement,
        target=target,
        rule_family=family,
        rule_draft_record_id=(
            "draft::" + gid if level == UnifiedActivationComparisonLevel.RULE else None
        ),
    )


def test_exact_equivalent() -> None:
    comparisons = compare_references(
        [_v1("v1a", program="PROG", output="R001")],
        [_unified("ua", "g1", program="PROG", output="R001")],
        v1_available=True,
        unified_available=True,
    )
    assert [c.kind for c in comparisons] == [UnifiedActivationComparisonKind.EXACT_EQUIVALENT]


def test_exact_equivalent_at_rule_level_requires_matching_statement() -> None:
    comparisons = compare_references(
        [_v1("v1a", level=UnifiedActivationComparisonLevel.RULE, statement="same text")],
        [_unified("ua", "g1", level=UnifiedActivationComparisonLevel.RULE, statement="same text")],
        v1_available=True,
        unified_available=True,
    )
    assert comparisons[0].kind == UnifiedActivationComparisonKind.EXACT_EQUIVALENT


def test_unified_additive_when_v1_has_no_equivalent() -> None:
    comparisons = compare_references(
        [],
        [_unified("ua", "g1", program="PROG3", output="R004")],
        v1_available=True,
        unified_available=True,
    )
    assert [c.kind for c in comparisons] == [UnifiedActivationComparisonKind.UNIFIED_ADDITIVE]


def test_v1_only_when_unified_has_no_equivalent() -> None:
    comparisons = compare_references(
        [_v1("v1e", program="PROG2", output="R003")], [], v1_available=True, unified_available=True
    )
    assert [c.kind for c in comparisons] == [UnifiedActivationComparisonKind.V1_ONLY]


def test_related_when_only_program_matches() -> None:
    comparisons = compare_references(
        [_v1("v1d", program="PROG", output="R002")],
        [_unified("ud", "g5", program="PROG", output="R001")],
        v1_available=True,
        unified_available=True,
    )
    assert [c.kind for c in comparisons] == [UnifiedActivationComparisonKind.RELATED]


def test_conflicting_when_statement_differs_at_rule_level() -> None:
    comparisons = compare_references(
        [_v1("v1a", level=UnifiedActivationComparisonLevel.RULE, statement="statement A")],
        [
            _unified(
                "ua", "g1", level=UnifiedActivationComparisonLevel.RULE, statement="statement B"
            )
        ],
        v1_available=True,
        unified_available=True,
    )
    assert comparisons[0].kind == UnifiedActivationComparisonKind.CONFLICTING
    assert "statement" in comparisons[0].reason_code


def test_not_comparable_when_levels_differ() -> None:
    comparisons = compare_references(
        [_v1("v1h", level=UnifiedActivationComparisonLevel.CANDIDATE)],
        [_unified("uh", "g7", level=UnifiedActivationComparisonLevel.RULE, statement="stmt")],
        v1_available=True,
        unified_available=True,
    )
    assert comparisons[0].kind == UnifiedActivationComparisonKind.NOT_COMPARABLE


def test_not_evaluated_when_v1_not_available() -> None:
    comparisons = compare_references(
        [],
        [_unified("uf", "g6", program="PROG3", output="R004")],
        v1_available=False,
        unified_available=True,
    )
    assert comparisons[0].kind == UnifiedActivationComparisonKind.NOT_EVALUATED


def test_not_evaluated_when_unified_not_available() -> None:
    comparisons = compare_references(
        [_v1("v1e", program="PROG2", output="R003")], [], v1_available=True, unified_available=False
    )
    assert comparisons[0].kind == UnifiedActivationComparisonKind.NOT_EVALUATED


def test_target_vacuous_match_never_conflicts_when_v1_lacks_target() -> None:
    """V1 nunca expone `target` -- un `target` presente solo del lado
    unified NUNCA produce CONFLICTING (comparacion vacuamente
    satisfecha, ver docstring del comparador)."""
    comparisons = compare_references(
        [_v1("v1b", target=None)],
        [_unified("ub", "g2", target="WS-X")],
        v1_available=True,
        unified_available=True,
    )
    assert comparisons[0].kind == UnifiedActivationComparisonKind.EXACT_EQUIVALENT


def test_no_fuzzy_matching_different_output_literal_never_equivalent() -> None:
    comparisons = compare_references(
        [_v1("v1a", output="R001")],
        [_unified("ua", "g1", output="R001X")],
        v1_available=True,
        unified_available=True,
    )
    # anclas distintas (R001 vs R001X): no EXACT_EQUIVALENT, cae a V1_ONLY/UNIFIED_ADDITIVE
    assert comparisons[0].kind != UnifiedActivationComparisonKind.EXACT_EQUIVALENT


def test_same_pair_never_serialized_twice() -> None:
    comparisons = compare_references(
        [_v1("v1a", program="PROG", output="R001")],
        [_unified("ua", "g1", program="PROG", output="R001")],
        v1_available=True,
        unified_available=True,
    )
    keys = [
        (tuple(sorted(c.v1_reference_ids)), tuple(sorted(c.unified_reference_ids)))
        for c in comparisons
    ]
    assert len(keys) == len(set(keys))


def test_comparisons_are_order_independent() -> None:
    v1_refs = [_v1("v1a", program="A", output="R1"), _v1("v1b", program="B", output="R2")]
    unified_refs = [
        _unified("ua", "g1", program="A", output="R1"),
        _unified("ub", "g2", program="B", output="R2"),
    ]

    forward = compare_references(v1_refs, unified_refs, v1_available=True, unified_available=True)
    backward = compare_references(
        list(reversed(v1_refs)),
        list(reversed(unified_refs)),
        v1_available=True,
        unified_available=True,
    )
    assert [c.comparison_id for c in forward] == [c.comparison_id for c in backward]


def test_comparator_does_not_mutate_inputs() -> None:
    v1_refs = [_v1("v1a")]
    unified_refs = [_unified("ua", "g1")]
    v1_snapshot = [r.model_copy(deep=True) for r in v1_refs]
    unified_snapshot = [r.model_copy(deep=True) for r in unified_refs]
    compare_references(v1_refs, unified_refs, v1_available=True, unified_available=True)
    assert v1_refs == v1_snapshot
    assert unified_refs == unified_snapshot
