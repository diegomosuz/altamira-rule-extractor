"""DomainTermMapper: carga `config/domain-glossary.example.yml` y resuelve
el mapping `DataItemSemanticTag -> DomainTerm` ya terminado. Prompt 8
(SemanticGraphBuilder) solo traduce estos `DataItemDomainTermMapping` a
`HAS_DOMAIN_TERM`: no vuelve a implementar ninguna logica de matching.

`DomainTermRecord` representa un concepto funcional (reutilizable, de
negocio), distinto de `semantic_tag` (una etiqueta tecnica sobre un
DataItem puntual). Ambigüedades de catalogo (functional_key duplicada,
un semantic_tag asignado a mas de un termino) son errores fatales de
configuracion, nunca se resuelven eligiendo arbitrariamente.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from ..contracts.base import AltamiraBaseModel
from ..contracts.semantic_enrichment import (
    DataItemDomainTermMapping,
    DataItemSemanticTag,
    DomainTermRecord,
)
from .errors import SemanticConfigError
from .identifiers import normalize_identifier
from .yaml_utils import read_yaml_config

_MAPPING_DERIVATION_RULE = "SEMANTIC_TAG_GLOSSARY_MATCH"


class _DomainGlossaryTermMatchConfig(AltamiraBaseModel):
    semantic_tags: list[str] = Field(default_factory=list)


class _DomainGlossaryTermConfig(AltamiraBaseModel):
    key: str = Field(min_length=1)
    functional_name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    authoritative_source: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    match: _DomainGlossaryTermMatchConfig


class _DomainGlossaryConfig(AltamiraBaseModel):
    version: str = Field(min_length=1)
    terms: list[_DomainGlossaryTermConfig] = Field(default_factory=list)


@dataclass(frozen=True)
class LoadedDomainGlossary:
    config_hash: str
    terms: list[DomainTermRecord]
    term_id_by_tag: dict[str, str]


def load_domain_glossary(path: Path) -> LoadedDomainGlossary:
    document, config_hash = read_yaml_config(path)
    try:
        parsed = _DomainGlossaryConfig.model_validate(document)
    except ValueError as exc:
        raise SemanticConfigError(f"{path.name}: estructura invalida: {exc}") from exc

    seen_normalized_keys: set[str] = set()
    terms: list[DomainTermRecord] = []
    tag_to_term_ids: dict[str, set[str]] = {}

    for term in parsed.terms:
        normalized_key = normalize_identifier(term.key)
        if normalized_key in seen_normalized_keys:
            raise SemanticConfigError(f"{path.name}: functional_key duplicada: {term.key!r}")
        seen_normalized_keys.add(normalized_key)

        term_id = f"term::{parsed.version}::{normalized_key}"

        for tag in term.match.semantic_tags:
            tag_to_term_ids.setdefault(tag, set()).add(term_id)

        terms.append(
            DomainTermRecord(
                domain_term_id=term_id,
                functional_key=term.key,
                normalized_functional_key=normalized_key,
                functional_name=term.functional_name,
                definition=term.definition,
                entity_type=term.entity_type,
                authoritative_source=term.authoritative_source,
                source_kind=term.source_kind,
                catalog_version=parsed.version,
                semantic_tags=sorted(term.match.semantic_tags),
            )
        )

    for tag, term_ids in tag_to_term_ids.items():
        if len(term_ids) > 1:
            raise SemanticConfigError(
                f"{path.name}: el semantic_tag {tag!r} esta asignado a multiples "
                f"DomainTerm: {sorted(term_ids)}"
            )

    term_id_by_tag = {tag: next(iter(ids)) for tag, ids in tag_to_term_ids.items()}
    terms.sort(key=lambda record: record.domain_term_id)
    return LoadedDomainGlossary(config_hash=config_hash, terms=terms, term_id_by_tag=term_id_by_tag)


def map_data_item_tags_to_domain_terms(
    data_item_tags: list[DataItemSemanticTag],
    glossary: LoadedDomainGlossary,
    warnings: list[str],
) -> list[DataItemDomainTermMapping]:
    mappings: list[DataItemDomainTermMapping] = []
    for tagged in data_item_tags:
        term_id = glossary.term_id_by_tag.get(tagged.semantic_tag)
        if term_id is None:
            warnings.append(
                f"{tagged.data_item_id}: semantic_tag {tagged.semantic_tag!r} no tiene un "
                "DomainTerm asociado en el glosario"
            )
            continue
        mappings.append(
            DataItemDomainTermMapping(
                data_item_id=tagged.data_item_id,
                semantic_tag=tagged.semantic_tag,
                domain_term_id=term_id,
                derivation_rule=_MAPPING_DERIVATION_RULE,
                confidence=tagged.semantic_confidence,
                evidence=(
                    f"semantic_tag {tagged.semantic_tag!r} declarado en match.semantic_tags "
                    f"de {term_id!r}"
                ),
            )
        )
    mappings.sort(key=lambda mapping: (mapping.data_item_id, mapping.domain_term_id))
    return mappings
