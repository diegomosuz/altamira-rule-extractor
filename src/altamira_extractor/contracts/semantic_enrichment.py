"""Contrato tipado del artefacto artifacts/03b-semantic-enrichment.json.

Insumos tipados producidos por ParameterLoader/SemanticTagger/
DomainTermMapper (Prompt 7 del runbook) para que Prompt 8
(SemanticGraphBuilder) construya despues `ParameterTable`,
`ParameterEntry`, `DomainTerm`, `HAS_ENTRY` y `HAS_DOMAIN_TERM` sin
tener que re-implementar ninguna de estas tres piezas: los mappings
`DataItemDomainTermMapping` ya vienen resueltos, Prompt 8 solo los
traduce a relaciones de grafo.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from .base import AltamiraBaseModel, RelativePath, Sha256Hex
from .enums import ParseSupportStatus


class ParameterColumnDefinition(AltamiraBaseModel):
    """Una columna interpretada desde el subconjunto DDL soportado."""

    original_name: str = Field(min_length=1)
    normalized_name: str = Field(min_length=1)
    declared_type: str = Field(min_length=1)
    nullable: bool | None = None
    is_primary_key: bool | None = None
    ordinal: int = Field(ge=1)


class ParameterEntryRecord(AltamiraBaseModel):
    """Una fila del snapshot CSV. `raw_row`/`normalized_row` estan
    indexados por el encabezado ORIGINAL (no normalizado) de la columna."""

    parameter_entry_id: str = Field(min_length=1)
    row_number: int = Field(ge=1)
    row_hash: Sha256Hex
    raw_row: dict[str, str] = Field(default_factory=dict)
    normalized_row: dict[str, str] = Field(default_factory=dict)


class ParameterTableRecord(AltamiraBaseModel):
    """Una tabla declarada en `Manifest.parameter_tables`."""

    table_id: str = Field(min_length=1)
    parameter_table_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    normalized_name: str = Field(min_length=1)
    country_code: str = Field(min_length=1)
    ddl_relative_path: RelativePath | None = None
    snapshot_relative_path: RelativePath | None = None
    snapshot_date: date | None = None
    ddl_hash: Sha256Hex | None = None
    snapshot_hash: Sha256Hex | None = None
    # None significa "no declarado en Manifest.parameter_tables" (nunca se
    # intento cargar). SUPPORTED/PARTIAL/UNSUPPORTED significan "declarado
    # y se intento procesar"; UNSUPPORTED nunca implica ausencia del
    # recurso, sino que el recurso declarado no pudo interpretarse.
    ddl_support_status: ParseSupportStatus | None
    snapshot_support_status: ParseSupportStatus | None
    columns: list[ParameterColumnDefinition] = Field(default_factory=list)
    entries: list[ParameterEntryRecord] = Field(default_factory=list)


class SemanticTagRuleMatch(AltamiraBaseModel):
    """Evidencia de una regla de `config/semantic-tags.yml` que coincidio
    (aunque no haya sido la ganadora, para no descartar evidencia)."""

    rule_id: str = Field(min_length=1)
    tag: str = Field(min_length=1)
    base_confidence: float = Field(ge=0, le=1)
    matched_name_regex: str | None = None
    pic_observed: str | None = None
    numeric_pic_required: bool | None = None
    numeric_pic_satisfied: bool | None = None


class DataItemSemanticTag(AltamiraBaseModel):
    """`semantic_tag` resuelto para un CanonicalDataItem puntual."""

    data_item_id: str = Field(min_length=1)
    program_id: str = Field(min_length=1)
    source_file: RelativePath | None = None
    original_name: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    semantic_tag: str = Field(min_length=1)
    semantic_confidence: float = Field(ge=0, le=1)
    evidence: list[SemanticTagRuleMatch] = Field(min_length=1)


class DomainTermRecord(AltamiraBaseModel):
    """Un concepto funcional del catalogo `config/domain-glossary.example.yml`.

    Distinto de `semantic_tag`: el tag es una etiqueta tecnica sobre un
    DataItem puntual; el termino es un concepto de negocio reutilizable.
    """

    domain_term_id: str = Field(min_length=1)
    functional_key: str = Field(min_length=1)
    normalized_functional_key: str = Field(min_length=1)
    functional_name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    authoritative_source: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    semantic_tags: list[str] = Field(default_factory=list)


class DataItemDomainTermMapping(AltamiraBaseModel):
    """Mapping ya resuelto DataItemSemanticTag -> DomainTerm. Prompt 8 solo
    traduce estos registros a `HAS_DOMAIN_TERM`, no vuelve a resolverlos."""

    data_item_id: str = Field(min_length=1)
    semantic_tag: str = Field(min_length=1)
    domain_term_id: str = Field(min_length=1)
    derivation_rule: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1)


class SemanticEnrichmentArtifact(AltamiraBaseModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    source_package_hash: Sha256Hex
    # Hashes de los YAML de configuracion realmente usados: impiden
    # reutilizar un artefacto generado con una version anterior de
    # semantic-tags.yml/domain-glossary.example.yml.
    semantic_tags_config_hash: Sha256Hex
    domain_glossary_config_hash: Sha256Hex
    parameter_tables: list[ParameterTableRecord] = Field(default_factory=list)
    data_item_tags: list[DataItemSemanticTag] = Field(default_factory=list)
    domain_terms: list[DomainTermRecord] = Field(default_factory=list)
    data_item_domain_term_mappings: list[DataItemDomainTermMapping] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_parameter_tables(self) -> SemanticEnrichmentArtifact:
        table_ids = [t.parameter_table_id for t in self.parameter_tables]
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("parameter_table_id duplicado")
        names = [t.normalized_name for t in self.parameter_tables]
        if names != sorted(names):
            raise ValueError("parameter_tables no esta ordenado por normalized_name")

        for table in self.parameter_tables:
            entry_ids = [e.parameter_entry_id for e in table.entries]
            if len(entry_ids) != len(set(entry_ids)):
                raise ValueError(f"parameter_entry_id duplicado en la tabla {table.name!r}")
            row_numbers = [e.row_number for e in table.entries]
            if row_numbers != sorted(row_numbers):
                raise ValueError(f"entries de {table.name!r} no estan ordenadas por row_number")
        return self

    @model_validator(mode="after")
    def _check_data_item_tags(self) -> SemanticEnrichmentArtifact:
        tag_ids = [t.data_item_id for t in self.data_item_tags]
        if len(tag_ids) != len(set(tag_ids)):
            raise ValueError("data_item_id duplicado en data_item_tags")
        if tag_ids != sorted(tag_ids):
            raise ValueError("data_item_tags no esta ordenado por data_item_id")
        return self

    @model_validator(mode="after")
    def _check_domain_terms(self) -> SemanticEnrichmentArtifact:
        term_ids = [t.domain_term_id for t in self.domain_terms]
        if len(term_ids) != len(set(term_ids)):
            raise ValueError("domain_term_id duplicado")
        if term_ids != sorted(term_ids):
            raise ValueError("domain_terms no esta ordenado por domain_term_id")
        return self

    @model_validator(mode="after")
    def _check_mappings_reference_existing_ids(self) -> SemanticEnrichmentArtifact:
        mapping_keys = [
            (m.data_item_id, m.domain_term_id) for m in self.data_item_domain_term_mappings
        ]
        if len(mapping_keys) != len(set(mapping_keys)):
            raise ValueError("data_item_domain_term_mappings tiene un par duplicado")

        data_item_ids = {t.data_item_id for t in self.data_item_tags}
        domain_term_ids = {t.domain_term_id for t in self.domain_terms}
        for mapping in self.data_item_domain_term_mappings:
            if mapping.data_item_id not in data_item_ids:
                raise ValueError(
                    f"mapping referencia data_item_id inexistente: {mapping.data_item_id!r}"
                )
            if mapping.domain_term_id not in domain_term_ids:
                raise ValueError(
                    f"mapping referencia domain_term_id inexistente: {mapping.domain_term_id!r}"
                )
        return self

    @model_validator(mode="after")
    def _check_warnings(self) -> SemanticEnrichmentArtifact:
        if len(self.warnings) != len(set(self.warnings)):
            raise ValueError("warnings contiene duplicados")
        if self.warnings != sorted(self.warnings):
            raise ValueError("warnings no esta ordenado deterministicamente")
        return self
