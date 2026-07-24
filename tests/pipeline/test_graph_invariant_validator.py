"""Tests de GraphInvariantValidator (sin Neo4j real).

La ejecucion real de `queries/v1/invariants.cypher` contra un servidor
Neo4j (incluidas las pruebas de que DomainTerm sin mapping y Decision sin
LEADS_TO NO son violaciones, que dependen del comportamiento real del
motor Cypher) vive en tests/neo4j_integration/. Aqui se prueba la logica
propia de este modulo: derivacion de firmas de endpoints, verificacion
del hash de configuracion antes de ejecutar la consulta, mapeo de filas a
`InvariantViolation`, deduplicacion/orden deterministico y clasificacion
de una `severity` desconocida como error fatal.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.contracts.enums import Severity
from altamira_extractor.contracts.invariants import InvariantViolation
from altamira_extractor.pipeline.errors import GraphValidationError
from altamira_extractor.pipeline.graph_invariant_validator import (
    relationship_endpoint_signatures,
    run_invariants,
)

_VALID_SEMANTIC_TAGS_YAML = """\
version: "1.0"
allowed_tags:
  - ACCOUNT_NUMBER
  - CURRENCY_CODE
rules: []
"""


class _FakeRepository:
    def __init__(self, rows: list[dict[str, str]] | Exception) -> None:
        self._rows = rows
        self.received_kwargs: dict[str, Any] | None = None

    def run_invariants(
        self,
        cypher_text: str,
        *,
        package_hash: str,
        allowed_semantic_tags: list[str],
        allowed_relationship_signatures: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        self.received_kwargs = {
            "cypher_text": cypher_text,
            "package_hash": package_hash,
            "allowed_semantic_tags": allowed_semantic_tags,
            "allowed_relationship_signatures": allowed_relationship_signatures,
        }
        if isinstance(self._rows, Exception):
            raise self._rows
        return self._rows


def _write_semantic_tags_yaml(path: Path) -> str:
    path.write_text(_VALID_SEMANTIC_TAGS_YAML, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


_DEFAULT_CYPHER = (
    "MATCH (n) RETURN 1 AS code, 'WARNING' AS severity, 'x' AS entity_id, 'm' AS message"
)


def _write_cypher(path: Path, text: str = _DEFAULT_CYPHER) -> None:
    path.write_text(text, encoding="utf-8")


# --- relationship_endpoint_signatures ---


def test_relationship_endpoint_signatures_has_exactly_21_entries() -> None:
    signatures = relationship_endpoint_signatures()
    assert len(signatures) == 21


def test_relationship_endpoint_signatures_includes_reads_from_paragraph_and_batch_job() -> None:
    signatures = relationship_endpoint_signatures()
    assert {"type": "READS", "from_label": "Paragraph", "to_label": "Table"} in signatures
    assert {"type": "READS", "from_label": "BatchJob", "to_label": "Table"} in signatures


def test_relationship_endpoint_signatures_is_sorted_deterministically() -> None:
    signatures = relationship_endpoint_signatures()
    keys = [(s["type"], s["from_label"], s["to_label"]) for s in signatures]
    assert keys == sorted(keys)


# --- run_invariants: precondiciones ---


def test_run_invariants_raises_if_cypher_file_missing(tmp_path: Path) -> None:
    semantic_tags_path = tmp_path / "semantic-tags.yml"
    expected_hash = _write_semantic_tags_yaml(semantic_tags_path)
    repository = _FakeRepository([])

    with pytest.raises(GraphValidationError):
        run_invariants(
            repository,  # type: ignore[arg-type]
            invariants_cypher_path=tmp_path / "no-existe.cypher",
            package_hash="a" * 64,
            semantic_tags_path=semantic_tags_path,
            expected_semantic_tags_config_hash=expected_hash,
        )


def test_run_invariants_raises_if_semantic_tags_config_hash_changed(tmp_path: Path) -> None:
    semantic_tags_path = tmp_path / "semantic-tags.yml"
    _write_semantic_tags_yaml(semantic_tags_path)
    cypher_path = tmp_path / "invariants.cypher"
    _write_cypher(cypher_path)
    repository = _FakeRepository([])

    with pytest.raises(GraphValidationError):
        run_invariants(
            repository,  # type: ignore[arg-type]
            invariants_cypher_path=cypher_path,
            package_hash="a" * 64,
            semantic_tags_path=semantic_tags_path,
            expected_semantic_tags_config_hash="0" * 64,
        )

    # el drift de configuracion se detecta ANTES de tocar Neo4j.
    assert repository.received_kwargs is None


# --- run_invariants: ejecucion, mapeo, orden, deduplicacion ---


def test_run_invariants_passes_allowed_tags_and_signatures_through(tmp_path: Path) -> None:
    semantic_tags_path = tmp_path / "semantic-tags.yml"
    expected_hash = _write_semantic_tags_yaml(semantic_tags_path)
    cypher_path = tmp_path / "invariants.cypher"
    _write_cypher(cypher_path)
    repository = _FakeRepository([])

    run_invariants(
        repository,  # type: ignore[arg-type]
        invariants_cypher_path=cypher_path,
        package_hash="a" * 64,
        semantic_tags_path=semantic_tags_path,
        expected_semantic_tags_config_hash=expected_hash,
    )

    assert repository.received_kwargs is not None
    assert repository.received_kwargs["package_hash"] == "a" * 64
    assert repository.received_kwargs["allowed_semantic_tags"] == [
        "ACCOUNT_NUMBER",
        "CURRENCY_CODE",
    ]
    assert repository.received_kwargs["allowed_relationship_signatures"] == (
        relationship_endpoint_signatures()
    )
    assert repository.received_kwargs["cypher_text"] == cypher_path.read_text(encoding="utf-8")


def test_run_invariants_maps_rows_and_returns_query_hash(tmp_path: Path) -> None:
    semantic_tags_path = tmp_path / "semantic-tags.yml"
    expected_hash = _write_semantic_tags_yaml(semantic_tags_path)
    cypher_path = tmp_path / "invariants.cypher"
    _write_cypher(cypher_path)
    rows = [
        {"code": "ORPHAN_DECISION", "severity": "WARNING", "entity_id": "d::1", "message": "m1"},
    ]
    repository = _FakeRepository(rows)

    violations, invariants_query_hash = run_invariants(
        repository,  # type: ignore[arg-type]
        invariants_cypher_path=cypher_path,
        package_hash="a" * 64,
        semantic_tags_path=semantic_tags_path,
        expected_semantic_tags_config_hash=expected_hash,
    )

    assert violations == [
        InvariantViolation(
            code="ORPHAN_DECISION", severity=Severity.WARNING, entity_id="d::1", message="m1"
        )
    ]
    assert invariants_query_hash == hashlib.sha256(cypher_path.read_bytes()).hexdigest()


def test_run_invariants_deduplicates_exact_duplicate_rows(tmp_path: Path) -> None:
    semantic_tags_path = tmp_path / "semantic-tags.yml"
    expected_hash = _write_semantic_tags_yaml(semantic_tags_path)
    cypher_path = tmp_path / "invariants.cypher"
    _write_cypher(cypher_path)
    row = {"code": "ORPHAN_DECISION", "severity": "WARNING", "entity_id": "d::1", "message": "m1"}
    repository = _FakeRepository([row, dict(row)])

    violations, _ = run_invariants(
        repository,  # type: ignore[arg-type]
        invariants_cypher_path=cypher_path,
        package_hash="a" * 64,
        semantic_tags_path=semantic_tags_path,
        expected_semantic_tags_config_hash=expected_hash,
    )

    assert len(violations) == 1


def test_run_invariants_sorts_violations_deterministically(tmp_path: Path) -> None:
    semantic_tags_path = tmp_path / "semantic-tags.yml"
    expected_hash = _write_semantic_tags_yaml(semantic_tags_path)
    cypher_path = tmp_path / "invariants.cypher"
    _write_cypher(cypher_path)
    rows = [
        {"code": "Z_CODE", "severity": "WARNING", "entity_id": "z::1", "message": "m"},
        {"code": "A_CODE", "severity": "ERROR", "entity_id": "a::1", "message": "m"},
    ]
    repository = _FakeRepository(rows)

    violations, _ = run_invariants(
        repository,  # type: ignore[arg-type]
        invariants_cypher_path=cypher_path,
        package_hash="a" * 64,
        semantic_tags_path=semantic_tags_path,
        expected_semantic_tags_config_hash=expected_hash,
    )

    assert [v.code for v in violations] == ["A_CODE", "Z_CODE"]


def test_run_invariants_rejects_unknown_severity(tmp_path: Path) -> None:
    semantic_tags_path = tmp_path / "semantic-tags.yml"
    expected_hash = _write_semantic_tags_yaml(semantic_tags_path)
    cypher_path = tmp_path / "invariants.cypher"
    _write_cypher(cypher_path)
    rows = [{"code": "X", "severity": "CRITICAL", "entity_id": "x::1", "message": "m"}]
    repository = _FakeRepository(rows)

    with pytest.raises(GraphValidationError):
        run_invariants(
            repository,  # type: ignore[arg-type]
            invariants_cypher_path=cypher_path,
            package_hash="a" * 64,
            semantic_tags_path=semantic_tags_path,
            expected_semantic_tags_config_hash=expected_hash,
        )


def test_run_invariants_propagates_repository_query_error(tmp_path: Path) -> None:
    from altamira_extractor.pipeline.errors import Neo4jQueryError

    semantic_tags_path = tmp_path / "semantic-tags.yml"
    expected_hash = _write_semantic_tags_yaml(semantic_tags_path)
    cypher_path = tmp_path / "invariants.cypher"
    _write_cypher(cypher_path)
    repository = _FakeRepository(Neo4jQueryError("sintaxis invalida"))

    with pytest.raises(Neo4jQueryError):
        run_invariants(
            repository,  # type: ignore[arg-type]
            invariants_cypher_path=cypher_path,
            package_hash="a" * 64,
            semantic_tags_path=semantic_tags_path,
            expected_semantic_tags_config_hash=expected_hash,
        )
