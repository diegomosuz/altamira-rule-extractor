"""Guardia de integridad checkout-independiente entre `config/ground_truth/
synthetic_engineering.yaml` y los archivos `.cbl` que referencia (mismo
patron que `test_catherine_fixtures_integrity.py`, aplicado al catalogo
completo en vez de a un conjunto fijo de hashes hardcodeados).

`pipeline/functional_validation_matcher.py::compute_case_applicability`
decide APPLICABLE/NOT_APPLICABLE comparando el SHA-256 declarado de cada
`GroundTruthFixtureReference` contra el hash REAL de los bytes
extraidos en el run evaluado -- exige coincidencia byte a byte exacta.
`config/ground_truth/fixtures/*.cbl` no tenia una politica de fin de
linea fijada (`.gitattributes`); con `core.autocrlf=true` y sin esa
politica, un checkout nuevo (`git worktree add`, un clon fresco, CI)
puede materializar estos archivos con CRLF aunque el blob commiteado
sea LF, cambiando su SHA-256 real y dejando esos casos
NOT_APPLICABLE/NOT_EVALUATED de forma silenciosa e independiente del
entorno -- nunca un defecto de `synthetic_engineering.yaml` ni del
motor de matching, sino del checkout. Esta prueba detecta esa
divergencia directamente (sin JAR, sin Neo4j, sin pipeline real) contra
el catalogo real completo, para cualquier fixture presente o futura."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from altamira_extractor.pipeline.functional_validation_service import load_ground_truth_set

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GROUND_TRUTH_PATH = _REPO_ROOT / "config" / "ground_truth" / "synthetic_engineering.yaml"


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_reference_cases() -> list[tuple[str, str, str]]:
    ground_truth = load_ground_truth_set(_GROUND_TRUTH_PATH)
    return [
        (case.case_id, fixture.relative_path, fixture.sha256)
        for case in ground_truth.cases
        for fixture in case.fixtures
    ]


@pytest.mark.parametrize(
    "case_id,relative_path,expected_sha256",
    _fixture_reference_cases(),
    ids=lambda value: value if isinstance(value, str) and len(value) < 40 else "sha256",
)
def test_ground_truth_fixture_checkout_matches_declared_sha256(
    case_id: str, relative_path: str, expected_sha256: str
) -> None:
    fixture_path = _REPO_ROOT / relative_path
    assert fixture_path.is_file(), f"{case_id}: fixture ausente en este checkout: {relative_path}"
    actual_sha256 = _sha256_of(fixture_path)
    assert actual_sha256 == expected_sha256, (
        f"{case_id}: {relative_path} no coincide con el sha256 declarado en "
        f"synthetic_engineering.yaml (esperado {expected_sha256}, obtenido "
        f"{actual_sha256}) -- probable conversion de fin de linea en este checkout "
        "(ver .gitattributes); el caso quedaria NOT_APPLICABLE de forma silenciosa."
    )
