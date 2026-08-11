"""Tests unitarios de semantic_tagger: carga de config/semantic-tags.yml y
matching contra CanonicalDataItem."""

from __future__ import annotations

from pathlib import Path

import pytest

from altamira_extractor.contracts.canonical import CanonicalDataItem, CanonicalProgram
from altamira_extractor.contracts.enums import LocationKind, SourceFormat
from altamira_extractor.pipeline.dependency_builder import ProgramIdentity
from altamira_extractor.pipeline.errors import SemanticConfigError
from altamira_extractor.pipeline.semantic_tagger import load_semantic_tags_config, tag_data_items

VALID_HASH = "a" * 64
IDENTITY = ProgramIdentity(country_code="AR", logical_name="OP-TRF-PROPIA", version="1.0")

_VALID_YAML = """
version: "1.0"
allowed_tags:
  - return_code
  - amount
  - amount_threshold
rules:
  - id: return-code-name
    tag: return_code
    name_regex: "(?i)^SQLCODE$"
    base_confidence: 1.0
  - id: amount-name
    tag: amount
    name_regex: "(?i).*(IMPORTE|MONTO).*"
    requires_numeric_pic: true
    base_confidence: 0.80
  - id: threshold-name
    tag: amount_threshold
    name_regex: "(?i).*(LIMITE).*"
    requires_numeric_pic: true
    base_confidence: 0.85
"""


def _write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "semantic-tags.yml"
    path.write_text(content, encoding="utf-8")
    return path


def _data_item(
    name: str, pic: str | None = None, qualified_name: str | None = None
) -> CanonicalDataItem:
    return CanonicalDataItem(
        name=name,
        qualified_name=qualified_name or name,
        level=1,
        pic=pic,
        location_kind=LocationKind.EXACT,
        source_file="01-codigo/cobol/PROG.cbl",
        line=1,
    )


def _program(data_items: list[CanonicalDataItem], source_hash: str = "b" * 64) -> CanonicalProgram:
    return CanonicalProgram(
        program_name="PROG1",
        source_file="01-codigo/cobol/PROG.cbl",
        source_hash=source_hash,
        source_package_hash=VALID_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=data_items,
        paragraphs=[],
    )


# --- Carga de config/semantic-tags.yml ---


def test_loads_valid_config(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, _VALID_YAML)
    config = load_semantic_tags_config(path)
    assert len(config.rules) == 3
    assert config.allowed_tags == {"return_code", "amount", "amount_threshold"}
    assert len(config.config_hash) == 64


def test_missing_file_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(SemanticConfigError):
        load_semantic_tags_config(tmp_path / "does-not-exist.yml")


def test_malformed_yaml_is_fatal(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "version: [unterminated")
    with pytest.raises(SemanticConfigError):
        load_semantic_tags_config(path)


def test_extra_field_is_fatal(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path,
        _VALID_YAML + "\nunexpected_field: true\n",
    )
    with pytest.raises(SemanticConfigError):
        load_semantic_tags_config(path)


def test_duplicate_rule_id_is_fatal(tmp_path: Path) -> None:
    yaml_text = """
version: "1.0"
allowed_tags: [amount]
rules:
  - id: dup
    tag: amount
    name_regex: "A"
    base_confidence: 0.5
  - id: dup
    tag: amount
    name_regex: "B"
    base_confidence: 0.6
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(SemanticConfigError, match="rule_id duplicado"):
        load_semantic_tags_config(path)


def test_invalid_regex_is_fatal(tmp_path: Path) -> None:
    yaml_text = """
version: "1.0"
allowed_tags: [amount]
rules:
  - id: bad-regex
    tag: amount
    name_regex: "("
    base_confidence: 0.5
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(SemanticConfigError, match="name_regex invalida"):
        load_semantic_tags_config(path)


def test_confidence_out_of_range_is_fatal(tmp_path: Path) -> None:
    yaml_text = """
version: "1.0"
allowed_tags: [amount]
rules:
  - id: r1
    tag: amount
    name_regex: "A"
    base_confidence: 1.5
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(SemanticConfigError):
        load_semantic_tags_config(path)


def test_tag_not_in_allowed_tags_is_fatal(tmp_path: Path) -> None:
    yaml_text = """
