"""Tests de la orquestacion CANDIDATES_DETECTED (sin Neo4j real).

`Neo4jRepository` y `detect_candidates` se sustituyen por dobles de
prueba: estos tests verifican la orquestacion (precondicion, deteccion
de drift, configuracion semantica, persistencia del artefacto,
traduccion de errores, idempotencia), no el comportamiento real de
Neo4j ni de Q0 (ver test_neo4j_repository.py, test_candidate_detector.py
y tests/neo4j_integration/).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
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
from altamira_extractor.pipeline import candidates_detected_stage as stage_module
from altamira_extractor.pipeline.candidates_detected_stage import run_candidates_detected_stage
from altamira_extractor.pipeline.errors import (
    CandidateDetectionError,
    Neo4jAuthenticationError,
    Neo4jConfigurationError,
)
from altamira_extractor.pipeline.neo4j_repository import ActiveGraphLoad, GraphDrift

_HASH_A = "a" * 64
_RUN_ID = "run-1"

_VALID_SEMANTIC_TAGS_YAML = """\
version: "1.0"
allowed_tags:
  - return_code
  - amount
rules: []
"""

_VALID_SEMANTIC_TAGS_YAML_WITHOUT_RETURN_CODE = """\
version: "1.0"
allowed_tags:
  - amount
