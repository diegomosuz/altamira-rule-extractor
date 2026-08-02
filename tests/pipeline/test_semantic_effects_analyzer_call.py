"""Tests de `_normalize_call` (Fase 6 de la ampliacion semantica,
fundacion interprocedural CALL/LINKAGE): `pipeline/
semantic_effects_analyzer.py`, rama `StatementKind.CALL ->
SemanticEffectKind.CALL_PROGRAM`. Mismo patron de helpers que
`test_semantic_effects_analyzer.py`, mantenido en un archivo dedicado
por el volumen de casos (target literal/dinamico/no identificable,
argumentos completos/parciales, RETURNING, clausulas ON EXCEPTION)."""

from __future__ import annotations

from altamira_extractor.contracts.canonical import (
    CanonicalCallArgument,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    CallPassingMode,
    CallTargetKind,
    LocationKind,
    SourceFormat,
    StatementKind,
)
from altamira_extractor.contracts.semantic_coverage import SemanticSupportStatus
from altamira_extractor.contracts.semantic_effects import SemanticEffect, SemanticEffectKind
from altamira_extractor.pipeline.semantic_effects_analyzer import analyze_semantic_effects

_HASH = "c" * 64
_REQUIRED_HASHES = {"artifacts/02-canonical": _HASH}


def _statement(**overrides: object) -> CanonicalStatement:
    defaults: dict[str, object] = {
        "statement_id": "P1::A::0::CALL",
        "kind": StatementKind.CALL,
        "source_text": "CALL",
        "location_kind": LocationKind.UNKNOWN,
        "call_target_kind": CallTargetKind.LITERAL,
        "called_program_name": "SUBPROG",
    }
    defaults.update(overrides)
    return CanonicalStatement(**defaults)  # type: ignore[arg-type]


def _paragraph(statements: list[CanonicalStatement]) -> CanonicalParagraph:
    return CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN, statements=statements
    )


def _program(statements: list[CanonicalStatement]) -> CanonicalProgram:
    return CanonicalProgram(
        program_name="P1",
        source_file="p1.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[_paragraph(statements)],
    )


def _argument(passing_mode: CallPassingMode, **overrides: object) -> CanonicalCallArgument:
    fields: dict[str, object] = {
        "ordinal": 1,
        "expression": "WS-A",
        "data_item_name": "WS-A",
        "qualified_data_item_name": "WS-A",
        "passing_mode": passing_mode,
        "location_kind": LocationKind.UNKNOWN,
    }
    fields.update(overrides)
    return CanonicalCallArgument(**fields)  # type: ignore[arg-type]


def _call_effect(statement: CanonicalStatement) -> SemanticEffect:
    artifact = analyze_semantic_effects(
        canonical_programs=[_program([statement])],
        run_id="run-1",
        source_package_hash=_HASH,
        source_artifact_hashes=_REQUIRED_HASHES,
    )
    effects = artifact.programs[0].effects
    assert len(effects) == 1
    assert effects[0].kind == SemanticEffectKind.CALL_PROGRAM
    return effects[0]


# --- Target literal ------------------------------------------------------------


def test_literal_target_with_no_arguments_is_fully_supported() -> None:
    effect = _call_effect(_statement())
    assert effect.support_status == SemanticSupportStatus.FULLY_SUPPORTED
    assert effect.diagnostic_codes == []
    assert effect.called_program_name == "SUBPROG"
    assert effect.call_target_kind == CallTargetKind.LITERAL


def test_literal_target_with_fully_captured_arguments_is_fully_supported() -> None:
    effect = _call_effect(_statement(call_arguments=[_argument(CallPassingMode.REFERENCE)]))
    assert effect.support_status == SemanticSupportStatus.FULLY_SUPPORTED
    assert effect.diagnostic_codes == []
    assert len(effect.call_arguments) == 1


def test_literal_target_with_omitted_argument_is_fully_supported() -> None:
    """`OMITTED` es una forma estructural completa por si misma (no
    requiere data_item_name/literal)."""
    effect = _call_effect(
        _statement(
            call_arguments=[
                _argument(
                    CallPassingMode.REFERENCE,
                    expression="OMITTED",
                    data_item_name=None,
                    qualified_data_item_name=None,
                    omitted=True,
                )
            ]
        )
    )
    assert effect.support_status == SemanticSupportStatus.FULLY_SUPPORTED


def test_literal_target_with_pure_literal_argument_is_fully_supported() -> None:
    effect = _call_effect(
        _statement(
            call_arguments=[
                _argument(
                    CallPassingMode.CONTENT,
                    expression="'X'",
                    data_item_name=None,
                    qualified_data_item_name=None,
                    literal="'X'",
                )
            ]
        )
    )
    assert effect.support_status == SemanticSupportStatus.FULLY_SUPPORTED


