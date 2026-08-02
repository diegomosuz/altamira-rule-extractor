"""Tests de CALL como barrera de propagacion (Fase 6/9 de la ampliacion
semantica, `pipeline/semantic_propagation_analyzer.py::_handle_call_program`).

Formaliza los 4 casos obligatorios A-D del contrato de propagacion
interprocedural (ver docs/SEMANTIC_PROPAGATION.md /
docs/INTERPROCEDURAL_CALL_LINKAGE.md, seccion "CALL como barrera"):

A. BY REFERENCE invalida el argumento; el literal previo nunca sobrevive
   ni se propaga a traves del CALL.
B. BY CONTENT preserva el valor propio del caller (nunca se invalida
   solo por pasarse) y nunca se propaga informacion DESDE el callee.
C. RETURNING invalida al receptor -- nunca se inventa el valor que
   devolveria el subprograma.
D. Un CALL dinamico nunca resuelve su target usando el valor propagado
   del identificador (la propagacion y la resolucion de programa son
   completamente independientes: `_handle_call_program` nunca inspecciona
   `called_program_expression`, solo `call_arguments`/
   `call_returning_data_item`).

Mismo patron de helpers que `test_semantic_propagation_analyzer.py`,
mantenido self-contained en este archivo dedicado."""

from __future__ import annotations