rules: []
"""


def _stage(stage: PipelineStage, status: StageStatus) -> StageExecution:
    now = datetime.now(UTC)
    kwargs: dict[str, Any] = {"stage": stage, "status": status}
    if status in (StageStatus.SUCCEEDED, StageStatus.FAILED):
        kwargs["started_at"] = now
        kwargs["finished_at"] = now
    if status == StageStatus.FAILED:
        kwargs["error"] = "fallo simulado"
    return StageExecution(**kwargs)


def _succeeded_stages() -> list[StageExecution]:
    return [_stage(PipelineStage.GRAPH_VALIDATED, StageStatus.SUCCEEDED)]


def _sample_graph(source_package_hash: str = _HASH_A) -> SemanticGraph:
    country = GraphNode(id="country::AR", labels=[NodeLabel.COUNTRY], properties={})
    application = GraphNode(
        id="application::AR::TRF", labels=[NodeLabel.APPLICATION], properties={}
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


def _write_semantic_tags_yaml(path: Path, text: str = _VALID_SEMANTIC_TAGS_YAML) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_semantic_enrichment(path: Path, *, semantic_tags_config_hash: str) -> None:
    artifact = SemanticEnrichmentArtifact(
        run_id=_RUN_ID,
        source_package_hash=_HASH_A,
        semantic_tags_config_hash=semantic_tags_config_hash,
        domain_glossary_config_hash="d" * 64,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(artifact.model_dump_json(), encoding="utf-8")


def _write_invariants(
    path: Path,
    *,
    semantic_graph_hash: str,
    source_package_hash: str = _HASH_A,
    graph_validated: bool = True,
    error_count: int = 0,
) -> InvariantArtifact:
    violations = (
        [
            InvariantViolation(
                code="INVALID_RELATIONSHIP_ENDPOINT",
                severity=Severity.ERROR,
                entity_id="r::1",
                message="msg",
            )
        ]
        if error_count
        else []
    )
    artifact = InvariantArtifact(
        run_id=_RUN_ID,
        source_package_hash=source_package_hash,
        semantic_graph_hash=semantic_graph_hash,
        invariants_query_hash="c" * 64,
        neo4j_database="neo4j",
        neo4j_server_version="5.24.0",
        node_count=2,
        relationship_count=1,
        violations=violations,
        error_count=error_count,
        warning_count=0,
        graph_validated=graph_validated,
        warnings=[],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(artifact.model_dump_json(), encoding="utf-8")
    return artifact


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


def _install_detect_candidates(
    monkeypatch: pytest.MonkeyPatch, candidates: list[RuleCandidate]
) -> None:
    def _fake_detect_candidates(repository: Any, **kwargs: Any) -> list[RuleCandidate]:
        return candidates

    monkeypatch.setattr(stage_module, "detect_candidates", _fake_detect_candidates)


class _ExplodingRepositoryFactory:
    @staticmethod
    def connect(settings: Settings) -> Any:
        raise AssertionError("Neo4jRepository.connect no deberia llamarse")


def _write_q0_cypher(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("MATCH (n) RETURN n;", encoding="utf-8")


def _base_kwargs(tmp_path: Path, *, run_stages: list[StageExecution]) -> dict[str, Any]:
    graph_path = tmp_path / "04-semantic-graph.json"
    enrichment_path = tmp_path / "03b-semantic-enrichment.json"
    invariants_path = tmp_path / "05-invariants.json"
    q0_path = tmp_path / "q0_candidates.cypher"
    semantic_tags_path = tmp_path / "semantic-tags.yml"

    graph = _sample_graph()
    _write_graph(graph_path, graph)
    semantic_graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    config_hash = _write_semantic_tags_yaml(semantic_tags_path)
    _write_semantic_enrichment(enrichment_path, semantic_tags_config_hash=config_hash)
    _write_invariants(invariants_path, semantic_graph_hash=semantic_graph_hash)
    _write_q0_cypher(q0_path)

    settings = Settings(semantic_tags_path=semantic_tags_path)
    return {
        "run_id": _RUN_ID,
        "source_package_hash": _HASH_A,
        "run_stages": run_stages,
        "semantic_graph_path": graph_path,
        "semantic_enrichment_path": enrichment_path,
        "invariants_path": invariants_path,
        "q0_cypher_path": q0_path,
        "candidates_path": tmp_path / "06-candidates.json",
        "canonical_dir": tmp_path / "02-canonical",
        "settings": settings,
    }


def _happy_kwargs(tmp_path: Path) -> tuple[dict[str, Any], SemanticGraph, str]:
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    graph, semantic_graph_hash = (
        _sample_graph(),
        hashlib.sha256(kwargs["semantic_graph_path"].read_bytes()).hexdigest(),
    )
    return kwargs, graph, semantic_graph_hash


# --- precondicion ---


def test_missing_graph_validated_stage_raises_before_touching_neo4j(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=[])

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


def test_duplicate_graph_validated_stage_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    stages = [
        _stage(PipelineStage.GRAPH_VALIDATED, StageStatus.SUCCEEDED),
        _stage(PipelineStage.GRAPH_VALIDATED, StageStatus.SUCCEEDED),
    ]
    kwargs = _base_kwargs(tmp_path, run_stages=stages)

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


def test_graph_validated_not_succeeded_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    stages = [_stage(PipelineStage.GRAPH_VALIDATED, StageStatus.FAILED)]
    kwargs = _base_kwargs(tmp_path, run_stages=stages)

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


# --- artefactos releidos ---


def test_missing_semantic_graph_json_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    kwargs["semantic_graph_path"].unlink()

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


def test_missing_invariants_json_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    kwargs["invariants_path"].unlink()

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


def test_invariants_not_validated_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    graph_hash = hashlib.sha256(kwargs["semantic_graph_path"].read_bytes()).hexdigest()
    _write_invariants(
        kwargs["invariants_path"],
        semantic_graph_hash=graph_hash,
        graph_validated=False,
        error_count=1,
    )

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


def test_invariants_source_package_hash_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    graph_hash = hashlib.sha256(kwargs["semantic_graph_path"].read_bytes()).hexdigest()
    _write_invariants(
        kwargs["invariants_path"], semantic_graph_hash=graph_hash, source_package_hash="b" * 64
    )

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


def test_invariants_semantic_graph_hash_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    _write_invariants(kwargs["invariants_path"], semantic_graph_hash="c" * 64)

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


# --- configuracion semantica ---


def test_semantic_tags_config_hash_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    graph_hash = hashlib.sha256(kwargs["semantic_graph_path"].read_bytes()).hexdigest()
    # config/semantic-tags.yml "cambio" respecto de lo registrado en 03b:
    _write_semantic_enrichment(
        kwargs["semantic_enrichment_path"], semantic_tags_config_hash="0" * 64
    )
    _write_invariants(kwargs["invariants_path"], semantic_graph_hash=graph_hash)

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


def test_semantic_tags_config_without_return_code_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    graph_hash = hashlib.sha256(kwargs["semantic_graph_path"].read_bytes()).hexdigest()
    config_hash = _write_semantic_tags_yaml(
        kwargs["settings"].semantic_tags_path, _VALID_SEMANTIC_TAGS_YAML_WITHOUT_RETURN_CODE
    )
    _write_semantic_enrichment(
        kwargs["semantic_enrichment_path"], semantic_tags_config_hash=config_hash
    )
    _write_invariants(kwargs["invariants_path"], semantic_graph_hash=graph_hash)

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


# --- Q0 ausente/vacio ---


def test_q0_cypher_missing_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    kwargs["q0_cypher_path"].unlink()

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


def test_q0_cypher_empty_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    kwargs["q0_cypher_path"].write_text("", encoding="utf-8")

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


# --- drift contra AltamiraGraphLoad ---


def test_missing_active_graph_load_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stub = _StubRepository(active=None)
    _install_stub(monkeypatch, stub)
    _install_detect_candidates(monkeypatch, [])
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


def test_active_semantic_graph_hash_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    active = ActiveGraphLoad(
        semantic_graph_hash="c" * 64,
        source_package_hash=_HASH_A,
        node_count=2,
        relationship_count=1,
        server_version="5.24.0",
        database="neo4j",
    )
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_detect_candidates(monkeypatch, [])

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


def test_node_count_mismatch_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    active = ActiveGraphLoad(
        semantic_graph_hash=semantic_graph_hash,
        source_package_hash=_HASH_A,
        node_count=999,
        relationship_count=1,
        server_version="5.24.0",
        database="neo4j",
    )
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_detect_candidates(monkeypatch, [])

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


def test_drift_not_clean_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    dirty_drift = GraphDrift(
        missing_ids=["country::AR"], extra_ids=[], missing_edge_keys=[], extra_edge_keys=[]
    )
    stub = _StubRepository(active=active, drift=dirty_drift)
    _install_stub(monkeypatch, stub)
    _install_detect_candidates(monkeypatch, [])

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)


# --- traduccion de errores Neo4j ---


def test_connect_error_is_translated_and_never_calls_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, _graph, _hash = _happy_kwargs(tmp_path)
    stub = _StubRepository(connect_error=Neo4jConfigurationError("uri invalida"))
    _install_stub(monkeypatch, stub)
    _install_detect_candidates(monkeypatch, [])

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)

    assert "close" not in stub.calls


def test_connectivity_error_is_translated_and_still_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, _graph, _hash = _happy_kwargs(tmp_path)
    stub = _StubRepository(connectivity_error=Neo4jAuthenticationError("credenciales invalidas"))
    _install_stub(monkeypatch, stub)
    _install_detect_candidates(monkeypatch, [])

    with pytest.raises(CandidateDetectionError):
        run_candidates_detected_stage(**kwargs)

    assert stub.calls == ["connect", "verify_connectivity", "close"]


# --- flujo feliz, persistencia, idempotencia ---


def test_happy_path_persists_artifact_and_reports_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    candidate = RuleCandidate(
        candidate_id="candidate::q0-return-code-decision::1.0::" + _HASH_A + "::dec-1",
        paragraph_id="program::AR::op::PROG::1::abc::paragraph::MAIN",
        paragraph_name="MAIN",
        decision_id="dec-1",
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        condition="WS-COD = 'R001'",
        outcome_code="R001",
        rule_type=None,
        line_start=10,
        source_file="01-codigo/cobol/PROG.cbl",
        source_package_hash=_HASH_A,
    )
    _install_detect_candidates(monkeypatch, [candidate])

    warnings = run_candidates_detected_stage(**kwargs)

    assert warnings == ["detectados 1 candidato(s)"]
    assert stub.calls == [
        "connect",
        "verify_connectivity",
        "read_active_graph_load",
        "compute_drift",
        "close",
    ]
    assert stub.closed

    artifact = CandidateArtifact.model_validate_json(
        kwargs["candidates_path"].read_text(encoding="utf-8")
    )
    assert artifact.run_id == _RUN_ID
    assert artifact.source_package_hash == _HASH_A
    assert artifact.semantic_graph_hash == semantic_graph_hash
    assert (
        artifact.q0_query_hash == hashlib.sha256(kwargs["q0_cypher_path"].read_bytes()).hexdigest()
    )
    assert artifact.candidates == [candidate]


def test_no_candidates_is_a_valid_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_detect_candidates(monkeypatch, [])

    warnings = run_candidates_detected_stage(**kwargs)

    assert warnings == ["detectados 0 candidato(s)"]
    artifact = CandidateArtifact.model_validate_json(
        kwargs["candidates_path"].read_text(encoding="utf-8")
    )
    assert artifact.candidates == []


def test_second_run_with_identical_result_does_not_rewrite_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_detect_candidates(monkeypatch, [])

    run_candidates_detected_stage(**kwargs)
    first_bytes = kwargs["candidates_path"].read_bytes()

    second_warnings = run_candidates_detected_stage(**kwargs)

    assert second_warnings == ["detectados 0 candidato(s) (sin cambios)"]
    assert kwargs["candidates_path"].read_bytes() == first_bytes


# --- CandidateDetector no repara/recarga/revalida ---


def test_never_invokes_graph_validated_stage_or_invariants(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_detect_candidates(monkeypatch, [])

    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no deberia invocarse desde CANDIDATES_DETECTED")

    import altamira_extractor.pipeline.graph_invariant_validator as invariant_validator_module
    import altamira_extractor.pipeline.graph_validated_stage as graph_validated_module

    monkeypatch.setattr(graph_validated_module, "run_graph_validated_stage", _explode)
    monkeypatch.setattr(invariant_validator_module, "run_invariants", _explode)

    run_candidates_detected_stage(**kwargs)  # no debe lanzar


def test_does_not_create_context_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_detect_candidates(monkeypatch, [])

    run_candidates_detected_stage(**kwargs)

    assert not (tmp_path / "07-context").exists()


# --- Fase 15B3-B: enhanced_candidates_enabled (opt-in, orquestacion) ---


def _v1_candidate(decision_id: str = "dec-1") -> RuleCandidate:
    return RuleCandidate(
        candidate_id="candidate::q0-return-code-decision::1.0::" + _HASH_A + f"::{decision_id}",
        paragraph_id="program::AR::op::PROG::1::abc::paragraph::MAIN",
        paragraph_name="MAIN",
        decision_id=decision_id,
        detector_id="q0-return-code-decision",
        detector_version="1.0",
        detector_score=1.0,
        condition="WS-COD = 'R001'",
        outcome_code="R001",
        rule_type=None,
        line_start=10,
        source_file="01-codigo/cobol/PROG.cbl",
        source_package_hash=_HASH_A,
    )


def _enhanced_candidate(decision_id: str = "dec-2") -> RuleCandidate:
    from altamira_extractor.contracts.candidate_promotion_assessment import (
        CandidateSource,
        UnifiedRuleFamily,
    )

    return RuleCandidate(
        candidate_id="candidate::v2::V2_LEVEL_88_RETURN_CODE::deadbeef::" + _HASH_A,
        paragraph_id="program::AR::op::PROG::1::abc::paragraph::MAIN",
        paragraph_name="MAIN",
        decision_id=decision_id,
        detector_id="V2_LEVEL_88_RETURN_CODE",
        detector_version="1.0",
        detector_score=1.0,
        condition="ERROR-DE-ENTRADA",
        outcome_code="0005",
        rule_type=None,
        line_start=20,
        source_file="01-codigo/cobol/PROG.cbl",
        source_package_hash=_HASH_A,
        candidate_source=CandidateSource.V2,
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        evidence_ids=["effect::1"],
    )


def _install_detect_enhanced_candidates(
    monkeypatch: pytest.MonkeyPatch, *, candidates: list[RuleCandidate], warnings: list[str]
) -> list[Any]:
    calls: list[Any] = []

    def _fake(**kwargs: Any) -> tuple[list[RuleCandidate], list[str]]:
        calls.append(kwargs)
        return candidates, warnings

    monkeypatch.setattr(stage_module, "detect_enhanced_candidates", _fake)
    monkeypatch.setattr(
        stage_module, "_load_canonical_programs_for_enhanced_detection", lambda canonical_dir: []
    )
    return calls


def test_enhanced_disabled_by_default_never_calls_enhanced_detection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_detect_candidates(monkeypatch, [_v1_candidate()])

    def _explode(**kwargs: Any) -> Any:
        raise AssertionError("detect_enhanced_candidates no deberia invocarse (flag=false)")

    monkeypatch.setattr(stage_module, "detect_enhanced_candidates", _explode)

    warnings = run_candidates_detected_stage(**kwargs)

    assert warnings == ["detectados 1 candidato(s)"]
    artifact = CandidateArtifact.model_validate_json(
        kwargs["candidates_path"].read_text(encoding="utf-8")
    )
    assert artifact.candidates == [_v1_candidate()]
    assert artifact.warnings == []


def test_enhanced_enabled_adds_new_candidates_and_preserves_v1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    kwargs["settings"] = Settings(
        semantic_tags_path=kwargs["settings"].semantic_tags_path, enhanced_candidates_enabled=True
    )
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    v1 = _v1_candidate()
    _install_detect_candidates(monkeypatch, [v1])
    enhanced = _enhanced_candidate()
    calls = _install_detect_enhanced_candidates(
        monkeypatch, candidates=[enhanced], warnings=["nota de deteccion ampliada"]
    )

    warnings = run_candidates_detected_stage(**kwargs)

    assert calls[0]["source_package_hash"] == _HASH_A
    assert calls[0]["v1_candidates"].candidates == [v1]
    assert warnings == ["detectados 2 candidato(s)"]

    artifact = CandidateArtifact.model_validate_json(
        kwargs["candidates_path"].read_text(encoding="utf-8")
    )
    assert artifact.candidates == sorted([v1, enhanced], key=lambda c: c.candidate_id)
    assert v1 in artifact.candidates
    assert artifact.warnings == ["nota de deteccion ampliada"]


def test_enhanced_enabled_with_no_new_candidates_still_reports_dedup_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    kwargs["settings"] = Settings(
        semantic_tags_path=kwargs["settings"].semantic_tags_path, enhanced_candidates_enabled=True
    )
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    v1 = _v1_candidate()
    _install_detect_candidates(monkeypatch, [v1])
    _install_detect_enhanced_candidates(
        monkeypatch, candidates=[], warnings=["candidato V2 deduplicado contra V1"]
    )

    run_candidates_detected_stage(**kwargs)

    artifact = CandidateArtifact.model_validate_json(
        kwargs["candidates_path"].read_text(encoding="utf-8")
    )
    assert artifact.candidates == [v1]
    assert artifact.warnings == ["candidato V2 deduplicado contra V1"]


def test_v1_candidate_without_source_file_is_preserved_with_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fase 15B4-CANDIDATE-QUALITY-5A (P0-COPY-CANDIDATE-CRASH): un
    RuleCandidate V1 con `source_file=None` (Paragraph con
    location_kind != EXACT, hoy COPY) ya no hace fallar
    CANDIDATES_DETECTED (antes: ValidationError sin capturar,
    propagada como CandidateDetectionError) -- se persiste igual, con
    un warning trazable en el artefacto, incluso con
    `enhanced_candidates_enabled=False` (default)."""
    kwargs, graph, semantic_graph_hash = _happy_kwargs(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    v1 = _v1_candidate().model_copy(update={"source_file": None})
    _install_detect_candidates(monkeypatch, [v1])

    run_candidates_detected_stage(**kwargs)

    artifact = CandidateArtifact.model_validate_json(
        kwargs["candidates_path"].read_text(encoding="utf-8")
    )
    assert artifact.candidates == [v1]
    assert artifact.candidates[0].source_file is None
    assert len(artifact.warnings) == 1
    assert "sin source_file disponible" in artifact.warnings[0]
    assert v1.candidate_id in artifact.warnings[0]
