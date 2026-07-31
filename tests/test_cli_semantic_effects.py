"""Tests unitarios del comando CLI `semantic-effects` (Fase 2 de la
ampliacion semantica, checkpoint `feat/semantic-effects-foundation`).
Mismo patron que `tests/test_cli_semantic_coverage.py`: sin Docker, sin
JAR, sin Neo4j, sin FastAPI -- solo filesystem local (`tmp_path`) via
`CliRunner`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.canonical import (
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    LocationKind,
    PipelineStage,
    SourceFormat,
    StageStatus,
    StatementKind,
)
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.pipeline.artifact_store import atomic_write_json

from .api.conftest import HASH_A, RUN_ID, build_run_completed

runner = CliRunner()

_HASH = "9" * 64
_RUN_ID = "20260101T000000000000-dddddddd"
_NONEXISTENT_RUN_ID = "20260101T000000000000-99999999"


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


def _write_canonical_program(run_dir: Path, *, source_package_hash: str = _HASH) -> None:
    stmt1 = CanonicalStatement(
        statement_id="P1::A::1::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE '0005' TO WS-COD-AUX",
        location_kind=LocationKind.UNKNOWN,
        target_data_items=["WS-COD-AUX"],
        assigned_literal="0005",
    )
    stmt2 = CanonicalStatement(
        statement_id="P1::A::2::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE WS-COD-AUX TO WS-COD-RETORNO",
        location_kind=LocationKind.UNKNOWN,
        variables_read=["WS-COD-AUX"],
        target_data_items=["WS-COD-RETORNO"],
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[stmt1, stmt2],
        variables_read=["WS-COD-AUX"],
    )
    program = CanonicalProgram(
        program_name="PROG1",
        source_file="a.cbl",
        source_hash=source_package_hash,
        source_package_hash=source_package_hash,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[paragraph],
    )
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_dir.mkdir(parents=True)
    atomic_write_json(canonical_dir / "a.cbl.json", program)


def _write_valid_run(run_dir: Path) -> None:
    now = datetime.now(UTC)
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH,
        current_stage=PipelineStage.PARSED,
        stages=[
            StageExecution(
                stage=PipelineStage.PARSED,
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
    _write_canonical_program(run_dir)


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------


def test_semantic_effects_success_prints_readable_summary_and_exit_0(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-effects", _RUN_ID])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"run_id: {_RUN_ID}"
    assert any(line.startswith("programs:") for line in lines)
    assert any(line.startswith("effects_total:") for line in lines)
    assert any(line.startswith("fully_supported:") for line in lines)
    assert any(line.startswith("partially_supported:") for line in lines)
    assert any(line.startswith("preserved:") for line in lines)
    assert any(line.startswith("unsupported:") for line in lines)
    assert lines[-1] == "report: diagnostics/semantic-effects.json"


def test_semantic_effects_summary_reports_two_hop_move_chain_correctly(
    patched_settings: Settings,
) -> None:
    """El resumen legible debe reflejar el caso obligatorio de la Fase 5:
    un ASSIGN_LITERAL y un COPY_VALUE, nunca un tercer efecto."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-effects", _RUN_ID])

    assert result.exit_code == 0
    assert "effects_total: 2" in result.stdout
    assert "  ASSIGN_LITERAL: 1" in result.stdout
    assert "  COPY_VALUE: 1" in result.stdout