from altamira_extractor.contracts.canonical import (
    CanonicalCallArgument,
    CanonicalDataItem,
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
from altamira_extractor.contracts.semantic_effects import (
    SemanticEffectKind,
    SemanticEffectsArtifact,
)
from altamira_extractor.contracts.semantic_propagation import (
    PropagatedValueFact,
    PropagationBarrier,
    PropagationBarrierReason,
    PropagationFactKind,
    SemanticPropagationArtifact,
)
from altamira_extractor.pipeline.semantic_effects_analyzer import analyze_semantic_effects
from altamira_extractor.pipeline.semantic_propagation_analyzer import analyze_semantic_propagation

_HASH = "f" * 64
_REQUIRED_HASHES = {"artifacts/02-canonical": _HASH}


def _statement(**overrides: object) -> CanonicalStatement:
    defaults: dict[str, object] = {
        "statement_id": "P1::A::0::MOVE",
        "kind": StatementKind.MOVE,
        "source_text": "MOVE",
        "location_kind": LocationKind.UNKNOWN,
    }
    defaults.update(overrides)
    return CanonicalStatement(**defaults)  # type: ignore[arg-type]


def _move(
    stmt_id: str, *, target: str, literal: str | None = None, source: str | None = None
) -> CanonicalStatement:
    fields: dict[str, object] = {
        "statement_id": stmt_id,
        "kind": StatementKind.MOVE,
        "source_text": "MOVE",
        "target_data_items": [target],
        "variables_written": [target],
    }
    if literal is not None:
        fields["assigned_literal"] = literal
    if source is not None:
        fields["variables_read"] = [source]
    return _statement(**fields)


def _call(
    stmt_id: str,
    *,
    target_kind: CallTargetKind = CallTargetKind.LITERAL,
    program_name: str | None = "SUBPROG",
    program_expression: str | None = None,
    arguments: list[CanonicalCallArgument] | None = None,
    returning: str | None = None,
) -> CanonicalStatement:
    return _statement(
        statement_id=stmt_id,
        kind=StatementKind.CALL,
        source_text="CALL",
        call_target_kind=target_kind,
        called_program_name=program_name,
        called_program_expression=program_expression,
        call_arguments=arguments or [],
        call_returning_data_item=returning,
    )


def _argument(name: str, passing_mode: CallPassingMode, ordinal: int = 1) -> CanonicalCallArgument:
    return CanonicalCallArgument(
        ordinal=ordinal,
        expression=name,
        data_item_name=name,
        qualified_data_item_name=name,
        passing_mode=passing_mode,
        location_kind=LocationKind.UNKNOWN,
    )


def _paragraph(name: str, statements: list[CanonicalStatement]) -> CanonicalParagraph:
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


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _data_item(name: str) -> CanonicalDataItem:
    return CanonicalDataItem(
        name=name, qualified_name=name, level=1, location_kind=LocationKind.UNKNOWN
    )


def _program(
    name: str,
    paragraphs: list[CanonicalParagraph],
    data_items: list[CanonicalDataItem] | None = None,
) -> CanonicalProgram:
    return CanonicalProgram(
        program_name=name,
        source_file=f"{name.lower()}.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=data_items or [],
        paragraphs=paragraphs,
    )


def _propagate(
    programs: list[CanonicalProgram],
) -> tuple[SemanticPropagationArtifact, SemanticEffectsArtifact]:
    effects = analyze_semantic_effects(
        canonical_programs=programs,
        run_id="run-1",
        source_package_hash=_HASH,
        source_artifact_hashes=_REQUIRED_HASHES,
    )
    artifact = analyze_semantic_propagation(
        canonical_programs=programs,
        semantic_effects=effects,
        run_id="run-1",
        source_package_hash=_HASH,
        source_artifact_hashes=_REQUIRED_HASHES,
    )
    return artifact, effects


def _facts_of(artifact: SemanticPropagationArtifact) -> list[PropagatedValueFact]:
    return [fact for program in artifact.programs for fact in program.facts]


def _barriers_of(artifact: SemanticPropagationArtifact) -> list[PropagationBarrier]:
    return [barrier for program in artifact.programs for barrier in program.barriers]


def _facts_for(facts: list[PropagatedValueFact], target: str) -> list[PropagatedValueFact]:
    return [f for f in facts if f.target_variable == target]


# --- Caso A: BY REFERENCE invalida, el literal previo nunca sobrevive --------


def test_case_a_by_reference_argument_invalidates_and_blocks_further_propagation() -> None:
    s1 = _move("P1::A::0::MOVE", target="WS-A", literal="0005")
    s2 = _call("P1::A::1::CALL", arguments=[_argument("WS-A", CallPassingMode.REFERENCE)])
    s3 = _move("P1::A::2::MOVE", target="WS-B", source="WS-A")
    program = _program(
        "P1", [_paragraph("A", [s1, s2, s3])], data_items=[_data_item("WS-A"), _data_item("WS-B")]
    )

    artifact, _ = _propagate([program])
    facts = _facts_of(artifact)
    barriers = _barriers_of(artifact)

    ws_a_facts = _facts_for(facts, "WS-A")
    assert any(
        f.fact_kind == PropagationFactKind.DIRECT_LITERAL and f.literal == "0005"
        for f in ws_a_facts
    )
    assert any(
        f.fact_kind == PropagationFactKind.INVALIDATED_VALUE
        and "CALL_ARGUMENT_BY_REFERENCE_INVALIDATED" in f.diagnostic_codes
        for f in ws_a_facts
    )

    call_barriers = [b for b in barriers if b.reason == PropagationBarrierReason.CALL_BOUNDARY]
    assert len(call_barriers) == 1
    assert call_barriers[0].affected_variables == ["WS-A"]
    assert call_barriers[0].clears_entire_environment is False

    ws_b_fact = _facts_for(facts, "WS-B")[0]
    assert ws_b_fact.fact_kind == PropagationFactKind.UNRESOLVED_COPY
    assert ws_b_fact.literal is None


def test_case_a_returning_and_by_reference_together_invalidate_both() -> None:
    s1 = _move("P1::A::0::MOVE", target="WS-A", literal="0005")
    s2 = _move("P1::A::1::MOVE", target="WS-R", literal="9999")
    s3 = _call(
        "P1::A::2::CALL",
        arguments=[_argument("WS-A", CallPassingMode.REFERENCE)],
        returning="WS-R",
    )
    program = _program(
        "P1",
        [_paragraph("A", [s1, s2, s3])],
        data_items=[_data_item("WS-A"), _data_item("WS-R")],
    )
    artifact, _ = _propagate([program])
    barriers = _barriers_of(artifact)
    call_barriers = [b for b in barriers if b.reason == PropagationBarrierReason.CALL_BOUNDARY]
    assert len(call_barriers) == 1
    assert call_barriers[0].affected_variables == ["WS-A", "WS-R"]


# --- Caso B: BY CONTENT preserva el valor propio, sin propagacion desde el callee


def test_case_b_by_content_argument_preserves_caller_own_value() -> None:
    s1 = _move("P1::A::0::MOVE", target="WS-A", literal="0005")
    s2 = _call("P1::A::1::CALL", arguments=[_argument("WS-A", CallPassingMode.CONTENT)])
    s3 = _move("P1::A::2::MOVE", target="WS-B", source="WS-A")
    program = _program(
        "P1",
        [_paragraph("A", [s1, s2, s3])],
        data_items=[_data_item("WS-A"), _data_item("WS-B")],
    )
    artifact, _ = _propagate([program])
    facts = _facts_of(artifact)
    barriers = _barriers_of(artifact)

    ws_a_facts = _facts_for(facts, "WS-A")
    assert len(ws_a_facts) == 1
    assert ws_a_facts[0].fact_kind == PropagationFactKind.DIRECT_LITERAL
    assert not any(b.reason == PropagationBarrierReason.CALL_BOUNDARY for b in barriers)

    ws_b_fact = _facts_for(facts, "WS-B")[0]
    assert ws_b_fact.fact_kind == PropagationFactKind.PROPAGATED_LITERAL
    assert ws_b_fact.literal == "0005"


def test_case_b_by_value_argument_also_preserves_caller_own_value() -> None:
    s1 = _move("P1::A::0::MOVE", target="WS-A", literal="0007")
    s2 = _call("P1::A::1::CALL", arguments=[_argument("WS-A", CallPassingMode.VALUE)])
    s3 = _move("P1::A::2::MOVE", target="WS-B", source="WS-A")
    program = _program(
        "P1",
        [_paragraph("A", [s1, s2, s3])],
        data_items=[_data_item("WS-A"), _data_item("WS-B")],
    )
    artifact, _ = _propagate([program])
    barriers = _barriers_of(artifact)
    assert not any(b.reason == PropagationBarrierReason.CALL_BOUNDARY for b in barriers)
    ws_b_fact = _facts_for(_facts_of(artifact), "WS-B")[0]
    assert ws_b_fact.literal == "0007"


def test_case_b_call_never_registers_empty_barrier_when_nothing_is_invalidated() -> None:
    """CALL sin argumentos BY REFERENCE/UNKNOWN identificables ni
    RETURNING nunca registra un PropagationBarrier vacio -- sigue siendo
    conceptualmente una barrera (regla 1), pero sin efecto observable."""
    s1 = _call("P1::A::0::CALL", arguments=[])
    program = _program("P1", [_paragraph("A", [s1])])
    artifact, _ = _propagate([program])
    assert _barriers_of(artifact) == []
    assert _facts_of(artifact) == []


# --- Caso C: RETURNING invalida al receptor -----------------------------------


def test_case_c_returning_invalidates_receiver_and_blocks_further_propagation() -> None:
    s1 = _move("P1::A::0::MOVE", target="WS-RESULT", literal="OLD-VALUE")
    s2 = _call("P1::A::1::CALL", arguments=[], returning="WS-RESULT")
    s3 = _move("P1::A::2::MOVE", target="WS-COPY", source="WS-RESULT")
    program = _program(
        "P1",
        [_paragraph("A", [s1, s2, s3])],
        data_items=[_data_item("WS-RESULT"), _data_item("WS-COPY")],
    )
    artifact, _ = _propagate([program])
    facts = _facts_of(artifact)
    barriers = _barriers_of(artifact)

    result_facts = _facts_for(facts, "WS-RESULT")
    assert any(
        f.fact_kind == PropagationFactKind.INVALIDATED_VALUE
        and "CALL_RETURNING_INVALIDATED" in f.diagnostic_codes
        for f in result_facts
    )
    call_barriers = [b for b in barriers if b.reason == PropagationBarrierReason.CALL_BOUNDARY]
    assert len(call_barriers) == 1
    assert call_barriers[0].affected_variables == ["WS-RESULT"]

    copy_fact = _facts_for(facts, "WS-COPY")[0]
    assert copy_fact.fact_kind == PropagationFactKind.UNRESOLVED_COPY
    assert copy_fact.literal is None


def test_case_c_returning_into_variable_with_no_prior_known_value_still_invalidates() -> None:
    """Aun sin un valor previo conocido, RETURNING sigue produciendo un
    fact INVALIDATED_VALUE (nunca se omite solo porque el entorno ya no
    tenia informacion sobre ese target)."""
    s1 = _call("P1::A::0::CALL", arguments=[], returning="WS-RESULT")
    program = _program("P1", [_paragraph("A", [s1])], data_items=[_data_item("WS-RESULT")])
    artifact, _ = _propagate([program])
    facts = _facts_of(artifact)
    assert len(facts) == 1
    assert facts[0].fact_kind == PropagationFactKind.INVALIDATED_VALUE
    assert facts[0].target_variable == "WS-RESULT"


# --- Caso D: CALL dinamico nunca resuelve el target via valor propagado ------


def test_case_d_dynamic_call_target_identifier_keeps_its_propagated_value_untouched() -> None:
    """El identificador dinamico (`called_program_expression`) nunca se
    trata como argumento USING: `_handle_call_program` jamas lo inspecciona
    ni lo invalida, y el analizador interprocedural (Fase 11, ver
    `test_interprocedural_call_linkage_analyzer.py::
    test_dynamic_call_target_never_resolved_even_when_matching_program_name_exists`)
    nunca usa su valor conocido para resolver el programa invocado -- las
    dos fases son completamente independientes."""
    s1 = _move("P1::A::0::MOVE", target="WS-PROGRAM-NAME", literal="PROGX")
    s2 = _call(
        "P1::A::1::CALL",
        target_kind=CallTargetKind.DYNAMIC,
        program_name=None,
        program_expression="WS-PROGRAM-NAME",
        arguments=[],
    )
    s3 = _move("P1::A::2::MOVE", target="WS-COPY", source="WS-PROGRAM-NAME")
    program = _program(
        "P1",
        [_paragraph("A", [s1, s2, s3])],
        data_items=[_data_item("WS-PROGRAM-NAME"), _data_item("WS-COPY")],
    )
    artifact, effects = _propagate([program])

    # La sentencia CALL en si misma nunca genera fact/barrier: no tiene
    # argumentos USING ni RETURNING que invalidar (regla de la funcion de
    # transferencia, Fase 9).
    assert _barriers_of(artifact) == []

    # El valor conocido de WS-PROGRAM-NAME (establecido ANTES del CALL)
    # sobrevive intacto y sigue siendo propagable DESPUES del CALL --
    # nunca se usa para "resolver" el target, pero tampoco se invalida
    # solo por haber sido el target de un CALL dinamico.
    copy_fact = _facts_for(_facts_of(artifact), "WS-COPY")[0]
    assert copy_fact.fact_kind == PropagationFactKind.PROPAGATED_LITERAL
    assert copy_fact.literal == "PROGX"

    # El SemanticEffect de la llamada conserva el identificador dinamico
    # tal cual, sin ninguna sustitucion por el valor propagado conocido.
    call_effect = next(
        e for e in effects.programs[0].effects if e.kind == SemanticEffectKind.CALL_PROGRAM
    )
    assert call_effect.call_target_kind == CallTargetKind.DYNAMIC
    assert call_effect.called_program_expression == "WS-PROGRAM-NAME"
    assert call_effect.called_program_name is None


def test_case_d_dynamic_call_with_by_reference_argument_still_invalidates_that_argument() -> None:
    """El target dinamico no se resuelve, pero eso no exime a los
    argumentos USING reales de la invalidacion BY REFERENCE habitual
    (Caso A) -- ambas reglas son independientes y se aplican juntas."""
    s1 = _move("P1::A::0::MOVE", target="WS-PROGRAM-NAME", literal="PROGX")
    s2 = _move("P1::A::1::MOVE", target="WS-ARG", literal="0001")
    s3 = _call(
        "P1::A::2::CALL",
        target_kind=CallTargetKind.DYNAMIC,
        program_name=None,
        program_expression="WS-PROGRAM-NAME",
        arguments=[_argument("WS-ARG", CallPassingMode.REFERENCE)],
    )
    program = _program(
        "P1",
        [_paragraph("A", [s1, s2, s3])],
        data_items=[_data_item("WS-PROGRAM-NAME"), _data_item("WS-ARG")],
    )
    artifact, _ = _propagate([program])
    barriers = _barriers_of(artifact)
    call_barriers = [b for b in barriers if b.reason == PropagationBarrierReason.CALL_BOUNDARY]
    assert len(call_barriers) == 1
    assert call_barriers[0].affected_variables == ["WS-ARG"]
    assert call_barriers[0].diagnostic_code == "CALL_DYNAMIC_TARGET_UNRESOLVED"


# --- Caso extra: argumento sin forma estructural limpia TODO el entorno ------


def test_call_argument_with_unresolvable_shape_clears_entire_environment() -> None:
    """Un argumento USING sin forma estructural identificable (ni
    identificador, ni literal, ni OMITTED) es el unico caso en que CALL
    limpia el entorno COMPLETO, no solo su propia posicion -- no hay nada
    conservador que razonar sobre ese argumento en particular."""
    s1 = _move("P1::A::0::MOVE", target="WS-A", literal="0005")
    s2 = _move("P1::A::1::MOVE", target="WS-B", literal="0006")
    unresolved_argument = CanonicalCallArgument(
        ordinal=1,
        expression="<unsupported>",
        passing_mode=CallPassingMode.UNKNOWN,
        omitted=False,
        location_kind=LocationKind.UNKNOWN,
    )
    s3 = _call("P1::A::2::CALL", arguments=[unresolved_argument])
    program = _program(
        "P1",
        [_paragraph("A", [s1, s2, s3])],
        data_items=[_data_item("WS-A"), _data_item("WS-B")],
    )
    artifact, _ = _propagate([program])
    barriers = _barriers_of(artifact)
    call_barriers = [b for b in barriers if b.reason == PropagationBarrierReason.CALL_BOUNDARY]
    assert len(call_barriers) == 1
    assert call_barriers[0].clears_entire_environment is True
    assert call_barriers[0].affected_variables == ["WS-A", "WS-B"]
