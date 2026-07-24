"""Tests de la orquestacion GRAPH_VALIDATED (sin Neo4j real).

`Neo4jRepository` y `run_invariants` se sustituyen por dobles de prueba:
estos tests verifican la orquestacion (precondicion, deteccion de drift,
persistencia del artefacto, traduccion de errores), no el comportamiento
real de Neo4j ni de `invariants.cypher` (ver test_neo4j_repository.py,
test_graph_invariant_validator.py y tests/neo4j_integration/).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import (
    NodeLabel,
    PipelineStage,
    RelationshipType,
    Severity,
    StageStatus,
)
from altamira_extractor.contracts.invariants import InvariantArtifact, InvariantViolation
from altamira_extractor.contracts.run_state import StageExecution
from altamira_extractor.contracts.semantic_enrichment import SemanticEnrichmentArtifact
from altamira_extractor.contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from altamira_extractor.pipeline import graph_validated_stage as stage_module
from altamira_extractor.pipeline.errors import (
    GraphValidationError,
    Neo4jAuthenticationError,
    Neo4jConfigurationError,
)
from altamira_extractor.pipeline.graph_validated_stage import run_graph_validated_stage
from altamira_extractor.pipeline.neo4j_repository import ActiveGraphLoad, GraphDrift

_HASH_A = "a" * 64
_RUN_ID = "run-1"


def _stage(stage: PipelineStage, status: StageStatus) -> StageExecution:
    now = datetime.now(UTC)
    kwargs: dict[str, Any] = {"stage": stage, "status": status}
    if status in (StageStatus.SUCCEEDED, StageStatus.FAILED):
        kwargs["started_at"] = now
        kwargs["finished_at"] = now
    if status == StageStatus.FAILED:
        kwargs["error"] = "fallo simulado"
    return StageExecution(**kwargs)


def _sample_graph(source_package_hash: str = _HASH_A) -> SemanticGraph:
    country = GraphNode(id="country::AR", labels=[NodeLabel.COUNTRY], properties={"code": "AR"})
    application = GraphNode(
        id="application::AR::TRF", labels=[NodeLabel.APPLICATION], properties={"name": "TRF"}
    )
    relationship = GraphRelationship(
        type=RelationshipType.HAS_APPLICATION,
        from_id=country.id,
        to_id=application.id,
        properties={},
    )
    return SemanticGraph(
        source_package_hash=source_package_hash,
        nodes=sorted([country, application], key=lambda n: n.id),
        relationships=[relationship],
    )


def _write_graph(path: Path, graph: SemanticGraph) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(graph.model_dump_json(), encoding="utf-8")


def _write_semantic_enrichment(path: Path, *, semantic_tags_config_hash: str = "c" * 64) -> None:
    artifact = SemanticEnrichmentArtifact(
        run_id=_RUN_ID,
        source_package_hash=_HASH_A,
        semantic_tags_config_hash=semantic_tags_config_hash,
        domain_glossary_config_hash="d" * 64,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(artifact.model_dump_json(), encoding="utf-8")


def _matching_active_graph_load(graph: SemanticGraph, semantic_graph_hash: str) -> ActiveGraphLoad:
    return ActiveGraphLoad(
        semantic_graph_hash=semantic_graph_hash,
        source_package_hash=graph.source_package_hash,
        node_count=len(graph.nodes),
        relationship_count=len(graph.relationships),
        server_version="5.24.0",
        database="neo4j",
    )


_CLEAN_DRIFT = GraphDrift(missing_ids=[], extra_ids=[], missing_edge_keys=[], extra_edge_keys=[])


class _StubRepository:
    def __init__(
        self,
        *,
        connect_error: Exception | None = None,
        connectivity_error: Exception | None = None,
        active: ActiveGraphLoad | None = None,
        drift: GraphDrift = _CLEAN_DRIFT,
    ) -> None:
        self.connect_error = connect_error
        self.connectivity_error = connectivity_error
        self.active = active
        self.drift = drift
        self.calls: list[str] = []
        self.closed = False

    def verify_connectivity(self) -> None:
        self.calls.append("verify_connectivity")
        if self.connectivity_error is not None:
            raise self.connectivity_error

    def read_active_graph_load(self) -> ActiveGraphLoad | None:
        self.calls.append("read_active_graph_load")
        return self.active

    def compute_drift(self, graph: SemanticGraph) -> GraphDrift:
        self.calls.append("compute_drift")
        return self.drift

    def close(self) -> None:
        self.calls.append("close")
        self.closed = True


def _install_stub(monkeypatch: pytest.MonkeyPatch, stub: _StubRepository) -> None:
    class _Factory:
        @staticmethod
        def connect(settings: Settings) -> _StubRepository:
            stub.calls.append("connect")
            if stub.connect_error is not None:
                raise stub.connect_error
            return stub

    monkeypatch.setattr(stage_module, "Neo4jRepository", _Factory)


def _install_run_invariants(
    monkeypatch: pytest.MonkeyPatch,
    violations: list[InvariantViolation],
    *,
    query_hash: str = "f" * 64,
) -> None:
    def _fake_run_invariants(
        repository: Any, **kwargs: Any
    ) -> tuple[list[InvariantViolation], str]:
        return violations, query_hash

    monkeypatch.setattr(stage_module, "run_invariants", _fake_run_invariants)


class _ExplodingRepositoryFactory:
    @staticmethod
    def connect(settings: Settings) -> Any:
        raise AssertionError("Neo4jRepository.connect no deberia llamarse")


def _base_kwargs(tmp_path: Path, *, run_stages: list[StageExecution]) -> dict[str, Any]:
    graph_path = tmp_path / "04-semantic-graph.json"
    enrichment_path = tmp_path / "03b-semantic-enrichment.json"
    _write_graph(graph_path, _sample_graph())
    _write_semantic_enrichment(enrichment_path)
    return {
        "run_id": _RUN_ID,
        "source_package_hash": _HASH_A,
        "run_stages": run_stages,
        "semantic_graph_path": graph_path,
        "semantic_enrichment_path": enrichment_path,
        "invariants_cypher_path": tmp_path / "invariants.cypher",
        "invariants_path": tmp_path / "05-invariants.json",
        "settings": Settings(),
    }


# --- precondicion ---


def test_missing_semantic_graph_loaded_stage_raises_before_touching_neo4j(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=[])

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(**kwargs)


def test_duplicate_semantic_graph_loaded_stage_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    stages = [
        _stage(PipelineStage.SEMANTIC_GRAPH_LOADED, StageStatus.SUCCEEDED),
        _stage(PipelineStage.SEMANTIC_GRAPH_LOADED, StageStatus.SUCCEEDED),
    ]
    kwargs = _base_kwargs(tmp_path, run_stages=stages)

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(**kwargs)


def test_semantic_graph_loaded_not_succeeded_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    stages = [_stage(PipelineStage.SEMANTIC_GRAPH_LOADED, StageStatus.FAILED)]
    kwargs = _base_kwargs(tmp_path, run_stages=stages)

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(**kwargs)


def _succeeded_stages() -> list[StageExecution]:
    return [_stage(PipelineStage.SEMANTIC_GRAPH_LOADED, StageStatus.SUCCEEDED)]


# --- artefacto/config releidos ---


def test_missing_semantic_graph_json_is_translated_to_graph_validation_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    kwargs["semantic_graph_path"].unlink()

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(**kwargs)


def test_missing_semantic_enrichment_json_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    kwargs["semantic_enrichment_path"].unlink()

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(**kwargs)


# --- drift entre el artefacto y AltamiraGraphLoad ---


def test_missing_active_graph_load_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stub = _StubRepository(active=None)
    _install_stub(monkeypatch, stub)
    _install_run_invariants(monkeypatch, [])
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(**kwargs)


def test_semantic_graph_hash_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    graph = _sample_graph()
    stub = _StubRepository(active=_matching_active_graph_load(graph, "wrong-hash" + "0" * 54))
    _install_stub(monkeypatch, stub)
    _install_run_invariants(monkeypatch, [])
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(**kwargs)


def test_source_package_hash_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import hashlib

    graph_path = tmp_path / "04-semantic-graph.json"
    graph = _sample_graph()
    _write_graph(graph_path, graph)
    semantic_graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    active = ActiveGraphLoad(
        semantic_graph_hash=semantic_graph_hash,
        source_package_hash="b" * 64,
        node_count=len(graph.nodes),
        relationship_count=len(graph.relationships),
        server_version="5.24.0",
        database="neo4j",
    )
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_run_invariants(monkeypatch, [])
    enrichment_path = tmp_path / "03b-semantic-enrichment.json"
    _write_semantic_enrichment(enrichment_path)

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(
            run_id=_RUN_ID,
            source_package_hash=_HASH_A,
            run_stages=_succeeded_stages(),
            semantic_graph_path=graph_path,
            semantic_enrichment_path=enrichment_path,
            invariants_cypher_path=tmp_path / "invariants.cypher",
            invariants_path=tmp_path / "05-invariants.json",
            settings=Settings(),
        )


def test_node_count_mismatch_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import hashlib

    graph_path = tmp_path / "04-semantic-graph.json"
    graph = _sample_graph()
    _write_graph(graph_path, graph)
    semantic_graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    active = ActiveGraphLoad(
        semantic_graph_hash=semantic_graph_hash,
        source_package_hash=graph.source_package_hash,
        node_count=999,
        relationship_count=len(graph.relationships),
        server_version="5.24.0",
        database="neo4j",
    )
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_run_invariants(monkeypatch, [])
    enrichment_path = tmp_path / "03b-semantic-enrichment.json"
    _write_semantic_enrichment(enrichment_path)

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(
            run_id=_RUN_ID,
            source_package_hash=_HASH_A,
            run_stages=_succeeded_stages(),
            semantic_graph_path=graph_path,
            semantic_enrichment_path=enrichment_path,
            invariants_cypher_path=tmp_path / "invariants.cypher",
            invariants_path=tmp_path / "05-invariants.json",
            settings=Settings(),
        )


def test_relationship_count_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import hashlib

    graph_path = tmp_path / "04-semantic-graph.json"
    graph = _sample_graph()
    _write_graph(graph_path, graph)
    semantic_graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    active = ActiveGraphLoad(
        semantic_graph_hash=semantic_graph_hash,
        source_package_hash=graph.source_package_hash,
        node_count=len(graph.nodes),
        relationship_count=999,
        server_version="5.24.0",
        database="neo4j",
    )
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_run_invariants(monkeypatch, [])
    enrichment_path = tmp_path / "03b-semantic-enrichment.json"
    _write_semantic_enrichment(enrichment_path)

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(
            run_id=_RUN_ID,
            source_package_hash=_HASH_A,
            run_stages=_succeeded_stages(),
            semantic_graph_path=graph_path,
            semantic_enrichment_path=enrichment_path,
            invariants_cypher_path=tmp_path / "invariants.cypher",
            invariants_path=tmp_path / "05-invariants.json",
            settings=Settings(),
        )


def test_drift_not_clean_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import hashlib

    graph_path = tmp_path / "04-semantic-graph.json"
    graph = _sample_graph()
    _write_graph(graph_path, graph)
    semantic_graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    dirty_drift = GraphDrift(
        missing_ids=["country::AR"], extra_ids=[], missing_edge_keys=[], extra_edge_keys=[]
    )
    stub = _StubRepository(active=active, drift=dirty_drift)
    _install_stub(monkeypatch, stub)
    _install_run_invariants(monkeypatch, [])
    enrichment_path = tmp_path / "03b-semantic-enrichment.json"
    _write_semantic_enrichment(enrichment_path)

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(
            run_id=_RUN_ID,
            source_package_hash=_HASH_A,
            run_stages=_succeeded_stages(),
            semantic_graph_path=graph_path,
            semantic_enrichment_path=enrichment_path,
            invariants_cypher_path=tmp_path / "invariants.cypher",
            invariants_path=tmp_path / "05-invariants.json",
            settings=Settings(),
        )


# --- flujo feliz + persistencia del artefacto ---


def _happy_kwargs(tmp_path: Path) -> tuple[dict[str, Any], SemanticGraph, str]:
    import hashlib

    graph_path = tmp_path / "04-semantic-graph.json"
    graph = _sample_graph()
    _write_graph(graph_path, graph)
    semantic_graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    enrichment_path = tmp_path / "03b-semantic-enrichment.json"
    _write_semantic_enrichment(enrichment_path)
    kwargs = {
        "run_id": _RUN_ID,
        "source_package_hash": _HASH_A,
        "run_stages": _succeeded_stages(),
        "semantic_graph_path": graph_path,
        "semantic_enrichment_path": enrichment_path,
        "invariants_cypher_path": tmp_path / "invariants.cypher",
        "invariants_path": tmp_path / "05-invariants.json",
        "settings": Settings(),
    }
    return kwargs, graph, semantic_graph_hash


def test_happy_path_with_only_warnings_succeeds_and_persists_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    warning = InvariantViolation(
        code="ORPHAN_DECISION", severity=Severity.WARNING, entity_id="d::1", message="msg"
    )
    _install_run_invariants(monkeypatch, [warning])

    warnings = run_graph_validated_stage(**kwargs)

    assert warnings == ["ORPHAN_DECISION: msg (d::1)"]
    assert stub.calls == [
        "connect",
        "verify_connectivity",
        "read_active_graph_load",
        "compute_drift",
        "close",
    ]
    assert stub.closed

    artifact = InvariantArtifact.model_validate_json(
        kwargs["invariants_path"].read_text(encoding="utf-8")
    )
    assert artifact.graph_validated is True
    assert artifact.error_count == 0
    assert artifact.warning_count == 1
    assert artifact.violations == [warning]


def test_error_violation_blocks_and_still_persists_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    error = InvariantViolation(
        code="INVALID_RELATIONSHIP_ENDPOINT",
        severity=Severity.ERROR,
        entity_id="r::1",
        message="msg",
    )
    _install_run_invariants(monkeypatch, [error])

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(**kwargs)

    artifact = InvariantArtifact.model_validate_json(
        kwargs["invariants_path"].read_text(encoding="utf-8")
    )
    assert artifact.graph_validated is False
    assert artifact.error_count == 1


def test_no_violations_succeeds_with_empty_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_run_invariants(monkeypatch, [])

    warnings = run_graph_validated_stage(**kwargs)

    assert warnings == []


# --- traduccion de errores Neo4j ---


def test_connect_error_is_translated_and_never_calls_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, _graph, _hash = _happy_kwargs(tmp_path)
    stub = _StubRepository(connect_error=Neo4jConfigurationError("uri invalida"))
    _install_stub(monkeypatch, stub)
    _install_run_invariants(monkeypatch, [])

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(**kwargs)

    assert "close" not in stub.calls


def test_connectivity_error_is_translated_and_still_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, _graph, _hash = _happy_kwargs(tmp_path)
    stub = _StubRepository(connectivity_error=Neo4jAuthenticationError("credenciales invalidas"))
    _install_stub(monkeypatch, stub)
    _install_run_invariants(monkeypatch, [])

    with pytest.raises(GraphValidationError):
        run_graph_validated_stage(**kwargs)

    assert stub.calls == ["connect", "verify_connectivity", "close"]
