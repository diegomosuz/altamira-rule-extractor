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


def test_current_release_version_is_1_18_3() -> None:
    """Hotfix v1.18.3 (endurecimiento de confiabilidad sobre v1.18.2 ya
    publicado): PATCH, no MINOR -- fiabilidad de Claims de evidencia del
    guardrail (literales de negocio, gobernanza field-first, completitud
    por campo/multi-campo) y normalizacion de limites de token en el
    parser, sin cambios de contrato/schema persistido ni de deteccion de
    candidatos. El tag `v1.18.3` se crea recien en la fase final de
    release, nunca aqui. (Anteriormente
    `test_current_release_version_is_1_18_2`, ver historial de v1.18.2,
    ya publicado e inmutable.)"""
    assert _read_pyproject_version() == "1.18.3"
