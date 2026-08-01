"""Tests del analizador PURO de efectos semanticos normalizados (Fase 2
de la ampliacion semantica, checkpoint `feat/semantic-effects-foundation`):
`pipeline/semantic_effects_analyzer.py`. Nunca Neo4j, nunca LLM, nunca
filesystem -- todo se construye en memoria."""

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
from altamira_extractor.contracts.semantic_coverage import SemanticSupportStatus
from altamira_extractor.contracts.semantic_effects import SemanticEffectKind
from altamira_extractor.pipeline.semantic_effects_analyzer import analyze_semantic_effects

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


def _analyze(programs: list[CanonicalProgram]):
    return analyze_semantic_effects(
        canonical_programs=programs,
        run_id="run-1",
        source_package_hash=_HASH,
        source_artifact_hashes=_REQUIRED_HASHES,
    )


def _effects_of(programs: list[CanonicalProgram]) -> list:
    artifact = _analyze(programs)
    return [effect for program in artifact.programs for effect in program.effects]


# ---------------------------------------------------------------------------
# MOVE: caso A -- literal directo, un unico destino
# ---------------------------------------------------------------------------


def test_move_literal_single_target_produces_fully_supported_assign_literal() -> None:
    stmt = _statement(target_data_items=["WS-TARGET"], assigned_literal="X")
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.kind == SemanticEffectKind.ASSIGN_LITERAL
    assert effect.support_status == SemanticSupportStatus.FULLY_SUPPORTED
    assert effect.literal == "X"
    assert effect.target_data_items == ["WS-TARGET"]
    assert effect.writes == ["WS-TARGET"]


# ---------------------------------------------------------------------------
# MOVE: caso B -- literal, multiples destinos
# ---------------------------------------------------------------------------


def test_move_literal_multiple_targets_produces_one_effect_per_target() -> None:
    stmt = _statement(target_data_items=["W1", "W2", "W3"], assigned_literal="Y")
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 3
    for index, target in enumerate(["W1", "W2", "W3"]):
        effect = [e for e in effects if e.target_data_items == [target]][0]
        assert effect.kind == SemanticEffectKind.ASSIGN_LITERAL
        assert effect.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
        assert "MULTIPLE_TARGET_ASSIGNMENT" in effect.diagnostic_codes
        assert effect.effect_id.endswith(f"ASSIGN_LITERAL::{index}")


def test_move_literal_multiple_targets_ids_are_unique() -> None:
    stmt = _statement(target_data_items=["W1", "W2"], assigned_literal="Y")
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])
    ids = [e.effect_id for e in effects]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# MOVE: caso C -- variable a variable
# ---------------------------------------------------------------------------


