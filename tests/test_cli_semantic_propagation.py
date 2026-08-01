"""Tests unitarios del comando CLI `semantic-propagation` (Fase 4 de la
ampliacion semantica, `feat/constant-copy-propagation`). Mismo patron que
`tests/test_cli_semantic_effects.py`: sin Docker, sin JAR, sin Neo4j, sin
FastAPI -- solo filesystem local (`tmp_path`) via `CliRunner`."""

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
_RUN_ID = "20260101T000000000000-eeeeeeee"
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
        variables_written=["WS-COD-AUX"],
        assigned_literal="0005",
    )
    stmt2 = CanonicalStatement(
        statement_id="P1::A::2::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE WS-COD-AUX TO WS-COD-RETORNO",
        location_kind=LocationKind.UNKNOWN,
        variables_read=["WS-COD-AUX"],
        target_data_items=["WS-COD-RETORNO"],
        variables_written=["WS-COD-RETORNO"],
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[stmt1, stmt2],
        variables_read=["WS-COD-AUX"],
        variables_written=["WS-COD-AUX", "WS-COD-RETORNO"],
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


def test_semantic_propagation_success_prints_readable_summary_and_exit_0(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"run_id: {_RUN_ID}"
    assert any(line.startswith("programs:") for line in lines)
    assert any(line.startswith("paragraphs:") for line in lines)
    assert any(line.startswith("facts_total:") for line in lines)
    assert any(line.startswith("direct_literal:") for line in lines)
    assert any(line.startswith("propagated_literal:") for line in lines)
    assert any(line.startswith("condition_literal:") for line in lines)
    assert any(line.startswith("unresolved_copy:") for line in lines)
    assert any(line.startswith("invalidated:") for line in lines)
    assert any(line.startswith("blocked:") for line in lines)
    assert any(line.startswith("barriers:") for line in lines)
    assert lines[-1] == "report: diagnostics/semantic-propagation.json"


def test_semantic_propagation_summary_reports_the_obligatory_chain_correctly(
    patched_settings: Settings,
) -> None:
    """El resumen legible debe reflejar el caso obligatorio: un
    DIRECT_LITERAL y un PROPAGATED_LITERAL, literal '0005' demostrado."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID])

    assert result.exit_code == 0
    assert "facts_total: 2" in result.stdout
    assert "direct_literal: 1" in result.stdout
    assert "propagated_literal: 1" in result.stdout


def test_semantic_propagation_persists_the_artifact_file(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID])

    assert result.exit_code == 0
    artifact_path = run_dir / "diagnostics" / "semantic-propagation.json"
    assert artifact_path.is_file()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == _RUN_ID
    assert payload["schema_version"] == "1.0"
    assert payload["analyzer_version"] == "1.0"
    literals = {
        fact["target_variable"]: fact["literal"] for fact in payload["programs"][0]["facts"]
    }
    assert literals["WS-COD-AUX"] == "0005"
    assert literals["WS-COD-RETORNO"] == "0005"


def test_semantic_propagation_json_option_prints_full_artifact_after_persisting(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID, "--json"])

    assert result.exit_code == 0
    json_start = result.stdout.index("{")
    payload = json.loads(result.stdout[json_start:])
    assert payload["run_id"] == _RUN_ID
    assert payload["schema_version"] == "1.0"
    assert (run_dir / "diagnostics" / "semantic-propagation.json").is_file()


def test_semantic_propagation_no_timestamps_no_absolute_paths_in_persisted_artifact(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID])
    assert result.exit_code == 0
    text = (run_dir / "diagnostics" / "semantic-propagation.json").read_text(encoding="utf-8")
    for forbidden in ("generated_at", "timestamp", "created_at", "updated_at"):
        assert forbidden not in text
    assert str(patched_settings.runs_dir) not in text


# ---------------------------------------------------------------------------
# Errores sanitizados
# ---------------------------------------------------------------------------


def test_semantic_propagation_nonexistent_run_exits_nonzero_and_sanitized(
    patched_settings: Settings,
) -> None:
    result = runner.invoke(cli_module.app, ["semantic-propagation", _NONEXISTENT_RUN_ID])

    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stderr
    assert "Traceback" not in result.stderr


def test_semantic_propagation_before_parsed_exits_nonzero(patched_settings: Settings) -> None:
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

    result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID])

    assert result.exit_code != 0


def test_semantic_propagation_invalid_run_id_format_exits_2(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["semantic-propagation", "not/a-valid-run-id"])

    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Nunca modifica run.json ni artifacts/01-10 ni diagnostics preexistentes
# ---------------------------------------------------------------------------


def test_semantic_propagation_never_modifies_run_json(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    run_json_bytes_before = (run_dir / "run.json").read_bytes()

    result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID])

    assert result.exit_code == 0
    assert (run_dir / "run.json").read_bytes() == run_json_bytes_before


def test_semantic_propagation_never_modifies_canonical_artifacts(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    artifacts_dir = run_dir / "artifacts"
    before = {
        str(p.relative_to(artifacts_dir)): p.read_bytes()
        for p in artifacts_dir.rglob("*")
        if p.is_file()
    }

    result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID])

    assert result.exit_code == 0
    after = {
        str(p.relative_to(artifacts_dir)): p.read_bytes()
        for p in artifacts_dir.rglob("*")
        if p.is_file()
    }
    assert before == after


def test_semantic_propagation_never_creates_semantic_effects_json(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID])

    assert result.exit_code == 0
    assert not (run_dir / "diagnostics" / "semantic-effects.json").exists()


def test_semantic_propagation_never_modifies_preexisting_semantic_coverage_json(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    preexisting = run_dir / "diagnostics" / "semantic-coverage.json"
    preexisting.parent.mkdir(parents=True, exist_ok=True)
    preexisting.write_text('{"marker": "untouched"}', encoding="utf-8")
    before = preexisting.read_bytes()

    result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID])

    assert result.exit_code == 0
    assert preexisting.read_bytes() == before


# ---------------------------------------------------------------------------
# Prueba de no regresion explicita (Fase 14): artifacts/01-10 y
# diagnostics preexistentes intactos, unico archivo nuevo es
# diagnostics/semantic-propagation.json
# ---------------------------------------------------------------------------


def _write_semantic_propagation_prerequisite(run_dir: Path, state: RunState) -> None:
    """Agrega artifacts/02-canonical, diagnostics/semantic-coverage.json
    y diagnostics/semantic-effects.json (marcadores inertes, para probar
    que ninguno se modifica) y una StageExecution PARSED sobre un run
    COMPLETED ya construido por `build_run_completed` (01-inventory,
    03-dependencies, 03b-semantic-enrichment, 04-semantic-graph,
    05-invariants, 06-candidates, 07-context, 08-rule-drafts,
    09-guardrails, 10-rules)."""
    _write_canonical_program(run_dir, source_package_hash=HASH_A)
    diagnostics_dir = run_dir / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    (diagnostics_dir / "semantic-coverage.json").write_text(
        '{"marker": "coverage-untouched"}', encoding="utf-8"
    )
    (diagnostics_dir / "semantic-effects.json").write_text(
        '{"marker": "effects-untouched"}', encoding="utf-8"
    )
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


def test_semantic_propagation_never_regresses_v1_output_artifacts(
    patched_settings: Settings,
) -> None:
    """Prueba de no regresion explicita (Fase 14): construye un run
    COMPLETED real (01-inventory/03-dependencies/03b-semantic-enrichment/
    04-semantic-graph/05-invariants/06-candidates/07-context/
    08-rule-drafts/09-guardrails/10-rules via `build_run_completed`), mas
    diagnostics/semantic-coverage.json y diagnostics/semantic-effects.json
    preexistentes, corre `semantic-propagation`, y demuestra que ninguno
    de esos artefactos cambio ni un byte, que `RunState.current_stage`/
    `updated_at` no cambiaron, y que el UNICO archivo nuevo es
    `diagnostics/semantic-propagation.json`."""
    run_dir = patched_settings.runs_dir / RUN_ID
    state_before = build_run_completed(run_dir, run_id=RUN_ID)
    _write_semantic_propagation_prerequisite(run_dir, state_before)

    watched_paths = [
        run_dir / "artifacts" / "01-inventory.json",
        run_dir / "artifacts" / "03-dependencies.json",
        run_dir / "artifacts" / "03b-semantic-enrichment.json",
        run_dir / "artifacts" / "04-semantic-graph.json",
        run_dir / "artifacts" / "05-invariants.json",
        run_dir / "artifacts" / "06-candidates.json",
        run_dir / "artifacts" / "07-context",
        run_dir / "artifacts" / "08-rule-drafts",
        run_dir / "artifacts" / "09-guardrails",
        run_dir / "artifacts" / "10-rules",
        run_dir / "diagnostics" / "semantic-coverage.json",
        run_dir / "diagnostics" / "semantic-effects.json",
    ]

    def _snapshot() -> dict[str, object]:
        return {
            str(path): (
                _hash_tree(path)
                if path.is_dir()
                else (path.read_bytes() if path.is_file() else None)
            )
            for path in watched_paths
        }

    hashes_before = _snapshot()
    run_json_bytes_before = (run_dir / "run.json").read_bytes()
    entries_before = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()}

    result = runner.invoke(cli_module.app, ["semantic-propagation", RUN_ID])
    assert result.exit_code == 0, result.stderr

    hashes_after = _snapshot()
    run_json_bytes_after = (run_dir / "run.json").read_bytes()
    entries_after = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()}

    assert hashes_before == hashes_after
    assert run_json_bytes_before == run_json_bytes_after

    state_after = RunState.model_validate_json(run_json_bytes_after.decode("utf-8"))
    assert state_after.current_stage == state_before.current_stage == PipelineStage.COMPLETED
    assert state_after.updated_at == state_before.updated_at

    new_entries = entries_after - entries_before
    assert new_entries == {"diagnostics/semantic-propagation.json"}
