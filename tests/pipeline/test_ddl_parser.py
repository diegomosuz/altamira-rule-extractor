"""Tests unitarios de ddl_parser: subconjunto acotado de CREATE TABLE."""

from __future__ import annotations

from altamira_extractor.contracts.enums import ParseSupportStatus
from altamira_extractor.pipeline.ddl_parser import parse_ddl_for_table


def test_simple_create_table() -> None:
    ddl = """
    CREATE TABLE PARAM_TRANSFER (
        ID INTEGER NOT NULL,
        LIMITE DECIMAL(9,2) NOT NULL,
        DESCRIPCION VARCHAR(40)
    );
    """
    warnings: list[str] = []
    result = parse_ddl_for_table(
        ddl, declared_table_name="PARAM_TRANSFER", table_label="PARAM_TRANSFER", warnings=warnings
    )

    assert result.support_status == ParseSupportStatus.SUPPORTED
    assert not warnings
    names = [c.original_name for c in result.columns]
    assert names == ["ID", "LIMITE", "DESCRIPCION"]
    assert result.columns[0].nullable is False
    assert result.columns[2].nullable is None
    assert result.columns[0].ordinal == 1
    assert result.columns[2].ordinal == 3


def test_schema_qualified_table_name() -> None:
    ddl = "CREATE TABLE PARAMDB.PARAM_TRANSFER (ID INTEGER NOT NULL);"
    warnings: list[str] = []
    result = parse_ddl_for_table(
        ddl,
        declared_table_name="PARAMDB.PARAM_TRANSFER",
        table_label="t",
        warnings=warnings,
    )
    assert result.support_status == ParseSupportStatus.SUPPORTED
    assert not warnings


def test_quoted_identifiers() -> None:
    ddl = 'CREATE TABLE "Param Transfer" ("ID Col" INTEGER NOT NULL);'
    warnings: list[str] = []
    result = parse_ddl_for_table(
        ddl, declared_table_name="Param Transfer", table_label="t", warnings=warnings
    )
    assert result.support_status == ParseSupportStatus.SUPPORTED
    assert result.columns[0].original_name == "ID Col"
    assert result.columns[0].normalized_name == "ID COL"


def test_multiple_create_table_exact_match() -> None:
    ddl = """
    CREATE TABLE OTHER_TABLE (X INTEGER);
    CREATE TABLE PARAM_TRANSFER (ID INTEGER NOT NULL);
    """
    warnings: list[str] = []
    result = parse_ddl_for_table(
        ddl, declared_table_name="PARAM_TRANSFER", table_label="t", warnings=warnings
    )
    assert result.support_status == ParseSupportStatus.SUPPORTED
    assert len(result.columns) == 1
    assert not warnings


def test_terminal_name_match_when_manifest_unqualified() -> None:
    ddl = "CREATE TABLE PARAMDB.PARAM_TRANSFER (ID INTEGER NOT NULL);"
    warnings: list[str] = []
    result = parse_ddl_for_table(
        ddl, declared_table_name="PARAM_TRANSFER", table_label="t", warnings=warnings
    )
    assert result.support_status == ParseSupportStatus.SUPPORTED
    assert not warnings


def test_ambiguous_terminal_match_is_unsupported() -> None:
    ddl = """
    CREATE TABLE SCHEMA_A.PARAM_TRANSFER (ID INTEGER);
    CREATE TABLE SCHEMA_B.PARAM_TRANSFER (ID INTEGER);
    """
    warnings: list[str] = []
    result = parse_ddl_for_table(
        ddl, declared_table_name="PARAM_TRANSFER", table_label="t", warnings=warnings
    )
    assert result.support_status == ParseSupportStatus.UNSUPPORTED
    assert result.columns == []
    assert any("multiples" in w for w in warnings)


