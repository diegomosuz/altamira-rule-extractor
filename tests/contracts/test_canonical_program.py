"""Tests de CanonicalProgram, CanonicalStatement y su ubicacion nullable.

CanonicalProgram es el artefacto de salida del parser Java (Prompt 4);
estos tests validan unicamente el contrato Pydantic (Fase A). No hay
JSON Schema propio para este artefacto (ver canonical.py, docstring del
modulo): el contrato Pydantic es la unica fuente de verdad.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import (
    BranchKind,
    CallPassingMode,
    CallTargetKind,
    CanonicalCallArgument,
    CanonicalConditionName,
    CanonicalConditionValue,
    CanonicalDataItem,
    CanonicalEntryParameter,
    CanonicalLinkageDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalSqlAccess,
    CanonicalStatement,
    LocationKind,
    ProgramTerminationKind,
    SourceFormat,
    StatementKind,
    TableAccessOperation,
)

VALID_HASH = "a" * 64
SOURCE_FILE = "01-codigo/cobol/ARTRFPROP01.cbl"


def make_data_item(**overrides: object) -> CanonicalDataItem:
    fields: dict[str, object] = {
        "name": "WS-MONTO",
        "qualified_name": "WS-REGISTRO.WS-MONTO",
        "level": 5,
        "pic": "9(7)V99",
        "usage": "DISPLAY",
        "source_file": SOURCE_FILE,
        "line": 12,
        "location_kind": LocationKind.EXACT,
    }
    fields.update(overrides)
    return CanonicalDataItem(**fields)  # type: ignore[arg-type]


def make_sql_access(**overrides: object) -> CanonicalSqlAccess:
    fields: dict[str, object] = {
        "table": "CUENTAS",
        "operation": TableAccessOperation.UPDATES,
        "predicate_text": "WHERE ID_CUENTA = :WS-ID-CUENTA",
        "host_variables": ["WS-ID-CUENTA"],
        "source_file": SOURCE_FILE,
        "line_start": 40,
        "line_end": 42,
        "location_kind": LocationKind.EXACT,
    }
    fields.update(overrides)
    return CanonicalSqlAccess(**fields)  # type: ignore[arg-type]


def make_statement(**overrides: object) -> CanonicalStatement:
    fields: dict[str, object] = {
        "statement_id": "ARTRFPROP01::PARA-1::0::IF",
        "kind": StatementKind.IF,
        "source_text": "IF WS-MONTO > WS-LIMITE",
        "source_file": SOURCE_FILE,
        "line_start": 30,
        "line_end": 30,
        "location_kind": LocationKind.EXACT,
    }
    fields.update(overrides)
    return CanonicalStatement(**fields)  # type: ignore[arg-type]


def make_paragraph(statements: list[CanonicalStatement] | None = None) -> CanonicalParagraph:
    stmts = statements if statements is not None else [make_statement()]
    return CanonicalParagraph(
        name="PARA-1",
        source_text="PARA-1.\n    IF WS-MONTO > WS-LIMITE\n        ...",
        source_file=SOURCE_FILE,
        line_start=29,
        line_end=45,
        location_kind=LocationKind.EXACT,
        statements=stmts,
        variables_read=_agg([s.variables_read for s in stmts]),
        variables_written=_agg([s.variables_written for s in stmts]),
        sql_access=[access for s in stmts for access in s.sql_access],
    )


def _agg(lists: list[list[str]]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for values in lists:
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
    return result


def make_program(paragraphs: list[CanonicalParagraph] | None = None) -> CanonicalProgram:
    return CanonicalProgram(
        program_name="ARTRFPROP01",
        source_file=SOURCE_FILE,
        source_hash=VALID_HASH,
        source_package_hash=VALID_HASH,
        source_format=SourceFormat.FIXED,
        encoding="CP037",
        data_items=[make_data_item()],
        paragraphs=paragraphs if paragraphs is not None else [make_paragraph()],
        warnings=[],
        unsupported_constructs=[],
    )


# --- round trip ------------------------------------------------------------


def test_valid_program_round_trips() -> None:
    program = make_program()
    restored = CanonicalProgram.model_validate_json(program.to_stable_json())
    assert restored == program


def test_program_rejects_additional_properties() -> None:
    program = make_program()
    payload = program.model_dump(mode="json")
    payload["unexpected_field"] = "not allowed"
    with pytest.raises(ValidationError):
        CanonicalProgram.model_validate(payload)


def test_statement_rejects_additional_properties() -> None:
    payload = make_statement().model_dump(mode="json")
    payload["unexpected_field"] = "not allowed"
    with pytest.raises(ValidationError):
        CanonicalStatement.model_validate(payload)


# --- location_kind coherence: CanonicalStatement/CanonicalParagraph --------


def test_exact_location_requires_source_file_and_lines() -> None:
    with pytest.raises(ValidationError):
        make_statement(source_file=None, location_kind=LocationKind.EXACT)


def test_exact_location_requires_line_end() -> None:
    with pytest.raises(ValidationError):
        make_statement(line_end=None, location_kind=LocationKind.EXACT)


def test_preprocessed_stream_forbids_source_file() -> None:
    with pytest.raises(ValidationError, match="PREPROCESSED_STREAM"):
        make_statement(location_kind=LocationKind.PREPROCESSED_STREAM)


def test_preprocessed_stream_allows_lines_without_source_file() -> None:
    statement = make_statement(source_file=None, location_kind=LocationKind.PREPROCESSED_STREAM)
    assert statement.source_file is None
    assert statement.line_start == 30


def test_unknown_forbids_any_location_field() -> None:
    with pytest.raises(ValidationError, match="UNKNOWN"):
        make_statement(location_kind=LocationKind.UNKNOWN)


def test_unknown_with_no_location_fields_is_valid() -> None:
    statement = make_statement(
        source_file=None, line_start=None, line_end=None, location_kind=LocationKind.UNKNOWN
    )
    assert statement.location_kind == LocationKind.UNKNOWN


def test_line_end_before_line_start_is_rejected() -> None:
    with pytest.raises(ValidationError, match="line_end"):
        make_statement(line_start=30, line_end=20)


def test_data_item_location_kind_is_enforced() -> None:
    with pytest.raises(ValidationError):
        make_data_item(source_file=None, location_kind=LocationKind.EXACT)


def test_sql_access_location_kind_is_enforced() -> None:
    with pytest.raises(ValidationError):
        make_sql_access(source_file=None, location_kind=LocationKind.EXACT)


# --- Fase 15B3-C3-B: direccion de host variables ----------------------------


def test_sql_access_direction_fields_default_to_empty_and_backward_compatible() -> None:
    """JSON historico (sin los campos nuevos) sigue parseando identico:
    los cuatro campos nuevos son opcionales, con default lista vacia."""
    access = make_sql_access()
    assert access.input_host_variables == []
    assert access.output_host_variables == []
    assert access.predicate_host_variables == []
    assert access.selected_columns == []
    assert access.has_indicator_variables is False


def test_sql_access_accepts_demonstrated_direction() -> None:
    access = make_sql_access(
        operation=TableAccessOperation.READS,
        input_host_variables=["WS-CUENTA"],
        output_host_variables=["WS-SALDO"],
        predicate_host_variables=["WS-CUENTA"],
    )
    assert access.input_host_variables == ["WS-CUENTA"]
    assert access.output_host_variables == ["WS-SALDO"]


def test_sql_access_rejects_empty_host_variable_name() -> None:
    with pytest.raises(ValidationError):
        make_sql_access(input_host_variables=[""])


def test_sql_access_selected_columns_must_match_output_arity() -> None:
    with pytest.raises(ValidationError):
        make_sql_access(
            operation=TableAccessOperation.READS,
            output_host_variables=["WS-SALDO", "WS-ESTADO"],
            selected_columns=["SALDO"],
        )


def test_sql_access_selected_columns_with_matching_arity_is_valid() -> None:
    access = make_sql_access(
        operation=TableAccessOperation.READS,
        output_host_variables=["WS-SALDO", "WS-ESTADO"],
        selected_columns=["SALDO", "ESTADO"],
    )
    assert access.selected_columns == ["SALDO", "ESTADO"]


def test_sql_access_indicator_variables_cannot_carry_direction() -> None:
    with pytest.raises(ValidationError):
        make_sql_access(has_indicator_variables=True, input_host_variables=["WS-CUENTA"])


def test_sql_access_indicator_variables_with_no_direction_is_valid() -> None:
    access = make_sql_access(has_indicator_variables=True, host_variables=["WS-SALDO", "WS-IND"])
    assert access.has_indicator_variables is True
    assert access.input_host_variables == []


def test_paragraph_location_kind_is_enforced() -> None:
    with pytest.raises(ValidationError):
        CanonicalParagraph(
            name="PARA-1",
            source_text="PARA-1.",
            source_file=None,
            line_start=1,
            line_end=1,
            location_kind=LocationKind.EXACT,
            statements=[],
            variables_read=[],
            variables_written=[],
            sql_access=[],
        )


# --- branch_kind / parent_statement_id --------------------------------------


def test_branch_kind_requires_parent_statement_id() -> None:
    with pytest.raises(ValidationError, match="parent_statement_id"):
        make_statement(branch_kind=BranchKind.THEN, parent_statement_id=None)


def test_branch_kind_with_parent_is_valid() -> None:
    statement = make_statement(
        statement_id="ARTRFPROP01::PARA-1::1::IF",
        branch_kind=BranchKind.THEN,
        parent_statement_id="ARTRFPROP01::PARA-1::0::IF",
    )
    assert statement.branch_kind == BranchKind.THEN


# --- statement_id uniqueness / referential integrity ------------------------


def test_duplicate_statement_id_within_paragraph_is_rejected() -> None:
    duplicated = make_statement()
    with pytest.raises(ValidationError, match="duplicado"):
        make_paragraph(statements=[duplicated, duplicated.model_copy()])


def test_parent_statement_id_must_exist_within_paragraph() -> None:
    root = make_statement(statement_id="ARTRFPROP01::PARA-1::0::IF")
    orphan_branch = make_statement(
        statement_id="ARTRFPROP01::PARA-1::1::IF",
        branch_kind=BranchKind.THEN,
        parent_statement_id="does-not-exist",
    )
    with pytest.raises(ValidationError, match="parent_statement_id"):
        make_paragraph(statements=[root, orphan_branch])


def test_duplicate_statement_id_across_different_paragraphs_is_rejected() -> None:
    shared_id = "ARTRFPROP01::SHARED::0::IF"
    paragraph_a = make_paragraph(statements=[make_statement(statement_id=shared_id)])
    paragraph_b = CanonicalParagraph(
        name="PARA-2",
        source_text="PARA-2.\n    IF WS-OTRA > 0\n        ...",
        source_file=SOURCE_FILE,
        line_start=50,
        line_end=60,
        location_kind=LocationKind.EXACT,
        statements=[
            make_statement(
                statement_id=shared_id, source_text="IF WS-OTRA > 0", line_start=51, line_end=51
            )
        ],
        variables_read=[],
        variables_written=[],
        sql_access=[],
    )
    with pytest.raises(ValidationError, match="duplicado entre paragraphs"):
        make_program(paragraphs=[paragraph_a, paragraph_b])


def test_same_statement_id_is_fine_when_paragraph_matches_itself() -> None:
    # Sanity: un programa con un unico paragraph nunca dispara el chequeo
    # cross-paragraph (no hay "otro" paragraph con el que comparar).
    program = make_program()
    assert len(program.paragraphs) == 1


# --- agregados de CanonicalParagraph -----------------------------------------


def test_paragraph_variables_read_must_match_statement_union() -> None:
    statement = make_statement(variables_read=["WS-MONTO", "WS-LIMITE"])
    with pytest.raises(ValidationError, match="variables_read"):
        CanonicalParagraph(
            name="PARA-1",
            source_text="PARA-1.",
            source_file=SOURCE_FILE,
            line_start=29,
            line_end=45,
            location_kind=LocationKind.EXACT,
            statements=[statement],
            variables_read=["WS-MONTO"],  # falta WS-LIMITE
            variables_written=[],
            sql_access=[],
        )


def test_paragraph_variables_read_is_ordered_and_deduplicated() -> None:
    statement_a = make_statement(
        statement_id="a", variables_read=["WS-B", "WS-A"], line_start=1, line_end=1
    )
    statement_b = make_statement(
        statement_id="b", variables_read=["WS-A", "WS-C"], line_start=2, line_end=2
    )
    paragraph = make_paragraph(statements=[statement_a, statement_b])
    assert paragraph.variables_read == ["WS-B", "WS-A", "WS-C"]


def test_paragraph_sql_access_must_match_statement_aggregation() -> None:
    statement = make_statement(kind=StatementKind.EXEC_SQL, sql_access=[make_sql_access()])
    with pytest.raises(ValidationError, match="sql_access"):
        CanonicalParagraph(
            name="PARA-1",
            source_text="PARA-1.",
            source_file=SOURCE_FILE,
            line_start=29,
            line_end=45,
            location_kind=LocationKind.EXACT,
            statements=[statement],
            variables_read=[],
            variables_written=[],
            sql_access=[],  # falta el acceso del statement
        )


def test_statement_can_carry_multiple_sql_accesses() -> None:
    statement = make_statement(
        kind=StatementKind.EXEC_SQL,
        sql_access=[
            make_sql_access(table="CUENTAS"),
            make_sql_access(table="MOVIMIENTOS", operation=TableAccessOperation.INSERTS),
        ],
    )
    paragraph = make_paragraph(statements=[statement])
    assert [access.table for access in paragraph.sql_access] == ["CUENTAS", "MOVIMIENTOS"]


# --- targets multiples (MOVE ... TO A B, PERFORM A THRU B, GO TO A B) ------


def test_statement_supports_multiple_target_data_items() -> None:
    statement = make_statement(
        kind=StatementKind.MOVE,
        target_data_items=["WS-B", "WS-C"],
        assigned_literal="ZEROS",
    )
    assert statement.target_data_items == ["WS-B", "WS-C"]


def test_statement_supports_multiple_target_paragraphs() -> None:
    statement = make_statement(
        kind=StatementKind.GO_TO,
        target_paragraphs=["PARA-A", "PARA-B", "PARA-C"],
    )
    assert statement.target_paragraphs == ["PARA-A", "PARA-B", "PARA-C"]


# --- no paths absolutos ------------------------------------------------------


def test_source_file_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError):
        make_statement(source_file="/etc/passwd")


# ---------------------------------------------------------------------------
# Nivel 88 (Fase 3 de la ampliacion semantica): compatibilidad de contrato
# ---------------------------------------------------------------------------


def make_condition_value(**overrides: object) -> CanonicalConditionValue:
    fields: dict[str, object] = {
        "value": "0005",
        "location_kind": LocationKind.UNKNOWN,
    }
    fields.update(overrides)
    return CanonicalConditionValue(**fields)  # type: ignore[arg-type]


def make_condition_name(**overrides: object) -> CanonicalConditionName:
    fields: dict[str, object] = {
        "name": "COD-CAMPO-INVALIDO",
        "qualified_name": "WS-COD-RETORNO.COD-CAMPO-INVALIDO",
        "parent_name": "WS-COD-RETORNO",
        "parent_qualified_name": "WS-COD-RETORNO",
        "values": [make_condition_value()],
        "location_kind": LocationKind.UNKNOWN,
    }
    fields.update(overrides)
    return CanonicalConditionName(**fields)  # type: ignore[arg-type]


def test_historical_program_without_level_88_loads_without_condition_names_key() -> None:
    """Un CanonicalProgram JSON historico (anterior a la Fase 3) no tiene
    la clave `condition_names`: debe seguir cargando, con la lista vacia
    por defecto -- nunca un error de campo faltante."""
    program = make_program()
    payload = program.model_dump(mode="json")
    del payload["condition_names"]
    loaded = CanonicalProgram.model_validate(payload)
    assert loaded.condition_names == []


def test_program_without_level_88_serializes_with_empty_condition_names() -> None:
    program = make_program()
    assert program.condition_names == []
    assert '"condition_names": []' in program.to_stable_json()


def test_program_with_single_condition_round_trips() -> None:
    program = make_program()
    program = CanonicalProgram(
        **{
            **program.model_dump(mode="json"),
            "condition_names": [make_condition_name().model_dump(mode="json")],
        }
    )
    assert len(program.condition_names) == 1
    first = program.to_stable_json()
    second = CanonicalProgram.model_validate_json(first).to_stable_json()
    assert first == second


def test_program_with_multiple_conditions_under_different_parents() -> None:
    program = make_program()
    a = make_condition_name(
        name="COD-A", qualified_name="WS-X.COD-A", parent_name="WS-X", parent_qualified_name="WS-X"
    )
    b = make_condition_name(
        name="COD-B", qualified_name="WS-Y.COD-B", parent_name="WS-Y", parent_qualified_name="WS-Y"
    )
    program = CanonicalProgram(
        **{
            **program.model_dump(mode="json"),
            "condition_names": [a.model_dump(mode="json"), b.model_dump(mode="json")],
        }
    )
    assert {c.name for c in program.condition_names} == {"COD-A", "COD-B"}


def test_condition_value_single_value_has_no_through_value() -> None:
    value = make_condition_value(value="0000")
    assert value.value == "0000"
    assert value.through_value is None


def test_condition_name_supports_multiple_values() -> None:
    condition = make_condition_name(
        values=[
            make_condition_value(value="01"),
            make_condition_value(value="02"),
            make_condition_value(value="03"),
        ]
    )
    assert [v.value for v in condition.values] == ["01", "02", "03"]


def test_condition_name_supports_thru_range() -> None:
    condition = make_condition_name(
        values=[make_condition_value(value="0010", through_value="0019")]
    )
    assert condition.values[0].through_value == "0019"


def test_condition_name_requires_non_empty_values() -> None:
    with pytest.raises(ValidationError):
        make_condition_name(values=[])


def test_condition_name_resolved_parent_is_captured() -> None:
    condition = make_condition_name(parent_name="WS-SWITCHES", parent_qualified_name="WS-SWITCHES")
    assert condition.parent_name == "WS-SWITCHES"
    assert condition.parent_qualified_name == "WS-SWITCHES"


def test_condition_name_requires_parent_name() -> None:
    with pytest.raises(ValidationError):
        make_condition_name(parent_name="")


def test_condition_name_qualified_name_is_required() -> None:
    with pytest.raises(ValidationError):
        make_condition_name(qualified_name="")


def test_program_rejects_homonymous_condition_names_with_same_qualified_name() -> None:
    program = make_program()
    duplicate = make_condition_name()
    with pytest.raises(ValidationError):
        CanonicalProgram(
            **{
                **program.model_dump(mode="json"),
                "condition_names": [
                    duplicate.model_dump(mode="json"),
                    duplicate.model_dump(mode="json"),
                ],
            }
        )


def test_program_accepts_homonymous_condition_names_under_different_parents() -> None:
    """Mismo `name` bajo padres distintos es valido a nivel de contrato
    (qualified_name los distingue); la resolucion de ambiguedad por
    nombre simple ocurre en el parser Java (ver
    docs/LEVEL_88_SUPPORT.md), no aqui."""
    program = make_program()
    a = make_condition_name(
        name="COD-DUP",
        qualified_name="WS-X.COD-DUP",
        parent_name="WS-X",
        parent_qualified_name="WS-X",
    )
    b = make_condition_name(
        name="COD-DUP",
        qualified_name="WS-Y.COD-DUP",
        parent_name="WS-Y",
        parent_qualified_name="WS-Y",
    )
    program = CanonicalProgram(
        **{
            **program.model_dump(mode="json"),
            "condition_names": [a.model_dump(mode="json"), b.model_dump(mode="json")],
        }
    )
    assert len(program.condition_names) == 2


def test_set_statement_with_condition_name_target_resolved() -> None:
    statement = make_statement(
        kind=StatementKind.SET,
        source_text="SET COD-CAMPO-INVALIDO TO TRUE",
        target_data_items=["COD-CAMPO-INVALIDO"],
        variables_written=["COD-CAMPO-INVALIDO"],
        assigned_literal="true",
        condition_name_target="COD-CAMPO-INVALIDO",
        condition_set_value=True,
    )
    assert statement.condition_name_target == "COD-CAMPO-INVALIDO"
    assert statement.condition_set_value is True


def test_set_condition_target_and_value_must_both_be_present_or_absent() -> None:
    with pytest.raises(ValidationError):
        make_statement(
            kind=StatementKind.SET, condition_name_target="COD-X", condition_set_value=None
        )
    with pytest.raises(ValidationError):
        make_statement(kind=StatementKind.SET, condition_name_target=None, condition_set_value=True)


def test_ordinary_set_never_populates_condition_fields() -> None:
    statement = make_statement(
        kind=StatementKind.SET,
        source_text="SET WS-INDICE TO 1",
        target_data_items=["WS-INDICE"],
        variables_written=["WS-INDICE"],
        assigned_literal="1",
    )
    assert statement.condition_name_target is None
    assert statement.condition_set_value is None


def test_index_set_never_confused_with_condition_name() -> None:
    statement = make_statement(
        kind=StatementKind.SET,
        source_text="SET WS-IDX TO 5",
        target_data_items=["WS-IDX"],
        variables_written=["WS-IDX"],
        assigned_literal="5",
    )
    assert statement.condition_name_target is None


def test_if_with_direct_condition_reference() -> None:
    statement = make_statement(
        kind=StatementKind.IF,
        source_text="IF COD-CAMPO-INVALIDO",
        expression="COD-CAMPO-INVALIDO",
        referenced_condition_names=["COD-CAMPO-INVALIDO"],
    )
    assert statement.referenced_condition_names == ["COD-CAMPO-INVALIDO"]


def test_if_with_ordinary_variable_has_no_condition_reference() -> None:
    statement = make_statement(
        kind=StatementKind.IF,
        source_text="IF WS-MONTO > WS-LIMITE",
        operands=["WS-MONTO", "WS-LIMITE"],
    )
    assert statement.referenced_condition_names == []


def test_evaluate_when_branch_carries_referenced_condition_names() -> None:
    statement = make_statement(
        kind=StatementKind.OTHER,
        source_text="DISPLAY 'OK'",
        branch_kind=BranchKind.WHEN,
        parent_statement_id="P1::A::0::EVALUATE",
        referenced_condition_names=["COD-CAMPO-INVALIDO"],
    )
    assert statement.referenced_condition_names == ["COD-CAMPO-INVALIDO"]


def test_referenced_condition_names_rejects_unsorted() -> None:
    with pytest.raises(ValidationError):
        make_statement(referenced_condition_names=["COD-B", "COD-A"])


def test_referenced_condition_names_rejects_duplicates() -> None:
    with pytest.raises(ValidationError):
        make_statement(referenced_condition_names=["COD-A", "COD-A"])


def test_condition_name_has_no_source_text_field() -> None:
    assert "source_text" not in CanonicalConditionName.model_fields
    assert "source_text" not in CanonicalConditionValue.model_fields


def test_condition_value_rejects_absolute_source_file() -> None:
    with pytest.raises(ValidationError):
        make_condition_value(source_file="/etc/passwd")
    with pytest.raises(ValidationError):
        make_condition_value(source_file="C:/absolute/path.cbl")


# --- schema_version (Fase 3, soporte nivel 88): "1.0"/"1.1" condicional ----


def test_schema_version_defaults_to_1_0() -> None:
    assert make_program().schema_version == "1.0"


def test_schema_version_accepts_historical_1_0_explicitly() -> None:
    program = CanonicalProgram(
        **{**make_program().model_dump(mode="json"), "schema_version": "1.0"}
    )
    assert program.schema_version == "1.0"


def test_schema_version_accepts_1_1_for_program_with_level_88_extension() -> None:
    program = CanonicalProgram(
        **{
            **make_program().model_dump(mode="json"),
            "schema_version": "1.1",
            "condition_names": [make_condition_name().model_dump(mode="json")],
        }
    )
    assert program.schema_version == "1.1"
    assert len(program.condition_names) == 1


def test_schema_version_rejects_unknown_version() -> None:
    with pytest.raises(ValidationError):
        CanonicalProgram(
            **{**make_program().model_dump(mode="json"), "schema_version": "2.0"}
        )


# ---------------------------------------------------------------------------
# CALL / LINKAGE SECTION (Fase 6, fundacion interprocedural): schema 1.2
# ---------------------------------------------------------------------------


def make_call_argument(**overrides: object) -> CanonicalCallArgument:
    fields: dict[str, object] = {
        "ordinal": 1,
        "expression": "WS-INPUT",
        "data_item_name": "WS-INPUT",
        "qualified_data_item_name": "WS-INPUT",
        "passing_mode": CallPassingMode.REFERENCE,
        "source_file": SOURCE_FILE,
        "line": 15,
        "location_kind": LocationKind.EXACT,
    }
    fields.update(overrides)
    return CanonicalCallArgument(**fields)  # type: ignore[arg-type]


def make_entry_parameter(**overrides: object) -> CanonicalEntryParameter:
    fields: dict[str, object] = {
        "ordinal": 1,
        "name": "LK-INPUT",
        "qualified_name": "LK-INPUT",
        "linkage_item_qualified_name": "LK-INPUT",
        "passing_mode": CallPassingMode.REFERENCE,
        "source_file": SOURCE_FILE,
        "line": 9,
        "location_kind": LocationKind.EXACT,
    }
    fields.update(overrides)
    return CanonicalEntryParameter(**fields)  # type: ignore[arg-type]


def make_linkage_data_item(**overrides: object) -> CanonicalLinkageDataItem:
    fields: dict[str, object] = {
        "name": "LK-INPUT",
        "qualified_name": "LK-INPUT",
        "level": 1,
        "pic": "X(10)",
        "source_file": SOURCE_FILE,
        "line": 8,
        "location_kind": LocationKind.EXACT,
    }
    fields.update(overrides)
    return CanonicalLinkageDataItem(**fields)  # type: ignore[arg-type]


def make_call_statement(**overrides: object) -> CanonicalStatement:
    fields: dict[str, object] = {
        "statement_id": "ARTRFPROP01::PARA-1::0::CALL",
        "kind": StatementKind.CALL,
        "source_text": "CALL 'CALLEEP' USING WS-INPUT",
        "source_file": SOURCE_FILE,
        "line_start": 15,
        "line_end": 15,
        "location_kind": LocationKind.EXACT,
        "call_target_kind": CallTargetKind.LITERAL,
        "called_program_name": "CALLEEP",
    }
    fields.update(overrides)
    return CanonicalStatement(**fields)  # type: ignore[arg-type]


# --- CanonicalCallArgument ---------------------------------------------------


def test_call_argument_round_trips() -> None:
    argument = make_call_argument()
    restored = CanonicalCallArgument.model_validate_json(argument.to_stable_json())
    assert restored == argument


def test_call_argument_location_kind_is_enforced() -> None:
    with pytest.raises(ValidationError):
        make_call_argument(source_file=None, location_kind=LocationKind.EXACT)


def test_call_argument_omitted_forbids_data_item_and_literal() -> None:
    with pytest.raises(ValidationError, match="omitted=True"):
        make_call_argument(
            omitted=True,
            data_item_name="WS-INPUT",
            qualified_data_item_name="WS-INPUT",
        )


def test_call_argument_omitted_true_with_no_other_fields_is_valid() -> None:
    argument = make_call_argument(
        expression="OMITTED",
        data_item_name=None,
        qualified_data_item_name=None,
        omitted=True,
    )
    assert argument.omitted is True
    assert argument.data_item_name is None


def test_call_argument_literal_and_data_item_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="literal y data_item_name"):
        make_call_argument(literal="'X'", data_item_name="WS-INPUT")


def test_call_argument_pure_literal_is_valid() -> None:
    argument = make_call_argument(
        expression="'X'",
        data_item_name=None,
        qualified_data_item_name=None,
        literal="'X'",
        passing_mode=CallPassingMode.CONTENT,
    )
    assert argument.literal == "'X'"
    assert argument.data_item_name is None


# --- CanonicalEntryParameter --------------------------------------------------


def test_entry_parameter_round_trips() -> None:
    parameter = make_entry_parameter()
    restored = CanonicalEntryParameter.model_validate_json(parameter.to_stable_json())
    assert restored == parameter


def test_entry_parameter_location_kind_is_enforced() -> None:
    with pytest.raises(ValidationError):
        make_entry_parameter(source_file=None, location_kind=LocationKind.EXACT)


def test_entry_parameter_allows_unresolved_linkage_item() -> None:
    """Cuando el nombre formal no puede resolverse contra
    linkage_data_items (homonimo ambiguo o ausente), linkage_item_qualified_name
    queda None -- nunca se inventa una definicion."""
    parameter = make_entry_parameter(linkage_item_qualified_name=None, passing_mode=None)
    assert parameter.linkage_item_qualified_name is None
    assert parameter.passing_mode is None


# --- CanonicalLinkageDataItem --------------------------------------------------


def test_linkage_data_item_round_trips() -> None:
    item = make_linkage_data_item()
    restored = CanonicalLinkageDataItem.model_validate_json(item.to_stable_json())
    assert restored == item


def test_linkage_data_item_location_kind_is_enforced() -> None:
    with pytest.raises(ValidationError):
        make_linkage_data_item(source_file=None, location_kind=LocationKind.EXACT)


def test_linkage_data_item_never_mixed_with_working_storage_field() -> None:
    """linkage_data_items es un campo separado de data_items: WORKING-STORAGE
    y LINKAGE nunca se mezclan en la misma lista (ver docstring de
    CanonicalLinkageDataItem)."""
    program = CanonicalProgram(
        **{
            **make_program().model_dump(mode="json"),
            "linkage_data_items": [make_linkage_data_item().model_dump(mode="json")],
        }
    )
    assert len(program.linkage_data_items) == 1
    assert len(program.data_items) == 1
    assert program.data_items[0].name != program.linkage_data_items[0].name


# --- CanonicalStatement: campos estructurados de CALL -------------------------


def test_call_statement_requires_call_target_kind() -> None:
    with pytest.raises(ValidationError, match="call_target_kind"):
        make_call_statement(call_target_kind=None, called_program_name=None)


def test_non_call_statement_forbids_call_fields() -> None:
    with pytest.raises(ValidationError, match="no puede declarar campos estructurados de CALL"):
        make_statement(
            kind=StatementKind.MOVE,
            source_text="MOVE A TO B",
            call_target_kind=CallTargetKind.LITERAL,
            called_program_name="CALLEEP",
        )


def test_literal_call_requires_program_name_and_forbids_expression() -> None:
    with pytest.raises(ValidationError, match="LITERAL requiere called_program_name"):
        make_call_statement(called_program_name=None)
    with pytest.raises(
        ValidationError, match="LITERAL no puede declarar called_program_expression"
    ):
        make_call_statement(called_program_expression="WS-PROGRAM-NAME")


def test_dynamic_call_requires_expression_and_forbids_name() -> None:
    with pytest.raises(ValidationError, match="DYNAMIC requiere called_program_expression"):
        make_call_statement(
            call_target_kind=CallTargetKind.DYNAMIC,
            called_program_name=None,
            called_program_expression=None,
        )
    with pytest.raises(ValidationError, match="DYNAMIC no puede declarar called_program_name"):
        make_call_statement(
            call_target_kind=CallTargetKind.DYNAMIC,
            called_program_expression="WS-PROGRAM-NAME",
        )


def test_dynamic_call_is_valid_with_only_expression() -> None:
    statement = make_call_statement(
        call_target_kind=CallTargetKind.DYNAMIC,
        called_program_name=None,
        called_program_expression="WS-PROGRAM-NAME",
    )
    assert statement.called_program_expression == "WS-PROGRAM-NAME"
    assert statement.called_program_name is None


def test_unknown_target_kind_forbids_name_and_expression() -> None:
    with pytest.raises(ValidationError, match="UNKNOWN no puede declarar"):
        make_call_statement(
            call_target_kind=CallTargetKind.UNKNOWN,
            called_program_name="CALLEEP",
        )


def test_unknown_target_kind_with_no_name_or_expression_is_valid() -> None:
    statement = make_call_statement(
        call_target_kind=CallTargetKind.UNKNOWN,
        called_program_name=None,
    )
    assert statement.call_target_kind == CallTargetKind.UNKNOWN


def test_call_never_populates_generic_write_targets() -> None:
    """Fase 6: CALL nunca afirma un efecto de escritura CIERTO a nivel
    canonico (a diferencia de MOVE/SET/COMPUTE) -- BY REFERENCE y
    RETURNING solo describen un efecto POTENCIAL, capturado
    exclusivamente via call_arguments/call_returning_data_item."""
    statement = make_call_statement(
        call_arguments=[make_call_argument().model_dump(mode="json")],
        call_returning_data_item="WS-RESULT",
    )
    assert statement.target_data_items == []
    assert statement.variables_written == []


def test_call_arguments_require_consecutive_ordinals() -> None:
    with pytest.raises(ValidationError, match="ordinal consecutivo"):
        make_call_statement(
            call_arguments=[
                make_call_argument(ordinal=2).model_dump(mode="json"),
            ]
        )


def test_call_arguments_reject_out_of_order_ordinals() -> None:
    with pytest.raises(ValidationError, match="ordinal consecutivo"):
        make_call_statement(
            call_arguments=[
                make_call_argument(ordinal=1).model_dump(mode="json"),
                make_call_argument(
                    ordinal=3,
                    expression="WS-FLAG",
                    data_item_name="WS-FLAG",
                    qualified_data_item_name="WS-FLAG",
                ).model_dump(mode="json"),
            ]
        )


def test_call_statement_with_multiple_arguments_round_trips() -> None:
    statement = make_call_statement(
        call_arguments=[
            make_call_argument().model_dump(mode="json"),
            make_call_argument(
                ordinal=2,
                expression="WS-FLAG",
                data_item_name="WS-FLAG",
                qualified_data_item_name="WS-FLAG",
                passing_mode=CallPassingMode.CONTENT,
            ).model_dump(mode="json"),
        ],
        call_returning_data_item="WS-RESULT",
    )
    first = statement.to_stable_json()
    second = CanonicalStatement.model_validate_json(first).to_stable_json()
    assert first == second
    assert len(statement.call_arguments) == 2


def test_call_has_on_exception_flags_default_false() -> None:
    statement = make_call_statement()
    assert statement.call_has_on_exception is False
    assert statement.call_has_not_on_exception is False


# --- CanonicalProgram: linkage_data_items / entry_parameters ------------------


def test_program_without_linkage_serializes_with_empty_lists() -> None:
    program = make_program()
    assert program.linkage_data_items == []
    assert program.entry_parameters == []
    assert program.entry_returning_data_item is None
    assert '"linkage_data_items": []' in program.to_stable_json()
    assert '"entry_parameters": []' in program.to_stable_json()


def test_historical_program_without_linkage_loads_without_new_keys() -> None:
    """Un CanonicalProgram JSON historico (anterior a la Fase 6) no tiene
    las claves de LINKAGE/entry -- debe seguir cargando con listas vacias
    por defecto, nunca un error de campo faltante."""
    program = make_program()
    payload = program.model_dump(mode="json")
    del payload["linkage_data_items"]
    del payload["entry_parameters"]
    del payload["entry_returning_data_item"]
    loaded = CanonicalProgram.model_validate(payload)
    assert loaded.linkage_data_items == []
    assert loaded.entry_parameters == []


def test_linkage_data_items_reject_duplicate_qualified_name() -> None:
    duplicate = make_linkage_data_item()
    with pytest.raises(ValidationError, match="qualified_name duplicado"):
        CanonicalProgram(
            **{
                **make_program().model_dump(mode="json"),
                "linkage_data_items": [
                    duplicate.model_dump(mode="json"),
                    duplicate.model_dump(mode="json"),
                ],
            }
        )


def test_entry_parameters_require_consecutive_ordinals() -> None:
    with pytest.raises(ValidationError, match="ordinal consecutivo"):
        CanonicalProgram(
            **{
                **make_program().model_dump(mode="json"),
                "entry_parameters": [
                    make_entry_parameter(ordinal=2).model_dump(mode="json"),
                ],
            }
        )


def test_program_with_linkage_and_entry_parameters_round_trips() -> None:
    program = CanonicalProgram(
        **{
            **make_program().model_dump(mode="json"),
            "schema_version": "1.2",
            "linkage_data_items": [
                make_linkage_data_item().model_dump(mode="json"),
                make_linkage_data_item(
                    name="LK-RESULT", qualified_name="LK-RESULT", line=10
                ).model_dump(mode="json"),
            ],
            "entry_parameters": [make_entry_parameter().model_dump(mode="json")],
            "entry_returning_data_item": "LK-RESULT",
        }
    )
    first = program.to_stable_json()
    second = CanonicalProgram.model_validate_json(first).to_stable_json()
    assert first == second
    assert program.schema_version == "1.2"
    assert program.entry_returning_data_item == "LK-RESULT"


def test_schema_version_accepts_1_2_for_program_with_call_extension() -> None:
    program = make_program(paragraphs=[make_paragraph(statements=[make_call_statement()])])
    program = CanonicalProgram(
        **{**program.model_dump(mode="json"), "schema_version": "1.2"}
    )
    assert program.schema_version == "1.2"


# --- PROGRAM_TERMINATION (Fase 7b: GOBACK/STOP RUN/EXIT PROGRAM) -----------


def make_terminator_statement(**overrides: object) -> CanonicalStatement:
    fields: dict[str, object] = {
        "statement_id": "ARTRFPROP01::PARA-1::0::PROGRAM_TERMINATION",
        "kind": StatementKind.PROGRAM_TERMINATION,
        "source_text": "GOBACK",
        "source_file": SOURCE_FILE,
        "line_start": 15,
        "line_end": 15,
        "location_kind": LocationKind.EXACT,
        "program_termination_kind": ProgramTerminationKind.GOBACK,
    }
    fields.update(overrides)
    return CanonicalStatement(**fields)  # type: ignore[arg-type]


def test_program_termination_statement_round_trips() -> None:
    stmt = make_terminator_statement()
    assert stmt.kind == StatementKind.PROGRAM_TERMINATION
    assert stmt.program_termination_kind == ProgramTerminationKind.GOBACK


def test_program_termination_kind_accepts_all_four_values() -> None:
    for kind in ProgramTerminationKind:
        stmt = make_terminator_statement(program_termination_kind=kind)
        assert stmt.program_termination_kind == kind


def test_program_termination_kind_required_when_kind_is_program_termination() -> None:
    with pytest.raises(ValidationError, match="program_termination_kind"):
        make_terminator_statement(program_termination_kind=None)


def test_program_termination_kind_rejected_for_other_statement_kinds() -> None:
    with pytest.raises(ValidationError, match="program_termination_kind"):
        CanonicalStatement(
            statement_id="ARTRFPROP01::PARA-1::0::MOVE",
            kind=StatementKind.MOVE,
            source_text="MOVE",
            location_kind=LocationKind.UNKNOWN,
            program_termination_kind=ProgramTerminationKind.GOBACK,
        )


def test_schema_version_accepts_1_3_for_program_with_program_termination_extension() -> None:
    program = make_program(
        paragraphs=[make_paragraph(statements=[make_terminator_statement()])]
    )
    program = CanonicalProgram(**{**program.model_dump(mode="json"), "schema_version": "1.3"})
    assert program.schema_version == "1.3"


def test_schema_version_rejects_1_4() -> None:
    program = make_program()
    with pytest.raises(ValidationError):
        CanonicalProgram(**{**program.model_dump(mode="json"), "schema_version": "1.4"})


# --- Forma anterior (v1.5.0, kind=OTHER) vs forma nueva (1.3, --------------
# --- kind=PROGRAM_TERMINATION): comparacion explicita de identidad --------
# --- posicional (auditoria de cierre, Parte 1/mid-turn). -------------------
#
# statement_id tiene el formato preexistente (anterior a Fase 7b)
# "<program>::<paragraph>::<ordinal>::<kind>" (StatementExtractor.nextId,
# lado Java): el SUFIJO de kind SIEMPRE formo parte del ID, para
# CUALQUIER StatementKind. Reclasificar OTHER->PROGRAM_TERMINATION
# cambia inevitablemente ese sufijo -- lo que NUNCA debe cambiar es el
# PREFIJO posicional "<program>::<paragraph>::<ordinal>", que es la
# identidad real de "que statement es este" independiente de su kind.
# Deuda arquitectonica registrada (no resuelta aqui, ver
# docs/INTERPROCEDURAL_PROPAGATION.md): "statement_id incorpora una
# clasificacion semantica mutable (kind); evaluar desacoplar identidad
# posicional y kind en una fase especifica de migracion de IDs."


def _statement_id_prefix(statement_id: str) -> str:
    return statement_id.rsplit("::", 1)[0]


def _statement_id_suffix(statement_id: str) -> str:
    return statement_id.rsplit("::", 1)[-1]


def _old_shape_terminator_statement(
    *, statement_id: str, parent_statement_id: str | None = None
) -> CanonicalStatement:
    """Statement tal como lo habria producido v1.5.0 para GOBACK/STOP
    RUN/EXIT PROGRAM: siempre kind=OTHER, source_text conservado, sin
    program_termination_kind (el campo ni existia)."""
    return CanonicalStatement(
        statement_id=statement_id,
        kind=StatementKind.OTHER,
        source_text="GOBACK",
        source_file=SOURCE_FILE,
        line_start=20,
        line_end=20,
        location_kind=LocationKind.EXACT,
        parent_statement_id=parent_statement_id,
    )


def _new_shape_terminator_statement(
    *, statement_id: str, parent_statement_id: str | None = None
) -> CanonicalStatement:
    """Mismo statement, forma 1.3: kind=PROGRAM_TERMINATION,
    program_termination_kind poblado. Mismo statement_id COMPLETO que se
    le pasa -- el llamador decide si usa el mismo sufijo (para probar
    que NO deberia coincidir en la practica) o el sufijo correcto."""
    return CanonicalStatement(
        statement_id=statement_id,
        kind=StatementKind.PROGRAM_TERMINATION,
        source_text="GOBACK",
        source_file=SOURCE_FILE,
        line_start=20,
        line_end=20,
        location_kind=LocationKind.EXACT,
        parent_statement_id=parent_statement_id,
        program_termination_kind=ProgramTerminationKind.GOBACK,
    )


def test_A_statement_id_prefix_identical_between_old_and_new_shape() -> None:
    """El prefijo program::paragraph::ordinal es identico entre la forma
    v1.5.0 (OTHER) y la forma 1.3 (PROGRAM_TERMINATION) para el MISMO
    statement fisico."""
    old = _old_shape_terminator_statement(statement_id="ARTRFPROP01::PARA-1::5::OTHER")
    new = _new_shape_terminator_statement(
        statement_id="ARTRFPROP01::PARA-1::5::PROGRAM_TERMINATION"
    )
    assert _statement_id_prefix(old.statement_id) == _statement_id_prefix(new.statement_id)
    assert _statement_id_prefix(old.statement_id) == "ARTRFPROP01::PARA-1::5"


def test_B_only_kind_suffix_and_program_termination_kind_change() -> None:
    """Unicamente cambian: el sufijo de statement_id, kind y
    program_termination_kind. Todo lo demas (source_file, line_start/end,
    location_kind, parent_statement_id, branch_kind, variables_read/
    written, target_data_items, assigned_literal) permanece identico."""
    old = _old_shape_terminator_statement(
        statement_id="ARTRFPROP01::PARA-1::5::OTHER", parent_statement_id=None
    )
    new = _new_shape_terminator_statement(
        statement_id="ARTRFPROP01::PARA-1::5::PROGRAM_TERMINATION", parent_statement_id=None
    )

    assert _statement_id_suffix(old.statement_id) == "OTHER"
    assert _statement_id_suffix(new.statement_id) == "PROGRAM_TERMINATION"
    assert old.kind != new.kind
    assert old.program_termination_kind is None
    assert new.program_termination_kind == ProgramTerminationKind.GOBACK

    frozen_fields = [
        "source_file", "line_start", "line_end", "location_kind",
        "parent_statement_id", "branch_kind", "branch_condition",
        "variables_read", "variables_written", "target_data_items",
        "assigned_literal", "target_paragraphs", "operands",
    ]
    for field in frozen_fields:
        assert getattr(old, field) == getattr(new, field), f"{field} no deberia cambiar"


def test_C_non_terminator_statements_keep_full_id_unchanged() -> None:
    """En un parrafo con un MOVE seguido de un terminador, el MOVE
    conserva su statement_id COMPLETO (prefijo Y sufijo) sin ningun
    cambio -- solo el terminador se ve afectado."""
    move_stmt = CanonicalStatement(
        statement_id="ARTRFPROP01::PARA-1::0::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE 'A' TO WS-X",
        source_file=SOURCE_FILE,
        line_start=10,
        line_end=10,
        location_kind=LocationKind.EXACT,
        target_data_items=["WS-X"],
        variables_written=["WS-X"],
        assigned_literal="A",
    )
    old_terminator = _old_shape_terminator_statement(
        statement_id="ARTRFPROP01::PARA-1::1::OTHER"
    )
    new_terminator = _new_shape_terminator_statement(
        statement_id="ARTRFPROP01::PARA-1::1::PROGRAM_TERMINATION"
    )

    old_paragraph = make_paragraph(statements=[move_stmt, old_terminator])
    new_paragraph = make_paragraph(statements=[move_stmt, new_terminator])

    assert old_paragraph.statements[0].statement_id == new_paragraph.statements[0].statement_id
    assert old_paragraph.statements[0].statement_id == "ARTRFPROP01::PARA-1::0::MOVE"


def test_D_statement_order_and_cardinality_unchanged() -> None:
    """La cantidad de statements y su orden dentro del parrafo son
    identicos entre la forma antigua y la nueva -- la reclasificacion
    nunca inserta, elimina ni reordena statements."""
    move_stmt = CanonicalStatement(
        statement_id="ARTRFPROP01::PARA-1::0::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE 'A' TO WS-X",
        location_kind=LocationKind.UNKNOWN,
        target_data_items=["WS-X"],
        variables_written=["WS-X"],
        assigned_literal="A",
    )
    old_paragraph = make_paragraph(
        statements=[
            move_stmt,
            _old_shape_terminator_statement(statement_id="ARTRFPROP01::PARA-1::1::OTHER"),
        ]
    )
    new_paragraph = make_paragraph(
        statements=[
            move_stmt,
            _new_shape_terminator_statement(
                statement_id="ARTRFPROP01::PARA-1::1::PROGRAM_TERMINATION"
            ),
        ]
    )
    assert len(old_paragraph.statements) == len(new_paragraph.statements) == 2
    assert [s.kind for s in old_paragraph.statements] == [StatementKind.MOVE, StatementKind.OTHER]
    assert [s.kind for s in new_paragraph.statements] == [
        StatementKind.MOVE,
        StatementKind.PROGRAM_TERMINATION,
    ]


def test_G_reclassification_mapping_is_bijective_by_position() -> None:
    """El mapping posicional old_statement_id -> new_statement_id (SOLO
    para verificacion, nunca persistido) es biunivoco: cada ID antiguo
    reclasificado corresponde a exactamente un ID nuevo, identificados
    por la MISMA posicion (program::paragraph::ordinal)."""
    positions = [(f"ARTRFPROP01::PARA-1::{i}", ) for i in range(3)]
    old_ids = [f"{prefix}::OTHER" for (prefix,) in positions]
    new_ids = [f"{prefix}::PROGRAM_TERMINATION" for (prefix,) in positions]
    mapping = dict(zip(old_ids, new_ids, strict=True))
    assert len(mapping) == len(old_ids) == len(set(new_ids))
    for old_id, new_id in mapping.items():
        assert _statement_id_prefix(old_id) == _statement_id_prefix(new_id)