def test_semantic_effects_persists_the_artifact_file(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-effects", _RUN_ID])

    assert result.exit_code == 0
    artifact_path = run_dir / "diagnostics" / "semantic-effects.json"
    assert artifact_path.is_file()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == _RUN_ID
    assert payload["schema_version"] == "1.0"


def test_semantic_effects_json_option_prints_full_artifact_after_persisting(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-effects", _RUN_ID, "--json"])

    assert result.exit_code == 0
    json_start = result.stdout.index("{")
    payload = json.loads(result.stdout[json_start:])
    assert payload["run_id"] == _RUN_ID
    assert payload["schema_version"] == "1.0"
    assert (run_dir / "diagnostics" / "semantic-effects.json").is_file()


# ---------------------------------------------------------------------------
# Errores sanitizados
# ---------------------------------------------------------------------------


def test_semantic_effects_nonexistent_run_exits_nonzero_and_sanitized(
    patched_settings: Settings,
) -> None:
    result = runner.invoke(cli_module.app, ["semantic-effects", _NONEXISTENT_RUN_ID])

    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stderr
    assert "Traceback" not in result.stderr


def test_semantic_effects_before_parsed_exits_nonzero(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    now = datetime.now(UTC)
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH,
        current_stage=PipelineStage.INVENTORIED,
        stages=[
            StageExecution(
                stage=PipelineStage.INVENTORIED,
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

    result = runner.invoke(cli_module.app, ["semantic-effects", _RUN_ID])

    assert result.exit_code != 0


def test_semantic_effects_invalid_run_id_format_exits_2(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["semantic-effects", "not/a-valid-run-id"])

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Nunca modifica run.json ni artifacts/01-10
# ---------------------------------------------------------------------------


def test_semantic_effects_never_modifies_run_json(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    run_json_bytes_before = (run_dir / "run.json").read_bytes()

    result = runner.invoke(cli_module.app, ["semantic-effects", _RUN_ID])

    assert result.exit_code == 0
    assert (run_dir / "run.json").read_bytes() == run_json_bytes_before


def test_semantic_effects_never_modifies_canonical_artifacts(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    artifacts_dir = run_dir / "artifacts"
    before = {
        str(p.relative_to(artifacts_dir)): p.read_bytes()
        for p in artifacts_dir.rglob("*")
        if p.is_file()
    }

    result = runner.invoke(cli_module.app, ["semantic-effects", _RUN_ID])

    assert result.exit_code == 0
    after = {
        str(p.relative_to(artifacts_dir)): p.read_bytes()
        for p in artifacts_dir.rglob("*")
        if p.is_file()
    }
    assert before == after


# ---------------------------------------------------------------------------
# Prueba de no regresion explicita: artifacts/03,04,06,07,08,09,10 intactos
# ---------------------------------------------------------------------------


def _write_semantic_effects_prerequisite(run_dir: Path, state: RunState) -> None:
    """Agrega artifacts/02-canonical y una StageExecution PARSED
    (`build_run_completed` no la incluye: solo registra las etapas
    "milestone" COMPLETED necesita, y PARSED no es una de ellas) sobre
    un run COMPLETED ya construido por `build_run_completed` (que ya
    trae 06-candidates/07-context/08-rule-drafts/09-guardrails/
    10-rules). Reescribe run.json preservando run_id/current_stage/
    updated_at/created_at -- unicamente agrega la StageExecution que
    `semantic-effects` necesita para validar su precondicion."""
    _write_canonical_program(run_dir, source_package_hash=HASH_A)
    parsed_stage = StageExecution(
        stage=PipelineStage.PARSED,
        status=StageStatus.SUCCEEDED,
        started_at=state.created_at,
        finished_at=state.created_at,
        duration_seconds=0.0,
    )
    augmented_state = state.model_copy(update={"stages": [*state.stages, parsed_stage]})
    atomic_write_json(run_dir / "run.json", augmented_state)


def _hash_tree(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_semantic_effects_never_regresses_v1_output_artifacts(
    patched_settings: Settings,
) -> None:
    """Prueba de no regresion explicita (Fase 9): construye un run
    COMPLETED real (03-dependencies/04-semantic-graph/06-candidates/
    07-context/08-rule-drafts/09-guardrails/10-rules via
    `build_run_completed`), corre `semantic-effects`, y demuestra que
    ninguno de esos artefactos cambio ni un byte, que
    `RunState.current_stage`/`updated_at` no cambiaron, y que el UNICO
    artefacto nuevo es `diagnostics/semantic-effects.json`."""
    run_dir = patched_settings.runs_dir / RUN_ID
    state_before = build_run_completed(run_dir, run_id=RUN_ID)
    _write_semantic_effects_prerequisite(run_dir, state_before)

    watched_paths = [
        run_dir / "artifacts" / "03-dependencies.json",
        run_dir / "artifacts" / "04-semantic-graph.json",
        run_dir / "artifacts" / "06-candidates.json",
        run_dir / "artifacts" / "07-context",
        run_dir / "artifacts" / "08-rule-drafts",
        run_dir / "artifacts" / "09-guardrails",
        run_dir / "artifacts" / "10-rules",
    ]
    hashes_before = {
        str(path): (
            _hash_tree(path)
            if path.is_dir()
            else (path.read_bytes() if path.is_file() else None)
        )
        for path in watched_paths
    }
    run_json_bytes_before = (run_dir / "run.json").read_bytes()

    result = runner.invoke(cli_module.app, ["semantic-effects", RUN_ID])
    assert result.exit_code == 0, result.stderr

    hashes_after = {
        str(path): (
            _hash_tree(path)
            if path.is_dir()
            else (path.read_bytes() if path.is_file() else None)
        )
        for path in watched_paths
    }
    run_json_bytes_after = (run_dir / "run.json").read_bytes()

    assert hashes_before == hashes_after
    assert run_json_bytes_before == run_json_bytes_after

    state_after = RunState.model_validate_json(run_json_bytes_after.decode("utf-8"))
    assert state_after.current_stage == state_before.current_stage == PipelineStage.COMPLETED
    assert state_after.updated_at == state_before.updated_at

    assert (run_dir / "diagnostics" / "semantic-effects.json").is_file()
