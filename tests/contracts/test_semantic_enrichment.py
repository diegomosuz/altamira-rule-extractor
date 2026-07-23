"""Tests del contrato artifacts/03b-semantic-enrichment.json:
ParameterTableRecord, DataItemSemanticTag, DomainTermRecord,
DataItemDomainTermMapping y SemanticEnrichmentArtifact."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import (
    DataItemDomainTermMapping,
    DataItemSemanticTag,
    DomainTermRecord,
    ParameterColumnDefinition,
    ParameterEntryRecord,
    ParameterTableRecord,
    ParseSupportStatus,
    SemanticEnrichmentArtifact,
    SemanticTagRuleMatch,
)

VALID_HASH = "a" * 64
PROGRAM_ID = "program::AR::OP-TRF-PROPIA::PROG::1.0::abc123456789"
DATA_ITEM_ID = f"{PROGRAM_ID}::data::WS-FLAG"
TABLE_ID = "table::AR::default::PARAM_TRANSFER"
PARAMETER_TABLE_ID = f"parameter::{TABLE_ID}::unknown::deadbeefcafe"


def _column(**overrides: object) -> ParameterColumnDefinition:
    defaults: dict[str, object] = {
        "original_name": "ID",
        "normalized_name": "ID",
        "declared_type": "INTEGER",
        "nullable": False,
        "is_primary_key": True,
        "ordinal": 1,
    }
    defaults.update(overrides)
    return ParameterColumnDefinition(**defaults)  # type: ignore[arg-type]


def _entry(**overrides: object) -> ParameterEntryRecord:
    defaults: dict[str, object] = {
        "parameter_entry_id": f"{PARAMETER_TABLE_ID}::entry::1::{'b' * 12}",
        "row_number": 1,
        "row_hash": "b" * 64,
        "raw_row": {"ID": "1"},
        "normalized_row": {"ID": "1"},
    }
    defaults.update(overrides)
    return ParameterEntryRecord(**defaults)  # type: ignore[arg-type]


def _table(**overrides: object) -> ParameterTableRecord:
    defaults: dict[str, object] = {
        "table_id": TABLE_ID,
        "parameter_table_id": PARAMETER_TABLE_ID,
        "name": "PARAM_TRANSFER",
        "normalized_name": "PARAM_TRANSFER",
        "country_code": "AR",
        "ddl_relative_path": "02-parametria/ddl/PARAM_TRANSFER.sql",
        "snapshot_relative_path": "02-parametria/csv/PARAM_TRANSFER.csv",
        "snapshot_date": None,
        "ddl_hash": VALID_HASH,
        "snapshot_hash": VALID_HASH,
        "ddl_support_status": ParseSupportStatus.SUPPORTED,
        "snapshot_support_status": ParseSupportStatus.SUPPORTED,
        "columns": [_column()],
        "entries": [_entry()],
    }
    defaults.update(overrides)
    return ParameterTableRecord(**defaults)  # type: ignore[arg-type]


def _rule_match(**overrides: object) -> SemanticTagRuleMatch:
    defaults: dict[str, object] = {
        "rule_id": "amount_by_name",
        "tag": "amount",
        "base_confidence": 0.8,
        "matched_name_regex": "^WS-.*-AMT$",
    }
    defaults.update(overrides)
    return SemanticTagRuleMatch(**defaults)  # type: ignore[arg-type]


def _data_item_tag(**overrides: object) -> DataItemSemanticTag:
    defaults: dict[str, object] = {
        "data_item_id": DATA_ITEM_ID,
        "program_id": PROGRAM_ID,
        "source_file": "01-codigo/cobol/PROG.cbl",
        "original_name": "WS-FLAG",
        "qualified_name": "WS-FLAG",
        "semantic_tag": "amount",
        "semantic_confidence": 0.8,
        "evidence": [_rule_match()],
    }
    defaults.update(overrides)
    return DataItemSemanticTag(**defaults)  # type: ignore[arg-type]


def _domain_term(**overrides: object) -> DomainTermRecord:
    defaults: dict[str, object] = {
        "domain_term_id": "term::1.0::REQUESTED_AMOUNT",
        "functional_key": "requested_amount",
        "normalized_functional_key": "REQUESTED_AMOUNT",
        "functional_name": "importe solicitado",
        "definition": "Monto monetario solicitado.",
        "entity_type": "monetary_amount",
        "authoritative_source": "V1 controlled glossary",
        "source_kind": "CURATED_CONFIG",
        "catalog_version": "1.0",
        "semantic_tags": ["amount"],
    }
    defaults.update(overrides)
    return DomainTermRecord(**defaults)  # type: ignore[arg-type]


def _mapping(**overrides: object) -> DataItemDomainTermMapping:
    defaults: dict[str, object] = {
        "data_item_id": DATA_ITEM_ID,
        "semantic_tag": "amount",
        "domain_term_id": "term::1.0::REQUESTED_AMOUNT",
        "derivation_rule": "SEMANTIC_TAG_GLOSSARY_MATCH",
        "confidence": 0.8,
        "evidence": "semantic_tag=amount -> term::1.0::REQUESTED_AMOUNT",
    }
    defaults.update(overrides)
    return DataItemDomainTermMapping(**defaults)  # type: ignore[arg-type]


def _artifact(**overrides: object) -> SemanticEnrichmentArtifact:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": VALID_HASH,
        "semantic_tags_config_hash": VALID_HASH,
        "domain_glossary_config_hash": VALID_HASH,
        "parameter_tables": [_table()],
        "data_item_tags": [_data_item_tag()],
        "domain_terms": [_domain_term()],
        "data_item_domain_term_mappings": [_mapping()],
        "warnings": [],
    }
    defaults.update(overrides)
    return SemanticEnrichmentArtifact(**defaults)  # type: ignore[arg-type]


# --- round-trip ---


def test_artifact_valid_round_trips() -> None:
    artifact = _artifact()
    dumped = artifact.to_stable_json()
    restored = SemanticEnrichmentArtifact.model_validate_json(dumped)
    assert restored == artifact
    assert restored.to_stable_json() == dumped


def test_artifact_empty_collections_valid() -> None:
    artifact = SemanticEnrichmentArtifact(
        run_id="run-1",
        source_package_hash=VALID_HASH,
        semantic_tags_config_hash=VALID_HASH,
        domain_glossary_config_hash=VALID_HASH,
    )
    assert artifact.parameter_tables == []
    assert artifact.data_item_tags == []
    assert artifact.domain_terms == []
    assert artifact.data_item_domain_term_mappings == []


# --- parameter_tables ---


def test_duplicate_parameter_table_id_rejected() -> None:
    table = _table()
    with pytest.raises(ValidationError, match="parameter_table_id duplicado"):
        _artifact(parameter_tables=[table, table])


def test_parameter_tables_unsorted_by_normalized_name_rejected() -> None:
    first = _table(
        table_id="table::AR::default::ZZZ",
        parameter_table_id=f"parameter::table::AR::default::ZZZ::unknown::{'c' * 12}",
        name="ZZZ",
        normalized_name="ZZZ",
    )
    second = _table(
        table_id="table::AR::default::AAA",
        parameter_table_id=f"parameter::table::AR::default::AAA::unknown::{'d' * 12}",
        name="AAA",
        normalized_name="AAA",
    )
    with pytest.raises(ValidationError, match="normalized_name"):
        _artifact(parameter_tables=[first, second])


def test_duplicate_parameter_entry_id_in_table_rejected() -> None:
    entry = _entry()
    with pytest.raises(ValidationError, match="parameter_entry_id duplicado"):
        _artifact(parameter_tables=[_table(entries=[entry, entry])])


def test_entries_unsorted_by_row_number_rejected() -> None:
    first = _entry(
        parameter_entry_id=f"{PARAMETER_TABLE_ID}::entry::2::{'e' * 12}", row_number=2
    )
    second = _entry(
        parameter_entry_id=f"{PARAMETER_TABLE_ID}::entry::1::{'f' * 12}", row_number=1
    )
    with pytest.raises(ValidationError, match="row_number"):
        _artifact(parameter_tables=[_table(entries=[first, second])])


def test_row_number_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _entry(row_number=0)


def test_entry_row_hash_must_be_valid_sha256() -> None:
    with pytest.raises(ValidationError):
        _entry(row_hash="not-a-hash")


def test_ddl_and_snapshot_status_none_when_not_declared() -> None:
    table = _table(
        ddl_relative_path=None,
        snapshot_relative_path=None,
        snapshot_date=None,
        ddl_hash=None,
        snapshot_hash=None,
        ddl_support_status=None,
        snapshot_support_status=None,
        columns=[],
        entries=[],
    )
    assert table.ddl_support_status is None
    assert table.snapshot_support_status is None


def test_parameter_table_round_trips_with_none_statuses() -> None:
    table = _table(
        ddl_relative_path=None,
        snapshot_relative_path=None,
        snapshot_date=None,
        ddl_hash=None,
        snapshot_hash=None,
        ddl_support_status=None,
        snapshot_support_status=None,
        columns=[],
        entries=[],
    )
    artifact = _artifact(parameter_tables=[table])
    dumped = artifact.to_stable_json()
    restored = SemanticEnrichmentArtifact.model_validate_json(dumped)
    assert restored == artifact
    assert restored.parameter_tables[0].ddl_support_status is None
    assert restored.parameter_tables[0].snapshot_support_status is None


def test_unsupported_ddl_status_with_columns_allowed() -> None:
    # UNSUPPORTED con columnas vacias es el caso tipico; el contrato no
    # impide (deliberadamente) tener columnas ya interpretadas junto con
    # PARTIAL, asi que solo se valida aqui el caso de construccion normal.
    table = _table(ddl_support_status=ParseSupportStatus.UNSUPPORTED, columns=[])
    artifact = _artifact(parameter_tables=[table])
    assert artifact.parameter_tables[0].columns == []


def test_column_nullable_and_primary_key_accept_none() -> None:
    column = _column(nullable=None, is_primary_key=None)
    assert column.nullable is None
    assert column.is_primary_key is None


def test_column_ordinal_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _column(ordinal=0)


# --- data_item_tags ---


def test_duplicate_data_item_id_rejected() -> None:
    tag = _data_item_tag()
    with pytest.raises(ValidationError, match="data_item_id duplicado"):
        _artifact(data_item_tags=[tag, tag])


def test_data_item_tags_unsorted_rejected() -> None:
    first = _data_item_tag(data_item_id=f"{PROGRAM_ID}::data::WS-ZZZ")
    second = _data_item_tag(data_item_id=f"{PROGRAM_ID}::data::WS-AAA")
    with pytest.raises(ValidationError, match="data_item_tags no esta ordenado"):
        _artifact(data_item_tags=[first, second])


def test_data_item_tag_requires_at_least_one_evidence() -> None:
    with pytest.raises(ValidationError):
        _data_item_tag(evidence=[])


def test_data_item_tag_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        _data_item_tag(semantic_confidence=1.5)


def test_rule_match_base_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        _rule_match(base_confidence=-0.1)


# --- domain_terms ---


def test_duplicate_domain_term_id_rejected() -> None:
    term = _domain_term()
    with pytest.raises(ValidationError, match="domain_term_id duplicado"):
        _artifact(domain_terms=[term, term])


def test_domain_terms_unsorted_rejected() -> None:
    first = _domain_term(domain_term_id="term::1.0::ZZZ", functional_key="zzz")
    second = _domain_term(domain_term_id="term::1.0::AAA", functional_key="aaa")
    with pytest.raises(ValidationError, match="domain_terms no esta ordenado"):
        _artifact(
            domain_terms=[first, second],
            data_item_domain_term_mappings=[],
        )


# --- data_item_domain_term_mappings ---


def test_duplicate_mapping_pair_rejected() -> None:
    mapping = _mapping()
    with pytest.raises(ValidationError, match="par duplicado"):
        _artifact(data_item_domain_term_mappings=[mapping, mapping])


def test_mapping_referencing_missing_data_item_id_rejected() -> None:
    mapping = _mapping(data_item_id=f"{PROGRAM_ID}::data::WS-DOES-NOT-EXIST")
    with pytest.raises(ValidationError, match="data_item_id inexistente"):
        _artifact(data_item_domain_term_mappings=[mapping])


def test_mapping_referencing_missing_domain_term_id_rejected() -> None:
    mapping = _mapping(domain_term_id="term::1.0::DOES-NOT-EXIST")
    with pytest.raises(ValidationError, match="domain_term_id inexistente"):
        _artifact(data_item_domain_term_mappings=[mapping])


def test_mapping_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        _mapping(confidence=2.0)


# --- warnings ---


def test_duplicate_warnings_rejected() -> None:
    with pytest.raises(ValidationError, match="warnings contiene duplicados"):
        _artifact(warnings=["same", "same"])


def test_unsorted_warnings_rejected() -> None:
    with pytest.raises(ValidationError, match="warnings no esta ordenado"):
        _artifact(warnings=["zeta", "alpha"])


# --- source_package_hash coherente entre artefacto y hashes de config ---


def test_source_package_hash_must_be_valid_sha256() -> None:
    with pytest.raises(ValidationError):
        _artifact(source_package_hash="not-a-hash")


def test_semantic_tags_config_hash_must_be_valid_sha256() -> None:
    with pytest.raises(ValidationError):
        _artifact(semantic_tags_config_hash="not-a-hash")


def test_domain_glossary_config_hash_must_be_valid_sha256() -> None:
    with pytest.raises(ValidationError):
        _artifact(domain_glossary_config_hash="not-a-hash")
