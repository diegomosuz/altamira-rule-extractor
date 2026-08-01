"""Tests del analizador PURO de propagacion limitada de constantes y
copias (Fase 4 de la ampliacion semantica, `feat/constant-copy-
propagation`): `pipeline/semantic_propagation_analyzer.py`. Nunca Neo4j,
nunca LLM, nunca filesystem -- todo se construye en memoria. Mismo patron
de helpers que `test_semantic_effects_analyzer.py`, encadenando
`analyze_semantic_effects` (Fase 2/3, sin modificar) como entrada del
analizador de propagacion bajo prueba."""

from __future__ import annotations

from altamira_extractor.contracts.canonical import (
    CanonicalConditionName,
    CanonicalConditionValue,
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalSqlAccess,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    LocationKind,
    SourceFormat,
    StatementKind,
    TableAccessOperation,
)
from altamira_extractor.contracts.semantic_propagation import (
    PropagationBarrierReason,
    PropagationFactKind,
)
from altamira_extractor.pipeline.semantic_effects_analyzer import analyze_semantic_effects
from altamira_extractor.pipeline.semantic_propagation_analyzer import analyze_semantic_propagation

_HASH = "e" * 64
_REQUIRED_HASHES = {"artifacts/02-canonical": _HASH}


def _statement(**overrides: object) -> CanonicalStatement:
    defaults: dict[str, object] = {
        "statement_id": "P1::A::1::MOVE",
        "kind": StatementKind.MOVE,
        "source_text": "MOVE 'X' TO WS-TARGET",
        "location_kind": LocationKind.UNKNOWN,
    }
    defaults.update(overrides)
    return CanonicalStatement(**defaults)  # type: ignore[arg-type]


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _paragraph(
    name: str, statements: list[CanonicalStatement], **overrides: object
) -> CanonicalParagraph:
    defaults: dict[str, object] = {
        "name": name,
        "source_text": f"{name}.",
        "location_kind": LocationKind.UNKNOWN,
        "statements": statements,
        "variables_read": _ordered_unique([v for stmt in statements for v in stmt.variables_read]),
        "variables_written": _ordered_unique(
            [v for stmt in statements for v in stmt.variables_written]
        ),
        "sql_access": [access for stmt in statements for access in stmt.sql_access],
    }
    defaults.update(overrides)
    return CanonicalParagraph(**defaults)  # type: ignore[arg-type]


def _data_item(name: str, **overrides: object) -> CanonicalDataItem:
    defaults: dict[str, object] = {
        "name": name,
        "qualified_name": name,
        "level": 1,
        "location_kind": LocationKind.UNKNOWN,
    }
    defaults.update(overrides)
    return CanonicalDataItem(**defaults)  # type: ignore[arg-type]


def _program(
    name: str, paragraphs: list[CanonicalParagraph], **overrides: object
) -> CanonicalProgram:
    defaults: dict[str, object] = {
        "program_name": name,
        "source_file": f"{name.lower()}.cbl",
        "source_hash": _HASH,
        "source_package_hash": _HASH,
        "source_format": SourceFormat.FIXED,
        "encoding": "UTF-8",
        "paragraphs": paragraphs,
    }
    defaults.update(overrides)
    return CanonicalProgram(**defaults)  # type: ignore[arg-type]


def _propagate(programs: list[CanonicalProgram]):
    effects = analyze_semantic_effects(
        canonical_programs=programs,
        run_id="run-1",
        source_package_hash=_HASH,
        source_artifact_hashes=_REQUIRED_HASHES,
    )
    return analyze_semantic_propagation(
        canonical_programs=programs,
        semantic_effects=effects,
        run_id="run-1",
        source_package_hash=_HASH,
        source_artifact_hashes=_REQUIRED_HASHES,
    )


def _facts_of(programs: list[CanonicalProgram]) -> list:
    artifact = _propagate(programs)
    return [fact for program in artifact.programs for fact in program.facts]


def _barriers_of(programs: list[CanonicalProgram]) -> list:
    artifact = _propagate(programs)
    return [barrier for program in artifact.programs for barrier in program.barriers]


def _fact_for(facts: list, target: str):
    matches = [f for f in facts if f.target_variable == target]
    assert len(matches) == 1, f"esperaba exactamente un fact para {target!r}, hubo {len(matches)}"
    return matches[0]


def _move(stmt_id: str, *, target: str, literal: str | None = None, source: str | None = None,
          **overrides: object) -> CanonicalStatement:
    fields: dict[str, object] = {
        "statement_id": stmt_id, "kind": StatementKind.MOVE, "source_text": "MOVE",
        "target_data_items": [target], "variables_written": [target],
    }
    if literal is not None:
        fields["assigned_literal"] = literal
    if source is not None:
        fields["variables_read"] = [source]
    fields.update(overrides)
    return _statement(**fields)


# ---------------------------------------------------------------------------
# Casos obligatorios exactos (spec Fase 13)
# ---------------------------------------------------------------------------