def test_move_variable_to_variable_produces_copy_value() -> None:
    stmt = _statement(
        variables_read=["WS-SOURCE"], target_data_items=["WS-TARGET"], assigned_literal=None
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.kind == SemanticEffectKind.COPY_VALUE
    assert effect.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert effect.source_data_items == ["WS-SOURCE"]
    assert effect.target_data_items == ["WS-TARGET"]
    assert effect.literal is None
    assert effect.diagnostic_codes == []


# ---------------------------------------------------------------------------
# MOVE: caso D -- multiples fuentes/destinos, sin correspondencia inventada
# ---------------------------------------------------------------------------


def test_move_ambiguous_multi_field_produces_copy_value_without_pairing() -> None:
    stmt = _statement(
        variables_read=["S1", "S2"], target_data_items=["T1", "T2"], assigned_literal=None
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.kind == SemanticEffectKind.COPY_VALUE
    assert effect.source_data_items == ["S1", "S2"]
    assert effect.target_data_items == ["T1", "T2"]
    assert "AMBIGUOUS_MULTI_FIELD_COPY" in effect.diagnostic_codes


# ---------------------------------------------------------------------------
# MOVE: caso E -- MOVE CORRESPONDING / grupo no resoluble
# ---------------------------------------------------------------------------


def test_move_corresponding_or_group_produces_preserved_statement() -> None:
    stmt = _statement(variables_read=[], target_data_items=[], assigned_literal=None)
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.kind == SemanticEffectKind.PRESERVED_STATEMENT
    assert effect.support_status == SemanticSupportStatus.PRESERVED_ONLY
    assert effect.writes == []
    assert effect.target_data_items == []
    assert effect.literal is None
    assert "MOVE_GROUP_OR_CORRESPONDING_NOT_EXPANDED" in effect.diagnostic_codes


# ---------------------------------------------------------------------------
# SET
# ---------------------------------------------------------------------------


def test_set_produces_set_value_with_unresolved_diagnostic() -> None:
    stmt = _statement(
        statement_id="P1::A::1::SET",
        kind=StatementKind.SET,
        source_text="SET WS-INDEX TO 1",
        target_data_items=["WS-INDEX"],
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.kind == SemanticEffectKind.SET_VALUE
    assert effect.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert "SET_SEMANTIC_VARIANT_UNRESOLVED" in effect.diagnostic_codes
    assert "LEVEL_88_VALUE_NOT_AVAILABLE" not in effect.diagnostic_codes


def test_set_over_verified_level_88_target_adds_diagnostic_without_inference() -> None:
    stmt = _statement(
        statement_id="P1::A::1::SET",
        kind=StatementKind.SET,
        source_text="SET WS-FLAG-OK TO TRUE",
        target_data_items=["WS-FLAG-OK"],
    )
    data_item = CanonicalDataItem(
        name="WS-FLAG-OK", qualified_name="WS-FLAG-OK", level=88, location_kind=LocationKind.UNKNOWN
    )
    program = _program("P1", [_paragraph("A", [stmt])], data_items=[data_item])
    effects = _effects_of([program])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.kind == SemanticEffectKind.SET_VALUE
    assert "LEVEL_88_VALUE_NOT_AVAILABLE" in effect.diagnostic_codes
    # Nunca se infiere VALUE/parent/THRU: ningun campo del contrato los expresa.
    assert effect.literal is None


def test_set_over_non_level_88_target_never_adds_level_88_diagnostic() -> None:
    stmt = _statement(
        statement_id="P1::A::1::SET",
        kind=StatementKind.SET,
        source_text="SET WS-INDEX TO 1",
        target_data_items=["WS-INDEX"],
    )
    data_item = CanonicalDataItem(
        name="WS-INDEX", qualified_name="WS-INDEX", level=5, location_kind=LocationKind.UNKNOWN
    )
    program = _program("P1", [_paragraph("A", [stmt])], data_items=[data_item])
    effects = _effects_of([program])

    assert "LEVEL_88_VALUE_NOT_AVAILABLE" not in effects[0].diagnostic_codes


# ---------------------------------------------------------------------------
# COMPUTE
# ---------------------------------------------------------------------------


def test_compute_produces_compute_value_without_evaluating_expression() -> None:
    stmt = _statement(
        statement_id="P1::A::1::COMPUTE",
        kind=StatementKind.COMPUTE,
        source_text="COMPUTE WS-TOTAL = WS-A + WS-B",
        variables_read=["WS-A", "WS-B"],
        target_data_items=["WS-TOTAL"],
        expression="WS-A + WS-B",
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.kind == SemanticEffectKind.COMPUTE_VALUE
    assert effect.expression == "WS-A + WS-B"
    assert effect.target_data_items == ["WS-TOTAL"]
    assert "COMPUTE_EXPRESSION_NOT_EVALUATED" in effect.diagnostic_codes


# ---------------------------------------------------------------------------
# GO_TO
# ---------------------------------------------------------------------------


def test_go_to_produces_fully_supported_control_transfer() -> None:
    stmt = _statement(
        statement_id="P1::A::1::GO_TO",
        kind=StatementKind.GO_TO,
        source_text="GO TO B",
        target_paragraphs=["B"],
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.kind == SemanticEffectKind.CONTROL_TRANSFER
    assert effect.support_status == SemanticSupportStatus.FULLY_SUPPORTED
    assert effect.control_targets == ["B"]


# ---------------------------------------------------------------------------
# PERFORM
# ---------------------------------------------------------------------------


def test_perform_produces_partially_supported_control_transfer() -> None:
    stmt = _statement(
        statement_id="P1::A::1::PERFORM",
        kind=StatementKind.PERFORM,
        source_text="PERFORM B THRU B-EXIT",
        target_paragraphs=["B", "B-EXIT"],
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.kind == SemanticEffectKind.CONTROL_TRANSFER
    assert effect.support_status == SemanticSupportStatus.PARTIALLY_SUPPORTED
    assert "PERFORM_LOOP_CONTROL_NOT_FULLY_REPRESENTED" in effect.diagnostic_codes


# ---------------------------------------------------------------------------
# EXEC_SQL
# ---------------------------------------------------------------------------


def test_exec_sql_produces_one_execute_sql_effect_per_sql_access() -> None:
    sql_read = CanonicalSqlAccess(
        table="CUSTOMER",
        operation=TableAccessOperation.READS,
        host_variables=["WS-ID"],
        location_kind=LocationKind.UNKNOWN,
    )
    stmt = _statement(
        statement_id="P1::A::1::EXEC_SQL",
        kind=StatementKind.EXEC_SQL,
        source_text="EXEC SQL SELECT ... END-EXEC",
        sql_access=[sql_read],
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.kind == SemanticEffectKind.EXECUTE_SQL
    assert effect.sql_operation == TableAccessOperation.READS
    assert effect.sql_tables == ["CUSTOMER"]
    # CanonicalSqlAccess.host_variables no distingue entrada/salida:
    # nunca se le asigna una direccion sin evidencia estructural.
    assert effect.reads == []
    assert effect.writes == []
    assert effect.sql_host_variables == ["WS-ID"]
    assert "SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED" in effect.diagnostic_codes


def test_exec_sql_predicate_text_is_never_copied_into_effect() -> None:
    sql_access = CanonicalSqlAccess(
        table="CUSTOMER",
        operation=TableAccessOperation.WRITES,
        predicate_text="WHERE SECRET_COLUMN = :X",
        location_kind=LocationKind.UNKNOWN,
    )
    stmt = _statement(
        statement_id="P1::A::1::EXEC_SQL",
        kind=StatementKind.EXEC_SQL,
        source_text="EXEC SQL UPDATE ... END-EXEC",
        sql_access=[sql_access],
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])
    assert "SECRET_COLUMN" not in effects[0].model_dump_json()


def test_exec_sql_select_never_assigns_where_and_into_variables_to_same_direction() -> None:
    """Caso real (EmbeddedSqlExtractorTest.selectWithHostVariablesAndPredicate):
    SELECT SALDO INTO :WS-SALDO FROM CUENTAS WHERE ID_CUENTA = :WS-CUENTA-ID
    produce host_variables=[WS-SALDO, WS-CUENTA-ID] en una unica lista plana
    (WS-SALDO es salida INTO, WS-CUENTA-ID es entrada WHERE). El analizador
    nunca debe asignar ambas indiscriminadamente a reads o a writes."""
    sql_access = CanonicalSqlAccess(
        table="CUENTAS",
        operation=TableAccessOperation.READS,
        predicate_text="WHERE ID_CUENTA = :WS-CUENTA-ID",
        host_variables=["WS-SALDO", "WS-CUENTA-ID"],
        location_kind=LocationKind.UNKNOWN,
    )
    stmt = _statement(
        statement_id="P1::A::1::EXEC_SQL",
        kind=StatementKind.EXEC_SQL,
        source_text="EXEC SQL SELECT SALDO INTO :WS-SALDO FROM CUENTAS "
        "WHERE ID_CUENTA = :WS-CUENTA-ID END-EXEC",
        sql_access=[sql_access],
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.reads == []
    assert effect.writes == []
    assert effect.sql_host_variables == ["WS-CUENTA-ID", "WS-SALDO"]


def test_exec_sql_insert_conserves_host_variables_without_inventing_direction() -> None:
    sql_access = CanonicalSqlAccess(
        table="MOVIMIENTOS",
        operation=TableAccessOperation.INSERTS,
        host_variables=["WS-CUENTA-ID", "WS-MONTO"],
        location_kind=LocationKind.UNKNOWN,
    )
    stmt = _statement(
        statement_id="P1::A::1::EXEC_SQL",
        kind=StatementKind.EXEC_SQL,
        source_text="EXEC SQL INSERT INTO MOVIMIENTOS (ID_CUENTA, MONTO) "
        "VALUES (:WS-CUENTA-ID, :WS-MONTO) END-EXEC",
        sql_access=[sql_access],
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.sql_operation == TableAccessOperation.INSERTS
    assert effect.reads == []
    assert effect.writes == []
    assert effect.sql_host_variables == ["WS-CUENTA-ID", "WS-MONTO"]


def test_exec_sql_update_conserves_set_and_where_variables_without_confusing_them() -> None:
    sql_access = CanonicalSqlAccess(
        table="CUENTAS",
        operation=TableAccessOperation.UPDATES,
        predicate_text="WHERE ID_CUENTA = :WS-ID",
        host_variables=["WS-SALDO", "WS-ID"],
        location_kind=LocationKind.UNKNOWN,
    )
    stmt = _statement(
        statement_id="P1::A::1::EXEC_SQL",
        kind=StatementKind.EXEC_SQL,
        source_text="EXEC SQL UPDATE CUENTAS SET SALDO = :WS-SALDO "
        "WHERE ID_CUENTA = :WS-ID END-EXEC",
        sql_access=[sql_access],
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.sql_operation == TableAccessOperation.UPDATES
    assert effect.reads == []
    assert effect.writes == []
    assert effect.sql_host_variables == ["WS-ID", "WS-SALDO"]


def test_exec_sql_delete_conserves_where_variable() -> None:
    sql_access = CanonicalSqlAccess(
        table="MOVIMIENTOS",
        operation=TableAccessOperation.WRITES,
        predicate_text="WHERE ID_CUENTA = :WS-ID",
        host_variables=["WS-ID"],
        location_kind=LocationKind.UNKNOWN,
    )
    stmt = _statement(
        statement_id="P1::A::1::EXEC_SQL",
        kind=StatementKind.EXEC_SQL,
        source_text="EXEC SQL DELETE FROM MOVIMIENTOS WHERE ID_CUENTA = :WS-ID END-EXEC",
        sql_access=[sql_access],
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.sql_operation == TableAccessOperation.WRITES
    assert effect.sql_host_variables == ["WS-ID"]
    assert effect.reads == []
    assert effect.writes == []


def test_exec_sql_host_variables_are_sorted_and_deduplicated() -> None:
    sql_access = CanonicalSqlAccess(
        table="CUENTAS",
        operation=TableAccessOperation.READS,
        host_variables=["WS-B", "WS-A", "WS-B", "WS-A"],
        location_kind=LocationKind.UNKNOWN,
    )
    stmt = _statement(
        statement_id="P1::A::1::EXEC_SQL",
        kind=StatementKind.EXEC_SQL,
        source_text="EXEC SQL SELECT X FROM CUENTAS WHERE A = :WS-A AND B = :WS-B END-EXEC",
        sql_access=[sql_access],
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])
    assert effects[0].sql_host_variables == ["WS-A", "WS-B"]


def test_exec_sql_without_host_variables_has_no_direction_diagnostic() -> None:
    sql_access = CanonicalSqlAccess(
        table="CUENTAS", operation=TableAccessOperation.READS, location_kind=LocationKind.UNKNOWN
    )
    stmt = _statement(
        statement_id="P1::A::1::EXEC_SQL",
        kind=StatementKind.EXEC_SQL,
        source_text="EXEC SQL SELECT COUNT(*) FROM CUENTAS END-EXEC",
        sql_access=[sql_access],
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])
    assert effects[0].sql_host_variables == []
    assert "SQL_HOST_VARIABLE_DIRECTION_UNRESOLVED" not in effects[0].diagnostic_codes


def test_exec_sql_output_is_deterministic_across_runs() -> None:
    sql_access = CanonicalSqlAccess(
        table="CUENTAS",
        operation=TableAccessOperation.READS,
        host_variables=["WS-SALDO", "WS-CUENTA-ID"],
        location_kind=LocationKind.UNKNOWN,
    )
    stmt = _statement(
        statement_id="P1::A::1::EXEC_SQL",
        kind=StatementKind.EXEC_SQL,
        source_text="EXEC SQL SELECT SALDO INTO :WS-SALDO FROM CUENTAS "
        "WHERE ID_CUENTA = :WS-CUENTA-ID END-EXEC",
        sql_access=[sql_access],
    )
    program = _program("P1", [_paragraph("A", [stmt])])

    artifact1 = _analyze([program])
    artifact2 = _analyze([program])
    assert artifact1.to_stable_json() == artifact2.to_stable_json()


# ---------------------------------------------------------------------------
# IF/EVALUATE: nunca un efecto artificial
# ---------------------------------------------------------------------------


def test_if_statement_produces_zero_effects() -> None:
    stmt = _statement(statement_id="P1::A::1::IF", kind=StatementKind.IF, source_text="IF X = 1")
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])
    assert effects == []


def test_evaluate_statement_produces_zero_effects() -> None:
    stmt = _statement(
        statement_id="P1::A::1::EVALUATE", kind=StatementKind.EVALUATE, source_text="EVALUATE X"
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])
    assert effects == []


def test_if_child_statement_effect_preserves_branch_and_parent() -> None:
    if_stmt = _statement(statement_id="P1::A::1::IF", kind=StatementKind.IF, source_text="IF X = 1")
    child = _statement(
        statement_id="P1::A::2::MOVE",
        source_text="MOVE 'X' TO W",
        target_data_items=["W"],
        assigned_literal="X",
        parent_statement_id="P1::A::1::IF",
        branch_kind="THEN",
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [if_stmt, child])])])

    assert len(effects) == 1
    ref = effects[0].source_reference
    assert ref.parent_statement_id == "P1::A::1::IF"
    assert ref.branch_kind.value == "THEN"


# ---------------------------------------------------------------------------
# OTHER
# ---------------------------------------------------------------------------


def test_other_produces_preserved_statement_without_source_text() -> None:
    stmt = _statement(
        statement_id="P1::A::1::OTHER", kind=StatementKind.OTHER, source_text="DISPLAY 'X'"
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt])])])

    assert len(effects) == 1
    effect = effects[0]
    assert effect.kind == SemanticEffectKind.PRESERVED_STATEMENT
    assert effect.support_status == SemanticSupportStatus.PRESERVED_ONLY
    assert "STATEMENT_TEXT_PRESERVED_WITHOUT_SEMANTIC_EFFECT" in effect.diagnostic_codes
    assert "DISPLAY" not in effect.model_dump_json()


