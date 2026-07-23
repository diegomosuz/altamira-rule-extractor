"""Tests unitarios de yaml_utils.read_yaml_config: archivo ausente, no
regular, mal codificado, mal formado, y que los mensajes de error no
filtren paths absolutos (solo se usa `path.name`)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from altamira_extractor.pipeline.errors import SemanticConfigError
from altamira_extractor.pipeline.yaml_utils import read_yaml_config


def test_valid_yaml_returns_document_and_real_sha256(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("version: '1.0'\nvalues: [a, b]\n", encoding="utf-8")

    document, config_hash = read_yaml_config(path)

    assert document == {"version": "1.0", "values": ["a", "b"]}
    assert config_hash == hashlib.sha256(path.read_bytes()).hexdigest()


def test_missing_file_is_fatal_without_leaking_absolute_path(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist.yml"

    with pytest.raises(SemanticConfigError) as exc_info:
        read_yaml_config(path)

    assert str(tmp_path) not in str(exc_info.value)
    assert "does-not-exist.yml" in str(exc_info.value)


def test_directory_instead_of_regular_file_is_fatal(tmp_path: Path) -> None:
    directory = tmp_path / "semantic-tags.yml"
    directory.mkdir()

    with pytest.raises(SemanticConfigError):
        read_yaml_config(directory)


def test_non_utf8_bytes_are_fatal(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_bytes(b"version: \xff\xfe invalid utf-8")

    with pytest.raises(SemanticConfigError, match="UTF-8"):
        read_yaml_config(path)


def test_malformed_yaml_is_fatal(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("version: [unterminated", encoding="utf-8")

    with pytest.raises(SemanticConfigError, match="mal formado"):
        read_yaml_config(path)


def test_hash_is_computed_over_raw_bytes_not_parsed_document(tmp_path: Path) -> None:
    # Dos YAML semanticamente identicos pero con distinto formato
    # (espacios/comentarios) deben producir hashes distintos: el hash es
    # sobre los bytes reales del archivo, no sobre el documento parseado.
    path_a = tmp_path / "a.yml"
    path_a.write_text("version: '1.0'\n", encoding="utf-8")
    path_b = tmp_path / "b.yml"
    path_b.write_text("version: '1.0'  # comentario\n", encoding="utf-8")

    _, hash_a = read_yaml_config(path_a)
    _, hash_b = read_yaml_config(path_b)

    assert hash_a != hash_b
