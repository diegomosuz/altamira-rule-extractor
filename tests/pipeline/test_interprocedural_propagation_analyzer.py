"""Tests del analizador PURO de propagacion interprocedural conservadora
en shadow mode (Fase 7 de la ampliacion semantica,
`feat/interprocedural-propagation-shadow`):
`pipeline/interprocedural_propagation_analyzer.py`. Encadena los
analizadores reales de Fase 2-6 (`analyze_semantic_effects`,
`analyze_semantic_propagation`, `analyze_interprocedural_call_linkage`)
como entrada del analizador bajo prueba -- nunca fabrica un
`SemanticPropagationArtifact`/`InterproceduralCallLinkageArtifact` a
mano, salvo el unico caso explicitamente marcado (programa ambiguo, ver
seccion dedicada) donde la cadena real es estructuralmente
inalcanzable."""

from __future__ import annotations

import copy

from altamira_extractor.contracts.canonical import (
    CanonicalCallArgument,
    CanonicalDataItem,
    CanonicalEntryParameter,
    CanonicalLinkageDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    CallPassingMode,
    CallTargetKind,
    LocationKind,
    ProgramTerminationKind,
    SourceFormat,
    StatementKind,
)
from altamira_extractor.contracts.interprocedural_call_linkage import (
    InterproceduralAnalysisSummary,
    InterproceduralCallLinkageArtifact,
    InterproceduralCallSite,
    InterproceduralSourceReference,
    ProgramInterface,
    ProgramResolutionStatus,
)
from altamira_extractor.contracts.interprocedural_propagation import (
    InterproceduralFactKind,
    InterproceduralPropagationArtifact,
    InterproceduralPropagationBarrier,
    InterproceduralPropagationDirection,
    InterproceduralPropagationFact,
    InterproceduralPropagationStatus,
)
from altamira_extractor.contracts.semantic_coverage import SemanticSupportStatus
from altamira_extractor.contracts.semantic_effects import SemanticEffectsArtifact
from altamira_extractor.contracts.semantic_propagation import SemanticPropagationArtifact
from altamira_extractor.pipeline.interprocedural_call_linkage_analyzer import (
    analyze_interprocedural_call_linkage,
)
from altamira_extractor.pipeline.interprocedural_propagation_analyzer import (
    analyze_interprocedural_propagation,
)
from altamira_extractor.pipeline.semantic_effects_analyzer import analyze_semantic_effects
from altamira_extractor.pipeline.semantic_propagation_analyzer import analyze_semantic_propagation

HASH = "b" * 64
HASHES = {"artifacts/02-canonical": HASH}


def make_stmt(**overrides: object) -> CanonicalStatement:
    fields: dict[str, object] = {
        "statement_id": "P::A::0::MOVE",
        "kind": StatementKind.MOVE,
        "source_text": "MOVE",
        "location_kind": LocationKind.UNKNOWN,
    }
    fields.update(overrides)
    return CanonicalStatement(**fields)  # type: ignore[arg-type]


def make_move(stmt_id: str, *, target: str, literal: str) -> CanonicalStatement:
    return make_stmt(
        statement_id=stmt_id,
        kind=StatementKind.MOVE,
        target_data_items=[target],
        variables_written=[target],
        assigned_literal=literal,
    )


def make_terminator(
    stmt_id: str,
    termination_kind: ProgramTerminationKind,
    *,
    parent_statement_id: str | None = None,
) -> CanonicalStatement:
    return make_stmt(
        statement_id=stmt_id,
        kind=StatementKind.PROGRAM_TERMINATION,
        source_text=termination_kind.value,
        program_termination_kind=termination_kind,
        parent_statement_id=parent_statement_id,
    )


def make_call_arg(name: str, mode: CallPassingMode, ordinal: int = 1) -> CanonicalCallArgument:
    return CanonicalCallArgument(
        ordinal=ordinal,
        expression=name,
        data_item_name=name,
        qualified_data_item_name=name,
        passing_mode=mode,
        location_kind=LocationKind.UNKNOWN,
    )


def make_call(
    stmt_id: str,
    *,
    called_program_name: str | None = "CALLEE",
    call_target_kind: CallTargetKind = CallTargetKind.LITERAL,
    called_program_expression: str | None = None,
    call_arguments: list[CanonicalCallArgument] | None = None,
    call_returning_data_item: str | None = None,
) -> CanonicalStatement:
    return make_stmt(
        statement_id=stmt_id,
        kind=StatementKind.CALL,
        source_text="CALL",
        call_target_kind=call_target_kind,
        called_program_name=called_program_name,
        called_program_expression=called_program_expression,
        call_arguments=call_arguments or [],
        call_returning_data_item=call_returning_data_item,
    )


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def make_paragraph(name: str, statements: list[CanonicalStatement]) -> CanonicalParagraph:
    return CanonicalParagraph(
        name=name,
        source_text=f"{name}.",
        location_kind=LocationKind.UNKNOWN,
        statements=statements,
        variables_read=_ordered_unique([v for stmt in statements for v in stmt.variables_read]),
        variables_written=_ordered_unique(
            [v for stmt in statements for v in stmt.variables_written]
        ),
    )


def make_program(
    name: str,
    *,
    paragraphs: list[CanonicalParagraph] | None = None,
    data_items: list[CanonicalDataItem] | None = None,
    linkage_data_items: list[CanonicalLinkageDataItem] | None = None,
    entry_parameters: list[CanonicalEntryParameter] | None = None,
    entry_returning_data_item: str | None = None,
) -> CanonicalProgram:
    return CanonicalProgram(
        schema_version="1.2",
        program_name=name,
        source_file=f"01-codigo/cobol/{name}.cbl",
        source_hash=HASH,
        source_package_hash=HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=data_items or [],
        paragraphs=paragraphs if paragraphs is not None else [make_paragraph("MAIN", [])],
        linkage_data_items=linkage_data_items or [],
        entry_parameters=entry_parameters or [],
        entry_returning_data_item=entry_returning_data_item,
    )