# ---------------------------------------------------------------------------
# unsupported_constructs
# ---------------------------------------------------------------------------


def test_unsupported_constructs_produces_one_unsupported_statement_effect_each() -> None:
    program = _program(
        "P1",
        [_paragraph("A", [_statement(target_data_items=["W"], assigned_literal="X")])],
        unsupported_constructs=[
            "MOVE CORRESPONDING en paragraph A no decodificado estructuralmente "
            "(kind=MOVE, source_text conservado)",
            "STRING en paragraph B no decodificado estructuralmente (kind=OTHER, "
            "source_text conservado)",
        ],
    )
    effects = _effects_of([program])

    unsupported_effects = [e for e in effects if e.kind == SemanticEffectKind.UNSUPPORTED_STATEMENT]
    assert len(unsupported_effects) == 2
    for effect in unsupported_effects:
        assert effect.support_status == SemanticSupportStatus.UNSUPPORTED
        assert "DECLARED_UNSUPPORTED_BY_PRODUCER" in effect.diagnostic_codes


def test_unsupported_constructs_effect_ids_are_unique_and_deterministic() -> None:
    program = _program(
        "P1",
        [_paragraph("A", [])],
        unsupported_constructs=[
            "X en paragraph A no decodificado",
            "X en paragraph A no decodificado",
        ],
    )
    artifact1 = _analyze([program])
    artifact2 = _analyze([program])
    ids1 = [e.effect_id for e in artifact1.programs[0].effects]
    ids2 = [e.effect_id for e in artifact2.programs[0].effects]
    assert ids1 == ids2
    assert len(ids1) == len(set(ids1))


