"""Tests de Settings: manifest_xsd_path debe ser estable frente al CWD."""

from __future__ import annotations

from pathlib import Path

import pytest

from altamira_extractor.config import Settings, _discover_repo_root


def test_manifest_xsd_path_points_to_the_real_schema_file() -> None:
    settings = Settings()
    assert settings.manifest_xsd_path.is_absolute()
    assert settings.manifest_xsd_path.name == "manifest.xsd"
    assert settings.manifest_xsd_path.is_file()


def test_manifest_xsd_path_is_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = Settings().manifest_xsd_path

    other_dir = tmp_path / "somewhere" / "else"
    other_dir.mkdir(parents=True)
    monkeypatch.chdir(other_dir)

    from_elsewhere = Settings().manifest_xsd_path

    assert from_elsewhere == baseline
    assert from_elsewhere.is_file()


def test_semantic_tags_path_points_to_the_real_config_file() -> None:
    settings = Settings()
    assert settings.semantic_tags_path.is_absolute()
    assert settings.semantic_tags_path.name == "semantic-tags.yml"
    assert settings.semantic_tags_path.is_file()


def test_semantic_tags_path_is_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = Settings().semantic_tags_path

    other_dir = tmp_path / "somewhere" / "else"
    other_dir.mkdir(parents=True)
    monkeypatch.chdir(other_dir)

    from_elsewhere = Settings().semantic_tags_path

    assert from_elsewhere == baseline
    assert from_elsewhere.is_file()


def test_domain_glossary_path_points_to_the_real_config_file() -> None:
    settings = Settings()
    assert settings.domain_glossary_path.is_absolute()
    assert settings.domain_glossary_path.name == "domain-glossary.example.yml"
    assert settings.domain_glossary_path.is_file()


def test_domain_glossary_path_is_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = Settings().domain_glossary_path

    other_dir = tmp_path / "somewhere" / "else"
    other_dir.mkdir(parents=True)
    monkeypatch.chdir(other_dir)

    from_elsewhere = Settings().domain_glossary_path

    assert from_elsewhere == baseline
    assert from_elsewhere.is_file()


def test_discover_repo_root_raises_descriptive_error_when_not_found(tmp_path: Path) -> None:
    # tmp_path vive fuera del repo (temp del sistema): ninguno de sus
    # ancestros contiene pyproject.toml, asi que el recorrido hacia arriba
    # debe agotarse y fallar con un mensaje claro, no un traceback opaco.
    isolated = tmp_path / "no-repo-here"
    isolated.mkdir()

    with pytest.raises(RuntimeError, match="pyproject.toml"):
        _discover_repo_root(start=isolated)
