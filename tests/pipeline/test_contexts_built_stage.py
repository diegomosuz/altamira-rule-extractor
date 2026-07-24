"""Tests de la orquestacion CONTEXTS_BUILT (sin Neo4j real).

`Neo4jRepository` y `build_context_packages` se sustituyen por dobles de
prueba: estos tests verifican la orquestacion (precondicion, deteccion
de drift, manifest, reemplazo de directorio, idempotencia, traduccion de
errores), no el comportamiento real de Neo4j ni de Q1-Q7 (ver
test_context_package_builder.py y tests/neo4j_integration/).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.context_manifest import ContextDirectoryManifest
from altamira_extractor.contracts.context_package import (
    BatchContext,
    CodeSliceEntry,
    Completeness,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageDecision,
    ContextPackageOperation,
    ContextPackageScope,
    DataContext,
    Effects,
    EvidenceEntry,
)
from altamira_extractor.contracts.enums import (
    BatchContextStatus,
    CandidateStatus,
    CompletenessStatus,
    InclusionReason,
    NodeLabel,
    PipelineStage,
    RelationshipType,
    StageStatus,
)
from altamira_extractor.contracts.run_state import StageExecution
from altamira_extractor.contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from altamira_extractor.pipeline import contexts_built_stage as stage_module
from altamira_extractor.pipeline.contexts_built_stage import run_contexts_built_stage
from altamira_extractor.pipeline.errors import (
    ContextBuildError,
    Neo4jAuthenticationError,
    Neo4jConfigurationError,
)
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


def _succeeded_stages() -> list[StageExecution]:
    return [_stage(PipelineStage.CANDIDATES_DETECTED, StageStatus.SUCCEEDED)]


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


def _sample_candidate() -> RuleCandidate:
    return RuleCandidate(
        candidate_id=f"candidate::det::1.0::{_HASH_A}::dec-1",
        paragraph_id="para-1",
        paragraph_name="MAIN",
        decision_id="dec-1",
        detector_id="det",
        detector_version="1.0",
        detector_score=1.0,
        condition="A",
        outcome_code="R001",
        rule_type=None,
        line_start=10,
        source_file="01-codigo/cobol/PROG.cbl",
        source_package_hash=_HASH_A,
    )


def _write_candidates(
    path: Path, *, candidates: list[RuleCandidate], semantic_graph_hash: str
) -> CandidateArtifact:
    artifact = CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash=_HASH_A,
        semantic_graph_hash=semantic_graph_hash,
        invariants_query_hash="c" * 64,
        q0_query_hash="d" * 64,
        candidates=candidates,
        warnings=[],
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(artifact.model_dump_json(), encoding="utf-8")
    return artifact


def _sample_context_package(candidate: RuleCandidate) -> ContextPackage:
    evidence = EvidenceEntry(
        evidence_id="evidence::" + "0" * 16,
        kind="code_slice",
        source_file=candidate.source_file,
        line_start=candidate.line_start,
        line_end=candidate.line_start,
        source_package_hash=candidate.source_package_hash,
    )
    return ContextPackage(
        schema_version="2.0",
        candidate=ContextPackageCandidate(
            candidate_id=candidate.candidate_id,
            decision_id=candidate.decision_id,
            detector_id=candidate.detector_id,
            detector_version=candidate.detector_version,
            detector_score=candidate.detector_score,
        ),
        scope=ContextPackageScope(
            country="AR",
            application="Transferencias",
            operation=ContextPackageOperation(logical_name="OP1", description=None),
            program="PROG1",
            program_version="1.0",
            paragraph="MAIN",
            source_file=candidate.source_file,
            line_start=candidate.line_start,
            line_end=candidate.line_start,
            source_package_hash=candidate.source_package_hash,
        ),
        code_slice=[
            CodeSliceEntry(
                paragraph_id=candidate.paragraph_id,
                paragraph="MAIN",
                source_file=candidate.source_file,
                source_text="IF WS-COD = 'R001'",
                line_start=candidate.line_start,
                line_end=candidate.line_start,
                inclusion_reason=InclusionReason.CANDIDATE,
                evidence_ids=[evidence.evidence_id],
            )
        ],
        data_context=DataContext(parameter_tables=[], transactional_tables_read=[]),
        decision=ContextPackageDecision(
            expression=candidate.condition,
            normalized_expression=candidate.condition,
            operands=[],
            rule_type=None,
            outcome_code=candidate.outcome_code,
            evidence_ids=[evidence.evidence_id],
        ),
        effects=Effects(return_codes=[], table_effects=[]),
        batch_context=BatchContext(status=BatchContextStatus.NOT_AVAILABLE, downstream_jobs=[]),
        domain_glossary=[],
        evidence=[evidence],
        completeness=Completeness(
            D1=CompletenessStatus.COMPLETE,
            D2=CompletenessStatus.COMPLETE,
            D3=CompletenessStatus.NOT_AVAILABLE,
            D4=CompletenessStatus.COMPLETE,
            D5=CompletenessStatus.NOT_AVAILABLE,
            D6=CompletenessStatus.NOT_AVAILABLE,
            D7=CompletenessStatus.NOT_AVAILABLE,
        ),
    )


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
        active_after: ActiveGraphLoad | None = None,
        drift: GraphDrift = _CLEAN_DRIFT,
    ) -> None:
        self.connect_error = connect_error
        self.connectivity_error = connectivity_error
        self.active = active
        self.active_after = active_after if active_after is not None else active
        self.drift = drift
        self.calls: list[str] = []
        self.closed = False
        self._read_count = 0

    def verify_connectivity(self) -> None:
        self.calls.append("verify_connectivity")
        if self.connectivity_error is not None:
            raise self.connectivity_error

    def read_active_graph_load(self) -> ActiveGraphLoad | None:
        self.calls.append("read_active_graph_load")
        self._read_count += 1
        return self.active if self._read_count == 1 else self.active_after

    def compute_drift(self, graph: SemanticGraph) -> GraphDrift:
        self.calls.append("compute_drift")
        return self.drift

    def run_in_read_transaction(self, work: Any) -> Any:
        self.calls.append("run_in_read_transaction")
        return work(None)

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


def _install_build_context_packages(
    monkeypatch: pytest.MonkeyPatch, packages_by_candidate: dict[str, ContextPackage]
) -> None:
    def _fake(tx: Any, candidates: list[RuleCandidate], **kwargs: Any) -> list[ContextPackage]:
        return [packages_by_candidate[c.candidate_id] for c in candidates]

    monkeypatch.setattr(stage_module, "build_context_packages", _fake)


class _ExplodingRepositoryFactory:
    @staticmethod
    def connect(settings: Settings) -> Any:
        raise AssertionError("Neo4jRepository.connect no deberia llamarse")


def _base_kwargs(tmp_path: Path, *, run_stages: list[StageExecution]) -> dict[str, Any]:
    graph_path = tmp_path / "04-semantic-graph.json"
    candidates_path = tmp_path / "06-candidates.json"
    graph = _sample_graph()
    _write_graph(graph_path, graph)
    semantic_graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    _write_candidates(candidates_path, candidates=[], semantic_graph_hash=semantic_graph_hash)
    return {
        "run_id": _RUN_ID,
        "source_package_hash": _HASH_A,
        "run_stages": run_stages,
        "semantic_graph_path": graph_path,
        "candidates_path": candidates_path,
        "context_dir": tmp_path / "07-context",
        "settings": Settings(),
    }


def _happy_kwargs_with_one_candidate(
    tmp_path: Path,
) -> tuple[dict[str, Any], SemanticGraph, str, RuleCandidate]:
    graph_path = tmp_path / "04-semantic-graph.json"
    candidates_path = tmp_path / "06-candidates.json"
    graph = _sample_graph()
    _write_graph(graph_path, graph)
    semantic_graph_hash = hashlib.sha256(graph_path.read_bytes()).hexdigest()
    candidate = _sample_candidate()
    _write_candidates(
        candidates_path, candidates=[candidate], semantic_graph_hash=semantic_graph_hash
    )
    kwargs = {
        "run_id": _RUN_ID,
        "source_package_hash": _HASH_A,
        "run_stages": _succeeded_stages(),
        "semantic_graph_path": graph_path,
        "candidates_path": candidates_path,
        "context_dir": tmp_path / "07-context",
        "settings": Settings(),
    }
    return kwargs, graph, semantic_graph_hash, candidate


# --- precondicion ---


def test_missing_candidates_detected_stage_raises_before_touching_neo4j(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=[])

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)


def test_duplicate_candidates_detected_stage_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    stages = [
        _stage(PipelineStage.CANDIDATES_DETECTED, StageStatus.SUCCEEDED),
        _stage(PipelineStage.CANDIDATES_DETECTED, StageStatus.SUCCEEDED),
    ]
    kwargs = _base_kwargs(tmp_path, run_stages=stages)

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)


def test_candidates_detected_not_succeeded_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    stages = [_stage(PipelineStage.CANDIDATES_DETECTED, StageStatus.FAILED)]
    kwargs = _base_kwargs(tmp_path, run_stages=stages)

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)


# --- artefactos releidos ---


def test_missing_semantic_graph_json_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    kwargs["semantic_graph_path"].unlink()

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)


def test_missing_candidates_json_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    kwargs["candidates_path"].unlink()

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)


def test_candidates_source_package_hash_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    graph_hash = hashlib.sha256(kwargs["semantic_graph_path"].read_bytes()).hexdigest()
    artifact = CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash="b" * 64,
        semantic_graph_hash=graph_hash,
        invariants_query_hash="c" * 64,
        q0_query_hash="d" * 64,
        candidates=[],
        warnings=[],
    )
    kwargs["candidates_path"].write_text(artifact.model_dump_json(), encoding="utf-8")

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)


def test_candidates_semantic_graph_hash_mismatch_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "Neo4jRepository", _ExplodingRepositoryFactory)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    _write_candidates(kwargs["candidates_path"], candidates=[], semantic_graph_hash="c" * 64)

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)


# --- candidatos vacios ---


def test_no_candidates_produces_empty_valid_context_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())
    graph = _sample_graph()
    semantic_graph_hash = hashlib.sha256(kwargs["semantic_graph_path"].read_bytes()).hexdigest()
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)

    warnings = run_contexts_built_stage(**kwargs)

    assert "run_in_read_transaction" not in stub.calls
    manifest = ContextDirectoryManifest.model_validate_json(
        (kwargs["context_dir"] / "context-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.context_count == 0
    assert manifest.context_records == []
    assert len(warnings) == 1


# --- drift contra AltamiraGraphLoad ---


def test_missing_active_graph_load_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stub = _StubRepository(active=None)
    _install_stub(monkeypatch, stub)
    kwargs = _base_kwargs(tmp_path, run_stages=_succeeded_stages())

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)


def test_drift_not_clean_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    kwargs, graph, semantic_graph_hash, _candidate = _happy_kwargs_with_one_candidate(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    dirty_drift = GraphDrift(
        missing_ids=["country::AR"], extra_ids=[], missing_edge_keys=[], extra_edge_keys=[]
    )
    stub = _StubRepository(active=active, drift=dirty_drift)
    _install_stub(monkeypatch, stub)
    _install_build_context_packages(monkeypatch, {})

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)


def test_metadata_changed_during_transaction_discards_contexts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash, candidate = _happy_kwargs_with_one_candidate(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    changed = _matching_active_graph_load(graph, "c" * 64)
    stub = _StubRepository(active=active, active_after=changed)
    _install_stub(monkeypatch, stub)
    _install_build_context_packages(
        monkeypatch, {candidate.candidate_id: _sample_context_package(candidate)}
    )

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)

    assert not kwargs["context_dir"].exists()


# --- traduccion de errores Neo4j ---


def test_connect_error_is_translated_and_never_calls_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, _graph, _hash, _candidate = _happy_kwargs_with_one_candidate(tmp_path)
    stub = _StubRepository(connect_error=Neo4jConfigurationError("uri invalida"))
    _install_stub(monkeypatch, stub)

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)

    assert "close" not in stub.calls


def test_connectivity_error_is_translated_and_still_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, _graph, _hash, _candidate = _happy_kwargs_with_one_candidate(tmp_path)
    stub = _StubRepository(connectivity_error=Neo4jAuthenticationError("credenciales invalidas"))
    _install_stub(monkeypatch, stub)

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)

    assert stub.calls == ["connect", "verify_connectivity", "close"]


# --- flujo feliz, manifest, idempotencia ---


def test_happy_path_persists_context_directory_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash, candidate = _happy_kwargs_with_one_candidate(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_build_context_packages(
        monkeypatch, {candidate.candidate_id: _sample_context_package(candidate)}
    )

    warnings = run_contexts_built_stage(**kwargs)

    assert warnings == ["1 contexto(s)"]
    context_dir = kwargs["context_dir"]
    manifest_path = context_dir / "context-manifest.json"
    manifest = ContextDirectoryManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    assert manifest.context_count == 1
    assert len(manifest.query_records) == 9
    record = manifest.context_records[0]
    assert record.candidate_id == candidate.candidate_id

    expected_filename = hashlib.sha256(candidate.candidate_id.encode("utf-8")).hexdigest() + ".json"
    assert record.relative_filename == expected_filename
    assert ":" not in expected_filename
    assert (context_dir / expected_filename).is_file()


def test_second_run_with_identical_result_does_not_rewrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash, candidate = _happy_kwargs_with_one_candidate(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)
    _install_build_context_packages(
        monkeypatch, {candidate.candidate_id: _sample_context_package(candidate)}
    )

    run_contexts_built_stage(**kwargs)
    manifest_path = kwargs["context_dir"] / "context-manifest.json"
    first_bytes = manifest_path.read_bytes()

    second_warnings = run_contexts_built_stage(**kwargs)

    assert second_warnings == ["1 contexto(s) (sin cambios)"]
    assert manifest_path.read_bytes() == first_bytes


def test_schema_invalid_package_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs, graph, semantic_graph_hash, candidate = _happy_kwargs_with_one_candidate(tmp_path)
    active = _matching_active_graph_load(graph, semantic_graph_hash)
    stub = _StubRepository(active=active)
    _install_stub(monkeypatch, stub)

    bad_package = _sample_context_package(candidate).model_copy(
        update={
            "candidate": ContextPackageCandidate.model_construct(
                candidate_id=candidate.candidate_id,
                decision_id=candidate.decision_id,
                detector_id=candidate.detector_id,
                detector_version=candidate.detector_version,
                detector_score=2.0,  # fuera de [0,1]: Pydantic lo permite via model_construct
                status=CandidateStatus.DETECTED_CANDIDATE,
            )
        }
    )
    _install_build_context_packages(monkeypatch, {candidate.candidate_id: bad_package})

    with pytest.raises(ContextBuildError):
        run_contexts_built_stage(**kwargs)