def test_decimal_with_precision_and_scale_not_split_on_internal_comma() -> None:
    ddl = "CREATE TABLE T (MONTO DECIMAL(9,2) NOT NULL);"
    warnings: list[str] = []
    result = parse_ddl_for_table(ddl, declared_table_name="T", table_label="t", warnings=warnings)
    assert result.support_status == ParseSupportStatus.SUPPORTED
    assert len(result.columns) == 1
    assert result.columns[0].declared_type == "DECIMAL(9,2)"


def test_primary_key_inline() -> None:
    ddl = "CREATE TABLE T (ID INTEGER NOT NULL PRIMARY KEY, NOMBRE VARCHAR(10));"
    warnings: list[str] = []
    result = parse_ddl_for_table(ddl, declared_table_name="T", table_label="t", warnings=warnings)
    assert result.columns[0].is_primary_key is True
    assert result.columns[1].is_primary_key is None


def test_primary_key_table_level_constraint() -> None:
    ddl = """
    CREATE TABLE T (
        ID INTEGER NOT NULL,
        SUBID INTEGER NOT NULL,
        NOMBRE VARCHAR(10),
        PRIMARY KEY (ID, SUBID)
    );
    """
    warnings: list[str] = []
    result = parse_ddl_for_table(ddl, declared_table_name="T", table_label="t", warnings=warnings)
    assert result.support_status == ParseSupportStatus.SUPPORTED
    by_name = {c.original_name: c for c in result.columns}
    assert by_name["ID"].is_primary_key is True
    assert by_name["SUBID"].is_primary_key is True
    assert by_name["NOMBRE"].is_primary_key is None


def test_line_and_block_comments_are_ignored() -> None:
    ddl = """
    -- tabla de parametros
    CREATE TABLE T (
        ID INTEGER NOT NULL, -- identificador
        /* columna de nombre
           en varias lineas */
        NOMBRE VARCHAR(10)
    );
    """
    warnings: list[str] = []
    result = parse_ddl_for_table(ddl, declared_table_name="T", table_label="t", warnings=warnings)
    assert result.support_status == ParseSupportStatus.SUPPORTED
    assert [c.original_name for c in result.columns] == ["ID", "NOMBRE"]


def test_comment_like_text_inside_literal_is_preserved() -> None:
    # El valor por defecto contiene '--' dentro de un literal: no debe
    # tratarse como el inicio de un comentario real.
    ddl = "CREATE TABLE T (CODIGO VARCHAR(10) DEFAULT 'N--A');"
    warnings: list[str] = []
    result = parse_ddl_for_table(ddl, declared_table_name="T", table_label="t", warnings=warnings)
    # La columna se interpreta (con DEFAULT sin interpretar -> PARTIAL),
    # y el literal completo sigue presente en el tipo declarado crudo.
    assert result.support_status == ParseSupportStatus.PARTIAL
    assert "N--A" in result.columns[0].declared_type


def test_unsupported_constraint_marks_partial_but_keeps_columns() -> None:
    ddl = """
    CREATE TABLE T (
        ID INTEGER NOT NULL,
        OTHER_ID INTEGER,
        FOREIGN KEY (OTHER_ID) REFERENCES OTHER_TABLE(ID)
    );
    """
    warnings: list[str] = []
    result = parse_ddl_for_table(ddl, declared_table_name="T", table_label="t", warnings=warnings)
    assert result.support_status == ParseSupportStatus.PARTIAL
    assert [c.original_name for c in result.columns] == ["ID", "OTHER_ID"]
    assert any("FOREIGN KEY" in w or "no soportadas" in w for w in warnings)


def test_unrecognizable_ddl_is_unsupported() -> None:
    ddl = "ALTER TABLE T ADD COLUMN X INTEGER;"
    warnings: list[str] = []
    result = parse_ddl_for_table(ddl, declared_table_name="T", table_label="t", warnings=warnings)
    assert result.support_status == ParseSupportStatus.UNSUPPORTED
    assert result.columns == []
    assert warnings
