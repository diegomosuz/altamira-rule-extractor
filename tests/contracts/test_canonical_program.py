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
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalSqlAccess,
    CanonicalStatement,
    LocationKind,
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