# --- Target dinamico / no identificable -----------------------------------


def test_dynamic_target_is_partially_supported_with_diagnostic() -> None:
    effect = _call_effect(
        _statement(
            call_target_kind=CallTargetKind.DYNAMIC,
            called_program_name=None,
            called_program_expression="WS-PROGRAM-NAME",
        )
    )
    assert effect.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert effect.diagnostic_codes == ["CALL_DYNAMIC_TARGET_UNRESOLVED"]
    assert effect.called_program_expression == "WS-PROGRAM-NAME"
    assert effect.called_program_name is None


def test_unknown_target_kind_is_partially_supported_with_diagnostic() -> None:
    effect = _call_effect(
        _statement(call_target_kind=CallTargetKind.UNKNOWN, called_program_name=None)
    )
    assert effect.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert effect.diagnostic_codes == ["CALL_TARGET_NOT_IDENTIFIABLE"]


# --- Argumentos parciales -------------------------------------------------------


def test_argument_with_unresolvable_shape_is_partially_supported() -> None:
    unresolved = CanonicalCallArgument(
        ordinal=1,
        expression="<unsupported>",
        passing_mode=CallPassingMode.UNKNOWN,
        omitted=False,
        location_kind=LocationKind.UNKNOWN,
    )
    effect = _call_effect(_statement(call_arguments=[unresolved]))
    assert effect.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert effect.diagnostic_codes == ["CALL_ARGUMENT_PARTIAL"]


def test_one_resolved_and_one_unresolved_argument_is_still_partial() -> None:
    unresolved = CanonicalCallArgument(
        ordinal=2,
        expression="<unsupported>",
        passing_mode=CallPassingMode.UNKNOWN,
        omitted=False,
        location_kind=LocationKind.UNKNOWN,
    )
    effect = _call_effect(
        _statement(call_arguments=[_argument(CallPassingMode.REFERENCE), unresolved])
    )
    assert effect.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert effect.diagnostic_codes == ["CALL_ARGUMENT_PARTIAL"]


# --- RETURNING -------------------------------------------------------------------


def test_returning_makes_an_otherwise_complete_call_only_partially_supported() -> None:
    effect = _call_effect(_statement(call_returning_data_item="WS-RESULT"))
    assert effect.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert effect.diagnostic_codes == ["CALL_RETURNING_PARTIAL"]
    assert effect.call_returning_data_item == "WS-RESULT"


# --- ON EXCEPTION / NOT ON EXCEPTION ---------------------------------------------


def test_on_exception_clause_adds_diagnostic_without_forcing_partial_by_itself() -> None:
    """`call_has_on_exception` en si mismo no degrada un CALL literal sin
    argumentos/RETURNING a PARTIALLY_SUPPORTED por las otras reglas, pero
    SIEMPRE agrega su propio diagnostico -- nunca se omite silenciosamente."""
    effect = _call_effect(_statement(call_has_on_exception=True))
    assert "CALL_EXCEPTION_BRANCHES_NOT_MODELED" in effect.diagnostic_codes


def test_not_on_exception_clause_adds_diagnostic() -> None:
    effect = _call_effect(_statement(call_has_not_on_exception=True))
    assert "CALL_EXCEPTION_BRANCHES_NOT_MODELED" in effect.diagnostic_codes


# --- Combinacion: todas las banderas simultaneas -------------------------------


def test_dynamic_target_with_partial_argument_returning_exception_accumulates_diagnostics() -> None:
    unresolved = CanonicalCallArgument(
        ordinal=1,
        expression="<unsupported>",
        passing_mode=CallPassingMode.UNKNOWN,
        omitted=False,
        location_kind=LocationKind.UNKNOWN,
    )
    effect = _call_effect(
        _statement(
            call_target_kind=CallTargetKind.DYNAMIC,
            called_program_name=None,
            called_program_expression="WS-PROGRAM-NAME",
            call_arguments=[unresolved],
            call_returning_data_item="WS-RESULT",
            call_has_on_exception=True,
        )
    )
    assert effect.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert effect.diagnostic_codes == sorted(
        {
            "CALL_ARGUMENT_PARTIAL",
            "CALL_DYNAMIC_TARGET_UNRESOLVED",
            "CALL_EXCEPTION_BRANCHES_NOT_MODELED",
            "CALL_RETURNING_PARTIAL",
        }
    )


# --- Nunca afirma escritura cierta ----------------------------------------------


def test_call_effect_never_populates_writes_or_target_data_items() -> None:
    effect = _call_effect(
        _statement(
            call_arguments=[_argument(CallPassingMode.REFERENCE)],
            call_returning_data_item="WS-RESULT",
        )
    )
    assert effect.writes == []
    assert effect.target_data_items == []
    assert effect.literal is None
