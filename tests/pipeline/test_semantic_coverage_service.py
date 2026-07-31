"""Tests del servicio de filesystem de cobertura semantica (Fase 1 de la
ampliacion semantica): `pipeline/semantic_coverage_service.py`. Sin
Neo4j, sin LLM, sin Docker -- solo filesystem local (`tmp_path`)."""

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
from altamira_extractor.contracts.dependencies import DependencyArtifact
from altamira_extractor.contracts.enums import (
    LocationKind,
    PipelineStage,
    SourceFormat,
    StageStatus,
    StatementKind,
)
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.contracts.semantic_graph import SemanticGraph
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.errors import SemanticCoverageError
from altamira_extractor.pipeline.semantic_coverage_service import (
    compute_semantic_coverage_report,
    semantic_coverage_report_path,
    write_semantic_coverage_report,
)

_HASH = "f" * 64
_RUN_ID = "20260101T000000000000-aaaaaaaa"


def _write_run_state(
    run_dir: Path, *, stage: PipelineStage = PipelineStage.CANDIDATES_DETECTED
) -> RunState:
    now = datetime.now(UTC)
    stages = [
        StageExecution(
            stage=PipelineStage.RECEIVED,
            status=StageStatus.SUCCEEDED,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
        )
    ]
    if stage == PipelineStage.CANDIDATES_DETECTED:
        stages.append(
            StageExecution(
                stage=PipelineStage.CANDIDATES_DETECTED,
                status=StageStatus.SUCCEEDED,
                started_at=now,
                finished_at=now,
                duration_seconds=0.0,
            )
        )
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH,
        current_stage=stage,
        stages=stages,
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)
    return state


def _write_full_valid_run(run_dir: Path) -> RunState:
    state = _write_run_state(run_dir)

    stmt = CanonicalStatement(
        statement_id="P1::A::1::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE 'X' TO W",
        location_kind=LocationKind.UNKNOWN,
        target_data_items=["W"],
        assigned_literal="X",
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN, statements=[stmt]
    )
    program = CanonicalProgram(
        program_name="PROG1",
        source_file="a.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[paragraph],
    )
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_dir.mkdir(parents=True)
    atomic_write_json(canonical_dir / "a.cbl.json", program)

    atomic_write_json(
        run_dir / "artifacts" / "03-dependencies.json",
        DependencyArtifact(run_id=_RUN_ID, source_package_hash=_HASH),
    )
    atomic_write_json(
        run_dir / "artifacts" / "04-semantic-graph.json", SemanticGraph(source_package_hash=_HASH)
    )
    atomic_write_json(
        run_dir / "artifacts" / "06-candidates.json",
        CandidateArtifact(
            run_id=_RUN_ID,
            source_package_hash=_HASH,
            semantic_graph_hash=_HASH,
            invariants_query_hash=_HASH,
            q0_query_hash=_HASH,
        ),
    )
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


def test_creates_diagnostics_semantic_coverage_json(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)

    report = compute_semantic_coverage_report(run_dir, _RUN_ID)
    report_path = write_semantic_coverage_report(run_dir, report)

    assert report_path == run_dir / "diagnostics" / "semantic-coverage.json"
    assert report_path.is_file()
    assert report.run_id == _RUN_ID


def test_second_execution_is_byte_for_byte_identical(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)

    report1 = compute_semantic_coverage_report(run_dir, _RUN_ID)
    path1 = write_semantic_coverage_report(run_dir, report1)
    bytes1 = path1.read_bytes()

    report2 = compute_semantic_coverage_report(run_dir, _RUN_ID)
    path2 = write_semantic_coverage_report(run_dir, report2)
    bytes2 = path2.read_bytes()

    assert bytes1 == bytes2