version: "1.0"
allowed_tags: [amount]
rules:
  - id: r1
    tag: not_allowed
    name_regex: "A"
    base_confidence: 0.5
"""
    path = _write_yaml(tmp_path, yaml_text)
    with pytest.raises(SemanticConfigError):
        load_semantic_tags_config(path)


# --- Matching ---


def test_unique_match(tmp_path: Path) -> None:
    config = load_semantic_tags_config(_write_yaml(tmp_path, _VALID_YAML))
    program = _program([_data_item("SQLCODE")])
    warnings: list[str] = []

    tags = tag_data_items(program, IDENTITY, config, warnings)

    assert len(tags) == 1
    assert tags[0].semantic_tag == "return_code"
    assert tags[0].semantic_confidence == 1.0
    assert not warnings


def test_name_regex_matches_against_leaf_name_not_qualified_name(tmp_path: Path) -> None:
    config = load_semantic_tags_config(_write_yaml(tmp_path, _VALID_YAML))
    # qualified_name incluye un grupo padre; ^SQLCODE$ solo matchea si se
    # evalua contra el nombre hoja.
    program = _program([_data_item("SQLCODE", qualified_name="WS-SQL-AREA.SQLCODE")])
    warnings: list[str] = []

    tags = tag_data_items(program, IDENTITY, config, warnings)

    assert len(tags) == 1
    assert tags[0].semantic_tag == "return_code"
    assert tags[0].qualified_name == "WS-SQL-AREA.SQLCODE"


def test_numeric_pic_required_and_satisfied(tmp_path: Path) -> None:
    config = load_semantic_tags_config(_write_yaml(tmp_path, _VALID_YAML))
    program = _program([_data_item("WS-IMPORTE-SOLICITADO", pic="9(7)V99")])
    warnings: list[str] = []

    tags = tag_data_items(program, IDENTITY, config, warnings)

    assert len(tags) == 1
    assert tags[0].semantic_tag == "amount"
    assert tags[0].evidence[0].numeric_pic_satisfied is True


def test_numeric_pic_required_but_not_satisfied_skips_rule(tmp_path: Path) -> None:
    config = load_semantic_tags_config(_write_yaml(tmp_path, _VALID_YAML))
    program = _program([_data_item("WS-IMPORTE-DESC", pic="X(20)")])
    warnings: list[str] = []

    tags = tag_data_items(program, IDENTITY, config, warnings)

    assert tags == []
    assert not warnings


def test_same_tag_from_multiple_rules_uses_max_confidence_and_keeps_all_evidence(
    tmp_path: Path,
) -> None:
    yaml_text = """
version: "1.0"
allowed_tags: [amount]
rules:
  - id: rule-a
    tag: amount
    name_regex: "(?i).*IMPORTE.*"
    base_confidence: 0.6
  - id: rule-b
    tag: amount
    name_regex: "(?i).*MONTO.*"
    base_confidence: 0.9
"""
    config = load_semantic_tags_config(_write_yaml(tmp_path, yaml_text))
    program = _program([_data_item("WS-IMPORTE-MONTO")])
    warnings: list[str] = []

    tags = tag_data_items(program, IDENTITY, config, warnings)

    assert len(tags) == 1
    assert tags[0].semantic_tag == "amount"
    assert tags[0].semantic_confidence == 0.9
    assert {e.rule_id for e in tags[0].evidence} == {"rule-a", "rule-b"}
    assert not warnings


def test_different_tags_ambiguous_no_tag_assigned(tmp_path: Path) -> None:
    yaml_text = """
version: "1.0"
allowed_tags: [amount, status]
rules:
  - id: rule-a
    tag: amount
    name_regex: "(?i).*CODIGO.*"
    base_confidence: 0.6
  - id: rule-b
    tag: status
    name_regex: "(?i).*CODIGO.*"
    base_confidence: 0.7
