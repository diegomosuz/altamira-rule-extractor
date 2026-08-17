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


def test_current_release_version_is_1_18_2() -> None:
    """Hotfix v1.18.2 (production bugfix sobre v1.18.1 ya publicado):
    PATCH, no MINOR -- reemplaza la dependencia de reintentos LLM para
    la fuga de identificadores tecnicos en traceability por una
    correccion deterministica (sin cambios de contrato/schema: el
    unico campo nuevo en RepairAttemptRecord.response_hash ya era
    opcional), no agrega funcionalidad nueva. El tag `v1.18.2` se crea
    recien en la fase final de release, nunca aqui. (Anteriormente
    `test_current_release_version_is_1_18_1`, ver historial de v1.18.1,
    ya publicado e inmutable.)"""
    assert _read_pyproject_version() == "1.18.2"
