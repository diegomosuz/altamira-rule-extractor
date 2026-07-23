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
