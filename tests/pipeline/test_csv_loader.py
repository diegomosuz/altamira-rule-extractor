"""Tests unitarios de csv_loader: lectura acotada de snapshots CSV."""

from __future__ import annotations

from altamira_extractor.contracts.enums import ParseSupportStatus
from altamira_extractor.pipeline.csv_loader import load_csv_snapshot

TABLE_ID = "parameter::table::AR::DEFAULT::PARAM_TRANSFER::2026-05-15::abc123456789"


def test_utf8_snapshot_loaded() -> None:
    csv_bytes = "ID,NOMBRE\n1,José\n2,María\n".encode()
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="t",
        warnings=warnings,
    )
    assert result.support_status == ParseSupportStatus.SUPPORTED
    assert len(result.entries) == 2
    assert result.entries[0].raw_row == {"ID": "1", "NOMBRE": "José"}
    assert not warnings


def test_windows_1252_snapshot_loaded() -> None:
    # bytes 0x93/0x94 = comillas curvas en cp1252 (decodifican a U+201C/U+201D).
    csv_bytes = b"ID,NOMBRE\n1," + bytes([0x93]) + b"HOLA" + bytes([0x94]) + b"\n"
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="windows-1252",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="t",
        warnings=warnings,
    )
    assert result.support_status == ParseSupportStatus.SUPPORTED
    assert result.entries[0].raw_row["NOMBRE"] == "“HOLA”"


def test_iso_8859_1_snapshot_loaded() -> None:
    csv_bytes = "ID,NOMBRE\n1,José\n".encode("iso-8859-1")
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="ISO-8859-1",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="t",
        warnings=warnings,
    )
    assert result.support_status == ParseSupportStatus.SUPPORTED
    assert result.entries[0].raw_row["NOMBRE"] == "José"


def test_undecodable_bytes_for_declared_encoding_is_unsupported() -> None:
    csv_bytes = b"ID,NOMBRE\n1,\xff\xfe\n"
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="mi_tabla",
        warnings=warnings,
    )
    assert result.support_status == ParseSupportStatus.UNSUPPORTED
    assert result.entries == []
    assert any("mi_tabla" in w for w in warnings)


def test_empty_file_has_no_header_and_is_unsupported() -> None:
    warnings: list[str] = []
    result = load_csv_snapshot(
        b"",
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="t",
        warnings=warnings,
    )
    assert result.support_status == ParseSupportStatus.UNSUPPORTED
    assert warnings


def test_duplicate_normalized_headers_is_unsupported_no_entries() -> None:
    csv_bytes = b"id,ID\n1,2\n"
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="t",
        warnings=warnings,
    )
    assert result.support_status == ParseSupportStatus.UNSUPPORTED
    assert result.entries == []
    assert any("duplicado" in w for w in warnings)


def test_row_with_fewer_values_than_headers_is_partial() -> None:
    csv_bytes = b"ID,NOMBRE,ESTADO\n1,ANA\n"
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="t",
        warnings=warnings,
    )
    assert result.support_status == ParseSupportStatus.PARTIAL
    assert result.entries[0].raw_row == {"ID": "1", "NOMBRE": "ANA", "ESTADO": ""}


def test_row_with_more_values_than_headers_is_partial() -> None:
    csv_bytes = b"ID,NOMBRE\n1,ANA,EXTRA\n"
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="t",
        warnings=warnings,
    )
    assert result.support_status == ParseSupportStatus.PARTIAL
    assert result.entries[0].raw_row == {"ID": "1", "NOMBRE": "ANA"}


def test_quoted_value_with_comma_is_preserved_as_single_field() -> None:
    csv_bytes = b'ID,NOMBRE\n1,"Perez, Juan"\n'
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="t",
        warnings=warnings,
    )
    assert result.support_status == ParseSupportStatus.SUPPORTED
    assert result.entries[0].raw_row["NOMBRE"] == "Perez, Juan"


def test_repeated_identical_rows_get_different_ids() -> None:
    csv_bytes = b"ID,NOMBRE\n1,ANA\n1,ANA\n"
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="t",
        warnings=warnings,
    )
    assert len(result.entries) == 2
    assert result.entries[0].row_hash == result.entries[1].row_hash
    assert result.entries[0].parameter_entry_id != result.entries[1].parameter_entry_id
    assert result.entries[0].row_number == 1
    assert result.entries[1].row_number == 2


def test_max_rows_limit_is_enforced() -> None:
    csv_bytes = ("ID,NOMBRE\n" + "\n".join(f"{i},X" for i in range(1, 6))).encode()
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=3,
        table_label="mi_tabla",
        warnings=warnings,
    )
    assert result.support_status == ParseSupportStatus.UNSUPPORTED
    assert result.entries == []
    assert any("limite" in w for w in warnings)