def test_move_literal_then_move_copy_demonstrates_full_chain() -> None:
    s1 = _move("P1::A::1::MOVE", target="WS-COD-AUX", literal="0005")
    s2 = _move("P1::A::2::MOVE", target="WS-COD-RETORNO", source="WS-COD-AUX")
    program = _program(
        "P1",
        [_paragraph("A", [s1, s2])],
        data_items=[_data_item("WS-COD-AUX"), _data_item("WS-COD-RETORNO")],
    )
    facts = _facts_of([program])
    aux = _fact_for(facts, "WS-COD-AUX")
    ret = _fact_for(facts, "WS-COD-RETORNO")

    assert aux.fact_kind == PropagationFactKind.DIRECT_LITERAL
    assert aux.literal == "0005"
    assert ret.fact_kind == PropagationFactKind.PROPAGATED_LITERAL
    assert ret.literal == "0005"
    assert ret.derivation_depth == 2
    assert len(ret.derivation_steps) == 2

    # SemanticEffects nunca cambia: sigue exactamente un ASSIGN_LITERAL y
    # un COPY_VALUE, sin efecto nuevo agregado por la propagacion.
    effects = analyze_semantic_effects(
        canonical_programs=[program], run_id="run-1", source_package_hash=_HASH,
        source_artifact_hashes=_REQUIRED_HASHES,
    )
    kinds = sorted(e.kind.value for e in effects.programs[0].effects)
    assert kinds == ["ASSIGN_LITERAL", "COPY_VALUE"]


def test_level_88_set_true_then_move_copy_demonstrates_full_chain() -> None:
    condition = CanonicalConditionName(
        name="COD-AUX-INVALIDO", qualified_name="WS-COD-AUX.COD-AUX-INVALIDO",
        parent_name="WS-COD-AUX", parent_qualified_name="WS-COD-AUX",
        values=[CanonicalConditionValue(value="0005", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    s1 = _statement(
        statement_id="P1::A::1::SET", kind=StatementKind.SET, source_text="SET X TO TRUE",
        target_data_items=["COD-AUX-INVALIDO"], variables_written=["COD-AUX-INVALIDO"],
        condition_name_target="COD-AUX-INVALIDO", condition_set_value=True,
    )
    s2 = _move("P1::A::2::MOVE", target="WS-COD-RETORNO", source="WS-COD-AUX")
    program = _program(
        "P1",
        [_paragraph("A", [s1, s2])],
        data_items=[_data_item("WS-COD-AUX", pic="X(4)"), _data_item("WS-COD-RETORNO")],
        condition_names=[condition],
    )
    facts = _facts_of([program])
    aux = _fact_for(facts, "WS-COD-AUX")
    ret = _fact_for(facts, "WS-COD-RETORNO")

    assert aux.fact_kind == PropagationFactKind.CONDITION_LITERAL
    assert aux.literal == "0005"
    assert ret.fact_kind == PropagationFactKind.PROPAGATED_LITERAL
    assert ret.literal == "0005"

    effects = analyze_semantic_effects(
        canonical_programs=[program], run_id="run-1", source_package_hash=_HASH,
        source_artifact_hashes=_REQUIRED_HASHES,
    )
    kinds = sorted(e.kind.value for e in effects.programs[0].effects)
    assert kinds == ["COPY_VALUE", "SET_CONDITION_TRUE"]


# ---------------------------------------------------------------------------
# 1-3: literal directo / copia sin valor / copia con valor conocido
# ---------------------------------------------------------------------------


def test_case_1_direct_literal() -> None:
    s1 = _move("P1::A::1::MOVE", target="WS-A", literal="X")
    program = _program("P1", [_paragraph("A", [s1])], data_items=[_data_item("WS-A")])
    fact = _fact_for(_facts_of([program]), "WS-A")
    assert fact.fact_kind == PropagationFactKind.DIRECT_LITERAL
    assert fact.literal == "X"
    assert fact.derivation_depth == 1


def test_case_2_copy_without_known_value() -> None:
    s1 = _move("P1::A::1::MOVE", target="WS-B", source="WS-A")
    program = _program(
        "P1", [_paragraph("A", [s1])], data_items=[_data_item("WS-A"), _data_item("WS-B")]
    )
    fact = _fact_for(_facts_of([program]), "WS-B")
    assert fact.fact_kind == PropagationFactKind.UNRESOLVED_COPY
    assert fact.literal is None
    assert fact.source_variable == "WS-A"


def test_case_3_copy_with_known_value() -> None:
    s1 = _move("P1::A::1::MOVE", target="WS-A", literal="X")
    s2 = _move("P1::A::2::MOVE", target="WS-B", source="WS-A")
    program = _program(
        "P1", [_paragraph("A", [s1, s2])], data_items=[_data_item("WS-A"), _data_item("WS-B")]
    )
    fact = _fact_for(_facts_of([program]), "WS-B")
    assert fact.fact_kind == PropagationFactKind.PROPAGATED_LITERAL
    assert fact.literal == "X"


# ---------------------------------------------------------------------------
# 4: cadena de 3+ copias, sin limite de dos saltos
# ---------------------------------------------------------------------------


def test_case_4_chain_of_four_copies() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="0005")
    s2 = _move("P1::A::2::MOVE", target="B", source="A")
    s3 = _move("P1::A::3::MOVE", target="C", source="B")
    s4 = _move("P1::A::4::MOVE", target="D", source="C")
    program = _program(
        "P1", [_paragraph("A", [s1, s2, s3, s4])],
        data_items=[_data_item(x) for x in ("A", "B", "C", "D")],
    )
    facts = _facts_of([program])
    fact_d = _fact_for(facts, "D")
    assert fact_d.fact_kind == PropagationFactKind.PROPAGATED_LITERAL
    assert fact_d.literal == "0005"
    assert fact_d.derivation_depth == 4
    assert len(fact_d.derivation_steps) == 4


# ---------------------------------------------------------------------------
# 5-8: sobrescritura / COMPUTE / source modificado despues / ciclo
# ---------------------------------------------------------------------------


def test_case_5_overwrite_before_copy() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="0005")
    s2 = _move("P1::A::2::MOVE", target="A", literal="0007")
    s3 = _move("P1::A::3::MOVE", target="B", source="A")
    program = _program(
        "P1", [_paragraph("A", [s1, s2, s3])], data_items=[_data_item("A"), _data_item("B")]
    )
    fact_b = _fact_for(_facts_of([program]), "B")
    assert fact_b.fact_kind == PropagationFactKind.PROPAGATED_LITERAL
    assert fact_b.literal == "0007"


def test_case_6_compute_invalidates() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="0005")
    s2 = _statement(
        statement_id="P1::A::2::COMPUTE", kind=StatementKind.COMPUTE,
        source_text="COMPUTE A = B + 1",
        target_data_items=["A"], variables_written=["A"], variables_read=["B"],
        expression="B + 1",
    )
    s3 = _move("P1::A::3::MOVE", target="C", source="A")
    program = _program(
        "P1", [_paragraph("A", [s1, s2, s3])],
        data_items=[_data_item("A"), _data_item("B"), _data_item("C")],
    )
    facts = _facts_of([program])
    fact_c = _fact_for(facts, "C")
    assert fact_c.fact_kind == PropagationFactKind.UNRESOLVED_COPY
    assert fact_c.literal is None
    barriers = _barriers_of([program])
    assert any(b.reason == PropagationBarrierReason.COMPUTED_VALUE for b in barriers)