def make_data_item(name: str) -> CanonicalDataItem:
    return CanonicalDataItem(
        name=name, qualified_name=name, level=1, location_kind=LocationKind.UNKNOWN
    )


def make_linkage_item(name: str) -> CanonicalLinkageDataItem:
    return CanonicalLinkageDataItem(
        name=name, qualified_name=name, level=1, pic="X(10)", location_kind=LocationKind.UNKNOWN
    )


def make_entry_param(
    name: str, ordinal: int = 1, mode: CallPassingMode = CallPassingMode.REFERENCE
) -> CanonicalEntryParameter:
    return CanonicalEntryParameter(
        ordinal=ordinal,
        name=name,
        qualified_name=name,
        linkage_item_qualified_name=name,
        passing_mode=mode,
        location_kind=LocationKind.UNKNOWN,
    )


def analyze_all(
    programs: list[CanonicalProgram], *, run_id: str = "run1"
) -> tuple[
    InterproceduralPropagationArtifact,
    SemanticEffectsArtifact,
    SemanticPropagationArtifact,
    InterproceduralCallLinkageArtifact,
]:
    effects = analyze_semantic_effects(
        canonical_programs=programs,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    propagation = analyze_semantic_propagation(
        canonical_programs=programs,
        semantic_effects=effects,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    linkage = analyze_interprocedural_call_linkage(
        canonical_programs=programs,
        semantic_effects=effects,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    artifact = analyze_interprocedural_propagation(
        canonical_programs=programs,
        semantic_effects=effects,
        semantic_propagation=propagation,
        interprocedural_call_linkage=linkage,
        run_id=run_id,
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    return artifact, effects, propagation, linkage


def _fact_for(
    artifact: InterproceduralPropagationArtifact, call_site_id: str, suffix: str
) -> InterproceduralPropagationFact:
    matches = [f for f in artifact.facts if f.fact_id == f"fact::{call_site_id}::{suffix}"]
    assert len(matches) == 1, f"esperaba 1 fact para {call_site_id}::{suffix}: {matches}"
    return matches[0]


def _entry_fact(
    artifact: InterproceduralPropagationArtifact, call_site_id: str, ordinal: int = 1
) -> InterproceduralPropagationFact:
    return _fact_for(artifact, call_site_id, f"entry::{ordinal}")


def _return_fact(
    artifact: InterproceduralPropagationArtifact, call_site_id: str, label: str
) -> InterproceduralPropagationFact:
    return _fact_for(artifact, call_site_id, f"return::{label}")


# --- 1/4. BY CONTENT: propaga actual->formal, nunca devuelve modificaciones ---


def test_by_content_propagates_actual_to_formal() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-A", literal="0005"),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.CONTENT)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    entry = _entry_fact(artifact, call_site_id)
    assert entry.status == InterproceduralPropagationStatus.PROPAGATED
    assert entry.literal == "0005"
    assert entry.actual_name == "WS-A"
    assert entry.formal_name == "LK-A"
    assert entry.direction == InterproceduralPropagationDirection.CALLER_TO_CALLEE
    assert entry.kind == InterproceduralFactKind.ENTRY_FACT


def test_by_content_never_returns_modifications() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-A", literal="0005"),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.CONTENT)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    return_facts = [
        f for f in artifact.facts if f.fact_id.startswith(f"fact::{call_site_id}::return")
    ]
    assert return_facts == []


# --- 2/5. BY VALUE: mismo comportamiento que BY CONTENT ------------------------


def test_by_value_propagates_actual_to_formal() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-A", literal="0007"),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.VALUE)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    entry = _entry_fact(artifact, call_site_id)
    assert entry.status == InterproceduralPropagationStatus.PROPAGATED
    assert entry.literal == "0007"


def test_by_value_never_returns_modifications() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-A", literal="0007"),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.VALUE)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    return_facts = [
        f for f in artifact.facts if f.fact_id.startswith(f"fact::{call_site_id}::return")
    ]
    assert return_facts == []


# --- 3/6/7. BY REFERENCE: entrada + salida (determinista o invalidada) --------


def test_by_reference_propagates_entry_fact() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-A", literal="0005"),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.REFERENCE)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    entry = _entry_fact(artifact, call_site_id)
    assert entry.status == InterproceduralPropagationStatus.PROPAGATED
    assert entry.literal == "0005"
    assert "ENTRY_BY_REFERENCE_POTENTIALLY_MUTABLE" in entry.diagnostics


def test_by_reference_returns_deterministic_literal() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-A", literal="0005"),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.REFERENCE)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_move("CALLEE::MAIN::0::MOVE", target="LK-A", literal="9999")]
            )
        ],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "1")
    assert ret.status == InterproceduralPropagationStatus.PROPAGATED
    assert ret.kind == InterproceduralFactKind.BY_REFERENCE_OUTPUT
    assert ret.literal == "9999"
    assert ret.direction == InterproceduralPropagationDirection.CALLEE_TO_CALLER


