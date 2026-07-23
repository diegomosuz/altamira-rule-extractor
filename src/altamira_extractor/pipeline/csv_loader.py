"""Lectura acotada de snapshots CSV de parametria usando el modulo `csv`
de la biblioteca estandar (V1: delimitador fijo `,`, encabezado
obligatorio, encoding exclusivamente desde `InventoryFile.detected_encoding`,
sin inferencia de tipos, filas en orden fisico, limite configurable de
filas). Nunca se detecta el delimitador automaticamente.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field

from ..contracts.enums import ParseSupportStatus
from ..contracts.semantic_enrichment import ParameterEntryRecord


def normalize_csv_header(header: str) -> str:
    return header.strip().upper()


@dataclass(frozen=True)
class LoadedCsvSnapshot:
    support_status: ParseSupportStatus
    entries: list[ParameterEntryRecord]
    original_headers: list[str] = field(default_factory=list)


def _row_hash(headers: list[str], values: list[str]) -> str:
    # Serializacion JSON canonica sobre encabezados y valores EN ORDEN
    # (no se usa sort_keys, que reordenaria alfabeticamente y perderia el
    # orden fisico de columnas): el orden de headers/values coincide
    # exactamente entre el hash y raw_row, por construccion.
    payload = json.dumps(
        {"headers": headers, "values": values}, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_csv_snapshot(
    csv_bytes: bytes,
    *,
    encoding: str,
    parameter_table_id: str,
    max_rows: int,
    table_label: str,
    warnings: list[str],
) -> LoadedCsvSnapshot:
    try:
        text = csv_bytes.decode(encoding)
    except (LookupError, UnicodeDecodeError) as exc:
        warnings.append(
            f"{table_label}: no se pudo decodificar el snapshot con encoding {encoding!r}: {exc}"
        )
        return LoadedCsvSnapshot(ParseSupportStatus.UNSUPPORTED, [], [])

    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=","))
    except csv.Error as exc:
        warnings.append(f"{table_label}: error de formato CSV: {exc}")
        return LoadedCsvSnapshot(ParseSupportStatus.UNSUPPORTED, [], [])

    if not rows:
        warnings.append(f"{table_label}: snapshot vacio, falta encabezado")
        return LoadedCsvSnapshot(ParseSupportStatus.UNSUPPORTED, [], [])

    headers = rows[0]
    normalized_headers = [normalize_csv_header(h) for h in headers]
    if len(set(normalized_headers)) != len(normalized_headers):
        warnings.append(
            f"{table_label}: encabezados duplicados tras normalizar; snapshot no soportado"
        )
        return LoadedCsvSnapshot(ParseSupportStatus.UNSUPPORTED, [], headers)

    data_rows = rows[1:]
    if len(data_rows) > max_rows:
        warnings.append(
            f"{table_label}: el snapshot tiene {len(data_rows)} filas, supera el limite "
            f"configurado ({max_rows}); no se procesa"
        )
        return LoadedCsvSnapshot(ParseSupportStatus.UNSUPPORTED, [], headers)

    status = ParseSupportStatus.SUPPORTED
    entries: list[ParameterEntryRecord] = []
    for row_number, row in enumerate(data_rows, start=1):
        values = list(row)
        if len(values) < len(headers):
            warnings.append(
                f"{table_label}: fila {row_number} tiene menos valores que columnas de "
                "encabezado; se completa con texto vacio"
            )
            values = values + [""] * (len(headers) - len(values))
            status = ParseSupportStatus.PARTIAL
        elif len(values) > len(headers):
            warnings.append(
                f"{table_label}: fila {row_number} tiene mas valores que columnas de "
                "encabezado; se descartan los sobrantes"
            )
            values = values[: len(headers)]
            status = ParseSupportStatus.PARTIAL

        raw_row = dict(zip(headers, values, strict=True))
        normalized_row = {key: value.strip() for key, value in raw_row.items()}
        row_hash = _row_hash(headers, values)

        entries.append(
            ParameterEntryRecord(
                parameter_entry_id=f"{parameter_table_id}::entry::{row_number}::{row_hash[:12]}",
                row_number=row_number,
                row_hash=row_hash,
                raw_row=raw_row,
                normalized_row=normalized_row,
            )
        )

    return LoadedCsvSnapshot(status, entries, headers)