def test_case_7_source_modified_after_copy_does_not_retroactively_change_target() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="0005")
    s2 = _move("P1::A::2::MOVE", target="B", source="A")
    s3 = _move("P1::A::3::MOVE", target="A", literal="0008")
    program = _program(
        "P1", [_paragraph("A", [s1, s2, s3])], data_items=[_data_item("A"), _data_item("B")]
    )
    facts = _facts_of([program])
    fact_b = _fact_for(facts, "B")
    assert fact_b.fact_kind == PropagationFactKind.PROPAGATED_LITERAL
    assert fact_b.literal == "0005"
    fact_a_facts = [f for f in facts if f.target_variable == "A"]
    assert any(f.literal == "0008" for f in fact_a_facts)


def test_case_8_copy_cycle_does_not_recurse_infinitely() -> None:
    s1 = _move("P1::A::1::MOVE", target="B", source="A")
    s2 = _move("P1::A::2::MOVE", target="A", source="B")
    program = _program(
        "P1", [_paragraph("A", [s1, s2])], data_items=[_data_item("A"), _data_item("B")]
    )
    facts = _facts_of([program])
    assert all(f.fact_kind == PropagationFactKind.UNRESOLVED_COPY for f in facts)
    assert all(f.literal is None for f in facts)


# ---------------------------------------------------------------------------
# 9: multiples targets
# ---------------------------------------------------------------------------


def test_case_9_multiple_targets_each_gets_own_fact() -> None:
    s1 = _statement(
        statement_id="P1::A::1::MOVE", kind=StatementKind.MOVE, source_text="MOVE 'X' TO A B",
        target_data_items=["A", "B"], variables_written=["A", "B"], assigned_literal="X",
    )
    program = _program("P1", [_paragraph("A", [s1])], data_items=[_data_item("A"), _data_item("B")])
    facts = _facts_of([program])
    assert _fact_for(facts, "A").literal == "X"
    assert _fact_for(facts, "B").literal == "X"


# ---------------------------------------------------------------------------
# 10-12: resolucion de simbolos
# ---------------------------------------------------------------------------


def test_case_10_resolve_by_qualified_name() -> None:
    s1 = _move("P1::A::1::MOVE", target="GRUPO.CAMPO", literal="X")
    program = _program(
        "P1", [_paragraph("A", [s1])],
        data_items=[_data_item("GRUPO", qualified_name="GRUPO"),
                    _data_item("CAMPO", qualified_name="GRUPO.CAMPO")],
    )
    fact = _fact_for(_facts_of([program]), "GRUPO.CAMPO")
    assert fact.target_qualified_name == "GRUPO.CAMPO"


def test_case_11_resolve_by_unique_simple_name() -> None:
    s1 = _move("P1::A::1::MOVE", target="CAMPO", literal="X")
    program = _program(
        "P1", [_paragraph("A", [s1])],
        data_items=[_data_item("GRUPO", qualified_name="GRUPO"),
                    _data_item("CAMPO", qualified_name="GRUPO.CAMPO")],
    )
    fact = _fact_for(_facts_of([program]), "CAMPO")
    assert fact.target_qualified_name == "GRUPO.CAMPO"
    assert fact.fact_kind == PropagationFactKind.DIRECT_LITERAL


