"""Tests del servicio de filesystem de detectores V2 en shadow mode
(Fase 5 de la ampliacion semantica, `feat/v2-detectors-shadow-mode`):
`pipeline/v2_shadow_candidates_service.py`. Sin Neo4j, sin LLM, sin
Docker -- solo filesystem local (`tmp_path`)."""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.canonical import (
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    LocationKind,
    NodeLabel,
    PipelineStage,
    RelationshipType,
    SourceFormat,
    StageStatus,
    StatementKind,
)
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.contracts.semantic_graph import GraphNode, GraphRelationship, SemanticGraph
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.errors import V2ShadowCandidatesError
from altamira_extractor.pipeline.v2_shadow_candidates_service import (
    compute_v2_shadow_candidates_artifact,
    v2_shadow_candidates_artifact_path,
    write_v2_shadow_candidates_artifact,
)

_HASH = "e" * 64
_RUN_ID = "20260101T000000000000-dddddddd"
_PROGRAM_NODE_ID = "program::AR::APP::PROG1::1.0::" + _HASH[:12]
_PARAGRAPH_NODE_ID = f"{_PROGRAM_NODE_ID}::paragraph::A"
_DECISION_NODE_ID = f"{_PARAGRAPH_NODE_ID}::decision::10::1"
_DATA_ITEM_NODE_ID = f"{_PROGRAM_NODE_ID}::data::WS-COD-RETORNO"

_REQUIRED_STAGES = (
    PipelineStage.PARSED,
    PipelineStage.SEMANTIC_GRAPH_BUILT,
    PipelineStage.CANDIDATES_DETECTED,
)


def _write_run_state(run_dir: Path, *, stages: tuple[PipelineStage, ...]) -> RunState:
    now = datetime.now(UTC)
    executions = [
        StageExecution(
            stage=stage, status=StageStatus.SUCCEEDED, started_at=now, finished_at=now,
            duration_seconds=0.0,
        )
        for stage in stages
    ]
    state = RunState(
        run_id=_RUN_ID, package_filename="input/package.zip", source_package_hash=_HASH,
        current_stage=stages[-1] if stages else PipelineStage.RECEIVED,
        stages=executions, created_at=now, updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)
    return state


def _write_canonical(run_dir: Path) -> None:
    if_stmt = CanonicalStatement(
        statement_id="P1::A::0::IF", kind=StatementKind.IF, source_text="IF CONDICION",
        location_kind=LocationKind.EXACT, source_file="a.cbl", line_start=10, line_end=10,
        expression="CONDICION",
    )
    move = CanonicalStatement(
        statement_id="P1::A::1::MOVE", kind=StatementKind.MOVE, source_text="MOVE '0005' TO X",
        location_kind=LocationKind.EXACT, source_file="a.cbl", line_start=11, line_end=11,
        target_data_items=["WS-COD-RETORNO"], variables_written=["WS-COD-RETORNO"],
        assigned_literal="0005", parent_statement_id="P1::A::0::IF", branch_kind="THEN",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN,
        statements=[if_stmt, move], variables_written=["WS-COD-RETORNO"],
    )
    program = CanonicalProgram(
        program_name="PROG1", source_file="a.cbl", source_hash=_HASH, source_package_hash=_HASH,
        source_format=SourceFormat.FIXED, encoding="UTF-8", paragraphs=[paragraph],
    )
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_dir.mkdir(parents=True)
    atomic_write_json(canonical_dir / "a.cbl.json", program)


