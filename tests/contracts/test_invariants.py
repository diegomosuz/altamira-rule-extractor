"""Tests del contrato de artifacts/05-invariants.json."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts import InvariantArtifact, InvariantViolation, Severity

VALID_HASH = "a" * 64


def _violation(**overrides: object) -> InvariantViolation:
    defaults: dict[str, object] = {
        "code": "ORPHAN_DECISION",
        "severity": Severity.ERROR,
        "entity_id": "decision::1",
        "message": "Decision sin Paragraph",
    }
    defaults.update(overrides)
    return InvariantViolation(**defaults)  # type: ignore[arg-type]


def _artifact(**overrides: object) -> InvariantArtifact:
    defaults: dict[str, object] = {
        "run_id": "run-1",
        "source_package_hash": VALID_HASH,
        "semantic_graph_hash": VALID_HASH,
        "invariants_query_hash": VALID_HASH,
        "neo4j_database": "neo4j",
        "neo4j_server_version": "5.24.0",
        "node_count": 10,
        "relationship_count": 5,
        "violations": [],
        "error_count": 0,
        "warning_count": 0,
        "graph_validated": True,
        "warnings": [],
    }
    defaults.update(overrides)
    return InvariantArtifact(**defaults)  # type: ignore[arg-type]


def test_valid_artifact_round_trips() -> None:
    artifact = _artifact()
    dumped = artifact.to_stable_json()
    restored = InvariantArtifact.model_validate_json(dumped)
    assert restored == artifact


def test_valid_artifact_with_error_round_trips() -> None:
    violation = _violation()
    artifact = _artifact(
        violations=[violation], error_count=1, warning_count=0, graph_validated=False
    )
    restored = InvariantArtifact.model_validate_json(artifact.to_stable_json())
    assert restored == artifact


def test_duplicate_violation_rejected() -> None:
    violation = _violation()
    with pytest.raises(ValidationError, match="duplicados"):
        _artifact(violations=[violation, violation], error_count=2)


def test_unsorted_violations_rejected() -> None:
    first = _violation(code="Z_CODE", entity_id="z")
    second = _violation(code="A_CODE", entity_id="a")
    with pytest.raises(ValidationError, match="no esta ordenado"):
        _artifact(violations=[first, second], error_count=2)


def test_error_count_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="error_count"):
        _artifact(violations=[_violation()], error_count=0, graph_validated=True)


def test_warning_count_mismatch_rejected() -> None:
    warning = _violation(code="SOME_WARNING", severity=Severity.WARNING)
    with pytest.raises(ValidationError, match="warning_count"):
        _artifact(violations=[warning], error_count=0, warning_count=0, graph_validated=True)


def test_graph_validated_true_with_errors_rejected() -> None:
    with pytest.raises(ValidationError, match="graph_validated"):
        _artifact(violations=[_violation()], error_count=1, graph_validated=True)


def test_graph_validated_false_without_errors_rejected() -> None:
    with pytest.raises(ValidationError, match="graph_validated"):
        _artifact(graph_validated=False)


def test_warnings_duplicated_rejected() -> None:
    with pytest.raises(ValidationError, match="warnings contiene duplicados"):
        _artifact(warnings=["same", "same"])


def test_warnings_unsorted_rejected() -> None:
    with pytest.raises(ValidationError, match="warnings no esta ordenado"):
        _artifact(warnings=["zeta", "alpha"])


def test_only_warning_violations_allow_graph_validated_true() -> None:
    warning = _violation(code="SOME_WARNING", severity=Severity.WARNING)
    artifact = _artifact(
        violations=[warning], error_count=0, warning_count=1, graph_validated=True
    )
    assert artifact.graph_validated is True