def test_case_12_ambiguous_simple_name_blocks_propagation() -> None:
    s1 = _move("P1::A::1::MOVE", target="CAMPO", literal="X")
    program = _program(
        "P1", [_paragraph("A", [s1])],
        data_items=[
            _data_item("CAMPO", qualified_name="GRUPO-A.CAMPO"),
            _data_item("CAMPO", qualified_name="GRUPO-B.CAMPO"),
        ],
    )
    fact = _fact_for(_facts_of([program]), "CAMPO")
    assert fact.fact_kind == PropagationFactKind.BLOCKED_PROPAGATION
    assert fact.literal is None
    barriers = _barriers_of([program])
    assert any(b.reason == PropagationBarrierReason.AMBIGUOUS_SYMBOL for b in barriers)
    assert any(b.diagnostic_code == "AMBIGUOUS_DATA_ITEM_REFERENCE" for b in barriers)


# ---------------------------------------------------------------------------
# 13: cruce de parrafo prohibido
# ---------------------------------------------------------------------------


def test_case_13_no_cross_paragraph_propagation() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="0005")
    s2 = _move("P1::B::1::MOVE", target="B", source="A")
    program = _program(
        "P1", [_paragraph("A", [s1]), _paragraph("B", [s2])],
        data_items=[_data_item("A"), _data_item("B")],
    )
    fact_b = _fact_for(_facts_of([program]), "B")
    assert fact_b.fact_kind == PropagationFactKind.UNRESOLVED_COPY
    assert fact_b.literal is None


# ---------------------------------------------------------------------------
# 14-18: ramas, merge conservador, EVALUATE/WHEN, decision anidada
# ---------------------------------------------------------------------------


def test_case_14_if_then_branch_value_known_inside_branch() -> None:
    if_stmt = _statement(
        statement_id="P1::A::1::IF", kind=StatementKind.IF, source_text="IF X",
    )
    then_stmt = _move(
        "P1::A::2::MOVE", target="A", literal="X",
        parent_statement_id="P1::A::1::IF", branch_kind="THEN",
    )
    then_copy = _move(
        "P1::A::3::MOVE", target="B", source="A",
        parent_statement_id="P1::A::1::IF", branch_kind="THEN",
    )
    program = _program(
        "P1", [_paragraph("A", [if_stmt, then_stmt, then_copy])],
        data_items=[_data_item("A"), _data_item("B")],
    )
    facts = _facts_of([program])
    # dentro de la rama THEN, B se conoce (propagacion intrarama valida).
    b_facts = [f for f in facts if f.target_variable == "B"]
    assert any(f.fact_kind == PropagationFactKind.PROPAGATED_LITERAL for f in b_facts)


def test_case_15_if_else_branch_value_known_inside_branch() -> None:
    if_stmt = _statement(statement_id="P1::A::1::IF", kind=StatementKind.IF, source_text="IF X")
    else_stmt = _move(
        "P1::A::2::MOVE", target="A", literal="Y",
        parent_statement_id="P1::A::1::IF", branch_kind="ELSE",
    )
    program = _program(
        "P1", [_paragraph("A", [if_stmt, else_stmt])], data_items=[_data_item("A")],
    )
    fact = _fact_for(_facts_of([program]), "A")
    assert fact.fact_kind == PropagationFactKind.DIRECT_LITERAL
    assert fact.literal == "Y"


def test_case_16_conservative_merge_invalidates_written_and_keeps_untouched() -> None:
    s0 = _move("P1::A::0::MOVE", target="UNTOUCHED", literal="KEEP")
    if_stmt = _statement(statement_id="P1::A::1::IF", kind=StatementKind.IF, source_text="IF X")
    then_stmt = _move(
        "P1::A::2::MOVE", target="A", literal="X",
        parent_statement_id="P1::A::1::IF", branch_kind="THEN",
    )
    else_stmt = _move(
        "P1::A::3::MOVE", target="A", literal="Y",
        parent_statement_id="P1::A::1::IF", branch_kind="ELSE",
    )
    after = _move("P1::A::4::MOVE", target="B", source="A")
    program = _program(
        "P1", [_paragraph("A", [s0, if_stmt, then_stmt, else_stmt, after])],
        data_items=[_data_item("A"), _data_item("B"), _data_item("UNTOUCHED")],
    )
    facts = _facts_of([program])
    # A fue escrito en ambas ramas: al salir del IF, A no tiene valor
    # conocido -> el MOVE B TO A posterior queda UNRESOLVED_COPY.
    fact_b = _fact_for(facts, "B")
    assert fact_b.fact_kind == PropagationFactKind.UNRESOLVED_COPY
    # UNTOUCHED nunca fue tocado por ninguna rama: sigue con su valor.
    untouched_facts = [f for f in facts if f.target_variable == "UNTOUCHED"]
    assert len(untouched_facts) == 1
    assert untouched_facts[0].literal == "KEEP"


def test_case_17_evaluate_multiple_when_branches_are_isolated_regions() -> None:
    evaluate_stmt = _statement(
        statement_id="P1::A::1::EVALUATE", kind=StatementKind.EVALUATE, source_text="EVALUATE X",
    )
    when1 = _move(
        "P1::A::2::MOVE", target="A", literal="ONE",
        parent_statement_id="P1::A::1::EVALUATE", branch_kind="WHEN",
        branch_condition="WHEN 1",
    )
    when2 = _move(
        "P1::A::3::MOVE", target="A", literal="TWO",
        parent_statement_id="P1::A::1::EVALUATE", branch_kind="WHEN",
        branch_condition="WHEN 2",
    )
    program = _program(
        "P1", [_paragraph("A", [evaluate_stmt, when1, when2])], data_items=[_data_item("A")],
    )
    facts = _facts_of([program])
    a_facts = {f.region_id: f.literal for f in facts if f.target_variable == "A"}
    # Cada WHEN produce su propio fact en una region propia (distinta):
    # nunca se fusionan en un unico region_id pese a compartir branch_kind.
    assert len(a_facts) == 2
    assert set(a_facts.values()) == {"ONE", "TWO"}