def test_by_reference_invalidates_on_unknown_output() -> None:
    """El callee SI modifica su formal, pero via COMPUTE (nunca evaluado
    aritmeticamente por SemanticPropagation, ver
    docs/SEMANTIC_PROPAGATION.md): el valor final nunca es un literal
    deterministico demostrable -- se invalida el argumento real, nunca
    se asume que permanece igual al valor de entrada."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-A", literal="0005"),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.REFERENCE)],
                    ),
                ],
            )
        ],
    )
    compute_stmt = make_stmt(
        statement_id="CALLEE::MAIN::0::COMPUTE",
        kind=StatementKind.COMPUTE,
        source_text="COMPUTE LK-A = LK-A + 1",
        expression="LK-A + 1",
        target_data_items=["LK-A"],
        variables_written=["LK-A"],
        variables_read=["LK-A"],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
        paragraphs=[make_paragraph("MAIN", [compute_stmt])],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "1")
    assert ret.status == InterproceduralPropagationStatus.INVALIDATED
    assert ret.kind == InterproceduralFactKind.INVALIDATION
    assert ret.literal is None


# --- 8/9. RETURNING: determinista o invalidado por multiples valores ---------


def test_returning_returns_deterministic_literal() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R")]
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[
            make_paragraph(
                "MAIN", [make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009")]
            )
        ],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    assert ret.status == InterproceduralPropagationStatus.PROPAGATED
    assert ret.kind == InterproceduralFactKind.RETURNING_FACT
    assert ret.literal == "0009"


def test_returning_survives_a_trailing_goback() -> None:
    """GOBACK final, incondicional y unico (Fase 7b): retorna control
    normalmente al caller -- se recorta EXACTAMENTE ese statement, sin
    invalidar el MOVE inmediatamente anterior. Ver
    `_effective_exit_cutoff`."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R")]
            )
        ],
    )
    goback = make_terminator("CALLEE::MAIN::1::PROGRAM_TERMINATION", ProgramTerminationKind.GOBACK)
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[
            make_paragraph(
                "MAIN",
                [make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009"), goback],
            )
        ],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    assert ret.status == InterproceduralPropagationStatus.PROPAGATED
    assert ret.literal == "0009"


def test_returning_survives_a_trailing_exit_program() -> None:
    """EXIT PROGRAM final, incondicional y unico: identico a GOBACK --
    retorna control normalmente al caller."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R")]
            )
        ],
    )
    exit_program = make_terminator(
        "CALLEE::MAIN::1::PROGRAM_TERMINATION", ProgramTerminationKind.EXIT_PROGRAM
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[
            make_paragraph(
                "MAIN",
                [make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009"), exit_program],
            )
        ],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    assert ret.status == InterproceduralPropagationStatus.PROPAGATED
    assert ret.literal == "0009"


def test_returning_blocked_by_a_trailing_stop_run() -> None:
    """STOP RUN final, incondicional y unico: CERTEZA estructural de que
    el callee nunca retorna control al caller (termina el run unit
    completo) -- BLOCKED explicito con NON_RETURNING_TERMINATION, nunca
    PROPAGATED ni un simple INVALIDATED por falta de evidencia."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R")]
            )
        ],
    )
    stop_run = make_terminator(
        "CALLEE::MAIN::1::PROGRAM_TERMINATION", ProgramTerminationKind.STOP_RUN
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[
            make_paragraph(
                "MAIN",
                [make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009"), stop_run],
            )
        ],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    assert ret.status == InterproceduralPropagationStatus.BLOCKED
    assert ret.literal is None
    assert ret.barriers == [InterproceduralPropagationBarrier.NON_RETURNING_TERMINATION]
    assert ret.kind == InterproceduralFactKind.RETURNING_FACT


def test_by_reference_output_blocked_by_a_trailing_stop_run() -> None:
    """Mismo bloqueo que RETURNING, pero para el hecho de salida de un
    argumento BY REFERENCE."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "CALLER::MAIN::0::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.REFERENCE)],
                    )
                ],
            )
        ],
    )
    stop_run = make_terminator(
        "CALLEE::MAIN::1::PROGRAM_TERMINATION", ProgramTerminationKind.STOP_RUN
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [make_move("CALLEE::MAIN::0::MOVE", target="LK-A", literal="9999"), stop_run],
            )
        ],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    output = _return_fact(artifact, call_site_id, "1")
    assert output.status == InterproceduralPropagationStatus.BLOCKED
    assert output.literal is None
    assert output.barriers == [InterproceduralPropagationBarrier.NON_RETURNING_TERMINATION]
    assert output.kind == InterproceduralFactKind.BY_REFERENCE_OUTPUT


def test_returning_still_invalidates_when_a_non_trailing_other_statement_touches_it() -> None:
    """El recorte de `_effective_exit_cutoff` es EXCLUSIVAMENTE para el
    UNICO terminador final calificado -- un `OTHER` intermedio (nunca un
    terminador) sigue invalidando el entorno normalmente, sin excepcion,
    incluso cuando el terminador final SI califica para recorte."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R")]
            )
        ],
    )
    middle_other = make_stmt(
        statement_id="CALLEE::MAIN::1::OTHER",
        kind=StatementKind.OTHER,
        source_text="MOVE CORRESPONDING WS-GROUP TO LK-GROUP",
    )
    trailing_goback = make_terminator(
        "CALLEE::MAIN::2::PROGRAM_TERMINATION", ProgramTerminationKind.GOBACK
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009"),
                    middle_other,
                    trailing_goback,
                ],
            )
        ],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    assert ret.status == InterproceduralPropagationStatus.INVALIDATED
    assert ret.literal is None
    assert ret.actual_name == "WS-R"


def test_returning_invalidates_on_multiple_possible_values() -> None:
    """El receptor formal solo se asigna DENTRO de un IF (condicional):
    nunca hay un unico literal deterministico demostrable al retornar --
    se invalida el receptor real."""
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R")]
            )
        ],
    )
    if_stmt = make_stmt(
        statement_id="CALLEE::MAIN::0::IF",
        kind=StatementKind.IF,
        source_text="IF X",
        expression="X",
    )
    move_in_branch = make_stmt(
        statement_id="CALLEE::MAIN::1::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE",
        target_data_items=["LK-R"],
        variables_written=["LK-R"],
        assigned_literal="0001",
        parent_statement_id="CALLEE::MAIN::0::IF",
        branch_kind="THEN",
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[make_paragraph("MAIN", [if_stmt, move_in_branch])],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    assert ret.status == InterproceduralPropagationStatus.INVALIDATED
    assert ret.literal is None


# --- Terminadores estructurales (Fase 7b): condicionales, no finales, --------
# --- multiples y anidados en IF/EVALUATE/PERFORM inline nunca se recortan ---


def _terminator_callee(*, statements: list[CanonicalStatement]) -> CanonicalProgram:
    return make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-R")],
        entry_returning_data_item="LK-R",
        paragraphs=[make_paragraph("MAIN", statements)],
    )


