"""Tests unitarios de domain_term_mapper: carga de
config/domain-glossary.example.yml y resolucion de mappings."""

from __future__ import annotations

from pathlib import Path

import pytest

from altamira_extractor.contracts.semantic_enrichment import (
    DataItemSemanticTag,
    SemanticTagRuleMatch,
)
from altamira_extractor.pipeline.domain_term_mapper import (
    load_domain_glossary,
    map_data_item_tags_to_domain_terms,
)
from altamira_extractor.pipeline.errors import SemanticConfigError

_VALID_YAML = """
version: "1.0"
terms:
  - key: requested_amount
    functional_name: "importe solicitado"
    definition: "Monto monetario solicitado."
    entity_type: "monetary_amount"
    authoritative_source: "V1 controlled glossary"
    source_kind: "CURATED_CONFIG"
    match:
      semantic_tags: [amount]

  - key: result_code
    functional_name: "codigo de resultado"
    definition: "Codigo tecnico de resultado."
    entity_type: "result_code"
    authoritative_source: "V1 controlled glossary"
    source_kind: "CURATED_CONFIG"
    match:
      semantic_tags: [return_code]
"""


def _write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "domain-glossary.example.yml"
    path.write_text(content, encoding="utf-8")
    return path


def _tag(data_item_id: str, semantic_tag: str, confidence: float = 0.8) -> DataItemSemanticTag:
    return DataItemSemanticTag(
        data_item_id=data_item_id,
        program_id="program::AR::OP::PROG::1.0::abc123456789",
        source_file="01-codigo/cobol/PROG.cbl",
        original_name="WS-FIELD",
        qualified_name="WS-FIELD",
        semantic_tag=semantic_tag,
        semantic_confidence=confidence,
        evidence=[
            SemanticTagRuleMatch(rule_id="r1", tag=semantic_tag, base_confidence=confidence)
        ],
    )


# --- Carga del glosario ---


def test_loads_valid_glossary(tmp_path: Path) -> None:
    glossary = load_domain_glossary(_write_yaml(tmp_path, _VALID_YAML))
    assert len(glossary.terms) == 2
    assert glossary.term_id_by_tag["amount"] == "term::1.0::REQUESTED_AMOUNT"
    assert len(glossary.config_hash) == 64


def test_missing_file_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(SemanticConfigError):
        load_domain_glossary(tmp_path / "does-not-exist.yml")


def test_malformed_yaml_is_fatal(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "version: [unterminated")
    with pytest.raises(SemanticConfigError):
        load_domain_glossary(path)


def test_duplicate_functional_key_is_fatal(tmp_path: Path) -> None:
    yaml_text = """
version: "1.0"
terms:
  - key: requested_amount
    functional_name: "a"
    definition: "d"
    entity_type: "e"
    authoritative_source: "s"
    source_kind: "CURATED_CONFIG"
    match:
      semantic_tags: [amount]
  - key: requested_amount
    functional_name: "b"
    definition: "d2"
    entity_type: "e2"
    authoritative_source: "s2"
    source_kind: "CURATED_CONFIG"
    match:
      semantic_tags: [amount_threshold]
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(SemanticConfigError, match="functional_key duplicada"):
        load_domain_glossary(path)


def test_tag_shared_by_two_terms_is_fatal(tmp_path: Path) -> None:
    yaml_text = """
version: "1.0"
terms:
  - key: term_a
    functional_name: "a"
    definition: "d"
    entity_type: "e"
    authoritative_source: "s"
    source_kind: "CURATED_CONFIG"
    match:
      semantic_tags: [amount]
  - key: term_b
    functional_name: "b"
    definition: "d2"
    entity_type: "e2"
    authoritative_source: "s2"
    source_kind: "CURATED_CONFIG"
    match:
      semantic_tags: [amount]
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(SemanticConfigError, match="multiples"):
        load_domain_glossary(path)


def test_missing_catalog_version_is_fatal(tmp_path: Path) -> None:
    yaml_text = """
terms:
  - key: term_a
    functional_name: "a"
    definition: "d"
    entity_type: "e"
    authoritative_source: "s"
    source_kind: "CURATED_CONFIG"
    match:
      semantic_tags: [amount]
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(SemanticConfigError):
        load_domain_glossary(path)


def test_extra_field_in_term_is_fatal(tmp_path: Path) -> None:
    yaml_text = _VALID_YAML.replace(
        'source_kind: "CURATED_CONFIG"\n    match:',
        'source_kind: "CURATED_CONFIG"\n    unexpected: true\n    match:',
        1,
    )
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(SemanticConfigError):
        load_domain_glossary(path)


# --- Mapping ---


def test_valid_mapping(tmp_path: Path) -> None:
    glossary = load_domain_glossary(_write_yaml(tmp_path, _VALID_YAML))
    tags = [_tag("program::x::data::WS-FLAG", "amount", confidence=0.8)]
    warnings: list[str] = []

    mappings = map_data_item_tags_to_domain_terms(tags, glossary, warnings)

    assert len(mappings) == 1
    assert mappings[0].domain_term_id == "term::1.0::REQUESTED_AMOUNT"
    assert mappings[0].data_item_id == "program::x::data::WS-FLAG"
    assert mappings[0].confidence == 0.8
    assert mappings[0].derivation_rule == "SEMANTIC_TAG_GLOSSARY_MATCH"
    assert not warnings


def test_tag_without_term_produces_warning_no_mapping(tmp_path: Path) -> None:
    glossary = load_domain_glossary(_write_yaml(tmp_path, _VALID_YAML))
    tags = [_tag("program::x::data::WS-STATUS", "status")]
    warnings: list[str] = []

    mappings = map_data_item_tags_to_domain_terms(tags, glossary, warnings)

    assert mappings == []
    assert any("status" in w for w in warnings)


def test_mapping_references_existing_domain_term_id(tmp_path: Path) -> None:
    glossary = load_domain_glossary(_write_yaml(tmp_path, _VALID_YAML))
    tags = [_tag("program::x::data::WS-CODE", "return_code")]
    warnings: list[str] = []

    mappings = map_data_item_tags_to_domain_terms(tags, glossary, warnings)

    term_ids = {t.domain_term_id for t in glossary.terms}
    assert mappings[0].domain_term_id in term_ids