def test_case_18_nested_decision_inside_then_branch() -> None:
    outer_if = _statement(statement_id="P1::A::1::IF", kind=StatementKind.IF, source_text="IF X")
    inner_if = _statement(
        statement_id="P1::A::2::IF", kind=StatementKind.IF, source_text="IF Y",
        parent_statement_id="P1::A::1::IF", branch_kind="THEN",
    )
    inner_then = _move(
        "P1::A::3::MOVE", target="A", literal="Z",
        parent_statement_id="P1::A::2::IF", branch_kind="THEN",
    )
    program = _program(
        "P1", [_paragraph("A", [outer_if, inner_if, inner_then])], data_items=[_data_item("A")],
    )
    fact = _fact_for(_facts_of([program]), "A")
    assert fact.fact_kind == PropagationFactKind.DIRECT_LITERAL
    assert fact.literal == "Z"
    assert "P1::A::1::IF" in fact.region_id
    assert "P1::A::2::IF" in fact.region_id


# ---------------------------------------------------------------------------
# 19-22: GO TO / PERFORM / OTHER / unsupported como barreras
# ---------------------------------------------------------------------------


def test_case_19_go_to_is_a_barrier_and_clears_environment() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="X")
    s2 = _statement(
        statement_id="P1::A::2::GO_TO", kind=StatementKind.GO_TO, source_text="GO TO OTHER-PARA",
        target_paragraphs=["OTHER-PARA"],
    )
    program = _program("P1", [_paragraph("A", [s1, s2])], data_items=[_data_item("A")])
    barriers = _barriers_of([program])
    assert any(b.reason == PropagationBarrierReason.CONTROL_FLOW_BOUNDARY for b in barriers)
    assert any(b.clears_entire_environment for b in barriers)


def test_case_20_perform_is_a_barrier_and_never_recurses_into_body() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="X")
    perform = _statement(
        statement_id="P1::A::2::PERFORM", kind=StatementKind.PERFORM, source_text="PERFORM B-PARA",
        target_paragraphs=["B-PARA"],
    )
    inline_body = _move(
        "P1::A::3::MOVE", target="B", literal="Y",
        parent_statement_id="P1::A::2::PERFORM", branch_kind=None,
    )
    program = _program(
        "P1", [_paragraph("A", [s1, perform, inline_body])],
        data_items=[_data_item("A"), _data_item("B")],
    )
    facts = _facts_of([program])
    # El cuerpo inline del PERFORM nunca se visita: ningun fact para B.
    assert not any(f.target_variable == "B" for f in facts)
    barriers = _barriers_of([program])
    assert any(b.reason == PropagationBarrierReason.LOOP_OR_PERFORM_BOUNDARY for b in barriers)


def test_case_21_other_statement_kind_is_unknown_side_effect() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="X")
    s2 = _statement(
        statement_id="P1::A::2::OTHER", kind=StatementKind.OTHER, source_text="DISPLAY 'X'",
    )
    program = _program("P1", [_paragraph("A", [s1, s2])], data_items=[_data_item("A")])
    barriers = _barriers_of([program])
    assert any(b.reason == PropagationBarrierReason.UNKNOWN_SIDE_EFFECT for b in barriers)
    facts = _facts_of([program])
    a_facts = [f for f in facts if f.target_variable == "A"]
    assert any(f.fact_kind == PropagationFactKind.INVALIDATED_VALUE for f in a_facts)


def test_case_22_unsupported_construct_statement_is_a_barrier() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="X")
    s2 = _statement(
        statement_id="P1::A::2::MOVE", kind=StatementKind.MOVE, source_text="MOVE CORRESPONDING",
    )
    program = _program("P1", [_paragraph("A", [s1, s2])], data_items=[_data_item("A")])
    barriers = _barriers_of([program])
    assert any(b.reason == PropagationBarrierReason.UNKNOWN_SIDE_EFFECT for b in barriers)


# ---------------------------------------------------------------------------
# 23: SQL host variables
# ---------------------------------------------------------------------------


def test_case_23_sql_host_variables_are_invalidated_never_assumed() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="X")
    sql = _statement(
        statement_id="P1::A::2::EXEC_SQL", kind=StatementKind.EXEC_SQL, source_text="EXEC SQL",
        sql_access=[
            CanonicalSqlAccess(
                table="TBL", operation=TableAccessOperation.READS, host_variables=["A"],
                location_kind=LocationKind.UNKNOWN,
            )
        ],
    )
    program = _program("P1", [_paragraph("A", [s1, sql])], data_items=[_data_item("A")])
    barriers = _barriers_of([program])
    assert any(b.reason == PropagationBarrierReason.SQL_HOST_DIRECTION_UNKNOWN for b in barriers)
    facts = _facts_of([program])
    a_facts = [f for f in facts if f.target_variable == "A"]
    assert any(f.fact_kind == PropagationFactKind.INVALIDATED_VALUE for f in a_facts)


# ---------------------------------------------------------------------------
# 24-28: SET condicion-88 TRUE/FALSE, SET ordinario
# ---------------------------------------------------------------------------