def _write_semantic_graph(run_dir: Path) -> None:
    graph = SemanticGraph(
        source_package_hash=_HASH,
        nodes=sorted(
            [
                GraphNode(
                    id=_PROGRAM_NODE_ID, labels=[NodeLabel.PROGRAM], properties={"name": "PROG1"}
                ),
                GraphNode(
                    id=_PARAGRAPH_NODE_ID, labels=[NodeLabel.PARAGRAPH],
                    properties={"name": "A", "line_start": 1, "line_end": 20},
                ),
                GraphNode(
                    id=_DECISION_NODE_ID, labels=[NodeLabel.DECISION],
                    properties={
                        "line_start": 10, "line_end": 10, "expression": "CONDICION",
                        "outcome_code": None, "rule_type": None,
                    },
                ),
                GraphNode(
                    id=_DATA_ITEM_NODE_ID, labels=[NodeLabel.DATA_ITEM],
                    properties={"qualified_name": "WS-COD-RETORNO", "semantic_tag": "return_code"},
                ),
            ],
            key=lambda node: node.id,
        ),
        relationships=sorted(
            [
                GraphRelationship(
                    type=RelationshipType.CONTAINS, from_id=_PROGRAM_NODE_ID,
                    to_id=_PARAGRAPH_NODE_ID,
                ),
                GraphRelationship(
                    type=RelationshipType.HAS_DECISION, from_id=_PARAGRAPH_NODE_ID,
                    to_id=_DECISION_NODE_ID,
                ),
            ],
            key=lambda rel: (rel.type.value, rel.from_id, rel.to_id),
        ),
    )
    atomic_write_json(run_dir / "artifacts" / "04-semantic-graph.json", graph)


def _write_v1_candidates(run_dir: Path) -> None:
    artifact = CandidateArtifact(
        run_id=_RUN_ID, source_package_hash=_HASH, semantic_graph_hash=_HASH,
        invariants_query_hash=_HASH, q0_query_hash=_HASH, candidates=[],
    )
    atomic_write_json(run_dir / "artifacts" / "06-candidates.json", artifact)


def _write_full_valid_run(run_dir: Path) -> RunState:
    state = _write_run_state(run_dir, stages=_REQUIRED_STAGES)
    _write_canonical(run_dir)
    _write_semantic_graph(run_dir)
    _write_v1_candidates(run_dir)
    return state


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root).as_posix()): _sha256_bytes(path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# Camino feliz / determinismo / atomicidad
# ---------------------------------------------------------------------------


def test_creates_diagnostics_v2_candidates_shadow_json(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)

    artifact = compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)
    artifact_path = write_v2_shadow_candidates_artifact(run_dir, artifact)

    assert artifact_path == run_dir / "diagnostics" / "v2-candidates-shadow.json"
    assert artifact_path.is_file()
    assert artifact.run_id == _RUN_ID
    assert artifact.summary.v2_candidate_count >= 1


def test_second_execution_is_byte_for_byte_identical(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)

    artifact1 = compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)
    path1 = write_v2_shadow_candidates_artifact(run_dir, artifact1)
    bytes1 = path1.read_bytes()

    artifact2 = compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)
    path2 = write_v2_shadow_candidates_artifact(run_dir, artifact2)
    bytes2 = path2.read_bytes()

    assert bytes1 == bytes2


def test_no_leftover_temp_files_after_write(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    artifact = compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)
    write_v2_shadow_candidates_artifact(run_dir, artifact)

    diagnostics_dir = run_dir / "diagnostics"
    assert not any(diagnostics_dir.glob("*.tmp"))


# ---------------------------------------------------------------------------
# Nunca lee ni escribe diagnostics/semantic-effects.json ni
# diagnostics/semantic-propagation.json (calculados en memoria)
# ---------------------------------------------------------------------------


def test_never_writes_semantic_effects_or_propagation_json(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    artifact = compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)
    write_v2_shadow_candidates_artifact(run_dir, artifact)
    assert not (run_dir / "diagnostics" / "semantic-effects.json").exists()
    assert not (run_dir / "diagnostics" / "semantic-propagation.json").exists()
    assert not (run_dir / "diagnostics" / "semantic-coverage.json").exists()


def test_preexisting_diagnostics_are_never_modified(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    preexisting = run_dir / "diagnostics" / "semantic-effects.json"
    preexisting.parent.mkdir(parents=True, exist_ok=True)
    preexisting.write_text('{"marker": "untouched"}', encoding="utf-8")
    before = preexisting.read_bytes()

    artifact = compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)
    write_v2_shadow_candidates_artifact(run_dir, artifact)

    assert preexisting.read_bytes() == before