def test_values_are_never_type_inferred() -> None:
    csv_bytes = b"ID,ACTIVO,MONTO,FECHA\n007,true,1000.50,2026-01-01\n"
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="t",
        warnings=warnings,
    )
    row = result.entries[0].raw_row
    assert row["ID"] == "007"  # no se convierte a int (perderia el cero)
    assert row["ACTIVO"] == "true"  # no se convierte a bool
    assert row["MONTO"] == "1000.50"  # no se convierte a float
    assert row["FECHA"] == "2026-01-01"  # no se convierte a date


def test_raw_row_not_stripped_normalized_row_stripped() -> None:
    csv_bytes = b"ID,NOMBRE\n1,  ANA  \n"
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="t",
        warnings=warnings,
    )
    assert result.entries[0].raw_row["NOMBRE"] == "  ANA  "
    assert result.entries[0].normalized_row["NOMBRE"] == "ANA"


def test_empty_cell_in_a_complete_row_is_kept_as_empty_string_not_null() -> None:
    """Matriz de complejidad (Prompt 15), caso "snapshots con valores
    nulos": la definicion exacta para V1 es una celda vacia dentro de un
    CSV de snapshot (`A,,C`), NUNCA equivalente a `"NULL"`/`"null"`/
    `N/A`/cero/columna ausente. Este es el comportamiento REAL de
    csv_loader.py, sin inferencia de tipos (ver docstring del modulo):
    la fila 2 esta COMPLETA (2 valores para 2 columnas, no dispara la
    rama PARTIAL de "menos valores que columnas de encabezado"), solo
    que uno de sus valores es la cadena vacia. No existe semantica SQL
    NULL real en V1: string vacio no equivale a NULL, y no se debe
    redactar una regla que atribuya un valor inexistente a esta fila."""
    csv_bytes = b"ID,NOMBRE\n1,ANA\n2,\n"
    warnings: list[str] = []
    result = load_csv_snapshot(
        csv_bytes,
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=100,
        table_label="t",
        warnings=warnings,
    )

    # El CSV se carga (status SUPPORTED, no PARTIAL/UNSUPPORTED): una
    # celda vacia en una fila completa no es un CSV malformado.
    assert result.support_status == ParseSupportStatus.SUPPORTED
    assert len(result.entries) == 2
    assert not warnings

    complete_row = result.entries[0]
    empty_cell_row = result.entries[1]

    assert complete_row.raw_row["NOMBRE"] == "ANA"

    # String vacio, nunca None/null: se conserva tal cual, nunca se
    # inventa un valor ni se reutiliza el de otra fila (la fila 1 sigue
    # con "ANA", la fila 2 nunca hereda ese valor).
    assert empty_cell_row.raw_row["NOMBRE"] == ""
    assert empty_cell_row.raw_row["NOMBRE"] is not None
    assert isinstance(empty_cell_row.raw_row["NOMBRE"], str)
    assert empty_cell_row.normalized_row["NOMBRE"] == ""
    assert empty_cell_row.raw_row["ID"] == "2", "el resto de la fila se identifica normalmente"

    # No hay una unica representacion JSON "null": ambos campos son
    # strings de Python, `raw_row`/`normalized_row` nunca contienen None
    # para una celda CSV vacia.
    assert all(value is not None for value in empty_cell_row.raw_row.values())
    assert all(value is not None for value in empty_cell_row.normalized_row.values())


def test_empty_cell_row_hash_is_deterministic() -> None:
    csv_bytes = b"ID,NOMBRE\n1,ANA\n2,\n"

    first = load_csv_snapshot(
        csv_bytes, encoding="UTF-8", parameter_table_id=TABLE_ID, max_rows=100,
        table_label="t", warnings=[],
    )
    second = load_csv_snapshot(
        csv_bytes, encoding="UTF-8", parameter_table_id=TABLE_ID, max_rows=100,
        table_label="t", warnings=[],
    )

    assert first.entries[1].row_hash == second.entries[1].row_hash
    assert first.entries[1].parameter_entry_id == second.entries[1].parameter_entry_id
    # La fila vacia y la fila completa nunca colisionan entre si.
    assert first.entries[0].row_hash != first.entries[1].row_hash


def test_warnings_never_contain_cell_values() -> None:
    csv_bytes = b"ID,NOMBRE\n1,SECRETO-SENSIBLE\n1,SECRETO-SENSIBLE\n"
    warnings: list[str] = []
    load_csv_snapshot(
        csv_bytes,
        encoding="UTF-8",
        parameter_table_id=TABLE_ID,
        max_rows=1,
        table_label="t",
        warnings=warnings,
    )
    assert all("SECRETO-SENSIBLE" not in w for w in warnings)