# ---------------------------------------------------------------------------
# Fase 5: prohibicion de propagacion (caso obligatorio)
# ---------------------------------------------------------------------------


def test_two_hop_move_chain_produces_no_propagation() -> None:
    """MOVE '0005' TO WS-COD-AUX seguido de MOVE WS-COD-AUX TO
    WS-COD-RETORNO debe producir exactamente dos efectos independientes:
    nunca un ASSIGN_LITERAL sobre WS-COD-RETORNO, nunca el literal '0005'
    agregado al segundo efecto, nunca un tercer efecto."""
    stmt1 = _statement(
        statement_id="P1::A::1::MOVE",
        source_text="MOVE '0005' TO WS-COD-AUX",
        target_data_items=["WS-COD-AUX"],
        assigned_literal="0005",
    )
    stmt2 = _statement(
        statement_id="P1::A::2::MOVE",
        source_text="MOVE WS-COD-AUX TO WS-COD-RETORNO",
        variables_read=["WS-COD-AUX"],
        target_data_items=["WS-COD-RETORNO"],
    )
    effects = _effects_of([_program("P1", [_paragraph("A", [stmt1, stmt2])])])

    assert len(effects) == 2

    assign_literal_effects = [e for e in effects if e.kind == SemanticEffectKind.ASSIGN_LITERAL]
    copy_value_effects = [e for e in effects if e.kind == SemanticEffectKind.COPY_VALUE]
    assert len(assign_literal_effects) == 1
    assert len(copy_value_effects) == 1

    assign_effect = assign_literal_effects[0]
    assert assign_effect.literal == "0005"
    assert assign_effect.target_data_items == ["WS-COD-AUX"]

    copy_effect = copy_value_effects[0]
    assert copy_effect.source_data_items == ["WS-COD-AUX"]
    assert copy_effect.target_data_items == ["WS-COD-RETORNO"]
    assert copy_effect.literal is None
    assert "0005" not in copy_effect.model_dump_json()

    # Nunca un ASSIGN_LITERAL sobre WS-COD-RETORNO.
    assert all("WS-COD-RETORNO" not in e.target_data_items for e in assign_literal_effects)


