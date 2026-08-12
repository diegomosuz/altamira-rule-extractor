"""Consistencia de version entre las tres fuentes de verdad (Fase
15B4-B): pyproject.toml, parser/pom.xml, src/altamira_extractor/
__init__.py. Nunca requiere Docker/Neo4j -- solo filesystem local."""

from __future__ import annotations

from scripts.release.version_check import (
    _read_package_version,
    _read_pom_version,
    _read_pyproject_version,
)


def test_pyproject_pom_and_package_versions_match() -> None:
    pyproject_version = _read_pyproject_version()
    pom_version = _read_pom_version()
    package_version = _read_package_version()

    assert pyproject_version == pom_version == package_version


def test_current_release_version_is_1_17_0() -> None:
    """Fase 15B4-B Seccion 0.A: decision de producto ya tomada, este
    release completo es 1.17.0 -- el tag `v1.17.0` se crea recien en
    la fase final de release, nunca aqui."""
    assert _read_pyproject_version() == "1.17.0"
