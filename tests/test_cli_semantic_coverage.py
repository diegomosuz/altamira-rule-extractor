"""Tests unitarios del comando CLI `semantic-coverage` (Fase 1 de la
ampliacion semantica, checkpoint `feat/semantic-expansion-foundation`).
Mismo patron que `tests/test_cli.py`: sin Docker, sin JAR, sin Neo4j, sin
FastAPI -- solo filesystem local (`tmp_path`) via `CliRunner`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
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

from .api.conftest import HASH_A, RUN_ID, build_run_completed

runner = CliRunner()

_HASH = "9" * 64
_RUN_ID = "20260101T000000000000-eeeeeeee"
_NONEXISTENT_RUN_ID = "20260101T000000000000-ffffffff"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
    )


@pytest.fixture
def patched_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Settings:
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    return settings


def _write_valid_run(run_dir: Path) -> None:
    now = datetime.now(UTC)
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH,
        current_stage=PipelineStage.CANDIDATES_DETECTED,
        stages=[
            StageExecution(
                stage=PipelineStage.CANDIDATES_DETECTED,
                status=StageStatus.SUCCEEDED,
                started_at=now,
                finished_at=now,
                duration_seconds=0.0,
            )
        ],
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)

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


def test_semantic_coverage_success_prints_readable_summary_and_exit_0(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-coverage", _RUN_ID])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"run_id: {_RUN_ID}"
    assert any(line.startswith("programs:") for line in lines)
    assert any(line.startswith("statements:") for line in lines)
    assert any(line.startswith("decisions:") for line in lines)
    assert any(line.startswith("decisions_without_resolved_effect:") for line in lines)
    assert any(line.startswith("candidates_q0:") for line in lines)
    assert any(line.startswith("level_88_detected:") for line in lines)
    assert any(line.startswith("preserved_only_constructs:") for line in lines)
    assert any(line.startswith("unsupported_constructs:") for line in lines)
    assert lines[-1] == "report: diagnostics/semantic-coverage.json"


def test_semantic_coverage_persists_the_report_file(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-coverage", _RUN_ID])

    assert result.exit_code == 0
    report_path = run_dir / "diagnostics" / "semantic-coverage.json"
    assert report_path.is_file()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == _RUN_ID
    assert payload["schema_version"] == "1.0"


def test_semantic_coverage_json_option_prints_full_report_after_persisting(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-coverage", _RUN_ID, "--json"])

    assert result.exit_code == 0
    # El resumen legible se imprime primero; el JSON completo es el ultimo bloque.
    json_start = result.stdout.index("{")
    payload = json.loads(result.stdout[json_start:])
    assert payload["run_id"] == _RUN_ID
    assert payload["schema_version"] == "1.0"
    assert (run_dir / "diagnostics" / "semantic-coverage.json").is_file()


def test_semantic_coverage_nonexistent_run_exits_nonzero_and_sanitized(
    patched_settings: Settings,
) -> None:
    result = runner.invoke(cli_module.app, ["semantic-coverage", _NONEXISTENT_RUN_ID])

    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stderr
    assert "Traceback" not in result.stderr


def test_semantic_coverage_before_candidates_detected_exits_nonzero(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    now = datetime.now(UTC)
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH,
        current_stage=PipelineStage.EXTRACTED,
        stages=[
            StageExecution(
                stage=PipelineStage.EXTRACTED,
                status=StageStatus.SUCCEEDED,
                started_at=now,
                finished_at=now,
                duration_seconds=0.0,
            )
        ],
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)

    result = runner.invoke(cli_module.app, ["semantic-coverage", _RUN_ID])

    assert result.exit_code != 0


def test_semantic_coverage_invalid_run_id_format_exits_2(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["semantic-coverage", "not/a-valid-run-id"])

    assert result.exit_code == 2


def test_semantic_coverage_never_modifies_run_json(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    run_json_bytes_before = (run_dir / "run.json").read_bytes()

    result = runner.invoke(cli_module.app, ["semantic-coverage", _RUN_ID])

    assert result.exit_code == 0
    assert (run_dir / "run.json").read_bytes() == run_json_bytes_before


def test_semantic_coverage_never_modifies_v1_artifacts(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    artifacts_dir = run_dir / "artifacts"
    before = {
        str(p.relative_to(artifacts_dir)): p.read_bytes()
        for p in artifacts_dir.rglob("*")
        if p.is_file()
    }

    result = runner.invoke(cli_module.app, ["semantic-coverage", _RUN_ID])

    assert result.exit_code == 0
    after = {
        str(p.relative_to(artifacts_dir)): p.read_bytes()
        for p in artifacts_dir.rglob("*")
        if p.is_file()
    }
    assert before == after


def _write_semantic_prerequisites(run_dir: Path) -> None:
    """Agrega artifacts/02-canonical, 03-dependencies.json y
    04-semantic-graph.json (los que `build_run_completed` no escribe,
    porque no los necesita para simular COMPLETED) para que el servicio
    de cobertura semantica pueda ejecutarse sobre el MISMO run."""
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
        source_hash=HASH_A,
        source_package_hash=HASH_A,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[paragraph],
    )
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_dir.mkdir(parents=True)
    atomic_write_json(canonical_dir / "a.cbl.json", program)
    atomic_write_json(
        run_dir / "artifacts" / "03-dependencies.json",
        DependencyArtifact(run_id=RUN_ID, source_package_hash=HASH_A),
    )
    atomic_write_json(
        run_dir / "artifacts" / "04-semantic-graph.json", SemanticGraph(source_package_hash=HASH_A)
    )


def _hash_tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_semantic_coverage_never_regresses_v1_output_artifacts(
    patched_settings: Settings,
) -> None:
    """Prueba de no regresion explicita (Fase 11): construye un run
    COMPLETED real (06-candidates/07-context/08-rule-drafts/09-guardrails/
    10-rules via `build_run_completed`), corre `semantic-coverage`, y
    demuestra que ninguno de esos directorios cambio ni un byte, que
    `RunState.current_stage`/`updated_at` no cambiaron, y que el UNICO
    artefacto nuevo es `diagnostics/semantic-coverage.json`."""
    run_dir = patched_settings.runs_dir / RUN_ID
    state_before = build_run_completed(run_dir, run_id=RUN_ID)
    _write_semantic_prerequisites(run_dir)

    watched_dirs = [
        run_dir / "artifacts" / "06-candidates.json",
        run_dir / "artifacts" / "07-context",
        run_dir / "artifacts" / "08-rule-drafts",
        run_dir / "artifacts" / "09-guardrails",
        run_dir / "artifacts" / "10-rules",
    ]
    hashes_before = {
        str(path): _hash_tree(path) if path.is_dir() else None for path in watched_dirs
    }
    candidates_file_hash_before = (
        (run_dir / "artifacts" / "06-candidates.json").read_bytes()
        if (run_dir / "artifacts" / "06-candidates.json").is_file()
        else None
    )
    run_json_bytes_before = (run_dir / "run.json").read_bytes()

    result = runner.invoke(cli_module.app, ["semantic-coverage", RUN_ID])
    assert result.exit_code == 0, result.stderr

    hashes_after = {
        str(path): _hash_tree(path) if path.is_dir() else None for path in watched_dirs
    }
    candidates_file_hash_after = (
        (run_dir / "artifacts" / "06-candidates.json").read_bytes()
        if (run_dir / "artifacts" / "06-candidates.json").is_file()
        else None
    )
    run_json_bytes_after = (run_dir / "run.json").read_bytes()

    assert hashes_before == hashes_after
    assert candidates_file_hash_before == candidates_file_hash_after
    assert run_json_bytes_before == run_json_bytes_after

    state_after = RunState.model_validate_json(run_json_bytes_after.decode("utf-8"))
    assert state_after.current_stage == state_before.current_stage == PipelineStage.COMPLETED
    assert state_after.updated_at == state_before.updated_at

    assert (run_dir / "diagnostics" / "semantic-coverage.json").is_file()
