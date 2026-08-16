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


def test_current_release_version_is_1_18_0() -> None:
    """Fase v1.18.0 (release preparation): decision de producto ya
    tomada -- MINOR, no PATCH, porque agrega funcionalidad nueva
    retrocompatible (identidad FIERN, progreso, About, duplicados,
    Limpiar job) ademas de la correccion de integridad deterministica.
    Este release completo es 1.18.0 -- el tag `v1.18.0` se crea recien
    en la fase final de release, nunca aqui. (Anteriormente
    `test_current_release_version_is_1_17_0`, ver historial de v1.17.0,
    ya publicado e inmutable.)"""
    assert _read_pyproject_version() == "1.18.0"