def _set_condition(statement_id: str, condition_name: str, *, set_true: bool = True):
    return _statement(
        statement_id=statement_id, kind=StatementKind.SET, source_text="SET X TO TRUE",
        target_data_items=[condition_name], variables_written=[condition_name],
        condition_name_target=condition_name, condition_set_value=set_true,
    )


def test_case_24_set_condition_true_single_value_produces_condition_literal() -> None:
    condition = CanonicalConditionName(
        name="COD-X", qualified_name="WS-A.COD-X", parent_name="WS-A", parent_qualified_name="WS-A",
        values=[CanonicalConditionValue(value="0005", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    s1 = _set_condition("P1::A::1::SET", "COD-X")
    program = _program(
        "P1", [_paragraph("A", [s1])], data_items=[_data_item("WS-A")], condition_names=[condition],
    )
    fact = _fact_for(_facts_of([program]), "WS-A")
    assert fact.fact_kind == PropagationFactKind.CONDITION_LITERAL
    assert fact.literal == "0005"


def test_case_25_set_condition_true_multiple_values_blocked() -> None:
    condition = CanonicalConditionName(
        name="COD-X", qualified_name="WS-A.COD-X", parent_name="WS-A", parent_qualified_name="WS-A",
        values=[
            CanonicalConditionValue(value="01", location_kind=LocationKind.UNKNOWN),
            CanonicalConditionValue(value="02", location_kind=LocationKind.UNKNOWN),
        ],
        location_kind=LocationKind.UNKNOWN,
    )
    s1 = _set_condition("P1::A::1::SET", "COD-X")
    program = _program(
        "P1", [_paragraph("A", [s1])], data_items=[_data_item("WS-A")], condition_names=[condition],
    )
    fact = _fact_for(_facts_of([program]), "WS-A")
    assert fact.fact_kind == PropagationFactKind.BLOCKED_PROPAGATION
    assert fact.literal is None
    barriers = _barriers_of([program])
    assert any(b.reason == PropagationBarrierReason.MULTIPLE_CONDITION_VALUES for b in barriers)


def test_case_26_set_condition_true_thru_blocked() -> None:
    condition = CanonicalConditionName(
        name="COD-X", qualified_name="WS-A.COD-X", parent_name="WS-A", parent_qualified_name="WS-A",
        values=[
            CanonicalConditionValue(
                value="01", through_value="09", location_kind=LocationKind.UNKNOWN
            )
        ],
        location_kind=LocationKind.UNKNOWN,
    )
    s1 = _set_condition("P1::A::1::SET", "COD-X")
    program = _program(
        "P1", [_paragraph("A", [s1])], data_items=[_data_item("WS-A")], condition_names=[condition],
    )
    fact = _fact_for(_facts_of([program]), "WS-A")
    assert fact.fact_kind == PropagationFactKind.BLOCKED_PROPAGATION
    barriers = _barriers_of([program])
    assert any(b.reason == PropagationBarrierReason.CONDITION_VALUE_RANGE for b in barriers)


def test_case_27_set_condition_false_never_invents_a_value() -> None:
    """(B) Condicion con un unico VALUE: aunque tenga un unico VALUE,
    SET FALSE nunca infiere el complemento como si fuera un literal."""
    condition = CanonicalConditionName(
        name="COD-X", qualified_name="WS-A.COD-X", parent_name="WS-A", parent_qualified_name="WS-A",
        values=[CanonicalConditionValue(value="0005", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    s1 = _set_condition("P1::A::1::SET", "COD-X", set_true=False)
    program = _program(
        "P1", [_paragraph("A", [s1])], data_items=[_data_item("WS-A")], condition_names=[condition],
    )
    fact = _fact_for(_facts_of([program]), "WS-A")
    assert fact.fact_kind == PropagationFactKind.BLOCKED_PROPAGATION
    assert fact.literal is None
    barriers = _barriers_of([program])
    assert any(
        b.reason == PropagationBarrierReason.CONDITION_FALSE_VALUE_UNDETERMINED for b in barriers
    )
    assert not any(b.reason == PropagationBarrierReason.MULTIPLE_CONDITION_VALUES for b in barriers)


# ---------------------------------------------------------------------------
# Correccion post-Fase-4: SET condicion TO FALSE usa un motivo de barrera
# propio (CONDITION_FALSE_VALUE_UNDETERMINED), nunca MULTIPLE_CONDITION_
# VALUES/CONDITION_VALUE_RANGE/UNKNOWN_SIDE_EFFECT/COMPUTED_VALUE -- esos
# quedan reservados a sus causas reales (TRUE con varios VALUE, TRUE con
# THRU, efecto desconocido, COMPUTE).
# ---------------------------------------------------------------------------


def test_case_set_false_a_invalidates_previously_known_literal_and_blocks_downstream_copy() -> None:
    """(A) Valor conocido previo invalidado:

    MOVE 'V' TO WS-ESTADO
    SET ESTADO-VALIDO TO FALSE
    MOVE WS-ESTADO TO WS-DESTINO
    """
    condition = CanonicalConditionName(
        name="ESTADO-VALIDO", qualified_name="WS-ESTADO.ESTADO-VALIDO",
        parent_name="WS-ESTADO", parent_qualified_name="WS-ESTADO",
        values=[CanonicalConditionValue(value="V", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    s1 = _move("P1::A::1::MOVE", target="WS-ESTADO", literal="V")
    s2 = _set_condition("P1::A::2::SET", "ESTADO-VALIDO", set_true=False)
    s3 = _move("P1::A::3::MOVE", target="WS-DESTINO", source="WS-ESTADO")
    program = _program(
        "P1", [_paragraph("A", [s1, s2, s3])],
        data_items=[_data_item("WS-ESTADO"), _data_item("WS-DESTINO")],
        condition_names=[condition],
    )
    facts = _facts_of([program])

    estado_facts = [f for f in facts if f.target_variable == "WS-ESTADO"]
    direct_literal_facts = [
        f for f in estado_facts if f.fact_kind == PropagationFactKind.DIRECT_LITERAL
    ]
    assert len(direct_literal_facts) == 1
    assert direct_literal_facts[0].literal == "V"

    blocked_facts = [
        f for f in estado_facts if f.fact_kind == PropagationFactKind.BLOCKED_PROPAGATION
    ]
    assert len(blocked_facts) == 1
    assert blocked_facts[0].literal is None

    destino_fact = _fact_for(facts, "WS-DESTINO")
    assert destino_fact.fact_kind == PropagationFactKind.UNRESOLVED_COPY
    assert destino_fact.literal is None
    assert destino_fact.literal != "V"

    barriers = _barriers_of([program])
    condition_false_barriers = [
        b for b in barriers
        if b.reason == PropagationBarrierReason.CONDITION_FALSE_VALUE_UNDETERMINED
    ]
    assert len(condition_false_barriers) == 1
    assert "WS-ESTADO" in condition_false_barriers[0].affected_variables
    assert condition_false_barriers[0].diagnostic_code == (
        "SET_CONDITION_FALSE_HAS_NO_UNIQUE_PARENT_VALUE"
    )


def test_case_set_false_c_multiple_values_still_uses_condition_false_reason() -> None:
    """(C) Condicion con multiples VALUE: SET FALSE sigue usando
    CONDITION_FALSE_VALUE_UNDETERMINED, nunca MULTIPLE_CONDITION_VALUES
    (esa categoria queda reservada a SET ... TO TRUE con varios VALUE)."""
    condition = CanonicalConditionName(
        name="COD-X", qualified_name="WS-A.COD-X", parent_name="WS-A", parent_qualified_name="WS-A",
        values=[
            CanonicalConditionValue(value="01", location_kind=LocationKind.UNKNOWN),
            CanonicalConditionValue(value="02", location_kind=LocationKind.UNKNOWN),
        ],
        location_kind=LocationKind.UNKNOWN,
    )
    s1 = _set_condition("P1::A::1::SET", "COD-X", set_true=False)
    program = _program(
        "P1", [_paragraph("A", [s1])], data_items=[_data_item("WS-A")], condition_names=[condition],
    )
    fact = _fact_for(_facts_of([program]), "WS-A")
    assert fact.fact_kind == PropagationFactKind.BLOCKED_PROPAGATION
    assert fact.literal is None
    barriers = _barriers_of([program])
    assert any(
        b.reason == PropagationBarrierReason.CONDITION_FALSE_VALUE_UNDETERMINED for b in barriers
    )
    assert not any(b.reason == PropagationBarrierReason.MULTIPLE_CONDITION_VALUES for b in barriers)


def test_case_set_false_d_thru_still_uses_condition_false_reason() -> None:
    """(D) Condicion con VALUE THRU: SET FALSE sigue usando
    CONDITION_FALSE_VALUE_UNDETERMINED, nunca CONDITION_VALUE_RANGE (esa
    categoria queda reservada a SET ... TO TRUE con THRU)."""
    condition = CanonicalConditionName(
        name="COD-X", qualified_name="WS-A.COD-X", parent_name="WS-A", parent_qualified_name="WS-A",
        values=[
            CanonicalConditionValue(
                value="01", through_value="09", location_kind=LocationKind.UNKNOWN
            )
        ],
        location_kind=LocationKind.UNKNOWN,
    )
    s1 = _set_condition("P1::A::1::SET", "COD-X", set_true=False)
    program = _program(
        "P1", [_paragraph("A", [s1])], data_items=[_data_item("WS-A")], condition_names=[condition],
    )
    fact = _fact_for(_facts_of([program]), "WS-A")
    assert fact.fact_kind == PropagationFactKind.BLOCKED_PROPAGATION
    barriers = _barriers_of([program])
    assert any(
        b.reason == PropagationBarrierReason.CONDITION_FALSE_VALUE_UNDETERMINED for b in barriers
    )
    assert not any(b.reason == PropagationBarrierReason.CONDITION_VALUE_RANGE for b in barriers)


def test_case_set_false_e_unresolved_parent_never_invents_name_or_literal() -> None:
    """(E) Padre no resuelto: el data item padre de la condicion no esta
    declarado en data_items (p. ej. viene de un COPY no capturado). Nunca
    se inventa un target_qualified_name ni un literal; el nombre crudo del
    padre (tal como lo declara el efecto) se preserva sin alteracion."""
    condition = CanonicalConditionName(
        name="COD-X", qualified_name="WS-A.COD-X", parent_name="WS-A", parent_qualified_name="WS-A",
        values=[CanonicalConditionValue(value="0005", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    s1 = _set_condition("P1::A::1::SET", "COD-X", set_true=False)
    program = _program(
        "P1", [_paragraph("A", [s1])], data_items=[], condition_names=[condition],
    )
    fact = _fact_for(_facts_of([program]), "WS-A")
    assert fact.fact_kind == PropagationFactKind.BLOCKED_PROPAGATION
    assert fact.literal is None
    assert fact.target_qualified_name is None
    assert fact.target_variable == "WS-A"


def test_case_set_false_f_summary_counts_the_new_reason() -> None:
    """(F) Metricas: SemanticPropagationSummary.counts_by_barrier_reason
    cuenta CONDITION_FALSE_VALUE_UNDETERMINED correctamente."""
    condition = CanonicalConditionName(
        name="COD-X", qualified_name="WS-A.COD-X", parent_name="WS-A", parent_qualified_name="WS-A",
        values=[CanonicalConditionValue(value="0005", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    s1 = _set_condition("P1::A::1::SET", "COD-X", set_true=False)
    program = _program(
        "P1", [_paragraph("A", [s1])], data_items=[_data_item("WS-A")], condition_names=[condition],
    )
    artifact = _propagate([program])
    summary = artifact.summary
    assert summary.counts_by_barrier_reason.get(
        PropagationBarrierReason.CONDITION_FALSE_VALUE_UNDETERMINED
    ) == 1
    assert (
        summary.counts_by_barrier_reason.get(PropagationBarrierReason.MULTIPLE_CONDITION_VALUES)
        is None
    )
    assert summary.blocked_count == 1
    assert summary.barrier_count == 1


def test_case_28_ordinary_set_never_propagates() -> None:
    s1 = _statement(
        statement_id="P1::A::1::SET", kind=StatementKind.SET, source_text="SET IDX TO 1",
        target_data_items=["IDX"], variables_written=["IDX"], assigned_literal="1",
    )
    program = _program("P1", [_paragraph("A", [s1])], data_items=[_data_item("IDX")])
    fact = _fact_for(_facts_of([program]), "IDX")
    assert fact.fact_kind == PropagationFactKind.INVALIDATED_VALUE
    assert fact.literal is None


# ---------------------------------------------------------------------------
# 29-30: constantes figurativas
# ---------------------------------------------------------------------------


def test_case_29_figurative_constant_space_is_a_canonical_direct_literal() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="SPACE")
    program = _program("P1", [_paragraph("A", [s1])], data_items=[_data_item("A")])
    fact = _fact_for(_facts_of([program]), "A")
    assert fact.fact_kind == PropagationFactKind.DIRECT_LITERAL
    assert fact.literal == "SPACE"


def test_case_30_figurative_constant_zero_is_a_canonical_direct_literal() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="ZERO")
    program = _program("P1", [_paragraph("A", [s1])], data_items=[_data_item("A")])
    fact = _fact_for(_facts_of([program]), "A")
    assert fact.fact_kind == PropagationFactKind.DIRECT_LITERAL
    assert fact.literal == "ZERO"


# ---------------------------------------------------------------------------
# 31-34: orden de salida, determinismo, no-mutacion, aislamiento entre programas
# ---------------------------------------------------------------------------


def test_case_31_output_is_sorted_regardless_of_processing_order() -> None:
    s1 = _move("P1::A::1::MOVE", target="Z-FIELD", literal="X")
    s2 = _move("P1::A::2::MOVE", target="A-FIELD", literal="Y")
    program = _program(
        "P1", [_paragraph("A", [s1, s2])],
        data_items=[_data_item("Z-FIELD"), _data_item("A-FIELD")],
    )
    artifact = _propagate([program])
    fact_ids = [f.fact_id for f in artifact.programs[0].facts]
    assert fact_ids == sorted(fact_ids)


def test_case_32_determinism_across_two_runs() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="X")
    s2 = _move("P1::A::2::MOVE", target="B", source="A")
    program = _program(
        "P1", [_paragraph("A", [s1, s2])], data_items=[_data_item("A"), _data_item("B")]
    )
    first = _propagate([program]).to_stable_json()
    second = _propagate([program]).to_stable_json()
    normalized_first = first.replace('"run_id": "run-1"', '"run_id": "run-1"')
    assert normalized_first == second


def test_case_33_analyzer_never_mutates_input_programs() -> None:
    s1 = _move("P1::A::1::MOVE", target="A", literal="X")
    program = _program("P1", [_paragraph("A", [s1])], data_items=[_data_item("A")])
    before = program.model_copy(deep=True)
    _propagate([program])
    assert program == before


def test_case_34_multiple_programs_are_isolated() -> None:
    s1 = _move("P1::A::1::MOVE", target="SHARED-NAME", literal="FROM-P1")
    s2 = _move("P2::A::1::MOVE", target="SHARED-NAME", literal="FROM-P2")
    program1 = _program("P1", [_paragraph("A", [s1])], data_items=[_data_item("SHARED-NAME")])
    program2 = _program("P2", [_paragraph("A", [s2])], data_items=[_data_item("SHARED-NAME")])
    artifact = _propagate([program1, program2])
    assert [p.program for p in artifact.programs] == ["P1", "P2"]
    fact_p1 = artifact.programs[0].facts[0]
    fact_p2 = artifact.programs[1].facts[0]
    assert fact_p1.literal == "FROM-P1"
    assert fact_p2.literal == "FROM-P2"
