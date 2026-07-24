"""Tests de la gramatica V1 acotada de resolucion de predicados (Q3,
Prompt 10b). No es un parser SQL general: solo conjunciones AND de
igualdades literales."""

from __future__ import annotations

from altamira_extractor.contracts.enums import ApplicabilityStatus
from altamira_extractor.pipeline.parameter_predicate_resolver import (
    ResolvedComparison,
    aggregate_applicability,
    entry_matches_comparisons,
    resolve_predicate_row,
)

# --- resolve_predicate_row: casos EXACT ---


def test_no_predicate_text_is_unresolved() -> None:
    resolution = resolve_predicate_row(None, None)
    assert resolution.status == ApplicabilityStatus.UNRESOLVED
    assert resolution.resolved == ()


def test_blank_predicate_text_is_unresolved() -> None:
    resolution = resolve_predicate_row("   ", None)
    assert resolution.status == ApplicabilityStatus.UNRESOLVED


def test_single_equality_literal_is_exact() -> None:
    resolution = resolve_predicate_row("COD = 'R001'", None)
    assert resolution.status == ApplicabilityStatus.EXACT
    assert resolution.resolved == (ResolvedComparison(column="COD", value="R001"),)


def test_conjunction_of_two_literals_is_exact() -> None:
    resolution = resolve_predicate_row("COD = 'R001' AND PAIS = 'AR'", None)
    assert resolution.status == ApplicabilityStatus.EXACT
    assert len(resolution.resolved) == 2


def test_numeric_literal_is_exact() -> None:
    resolution = resolve_predicate_row("MONTO = 100", None)
    assert resolution.status == ApplicabilityStatus.EXACT
    assert resolution.resolved == (ResolvedComparison(column="MONTO", value=100.0),)


def test_quoted_identifier_is_supported() -> None:
    resolution = resolve_predicate_row('"COD" = 123', None)
    assert resolution.status == ApplicabilityStatus.EXACT
    assert resolution.resolved[0].column == "COD"
    assert resolution.resolved[0].value == 123.0


def test_wrapping_parentheses_still_resolve() -> None:
    resolution = resolve_predicate_row("(COD = 'R001')", None)
    assert resolution.status == ApplicabilityStatus.EXACT


def test_column_is_normalized_case_insensitively() -> None:
    resolution = resolve_predicate_row("cod = 'r001'", None)
    assert resolution.resolved[0].column == "COD"
    # el VALOR del literal no se normaliza (es dato, no identificador).
    assert resolution.resolved[0].value == "r001"


# --- resolve_predicate_row: casos PARTIAL/UNRESOLVED ---


def test_host_variable_placeholder_is_unresolved() -> None:
    resolution = resolve_predicate_row("COD = :WS-COD", None)
    assert resolution.status == ApplicabilityStatus.UNRESOLVED
    assert resolution.resolved == ()


def test_mixed_literal_and_unsupported_condition_is_partial() -> None:
    resolution = resolve_predicate_row("COD = 'R001' AND MONTO > 100", None)
    assert resolution.status == ApplicabilityStatus.PARTIAL
    assert len(resolution.resolved) == 1
    assert resolution.resolved[0].column == "COD"


def test_or_is_not_supported() -> None:
    resolution = resolve_predicate_row("COD = 'R001' OR PAIS = 'AR'", None)
    assert resolution.status == ApplicabilityStatus.UNRESOLVED


def test_in_is_not_supported() -> None:
    resolution = resolve_predicate_row("COD IN ('R001', 'R002')", None)
    assert resolution.status == ApplicabilityStatus.UNRESOLVED


def test_between_is_not_supported() -> None:
    resolution = resolve_predicate_row("MONTO BETWEEN 1 AND 100", None)
    assert resolution.status != ApplicabilityStatus.EXACT


def test_like_is_not_supported() -> None:
    resolution = resolve_predicate_row("NOMBRE LIKE 'A%'", None)
    assert resolution.status == ApplicabilityStatus.UNRESOLVED


def test_function_call_is_not_supported() -> None:
    resolution = resolve_predicate_row("UPPER(COD) = 'R001'", None)
    assert resolution.status == ApplicabilityStatus.UNRESOLVED


# --- aggregate_applicability ---


def test_aggregate_no_evidence_is_unresolved() -> None:
    assert aggregate_applicability([]) == ApplicabilityStatus.UNRESOLVED


def test_aggregate_all_exact_is_exact() -> None:
    exact = resolve_predicate_row("COD = 'R001'", None)
    assert aggregate_applicability([exact, exact]) == ApplicabilityStatus.EXACT


def test_aggregate_all_unresolved_is_unresolved() -> None:
    unresolved = resolve_predicate_row(None, None)
    assert aggregate_applicability([unresolved, unresolved]) == ApplicabilityStatus.UNRESOLVED


def test_aggregate_mixed_exact_and_unresolved_is_partial() -> None:
    exact = resolve_predicate_row("COD = 'R001'", None)
    unresolved = resolve_predicate_row(None, None)
    assert aggregate_applicability([exact, unresolved]) == ApplicabilityStatus.PARTIAL


def test_aggregate_any_partial_makes_it_partial() -> None:
    partial = resolve_predicate_row("COD = 'R001' AND MONTO > 100", None)
    assert aggregate_applicability([partial]) == ApplicabilityStatus.PARTIAL


# --- entry_matches_comparisons ---


def test_entry_matches_string_comparison() -> None:
    assert entry_matches_comparisons(
        {"COD": "R001"}, (ResolvedComparison(column="COD", value="R001"),)
    )


def test_entry_matches_case_insensitive_column_name() -> None:
    assert entry_matches_comparisons(
        {"cod": "R001"}, (ResolvedComparison(column="COD", value="R001"),)
    )


def test_entry_does_not_match_different_value() -> None:
    assert not entry_matches_comparisons(
        {"COD": "R002"}, (ResolvedComparison(column="COD", value="R001"),)
    )


def test_entry_matches_numeric_with_type_coercion() -> None:
    assert entry_matches_comparisons(
        {"MONTO": "100"}, (ResolvedComparison(column="MONTO", value=100.0),)
    )
    assert entry_matches_comparisons(
        {"MONTO": 100}, (ResolvedComparison(column="MONTO", value=100.0),)
    )


def test_entry_missing_column_does_not_match() -> None:
    assert not entry_matches_comparisons({}, (ResolvedComparison(column="COD", value="R001"),))


def test_entry_must_match_all_comparisons() -> None:
    comparisons = (
        ResolvedComparison(column="COD", value="R001"),
        ResolvedComparison(column="PAIS", value="AR"),
    )
    assert entry_matches_comparisons({"COD": "R001", "PAIS": "AR"}, comparisons)
    assert not entry_matches_comparisons({"COD": "R001", "PAIS": "BR"}, comparisons)
