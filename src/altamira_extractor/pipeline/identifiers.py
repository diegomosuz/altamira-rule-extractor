"""Identidad versionada y determinística compartida por el pipeline
(CLAUDE.md, seccion "Identidad y versionado").

Unico lugar que implementa estas formulas. `dependency_builder.py`
(Prompt 6), `semantic_tagger.py`/`domain_term_mapper.py`/
`semantic_enrichment_stage.py` (Prompt 7) y `semantic_graph_builder.py`/
`semantic_graph_stage.py` (Prompt 8) importan desde aqui en vez de
reimplementar la logica o importarla unos de otros. Ninguna formula aqui
cambia respecto de lo que ya se persistio en `artifacts/03-dependencies.json`
y `artifacts/03b-semantic-enrichment.json`: este modulo solo reubica
codigo ya existente para dejar de duplicar imports cruzados entre etapas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


def normalize_identifier(name: str) -> str:
    """Normalizacion determinista para comparar identificadores COBOL
    (case-insensitive). El valor normalizado no siempre se persiste tal
    cual: en la resolucion de variables/paragraphs solo se usa para
    comparar (la identidad final usa el valor real ya emparejado); en
    DataItem/Table si forma parte del id persistido, tal como ya lo
    hacian `semantic_tagger.py`/`semantic_enrichment_stage.py`."""
    return name.strip().upper()


@dataclass(frozen=True)
class ProgramIdentity:
    """Componentes de identidad versionada (CLAUDE.md) que no viven en
    CanonicalProgram: provienen del Manifest embebido en Inventory."""

    country_code: str
    logical_name: str
    version: str

    def program_id(self, *, program_name: str, source_hash: str) -> str:
        return (
            f"program::{self.country_code}::{self.logical_name}::{program_name}::"
            f"{self.version}::{source_hash[:12]}"
        )


def paragraph_id(program_id: str, paragraph_name: str) -> str:
    return f"{program_id}::paragraph::{normalize_identifier(paragraph_name)}"


def data_item_id(program_id: str, qualified_name: str) -> str:
    """Mismo formato que ya persiste `DataItemSemanticTag.data_item_id`
    (Prompt 7, `semantic_tagger.py`): `{program_id}::data::{qualified_name
    normalizado}`."""
    return f"{program_id}::data::{normalize_identifier(qualified_name)}"


def table_id(country_code: str, table_name: str, *, schema_name: str | None = None) -> str:
    """Mismo formato que ya persiste `ParameterTableRecord.table_id`
    (Prompt 7, `semantic_enrichment_stage.py`) cuando `schema_name` se
    omite: `table::{country_code}::DEFAULT::{table_name normalizado}`.

    `schema_name` es una extension retrocompatible (no usada por Prompt 7,
    que siempre omite el parametro y por lo tanto conserva exactamente su
    formula original) que Prompt 8 usa para tablas accedidas por SQL con
    un esquema explicito (`schema.table`)."""
    schema_part = normalize_identifier(schema_name) if schema_name else "DEFAULT"
    return f"table::{country_code}::{schema_part}::{normalize_identifier(table_name)}"


def parameter_table_id(
    table_id_value: str, snapshot_date: date | None, snapshot_hash: str | None
) -> str:
    """Mismo formato que ya persiste `ParameterTableRecord.parameter_table_id`
    (Prompt 7, `semantic_enrichment_stage.py`)."""
    date_part = snapshot_date.isoformat() if snapshot_date else "unknown"
    hash_part = snapshot_hash[:12] if snapshot_hash else "unknown"
    return f"parameter::{table_id_value}::{date_part}::{hash_part}"