def test_no_leftover_temp_files_after_write(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    report = compute_semantic_coverage_report(run_dir, _RUN_ID)
    write_semantic_coverage_report(run_dir, report)

    diagnostics_dir = run_dir / "diagnostics"
    assert not any(diagnostics_dir.glob("*.tmp"))


# ---------------------------------------------------------------------------
# Errores claros y sanitizados
# ---------------------------------------------------------------------------


def test_nonexistent_run_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / "does-not-exist"
    with pytest.raises(SemanticCoverageError, match="no encontrado"):
        compute_semantic_coverage_report(run_dir, "does-not-exist")


def test_run_before_candidates_detected_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_run_state(run_dir, stage=PipelineStage.EXTRACTED)
    with pytest.raises(SemanticCoverageError, match="CANDIDATES_DETECTED"):
        compute_semantic_coverage_report(run_dir, _RUN_ID)


def test_missing_canonical_directory_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    shutil.rmtree(run_dir / "artifacts" / "02-canonical")
    with pytest.raises(SemanticCoverageError, match="02-canonical"):
        compute_semantic_coverage_report(run_dir, _RUN_ID)


def test_missing_dependencies_artifact_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "03-dependencies.json").unlink()
    with pytest.raises(SemanticCoverageError, match="03-dependencies.json"):
        compute_semantic_coverage_report(run_dir, _RUN_ID)


def test_missing_semantic_graph_artifact_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "04-semantic-graph.json").unlink()
    with pytest.raises(SemanticCoverageError, match="04-semantic-graph.json"):
        compute_semantic_coverage_report(run_dir, _RUN_ID)


def test_missing_candidates_artifact_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "06-candidates.json").unlink()
    with pytest.raises(SemanticCoverageError, match="06-candidates.json"):
        compute_semantic_coverage_report(run_dir, _RUN_ID)


def test_corrupt_json_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "03-dependencies.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(SemanticCoverageError, match="invalido"):
        compute_semantic_coverage_report(run_dir, _RUN_ID)


def test_schema_incompatible_json_raises_clear_error(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    # JSON valido pero incompatible con el contrato DependencyArtifact.
    (run_dir / "artifacts" / "03-dependencies.json").write_text('{"foo": "bar"}', encoding="utf-8")
    with pytest.raises(SemanticCoverageError, match="invalido"):
        compute_semantic_coverage_report(run_dir, _RUN_ID)


def test_error_messages_never_contain_absolute_paths(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "06-candidates.json").unlink()
    with pytest.raises(SemanticCoverageError) as excinfo:
        compute_semantic_coverage_report(run_dir, _RUN_ID)
    assert str(tmp_path) not in str(excinfo.value)


def test_no_partial_report_created_on_failure(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    (run_dir / "artifacts" / "06-candidates.json").unlink()
    with pytest.raises(SemanticCoverageError):
        compute_semantic_coverage_report(run_dir, _RUN_ID)
    assert not (run_dir / "diagnostics").exists()


# ---------------------------------------------------------------------------
# Nunca modifica artefactos de entrada / no regresion
# ---------------------------------------------------------------------------


def test_input_artifacts_are_never_modified(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    state_before = _write_full_valid_run(run_dir)

    before_hashes = _tree_hashes(run_dir / "artifacts")
    run_json_bytes_before = (run_dir / "run.json").read_bytes()

    report = compute_semantic_coverage_report(run_dir, _RUN_ID)
    write_semantic_coverage_report(run_dir, report)

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

    report = compute_semantic_coverage_report(run_dir, _RUN_ID)
    write_semantic_coverage_report(run_dir, report)

    entries_after = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()}
    new_entries = entries_after - entries_before
    assert new_entries == {"diagnostics/semantic-coverage.json"}


def test_report_path_helper_matches_actual_write_location(tmp_path: Path) -> None:
    run_dir = tmp_path / _RUN_ID
    _write_full_valid_run(run_dir)
    report = compute_semantic_coverage_report(run_dir, _RUN_ID)
    written_path = write_semantic_coverage_report(run_dir, report)
    assert written_path == semantic_coverage_report_path(run_dir)
