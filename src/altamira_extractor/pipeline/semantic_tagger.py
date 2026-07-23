"""SemanticTagger: aplica `config/semantic-tags.yml` sobre
`CanonicalDataItem`.

Carga y valida el YAML con Pydantic (`extra="forbid"`): campos
desconocidos, `name_regex` invalida, `base_confidence` fuera de rango, o
`rule_id` duplicado son errores fatales de configuracion
(`SemanticConfigError`) — una configuracion rota nunca produce un
resultado parcial silencioso.

El matching se aplica exclusivamente sobre `CanonicalDataItem.name` (no
`qualified_name`: reglas ancladas como `^SQLCODE$` solo tienen sentido
contra el nombre hoja) y queda scopeado a un `CanonicalProgram` a la vez
— nunca cruza programas. No se reconstruye contexto desde `source_text`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from ..contracts.base import AltamiraBaseModel
from ..contracts.canonical import CanonicalProgram
from ..contracts.semantic_enrichment import DataItemSemanticTag, SemanticTagRuleMatch
from .dependency_builder import ProgramIdentity, normalize_identifier
from .errors import SemanticConfigError
from .yaml_utils import read_yaml_config

_NUMERIC_PIC_CHARS = frozenset("9SVP()+-.,0123456789")


class _SemanticTagRuleConfig(AltamiraBaseModel):
    id: str = Field(min_length=1)
    tag: str = Field(min_length=1)
    name_regex: str = Field(min_length=1)
    base_confidence: float = Field(ge=0, le=1)
    requires_numeric_pic: bool = False


class _SemanticTagsConfig(AltamiraBaseModel):
    version: str = Field(min_length=1)
    allowed_tags: list[str] = Field(min_length=1)
    rules: list[_SemanticTagRuleConfig] = Field(default_factory=list)


@dataclass(frozen=True)
class CompiledSemanticTagRule:
    rule_id: str
    tag: str
    base_confidence: float
    requires_numeric_pic: bool
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class LoadedSemanticTagsConfig:
    config_hash: str
    rules: list[CompiledSemanticTagRule]
    allowed_tags: frozenset[str]


def load_semantic_tags_config(path: Path) -> LoadedSemanticTagsConfig:
    document, config_hash = read_yaml_config(path)
    try:
        parsed = _SemanticTagsConfig.model_validate(document)
    except ValueError as exc:
        raise SemanticConfigError(f"{path.name}: estructura invalida: {exc}") from exc

    allowed_tags = frozenset(parsed.allowed_tags)
    seen_rule_ids: set[str] = set()
    compiled: list[CompiledSemanticTagRule] = []
    for rule in parsed.rules:
        if rule.id in seen_rule_ids:
            raise SemanticConfigError(f"{path.name}: rule_id duplicado: {rule.id!r}")
        seen_rule_ids.add(rule.id)
        if rule.tag not in allowed_tags:
            raise SemanticConfigError(
                f"{path.name}: la regla {rule.id!r} usa un tag no declarado en "
                f"allowed_tags: {rule.tag!r}"
            )
        try:
            pattern = re.compile(rule.name_regex)
        except re.error as exc:
            raise SemanticConfigError(
                f"{path.name}: name_regex invalida en la regla {rule.id!r}: {exc}"
            ) from exc
        compiled.append(
            CompiledSemanticTagRule(
                rule_id=rule.id,
                tag=rule.tag,
                base_confidence=rule.base_confidence,
                requires_numeric_pic=rule.requires_numeric_pic,
                pattern=pattern,
            )
        )
    return LoadedSemanticTagsConfig(
        config_hash=config_hash, rules=compiled, allowed_tags=allowed_tags
    )


def _is_numeric_pic(pic: str | None) -> bool:
    if not pic:
        return False
    return all(char in _NUMERIC_PIC_CHARS for char in pic.upper())


def tag_data_items(
    program: CanonicalProgram,
    program_identity: ProgramIdentity,
    config: LoadedSemanticTagsConfig,
    warnings: list[str],
) -> list[DataItemSemanticTag]:
    """Etiqueta los `CanonicalDataItem` de un unico programa. Nunca cruza
    con otros programas: cada llamada esta scopeada a `program`."""
    program_id = program_identity.program_id(
        program_name=program.program_name, source_hash=program.source_hash
    )
    results: list[DataItemSemanticTag] = []

    for item in program.data_items:
        matches = [rule for rule in config.rules if rule.pattern.search(item.name)]
        if not matches:
            continue

        applicable = [
            rule for rule in matches if not rule.requires_numeric_pic or _is_numeric_pic(item.pic)
        ]
        if not applicable:
            continue

        distinct_tags = sorted({rule.tag for rule in applicable})
        if len(distinct_tags) > 1:
            warnings.append(
                f"{program.program_name}::{item.qualified_name}: multiples reglas de "
                f"semantic-tags con tags distintos coinciden ({distinct_tags}); no se "
                "asigna semantic_tag"
            )
            continue

        winning_tag = distinct_tags[0]
        best_confidence = max(rule.base_confidence for rule in applicable)
        evidence = [
            SemanticTagRuleMatch(
                rule_id=rule.rule_id,
                tag=rule.tag,
                base_confidence=rule.base_confidence,
                matched_name_regex=rule.pattern.pattern,
                pic_observed=item.pic,
                numeric_pic_required=rule.requires_numeric_pic,
                numeric_pic_satisfied=(
                    _is_numeric_pic(item.pic) if rule.requires_numeric_pic else None
                ),
            )
            for rule in sorted(applicable, key=lambda r: r.rule_id)
        ]

        results.append(
            DataItemSemanticTag(
                data_item_id=(
                    f"{program_id}::data::{normalize_identifier(item.qualified_name)}"
                ),
                program_id=program_id,
                source_file=item.source_file,
                original_name=item.name,
                qualified_name=item.qualified_name,
                semantic_tag=winning_tag,
                semantic_confidence=best_confidence,
                evidence=evidence,
            )
        )

    return results
