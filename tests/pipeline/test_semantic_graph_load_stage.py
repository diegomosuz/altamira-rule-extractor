"""Tests de la orquestacion SEMANTIC_GRAPH_LOADED (sin Neo4j real).

`Neo4jRepository` se sustituye por un doble de prueba minimo: estos tests
verifican la orquestacion (precondicion, secuencia de llamadas, traduccion
de errores), no el comportamiento real de Neo4j (eso vive en
test_neo4j_repository.py y en tests/neo4j_integration/).
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
    StageStatus,
)
from altamira_extractor.contracts.run_state import StageExecution
from altamira_extractor.contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from altamira_extractor.pipeline import semantic_graph_load_stage as stage_module
from altamira_extractor.pipeline.errors import (
    GraphLoadError,
    Neo4jAuthenticationError,
    Neo4jConfigurationError,
)
from altamira_extractor.pipeline.neo4j_repository import GraphLoadResult
from altamira_extractor.pipeline.semantic_graph_load_stage import (
    load_and_validate_semantic_graph,
    run_semantic_graph_load_stage,
)

_HASH_A = "a" * 64


def _stage(stage: PipelineStage, status: StageStatus) -> StageExecution:
    now = datetime.now(UTC)
    kwargs: dict[str, Any] = {"stage": stage, "status": status}
    if status in (StageStatus.SUCCEEDED, StageStatus.FAILED):
        kwargs["started_at"] = now
        kwargs["finished_at"] = now
    if status == StageStatus.FAILED:
        kwargs["error"] = "fallo simulado"
    return StageExecution(**kwargs)


def _sample_graph() -> SemanticGraph:
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
        source_package_hash=_HASH_A,
        nodes=sorted([country, application], key=lambda n: n.id),
        relationships=[relationship],
    )


def _write_graph(path: Path, graph: SemanticGraph) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(graph.model_dump_json(), encoding="utf-8")


class _StubRepository:
    def __init__(
        self,
        *,
        connect_error: Exception | None = None,
        connectivity_error: Exception | None = None,
        version_error: Exception | None = None,
        load_error: Exception | None = None,
        load_result: GraphLoadResult | None = None,
    ) -> None:
        self.connect_error = connect_error
        self.connectivity_error = connectivity_error
        self.version_error = version_error
        self.load_error = load_error
        self.load_result = load_result or GraphLoadResult(
            node_count=2, relationship_count=1, server_version="5.24.0", database="neo4j"
        )
        self.calls: list[str] = []
        self.closed = False

    @classmethod
    def connect(cls, settings: Settings, **kwargs: Any) -> _StubRepository:
        instance: _StubRepository = kwargs["instance"]
        instance.calls.append("connect")
        if instance.connect_error is not None:
            raise instance.connect_error
        return instance

    def verify_connectivity(self) -> None:
        self.calls.append("verify_connectivity")
        if self.connectivity_error is not None:
            raise self.connectivity_error

    def server_version(self) -> str:
        self.calls.append("server_version")
        if self.version_error is not None:
            raise self.version_error
        return "5.24.0"

    def ensure_schema(self) -> None:
        self.calls.append("ensure_schema")

    def load_graph(
        self, graph: SemanticGraph, *, semantic_graph_hash: str, server_version: str
    ) -> GraphLoadResult:
        self.calls.append("load_graph")
        if self.load_error is not None:
            raise self.load_error
        return self.load_result

    def close(self) -> None:
        self.calls.append("close")
        self.closed = True


def _install_stub(monkeypatch: pytest.MonkeyPatch, stub: _StubRepository) -> None:
    class _Factory:
        @staticmethod
        def connect(settings: Settings) -> _StubRepository:
            return _StubRepository.connect(settings, instance=stub)

    monkeypatch.setattr(stage_module, "Neo4jRepository", _Factory)


class _ExplodingRepositoryFactory:
    """Usado para probar que la precondicion falla ANTES de tocar Neo4j."""

    @staticmethod
    def connect(settings: Settings) -> Any:
        raise AssertionError("Neo4jRepository.connect no deberia llamarse")


# --- _verify_semantic_graph_built_precondition (via run_semantic_graph_load_stage) ---


def test_missing_semantic_graph_built_stage_raises_before_touching_neo4j(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    graph_path = tmp_path / "04-semantic-graph.json"
    _write_graph(graph_path, _sample_graph())

    with pytest.raises(GraphLoadError):
        run_semantic_graph_load_stage(
            run_stages=[], semantic_graph_path=graph_path, settings=Settings()
        )


def test_duplicate_semantic_graph_built_stage_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    graph_path = tmp_path / "04-semantic-graph.json"
    _write_graph(graph_path, _sample_graph())
    stages = [
        _stage(PipelineStage.SEMANTIC_GRAPH_BUILT, StageStatus.SUCCEEDED),
        _stage(PipelineStage.SEMANTIC_GRAPH_BUILT, StageStatus.SUCCEEDED),
    ]

    with pytest.raises(GraphLoadError):
        run_semantic_graph_load_stage(
            run_stages=stages, semantic_graph_path=graph_path, settings=Settings()
        )


def test_semantic_graph_built_not_succeeded_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    graph_path = tmp_path / "04-semantic-graph.json"
    _write_graph(graph_path, _sample_graph())
    stages = [_stage(PipelineStage.SEMANTIC_GRAPH_BUILT, StageStatus.FAILED)]

    with pytest.raises(GraphLoadError):
        run_semantic_graph_load_stage(
            run_stages=stages, semantic_graph_path=graph_path, settings=Settings()
        )


# --- load_and_validate_semantic_graph ---


def test_load_and_validate_semantic_graph_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(GraphLoadError):
        load_and_validate_semantic_graph(tmp_path / "no-existe.json")


def test_load_and_validate_semantic_graph_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "04-semantic-graph.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(GraphLoadError):
        load_and_validate_semantic_graph(path)


def test_load_and_validate_semantic_graph_returns_graph_and_real_hash(tmp_path: Path) -> None:
    import hashlib

    path = tmp_path / "04-semantic-graph.json"
    graph = _sample_graph()
    _write_graph(path, graph)

    loaded_graph, digest = load_and_validate_semantic_graph(path)

    assert loaded_graph == graph
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


# --- run_semantic_graph_load_stage: orquestacion feliz y traduccion de errores ---


def _succeeded_stages() -> list[StageExecution]:
    return [_stage(PipelineStage.SEMANTIC_GRAPH_BUILT, StageStatus.SUCCEEDED)]


def test_happy_path_calls_repository_in_order_and_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    graph_path = tmp_path / "04-semantic-graph.json"
    _write_graph(graph_path, _sample_graph())
    stub = _StubRepository()
    _install_stub(monkeypatch, stub)

    result = run_semantic_graph_load_stage(
        run_stages=_succeeded_stages(), semantic_graph_path=graph_path, settings=Settings()
    )

    assert result == stub.load_result
    assert stub.calls == [
        "connect",
        "verify_connectivity",
        "server_version",
        "ensure_schema",
        "load_graph",
        "close",
    ]
    assert stub.closed


def test_connect_error_is_translated_and_never_calls_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    graph_path = tmp_path / "04-semantic-graph.json"
    _write_graph(graph_path, _sample_graph())
    stub = _StubRepository(connect_error=Neo4jConfigurationError("uri invalida"))
    _install_stub(monkeypatch, stub)

    with pytest.raises(GraphLoadError):
        run_semantic_graph_load_stage(
            run_stages=_succeeded_stages(), semantic_graph_path=graph_path, settings=Settings()
        )

    assert "close" not in stub.calls


def test_connectivity_error_is_translated_and_still_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    graph_path = tmp_path / "04-semantic-graph.json"
    _write_graph(graph_path, _sample_graph())
    stub = _StubRepository(connectivity_error=Neo4jAuthenticationError("credenciales invalidas"))
    _install_stub(monkeypatch, stub)

    with pytest.raises(GraphLoadError):
        run_semantic_graph_load_stage(
            run_stages=_succeeded_stages(), semantic_graph_path=graph_path, settings=Settings()
        )

    assert stub.calls == ["connect", "verify_connectivity", "close"]


def test_graph_load_error_from_repository_propagates_unwrapped_and_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    graph_path = tmp_path / "04-semantic-graph.json"
    _write_graph(graph_path, _sample_graph())
    original = GraphLoadError("conteo de nodos administrados no coincide")
    stub = _StubRepository(load_error=original)
    _install_stub(monkeypatch, stub)

    with pytest.raises(GraphLoadError) as excinfo:
        run_semantic_graph_load_stage(
            run_stages=_succeeded_stages(), semantic_graph_path=graph_path, settings=Settings()
        )

    assert excinfo.value is original
    assert stub.calls[-1] == "close"