"""
    config = load_semantic_tags_config(_write_yaml(tmp_path, yaml_text))
    program = _program([_data_item("WS-CODIGO")])
    warnings: list[str] = []

    tags = tag_data_items(program, IDENTITY, config, warnings)

    assert tags == []
    assert any("multiples reglas" in w for w in warnings)


def test_data_item_id_is_versioned(tmp_path: Path) -> None:
    config = load_semantic_tags_config(_write_yaml(tmp_path, _VALID_YAML))
    program = _program([_data_item("SQLCODE")], source_hash="c" * 64)
    warnings: list[str] = []

    tags = tag_data_items(program, IDENTITY, config, warnings)

    expected_program_id = IDENTITY.program_id(program_name="PROG1", source_hash="c" * 64)
    assert tags[0].program_id == expected_program_id
    assert tags[0].data_item_id == f"{expected_program_id}::data::SQLCODE"


def test_no_match_produces_no_tag_and_no_warning(tmp_path: Path) -> None:
    config = load_semantic_tags_config(_write_yaml(tmp_path, _VALID_YAML))
    program = _program([_data_item("WS-UNRELATED-FIELD")])
    warnings: list[str] = []

    tags = tag_data_items(program, IDENTITY, config, warnings)

    assert tags == []
    assert not warnings


# --- Fase 15B3-C1: reglas status/status_flag en config/semantic-tags.yml
# real (nunca un YAML sintetico aparte -- prueba el archivo productivo tal
# como se despliega) ---

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CONFIG_PATH = _REPO_ROOT / "config" / "semantic-tags.yml"


def _tag_names(tmp_path: Path, *names: str) -> dict[str, str | None]:
    config = load_semantic_tags_config(_REAL_CONFIG_PATH)
    program = _program([_data_item(name) for name in names])
    warnings: list[str] = []
    tags = tag_data_items(program, IDENTITY, config, warnings)
    by_name = {tag.original_name: tag.semantic_tag for tag in tags}
    return {name: by_name.get(name) for name in names}


@pytest.mark.parametrize(
    "name",
    [
        "WS-ESTADO",
        "WS-ESTADO-OPERACION",
        "WS-STATUS",
        "WS-STATE",
        "WS-ESTADO-AUTORIZACION",
        "ESTADO-CUENTA",
    ],
)
def test_real_config_tags_status_names(tmp_path: Path, name: str) -> None:
    assert _tag_names(tmp_path, name)[name] == "status"


@pytest.mark.parametrize(
    "name",
    ["WS-STATUS-FLAG", "WS-STATE-FLAG", "WS-IND-ESTADO", "WS-INDICADOR-ESTADO"],
)
def test_real_config_tags_status_flag_names(tmp_path: Path, name: str) -> None:
    # Correccion pre-commit 15B3-C1: `status-name` y `status-flag-name`
    # deben ser mutuamente NO ambiguas via regex puro (lookbehind negativo
    # `(?<!IND)(?<!INDICADOR)` en status-name) -- IND-ESTADO/INDICADOR-ESTADO
    # son patrones POSITIVOS de status_flag, nunca colisionan con status.
    assert _tag_names(tmp_path, name)[name] == "status_flag"


def test_real_config_status_regex_does_not_match_statement(tmp_path: Path) -> None:
    """Precision > recall (Fase 15B3-C1, seccion 6): `STATE` como
    subcadena de `STATEMENT` nunca debe producir un falso positivo."""
    assert _tag_names(tmp_path, "WS-STATEMENT-COUNT")["WS-STATEMENT-COUNT"] is None


@pytest.mark.parametrize(
    "name",
    ["WS-SALDO", "WS-MONTO", "WS-BLOQUEADO", "WS-CLIENTE-ID", "WS-FLAG", "WS-IND"],
)
def test_real_config_ordinary_names_get_no_status_tag(tmp_path: Path, name: str) -> None:
    # WS-FLAG y WS-IND (Fase 15B3-C1, correccion pre-commit): ni
    # status-name ni status-flag-name deben matchear un nombre generico
    # que solo contiene el segmento FLAG o IND sin ESTADO/STATUS/STATE.
    assert _tag_names(tmp_path, name)[name] is None