# ---------------------------------------------------------------------------
# Errores claros y sanitizados
# ---------------------------------------------------------------------------


def test_nonexistent_run_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / "does-not-exist"
    with pytest.raises(V2ShadowCandidatesError, match="no encontrado"):
        compute_v2_shadow_candidates_artifact(run_dir, "does-not-exist")


def test_run_before_required_stages_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_run_state(run_dir, stages=(PipelineStage.PARSED,))
    with pytest.raises(V2ShadowCandidatesError):
        compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)


def test_missing_semantic_graph_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "04-semantic-graph.json").unlink()
    with pytest.raises(V2ShadowCandidatesError, match="04-semantic-graph"):
        compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)


def test_missing_v1_candidates_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "06-candidates.json").unlink()
    with pytest.raises(V2ShadowCandidatesError, match="06-candidates"):
        compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)


def test_missing_canonical_directory_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    shutil.rmtree(run_dir / "artifacts" / "02-canonical")
    with pytest.raises(V2ShadowCandidatesError, match="02-canonical"):
        compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)


def test_corrupt_semantic_graph_json_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "04-semantic-graph.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    with pytest.raises(V2ShadowCandidatesError, match="invalido"):
        compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)


def test_invalid_run_json_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(V2ShadowCandidatesError, match="invalido"):
        compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)


def test_error_messages_never_contain_absolute_paths(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    shutil.rmtree(run_dir / "artifacts" / "02-canonical")
    with pytest.raises(V2ShadowCandidatesError) as excinfo:
        compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)
    assert str(tmp_path) not in str(excinfo.value)


def test_no_partial_artifact_created_on_failure(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    shutil.rmtree(run_dir / "artifacts" / "02-canonical")
    with pytest.raises(V2ShadowCandidatesError):
        compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)
    assert not (run_dir / "diagnostics").exists()


# ---------------------------------------------------------------------------
# Nunca modifica artefactos de entrada / no regresion
# ---------------------------------------------------------------------------


def test_input_artifacts_are_never_modified(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    state_before = _write_full_valid_run(run_dir)

    before_hashes = _tree_hashes(run_dir / "artifacts")
    run_json_bytes_before = (run_dir / "run.json").read_bytes()

    artifact = compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)
    write_v2_shadow_candidates_artifact(run_dir, artifact)

    after_hashes = _tree_hashes(run_dir / "artifacts")
    run_json_bytes_after = (run_dir / "run.json").read_bytes()

    assert before_hashes == after_hashes
    assert run_json_bytes_before == run_json_bytes_after

    state_after = RunState.model_validate_json(run_json_bytes_after.decode("utf-8"))
    assert state_after.current_stage == state_before.current_stage
    assert state_after.updated_at == state_before.updated_at


def test_only_diagnostics_directory_is_created(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    entries_before = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()}

    artifact = compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)
    write_v2_shadow_candidates_artifact(run_dir, artifact)

    entries_after = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()}
    new_entries = entries_after - entries_before
    assert new_entries == {"diagnostics/v2-candidates-shadow.json"}


def test_artifact_path_helper_matches_actual_write_location(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    artifact = compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)
    written_path = write_v2_shadow_candidates_artifact(run_dir, artifact)
    assert written_path == v2_shadow_candidates_artifact_path(run_dir)


def test_run_after_required_stages_still_works(tmp_path: Path) -> None:
    """Un run que avanzo mas alla de CANDIDATES_DETECTED tambien debe
    poder calcular shadow mode: la unica precondicion es que PARSED/
    SEMANTIC_GRAPH_BUILT/CANDIDATES_DETECTED hayan SUCCEEDED, no que sea
    la etapa actual."""
    run_dir = tmp_path / _RUN_ID
    _write_run_state(
        run_dir,
        stages=(*_REQUIRED_STAGES, PipelineStage.CONTEXTS_BUILT),
    )
    _write_canonical(run_dir)
    _write_semantic_graph(run_dir)
    _write_v1_candidates(run_dir)

    artifact = compute_v2_shadow_candidates_artifact(run_dir, _RUN_ID)
    assert artifact.run_id == _RUN_ID