def _terminator_caller() -> CanonicalProgram:
    return make_program(
        "CALLER",
        data_items=[make_data_item("WS-R")],
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", call_returning_data_item="WS-R")]
            )
        ],
    )


def test_conditional_goback_is_never_trimmed() -> None:
    """GOBACK dentro de un IF (condicional, `parent_statement_id`
    poblado) como ultimo statement del flat list: NUNCA se trata como
    retorno final deterministico, sin importar que sea GOBACK -- no se
    recorta, se invalida por falta de evidencia (nunca BLOCKED: no hay
    certeza de no-retorno, solo ausencia de prueba)."""
    if_stmt = make_stmt(
        statement_id="CALLEE::MAIN::0::IF", kind=StatementKind.IF, source_text="IF X",
        expression="X",
    )
    # El MOVE vive DENTRO de la misma rama THEN que el GOBACK (nunca a
    # nivel superior): no hay ningun hecho top-level para LK-R, asi que
    # la busqueda de salida (parent_scope=None) no encuentra nada,
    # exactamente como si el GOBACK no existiera en absoluto.
    move_in_branch = make_stmt(
        statement_id="CALLEE::MAIN::1::MOVE", kind=StatementKind.MOVE, source_text="MOVE",
        target_data_items=["LK-R"], variables_written=["LK-R"], assigned_literal="0009",
        parent_statement_id="CALLEE::MAIN::0::IF", branch_kind="THEN",
    )
    conditional_goback = make_terminator(
        "CALLEE::MAIN::2::PROGRAM_TERMINATION",
        ProgramTerminationKind.GOBACK,
        parent_statement_id="CALLEE::MAIN::0::IF",
    )
    callee = _terminator_callee(statements=[if_stmt, move_in_branch, conditional_goback])
    artifact, *_ = analyze_all([_terminator_caller(), callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    assert ret.status == InterproceduralPropagationStatus.INVALIDATED
    assert ret.literal is None


def test_exit_program_inside_if_is_never_trimmed() -> None:
    """Mismo caso que arriba, con EXIT PROGRAM en vez de GOBACK."""
    if_stmt = make_stmt(
        statement_id="CALLEE::MAIN::0::IF", kind=StatementKind.IF, source_text="IF X",
        expression="X",
    )
    move_in_branch = make_stmt(
        statement_id="CALLEE::MAIN::1::MOVE", kind=StatementKind.MOVE, source_text="MOVE",
        target_data_items=["LK-R"], variables_written=["LK-R"], assigned_literal="0009",
        parent_statement_id="CALLEE::MAIN::0::IF", branch_kind="THEN",
    )
    conditional_exit = make_terminator(
        "CALLEE::MAIN::2::PROGRAM_TERMINATION",
        ProgramTerminationKind.EXIT_PROGRAM,
        parent_statement_id="CALLEE::MAIN::0::IF",
    )
    callee = _terminator_callee(statements=[if_stmt, move_in_branch, conditional_exit])
    artifact, *_ = analyze_all([_terminator_caller(), callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    assert ret.status == InterproceduralPropagationStatus.INVALIDATED
    assert ret.literal is None


def test_stop_run_inside_evaluate_is_blocked_never_trimmed() -> None:
    """STOP RUN dentro de un EVALUATE (condicional): sigue sin
    considerarse un retorno final -- no se recorta. El resultado es
    INVALIDATED (falta de evidencia de un valor final), nunca PROPAGATED:
    la certeza de "STOP RUN nunca retorna" solo se afirma via BLOCKED
    cuando STOP RUN es el UNICO terminador final incondicional."""
    evaluate_stmt = make_stmt(
        statement_id="CALLEE::MAIN::0::EVALUATE", kind=StatementKind.EVALUATE,
        source_text="EVALUATE X", expression="X",
    )
    move_in_branch = make_stmt(
        statement_id="CALLEE::MAIN::1::MOVE", kind=StatementKind.MOVE, source_text="MOVE",
        target_data_items=["LK-R"], variables_written=["LK-R"], assigned_literal="0009",
        parent_statement_id="CALLEE::MAIN::0::EVALUATE", branch_kind="WHEN",
    )
    conditional_stop = make_terminator(
        "CALLEE::MAIN::2::PROGRAM_TERMINATION",
        ProgramTerminationKind.STOP_RUN,
        parent_statement_id="CALLEE::MAIN::0::EVALUATE",
    )
    callee = _terminator_callee(statements=[evaluate_stmt, move_in_branch, conditional_stop])
    artifact, *_ = analyze_all([_terminator_caller(), callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    assert ret.status == InterproceduralPropagationStatus.INVALIDATED
    assert ret.literal is None
    assert ret.barriers == []


def test_terminator_inside_inline_perform_is_never_trimmed() -> None:
    """Un terminador dentro de un PERFORM inline (parent_statement_id
    apunta al PERFORM, sin branch_kind -- ver
    StatementExtractor.convertPerform) nunca se trata como retorno final
    deterministico."""
    perform_stmt = make_stmt(
        statement_id="CALLEE::MAIN::0::PERFORM", kind=StatementKind.PERFORM,
        source_text="PERFORM UNTIL X",
    )
    move_in_perform = make_stmt(
        statement_id="CALLEE::MAIN::1::MOVE", kind=StatementKind.MOVE, source_text="MOVE",
        target_data_items=["LK-R"], variables_written=["LK-R"], assigned_literal="0009",
        parent_statement_id="CALLEE::MAIN::0::PERFORM",
    )
    inline_goback = make_terminator(
        "CALLEE::MAIN::2::PROGRAM_TERMINATION",
        ProgramTerminationKind.GOBACK,
        parent_statement_id="CALLEE::MAIN::0::PERFORM",
    )
    callee = _terminator_callee(statements=[perform_stmt, move_in_perform, inline_goback])
    artifact, *_ = analyze_all([_terminator_caller(), callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    assert ret.status == InterproceduralPropagationStatus.INVALIDATED
    assert ret.literal is None


def test_non_final_terminator_is_never_trimmed() -> None:
    """Un GOBACK que NO es el ultimo statement (sentencia muerta despues,
    caso raro pero estructuralmente valido) nunca activa el recorte --
    el corte permanece en el largo total de la lista, gobernado por lo
    que realmente sea el ultimo statement."""
    goback = make_terminator("CALLEE::MAIN::0::PROGRAM_TERMINATION", ProgramTerminationKind.GOBACK)
    move_after = make_move("CALLEE::MAIN::1::MOVE", target="LK-R", literal="0009")
    callee = _terminator_callee(statements=[goback, move_after])
    artifact, *_ = analyze_all([_terminator_caller(), callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    # El GOBACK no final es irrelevante para el corte: el MOVE final (sin
    # ningun terminador que lo siga) se ve exactamente igual, y su
    # literal SI se demuestra -- prueba de que el GOBACK previo nunca
    # disparo un recorte fuera de lugar.
    assert ret.status == InterproceduralPropagationStatus.PROPAGATED
    assert ret.literal == "0009"


def test_two_consecutive_top_level_terminators_never_invent_an_exit() -> None:
    """Dos terminadores consecutivos, ambos top-level (GOBACK seguido de
    otro GOBACK -- codigo muerto pero estructuralmente valido): NUNCA se
    elige uno arbitrariamente. `terminator_count != 1` bloquea el
    recorte por completo, incluso cuando el ultimo, tomado aisladamente,
    parecería calificar."""
    move_before = make_move("CALLEE::MAIN::0::MOVE", target="LK-R", literal="0009")
    first_goback = make_terminator(
        "CALLEE::MAIN::1::PROGRAM_TERMINATION", ProgramTerminationKind.GOBACK
    )
    second_goback = make_terminator(
        "CALLEE::MAIN::2::PROGRAM_TERMINATION", ProgramTerminationKind.GOBACK
    )
    callee = _terminator_callee(statements=[move_before, first_goback, second_goback])
    artifact, *_ = analyze_all([_terminator_caller(), callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    assert ret.status == InterproceduralPropagationStatus.INVALIDATED
    assert ret.literal is None


def test_two_branches_with_different_literals_before_goback_never_invent_an_exit() -> None:
    """Dos caminos posibles (THEN/ELSE) con literales DISTINTOS asignados
    antes de un GOBACK propio en cada rama: dos terminadores en total
    (uno por rama) -- nunca se elige arbitrariamente uno de los dos
    caminos, se bloquea el recorte por completo."""
    if_stmt = make_stmt(
        statement_id="CALLEE::MAIN::0::IF", kind=StatementKind.IF, source_text="IF X",
        expression="X",
    )
    move_then = make_stmt(
        statement_id="CALLEE::MAIN::1::MOVE", kind=StatementKind.MOVE, source_text="MOVE",
        target_data_items=["LK-R"], variables_written=["LK-R"], assigned_literal="0001",
        parent_statement_id="CALLEE::MAIN::0::IF", branch_kind="THEN",
    )
    goback_then = make_terminator(
        "CALLEE::MAIN::2::PROGRAM_TERMINATION",
        ProgramTerminationKind.GOBACK,
        parent_statement_id="CALLEE::MAIN::0::IF",
    )
    move_else = make_stmt(
        statement_id="CALLEE::MAIN::3::MOVE", kind=StatementKind.MOVE, source_text="MOVE",
        target_data_items=["LK-R"], variables_written=["LK-R"], assigned_literal="0002",
        parent_statement_id="CALLEE::MAIN::0::IF", branch_kind="ELSE",
    )
    goback_else = make_terminator(
        "CALLEE::MAIN::4::PROGRAM_TERMINATION",
        ProgramTerminationKind.GOBACK,
        parent_statement_id="CALLEE::MAIN::0::IF",
    )
    callee = _terminator_callee(
        statements=[if_stmt, move_then, goback_then, move_else, goback_else]
    )
    artifact, *_ = analyze_all([_terminator_caller(), callee])
    call_site_id = artifact.facts[0].call_site_id
    ret = _return_fact(artifact, call_site_id, "returning")
    assert ret.status == InterproceduralPropagationStatus.INVALIDATED
    assert ret.literal is None


# --- 10-19. Barreras -----------------------------------------------------------


def test_dynamic_call_is_blocked() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-PROG")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "CALLER::MAIN::0::CALL",
                        call_target_kind=CallTargetKind.DYNAMIC,
                        called_program_name=None,
                        called_program_expression="WS-PROG",
                    )
                ],
            )
        ],
    )
    artifact, *_ = analyze_all([caller])
    pa = next(p for p in artifact.program_analyses if p.program == "CALLER")
    assert len(pa.blocked_call_sites) == 1
    assert artifact.summary.blocked_call_count == 1
    assert artifact.facts == []


def test_missing_program_is_blocked() -> None:
    caller = make_program(
        "CALLER",
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("CALLER::MAIN::0::CALL", called_program_name="ABSENT")]
            )
        ],
    )
    artifact, *_ = analyze_all([caller])
    pa = next(p for p in artifact.program_analyses if p.program == "CALLER")
    assert len(pa.blocked_call_sites) == 1
    assert "BLOCKED_CALL_SITES_INCLUDE_MISSING_PROGRAM" in pa.diagnostics


def test_ambiguous_program_is_blocked() -> None:
    """AMBIGUOUS_PROGRAM (dos versiones del mismo programa en el mismo
    paquete) es inalcanzable end-to-end a traves de la cadena real
    (`SemanticEffectsArtifact` ya exige `program_name` unico -- mismo
    hallazgo que la auditoria de Fase 6, ver docs/
    INTERPROCEDURAL_CALL_LINKAGE.md): se construye un
    `InterproceduralCallLinkageArtifact` directamente con un call site
    `AMBIGUOUS_PROGRAM` para probar la barrera en aislamiento."""
    caller = make_program("CALLER")
    source_ref = InterproceduralSourceReference(program="CALLER", paragraph="MAIN")
    call_site = InterproceduralCallSite(
        call_site_id="callsite::x",
        caller_program="CALLER",
        caller_paragraph="MAIN",
        statement_id="CALLER::MAIN::0::CALL",
        target_kind=CallTargetKind.LITERAL,
        declared_target="CALLEE",
        resolution_status=ProgramResolutionStatus.AMBIGUOUS_PROGRAM,
        resolved_callee_program=None,
        arguments=[],
        support_status=SemanticSupportStatus.PARTIALLY_SUPPORTED,
        source_reference=source_ref,
    )
    linkage = InterproceduralCallLinkageArtifact(
        canonical_schema_versions=["1.2"],
        semantic_effects_schema_version="1.2",
        semantic_effects_analyzer_version="1.2",
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
        summary=InterproceduralAnalysisSummary(
            program_count=1,
            interface_count=1,
            call_site_count=1,
            resolved_internal_count=0,
            dynamic_count=0,
            missing_program_count=0,
            ambiguous_program_count=1,
            recursive_call_count=0,
            cycle_count=0,
            binding_count=0,
            resolved_binding_count=0,
            unresolved_binding_count=0,
            counts_by_resolution_status={ProgramResolutionStatus.AMBIGUOUS_PROGRAM: 1},
            counts_by_binding_status={},
        ),
        interfaces=[ProgramInterface(program="CALLER", parameters=[], linkage_item_count=0)],
        call_sites=[call_site],
        call_edges=[],
        cycles=[],
    )
    effects = analyze_semantic_effects(
        canonical_programs=[caller],
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    propagation = analyze_semantic_propagation(
        canonical_programs=[caller],
        semantic_effects=effects,
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    artifact = analyze_interprocedural_propagation(
        canonical_programs=[caller],
        semantic_effects=effects,
        semantic_propagation=propagation,
        interprocedural_call_linkage=linkage,
        run_id="run1",
        source_package_hash=HASH,
        source_artifact_hashes=HASHES,
    )
    pa = next(p for p in artifact.program_analyses if p.program == "CALLER")
    assert pa.blocked_call_sites == ["callsite::x"]
    assert "BLOCKED_CALL_SITES_INCLUDE_AMBIGUOUS_PROGRAM" in pa.diagnostics


def test_self_call_is_blocked() -> None:
    caller = make_program(
        "SELFPROG",
        paragraphs=[
            make_paragraph(
                "MAIN", [make_call("SELFPROG::MAIN::0::CALL", called_program_name="SELFPROG")]
            )
        ],
    )
    artifact, *_ = analyze_all([caller])
    pa = next(p for p in artifact.program_analyses if p.program == "SELFPROG")
    assert len(pa.blocked_call_sites) == 1
    assert "BLOCKED_CALL_SITES_INCLUDE_RECURSION" in pa.diagnostics


def test_scc_is_blocked() -> None:
    prog_a = make_program(
        "PROGA",
        paragraphs=[
            make_paragraph("MAIN", [make_call("PROGA::MAIN::0::CALL", called_program_name="PROGB")])
        ],
    )
    prog_b = make_program(
        "PROGB",
        paragraphs=[
            make_paragraph("MAIN", [make_call("PROGB::MAIN::0::CALL", called_program_name="PROGA")])
        ],
    )
    artifact, *_ = analyze_all([prog_a, prog_b])
    pa_a = next(p for p in artifact.program_analyses if p.program == "PROGA")
    pa_b = next(p for p in artifact.program_analyses if p.program == "PROGB")
    assert len(pa_a.blocked_call_sites) == 1
    assert len(pa_b.blocked_call_sites) == 1
    assert "BLOCKED_CALL_SITES_INCLUDE_CYCLE" in pa_a.diagnostics
    assert artifact.facts == []


def test_missing_argument_is_blocked() -> None:
    caller = make_program(
        "CALLER",
        paragraphs=[
            make_paragraph("MAIN", [make_call("CALLER::MAIN::0::CALL", call_arguments=[])])
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    entry = _entry_fact(artifact, call_site_id)
    assert entry.status == InterproceduralPropagationStatus.BLOCKED
    assert InterproceduralPropagationBarrier.MISSING_ARGUMENT in entry.barriers


def test_extra_argument_is_blocked() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A"), make_data_item("WS-B")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "CALLER::MAIN::0::CALL",
                        call_arguments=[
                            make_call_arg("WS-A", CallPassingMode.REFERENCE, ordinal=1),
                            make_call_arg("WS-B", CallPassingMode.REFERENCE, ordinal=2),
                        ],
                    )
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    extra = _entry_fact(artifact, call_site_id, ordinal=2)
    assert extra.status == InterproceduralPropagationStatus.BLOCKED
    assert InterproceduralPropagationBarrier.EXTRA_ARGUMENT in extra.barriers


def test_unresolved_formal_is_blocked() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "CALLER::MAIN::0::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.REFERENCE)],
                    )
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        entry_parameters=[
            CanonicalEntryParameter(
                ordinal=1,
                name="LK-UNRESOLVED",
                qualified_name="LK-UNRESOLVED",
                linkage_item_qualified_name=None,
                location_kind=LocationKind.UNKNOWN,
            )
        ],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    entry = _entry_fact(artifact, call_site_id)
    assert entry.status == InterproceduralPropagationStatus.BLOCKED
    assert InterproceduralPropagationBarrier.UNRESOLVED_FORMAL in entry.barriers


def test_ambiguous_actual_is_blocked() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-DUP")],
        linkage_data_items=[make_linkage_item("WS-DUP")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "CALLER::MAIN::0::CALL",
                        call_arguments=[
                            CanonicalCallArgument(
                                ordinal=1,
                                expression="WS-DUP",
                                data_item_name="WS-DUP",
                                qualified_data_item_name=None,
                                passing_mode=CallPassingMode.REFERENCE,
                                location_kind=LocationKind.UNKNOWN,
                            )
                        ],
                    )
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    entry = _entry_fact(artifact, call_site_id)
    assert entry.status == InterproceduralPropagationStatus.BLOCKED
    assert InterproceduralPropagationBarrier.UNRESOLVED_ACTUAL in entry.barriers


def test_unknown_passing_mode_is_blocked() -> None:
    # `data_item_name` presente (identidad resoluble) pero `passing_mode`
    # UNKNOWN: distinto del caso "<unsupported>" (sin identidad alguna,
    # que Fase 6 clasifica ACTUAL_UNRESOLVED antes de llegar al modo de
    # paso) -- esta combinacion prueba especificamente la barrera
    # UNKNOWN_PASSING_MODE de Fase 7 en aislamiento.
    unknown_mode_argument = CanonicalCallArgument(
        ordinal=1,
        expression="WS-A",
        data_item_name="WS-A",
        qualified_data_item_name="WS-A",
        passing_mode=CallPassingMode.UNKNOWN,
        location_kind=LocationKind.UNKNOWN,
    )
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [make_call("CALLER::MAIN::0::CALL", call_arguments=[unknown_mode_argument])],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    artifact, *_ = analyze_all([caller, callee])
    call_site_id = artifact.facts[0].call_site_id
    entry = _entry_fact(artifact, call_site_id)
    assert entry.status == InterproceduralPropagationStatus.BLOCKED
    assert InterproceduralPropagationBarrier.UNKNOWN_PASSING_MODE in entry.barriers


# --- 20-23. Orden topologico, multiples callers, cadena A->B->C, sin fixed point


def test_topological_order_is_stable_regardless_of_input_order() -> None:
    def build() -> list[CanonicalProgram]:
        prog_a = make_program(
            "PROGA",
            data_items=[make_data_item("WS-A")],
            paragraphs=[
                make_paragraph(
                    "MAIN",
                    [
                        make_move("PROGA::MAIN::0::MOVE", target="WS-A", literal="0005"),
                        make_call(
                            "PROGA::MAIN::1::CALL",
                            called_program_name="PROGB",
                            call_arguments=[make_call_arg("WS-A", CallPassingMode.CONTENT)],
                        ),
                    ],
                )
            ],
        )
        prog_b = make_program(
            "PROGB",
            linkage_data_items=[make_linkage_item("LK-A")],
            entry_parameters=[make_entry_param("LK-A")],
        )
        return [prog_a, prog_b]

    forward, *_ = analyze_all(build())
    programs = build()
    backward, *_ = analyze_all([programs[1], programs[0]])
    assert forward.to_stable_json() == backward.to_stable_json()


def test_multiple_callers_to_same_callee() -> None:
    prog_a = make_program(
        "PROGA",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("PROGA::MAIN::0::MOVE", target="WS-A", literal="0005"),
                    make_call(
                        "PROGA::MAIN::1::CALL",
                        called_program_name="PROGC",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.CONTENT)],
                    ),
                ],
            )
        ],
    )
    prog_b = make_program(
        "PROGB",
        data_items=[make_data_item("WS-B")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("PROGB::MAIN::0::MOVE", target="WS-B", literal="0005"),
                    make_call(
                        "PROGB::MAIN::1::CALL",
                        called_program_name="PROGC",
                        call_arguments=[make_call_arg("WS-B", CallPassingMode.CONTENT)],
                    ),
                ],
            )
        ],
    )
    prog_c = make_program(
        "PROGC",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    artifact, *_ = analyze_all([prog_a, prog_b, prog_c])
    pa_c = next(p for p in artifact.program_analyses if p.program == "PROGC")
    assert len(pa_c.entry_facts) == 2
    assert all(f.status == InterproceduralPropagationStatus.PROPAGATED for f in pa_c.entry_facts)
    assert all(f.literal == "0005" for f in pa_c.entry_facts)


def test_conflicting_caller_values_never_propagate_and_emit_explicit_diagnostic() -> None:
    """Parte 3 (auditoria de cierre): cuando dos callers distintos
    aportan literales DIFERENTES para el mismo formal, el valor nunca se
    propaga -- pero, a diferencia de versiones anteriores de esta fase,
    NUNCA en silencio. Verifica: (1) ningun literal se propaga al nivel
    2 de `_known_literal_at`; (2) un diagnostico explicito aparece en el
    `InterproceduralProgramAnalysis` del callee afectado; (3) el
    resultado se refleja en `summary` como UNRESOLVED, nunca como un
    PROPAGATED inventado; (4) los `source_fact_ids`/`literal` de cada
    ENTRY_FACT individual (uno por caller) siguen disponibles para
    rastrear la discrepancia exacta."""
    prog_a = make_program(
        "PROGA",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("PROGA::MAIN::0::MOVE", target="WS-A", literal="A"),
                    make_call(
                        "PROGA::MAIN::1::CALL",
                        called_program_name="PROGC",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.CONTENT)],
                    ),
                ],
            )
        ],
    )
    prog_b = make_program(
        "PROGB",
        data_items=[make_data_item("WS-B")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("PROGB::MAIN::0::MOVE", target="WS-B", literal="B"),
                    make_call(
                        "PROGB::MAIN::1::CALL",
                        called_program_name="PROGC",
                        call_arguments=[make_call_arg("WS-B", CallPassingMode.CONTENT)],
                    ),
                ],
            )
        ],
    )
    # PROGD llama a PROGC DESPUES (orden topologico: A y B antes que C,
    # C antes que D) y reenvia LK-A sin tocarlo -- si el entorno de
    # entrada de PROGC "inventara" un consenso a pesar del conflicto,
    # este tercer nivel lo demostraria con un ENTRY_FACT PROPAGATED
    # inexistente. En cambio debe quedar UNRESOLVED.
    prog_c = make_program(
        "PROGC",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "PROGC::MAIN::0::CALL",
                        called_program_name="PROGD",
                        call_arguments=[make_call_arg("LK-A", CallPassingMode.CONTENT)],
                    )
                ],
            )
        ],
    )
    prog_d = make_program(
        "PROGD",
        linkage_data_items=[make_linkage_item("LK-D")],
        entry_parameters=[make_entry_param("LK-D")],
    )
    artifact, *_ = analyze_all([prog_a, prog_b, prog_c, prog_d])

    pa_c = next(p for p in artifact.program_analyses if p.program == "PROGC")
    assert len(pa_c.entry_facts) == 2
    literals_seen = sorted(f.literal for f in pa_c.entry_facts)
    assert literals_seen == ["A", "B"]
    assert all(f.status == InterproceduralPropagationStatus.PROPAGATED for f in pa_c.entry_facts)
    # Cada ENTRY_FACT individual sigue siendo trazable a su propio caller
    # y a su propio PropagatedValueFact de origen -- la discrepancia es
    # reconstruible sin campos nuevos.
    assert {f.caller_program for f in pa_c.entry_facts} == {"PROGA", "PROGB"}
    assert all(f.source_fact_ids for f in pa_c.entry_facts)

    # (2) Diagnostico explicito, nunca silencioso.
    assert "MULTIPLE_CALLER_VALUES_FOR_LK-A" in pa_c.diagnostics

    # (1)/(3) PROGC->PROGD nunca "inventa" un literal a partir del
    # conflicto: el ENTRY_FACT de PROGD queda UNRESOLVED, y summary lo
    # refleja (nunca un PROPAGATED fantasma).
    pa_d = next(p for p in artifact.program_analyses if p.program == "PROGD")
    assert len(pa_d.entry_facts) == 1
    forwarded = pa_d.entry_facts[0]
    assert forwarded.status == InterproceduralPropagationStatus.UNRESOLVED
    assert forwarded.literal is None
    assert artifact.summary.counts_by_status[InterproceduralPropagationStatus.UNRESOLVED] >= 1