# ---------------------------------------------------------------------------
# Caso obligatorio (Fase 12, ampliacion nivel 88): SET condicion-88 TO TRUE
# seguido de MOVE de la variable padre a otro destino, sin propagacion.
# ---------------------------------------------------------------------------


def test_set_condition_true_followed_by_move_of_parent_produces_no_propagation() -> None:
    """01 WS-COD-AUX PIC X(4).
          88 COD-AUX-INVALIDO VALUE '0005'.

       SET COD-AUX-INVALIDO TO TRUE
       MOVE WS-COD-AUX TO WS-COD-RETORNO

    Debe producir: SET_CONDITION_TRUE sobre COD-AUX-INVALIDO asociado a
    WS-COD-AUX con el valor declarado '0005' conservado; COPY_VALUE desde
    WS-COD-AUX hacia WS-COD-RETORNO; ningun ASSIGN_LITERAL inventado
    sobre WS-COD-RETORNO. (candidatos V1 y relaciones LEADS_TO son
    responsabilidad de semantic_graph_builder.py/candidate_detector.py,
    ninguno de los cuales este modulo consulta o modifica -- ver
    docstring del modulo.)"""
    condition = CanonicalConditionName(
        name="COD-AUX-INVALIDO",
        qualified_name="WS-COD-AUX.COD-AUX-INVALIDO",
        parent_name="WS-COD-AUX",
        parent_qualified_name="WS-COD-AUX",
        values=[CanonicalConditionValue(value="0005", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    stmt1 = _statement(
        statement_id="P1::A::1::SET",
        kind=StatementKind.SET,
        source_text="SET COD-AUX-INVALIDO TO TRUE",
        target_data_items=["COD-AUX-INVALIDO"],
        variables_written=["COD-AUX-INVALIDO"],
        assigned_literal="true",
        condition_name_target="COD-AUX-INVALIDO",
        condition_set_value=True,
    )
    stmt2 = _statement(
        statement_id="P1::A::2::MOVE",
        source_text="MOVE WS-COD-AUX TO WS-COD-RETORNO",
        variables_read=["WS-COD-AUX"],
        target_data_items=["WS-COD-RETORNO"],
    )
    program = _program(
        "P1", [_paragraph("A", [stmt1, stmt2])], condition_names=[condition]
    )
    effects = _effects_of([program])

    assert len(effects) == 2

    set_condition_effects = [e for e in effects if e.kind == SemanticEffectKind.SET_CONDITION_TRUE]
    copy_value_effects = [e for e in effects if e.kind == SemanticEffectKind.COPY_VALUE]
    assert len(set_condition_effects) == 1
    assert len(copy_value_effects) == 1

    set_effect = set_condition_effects[0]
    assert set_effect.condition_name == "WS-COD-AUX.COD-AUX-INVALIDO"
    assert set_effect.parent_data_item == "WS-COD-AUX"
    assert set_effect.condition_values == ["0005"]
    assert set_effect.literal == "0005"
    assert set_effect.support_status == SemanticSupportStatus.FULLY_SUPPORTED

    copy_effect = copy_value_effects[0]
    assert copy_effect.source_data_items == ["WS-COD-AUX"]
    assert copy_effect.target_data_items == ["WS-COD-RETORNO"]
    assert copy_effect.literal is None
    assert "0005" not in copy_effect.model_dump_json()

    # Ningun ASSIGN_LITERAL inventado sobre WS-COD-RETORNO (ni sobre
    # ningun otro destino): el unico efecto con literal es el
    # SET_CONDITION_TRUE sobre su propia condicion.
    assert not any(e.kind == SemanticEffectKind.ASSIGN_LITERAL for e in effects)


# ---------------------------------------------------------------------------
# Multiples programas / determinismo de orden
# ---------------------------------------------------------------------------


def test_multiple_programs_are_ordered_by_name_regardless_of_input_order() -> None:
    stmt = _statement(target_data_items=["W"], assigned_literal="X")
    program_b = _program("PROG-B", [_paragraph("A", [stmt])])
    program_a = _program("PROG-A", [_paragraph("A", [stmt])])

    artifact = _analyze([program_b, program_a])
    assert [p.program for p in artifact.programs] == ["PROG-A", "PROG-B"]


def test_analyzer_output_is_deterministic_across_runs() -> None:
    stmt = _statement(target_data_items=["W"], assigned_literal="X")
    program = _program("P1", [_paragraph("A", [stmt])])

    artifact1 = _analyze([program])
    artifact2 = _analyze([program])
    assert artifact1.to_stable_json() == artifact2.to_stable_json()


def test_analyzer_never_mutates_input_programs() -> None:
    stmt = _statement(target_data_items=["W"], assigned_literal="X")
    program = _program("P1", [_paragraph("A", [stmt])])
    before = program.to_stable_json()

    _analyze([program])

    assert program.to_stable_json() == before
