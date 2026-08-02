"""Tests de `_classify_call` (Fase 6 de la ampliacion semantica,
fundacion interprocedural CALL/LINKAGE): `pipeline/
semantic_coverage_analyzer.py`, rama `StatementKind.CALL`. Mismo patron
de helpers que `test_semantic_coverage_analyzer.py`, mantenido en un
archivo dedicado por el volumen de casos."""

from __future__ import annotations

from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.canonical import (
    CanonicalCallArgument,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.dependencies import DependencyArtifact
from altamira_extractor.contracts.enums import (
    CallPassingMode,
    CallTargetKind,
    LocationKind,
    SourceFormat,
    StatementKind,
)
from altamira_extractor.contracts.semantic_coverage import (
    CandidateImpact,
    ConstructCoverage,
    SemanticSupportStatus,
)
from altamira_extractor.contracts.semantic_graph import SemanticGraph
from altamira_extractor.pipeline.semantic_coverage_analyzer import analyze_semantic_coverage

_HASH = "d" * 64
_REQUIRED_HASHES = {
    "artifacts/02-canonical": _HASH,
    "artifacts/03-dependencies.json": _HASH,
    "artifacts/04-semantic-graph.json": _HASH,
    "artifacts/06-candidates.json": _HASH,
}


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


def _program(statement: CanonicalStatement) -> CanonicalProgram:
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN, statements=[statement]
    )
    return CanonicalProgram(
        program_name="P1",
        source_file="p1.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[paragraph],
    )


def _classification_for(statement: CanonicalStatement) -> ConstructCoverage:
    report = analyze_semantic_coverage(
        canonical_programs=[_program(statement)],
        dependency_artifact=DependencyArtifact(run_id="run-1", source_package_hash=_HASH),
        semantic_graph=SemanticGraph(source_package_hash=_HASH),
        candidate_artifact=CandidateArtifact(
            run_id="run-1",
            source_package_hash=_HASH,
            semantic_graph_hash=_HASH,
            invariants_query_hash=_HASH,
            q0_query_hash=_HASH,
        ),
        run_id="run-1",
        source_package_hash=_HASH,
        source_artifact_hashes=_REQUIRED_HASHES,
    )
    entries = [
        entry
        for program in report.programs
        for entry in program.construct_coverage
        if entry.construct_name == "CALL"
    ]
    assert len(entries) == 1, f"esperaba exactamente 1 entrada CALL: {entries}"
    return entries[0]


def test_fully_captured_literal_call_is_fully_supported() -> None:
    entry = _classification_for(_statement())
    assert entry.support_status == SemanticSupportStatus.FULLY_SUPPORTED
    assert entry.diagnostic_code == "CALL_LITERAL_CAPTURED"
    assert entry.candidate_impact == CandidateImpact.NONE


def test_fully_captured_literal_call_with_complete_argument_is_fully_supported() -> None:
    entry = _classification_for(_statement(call_arguments=[_argument(CallPassingMode.REFERENCE)]))
    assert entry.support_status == SemanticSupportStatus.FULLY_SUPPORTED
    assert entry.diagnostic_code == "CALL_LITERAL_CAPTURED"


def test_dynamic_target_takes_priority_over_everything_else() -> None:
    """Fase 6, orden de prioridad: target dinamico se clasifica ANTES que
    argumentos/RETURNING, aunque ambos tambien esten incompletos."""
    unresolved = CanonicalCallArgument(
        ordinal=1,
        expression="<unsupported>",
        passing_mode=CallPassingMode.UNKNOWN,
        omitted=False,
        location_kind=LocationKind.UNKNOWN,
    )
    entry = _classification_for(
        _statement(
            call_target_kind=CallTargetKind.DYNAMIC,
            called_program_name=None,
            called_program_expression="WS-PROGRAM-NAME",
            call_arguments=[unresolved],
            call_returning_data_item="WS-RESULT",
        )
    )
    assert entry.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert entry.diagnostic_code == "CALL_DYNAMIC_TARGET_UNRESOLVED"
    assert entry.candidate_impact == CandidateImpact.MEDIUM


def test_unknown_target_kind_also_maps_to_dynamic_target_category() -> None:
    entry = _classification_for(
        _statement(call_target_kind=CallTargetKind.UNKNOWN, called_program_name=None)
    )
    assert entry.diagnostic_code == "CALL_DYNAMIC_TARGET_UNRESOLVED"


def test_incomplete_argument_takes_priority_over_returning() -> None:
    unresolved = CanonicalCallArgument(
        ordinal=1,
        expression="<unsupported>",
        passing_mode=CallPassingMode.UNKNOWN,
        omitted=False,
        location_kind=LocationKind.UNKNOWN,
    )
    entry = _classification_for(
        _statement(call_arguments=[unresolved], call_returning_data_item="WS-RESULT")
    )
    assert entry.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert entry.diagnostic_code == "CALL_ARGUMENT_PARTIAL"
    assert entry.candidate_impact == CandidateImpact.LOW


def test_returning_alone_is_partially_supported() -> None:
    entry = _classification_for(_statement(call_returning_data_item="WS-RESULT"))
    assert entry.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert entry.diagnostic_code == "CALL_RETURNING_PARTIAL"
    assert entry.candidate_impact == CandidateImpact.LOW


def test_on_exception_clause_alone_still_reaches_the_final_fallback_category() -> None:
    """Sin target dinamico, sin argumentos incompletos, sin RETURNING:
    ON EXCEPTION por si solo cae en la categoria final (impacto NONE),
    nunca degrada las tres verificaciones anteriores."""
    entry = _classification_for(_statement(call_has_on_exception=True))
    assert entry.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert entry.diagnostic_code == "CALL_EXCEPTION_BRANCHES_NOT_MODELED"
    assert entry.candidate_impact == CandidateImpact.NONE