def test_chain_a_to_b_to_c_propagates_literal_to_formal() -> None:
    prog_a = make_program(
        "PROGA",
        data_items=[make_data_item("WS-LIT")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("PROGA::MAIN::0::MOVE", target="WS-LIT", literal="0005"),
                    make_call(
                        "PROGA::MAIN::1::CALL",
                        called_program_name="PROGB",
                        call_arguments=[make_call_arg("WS-LIT", CallPassingMode.CONTENT)],
                    ),
                ],
            )
        ],
    )
    prog_b = make_program(
        "PROGB",
        linkage_data_items=[make_linkage_item("LK-IN")],
        entry_parameters=[make_entry_param("LK-IN")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_call(
                        "PROGB::MAIN::0::CALL",
                        called_program_name="PROGC",
                        call_arguments=[make_call_arg("LK-IN", CallPassingMode.REFERENCE)],
                    )
                ],
            )
        ],
    )
    prog_c = make_program(
        "PROGC",
        linkage_data_items=[make_linkage_item("LK-C")],
        entry_parameters=[make_entry_param("LK-C")],
    )
    artifact, *_ = analyze_all([prog_a, prog_b, prog_c])
    pa_c = next(p for p in artifact.program_analyses if p.program == "PROGC")
    assert len(pa_c.entry_facts) == 1
    assert pa_c.entry_facts[0].status == InterproceduralPropagationStatus.PROPAGATED
    assert pa_c.entry_facts[0].literal == "0005"


def test_no_fixed_point_over_cycles() -> None:
    """Un ciclo A<->B nunca se itera hasta converger: ambos call sites
    quedan bloqueados de inmediato (barrier=CYCLE), sin ningun intento
    de propagar valores entre ellos, sin importar cuantas veces se
    ejecute el analizador."""
    prog_a = make_program(
        "PROGA",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("PROGA::MAIN::0::MOVE", target="WS-A", literal="0005"),
                    make_call(
                        "PROGA::MAIN::1::CALL",
                        called_program_name="PROGB",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.CONTENT)],
                    ),
                ],
            )
        ],
    )
    prog_b = make_program(
        "PROGB",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
        paragraphs=[
            make_paragraph("MAIN", [make_call("PROGB::MAIN::0::CALL", called_program_name="PROGA")])
        ],
    )
    artifact, *_ = analyze_all([prog_a, prog_b])
    assert artifact.facts == []
    assert artifact.summary.propagated_call_count == 0
    assert artifact.summary.eligible_call_count == 0
    assert artifact.summary.blocked_call_count == 2


# --- 26. Input no modificado ------------------------------------------------


def test_analysis_never_mutates_input_programs() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-A", literal="0005"),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.CONTENT)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    caller_before = copy.deepcopy(caller)
    callee_before = copy.deepcopy(callee)
    analyze_all([caller, callee])
    assert caller == caller_before
    assert callee == callee_before


# --- 24/25 (apoyo, ver tambien tests/contracts/): IDs deterministicos y ------
# serializacion byte a byte --------------------------------------------------


def test_fact_ids_are_deterministic_across_runs() -> None:
    caller = make_program(
        "CALLER",
        data_items=[make_data_item("WS-A")],
        paragraphs=[
            make_paragraph(
                "MAIN",
                [
                    make_move("CALLER::MAIN::0::MOVE", target="WS-A", literal="0005"),
                    make_call(
                        "CALLER::MAIN::1::CALL",
                        call_arguments=[make_call_arg("WS-A", CallPassingMode.CONTENT)],
                    ),
                ],
            )
        ],
    )
    callee = make_program(
        "CALLEE",
        linkage_data_items=[make_linkage_item("LK-A")],
        entry_parameters=[make_entry_param("LK-A")],
    )
    artifact_1, *_ = analyze_all([caller, callee])
    artifact_2, *_ = analyze_all([caller, callee])
    assert [f.fact_id for f in artifact_1.facts] == [f.fact_id for f in artifact_2.facts]
    assert artifact_1.to_stable_json() == artifact_2.to_stable_json()
