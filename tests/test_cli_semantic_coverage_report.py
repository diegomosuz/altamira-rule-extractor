"""Tests unitarios del comando CLI `semantic-coverage-report` (Fase
15B2-A, Parte H). Mismo patron que `tests/test_cli_semantic_coverage.py`:
sin Docker, sin JAR, sin Neo4j -- solo filesystem local (`tmp_path`) via
`CliRunner`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings, load_settings
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
)
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.pipeline.artifact_store import atomic_write_json

runner = CliRunner()

_HASH = "9" * 64
_RUN_ID = "20260101T000000000000-eeeeeeee"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    real_settings = load_settings()
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
        semantic_coverage_manifest_path=real_settings.semantic_coverage_manifest_path,
        ground_truth_path=real_settings.ground_truth_path,
        release_readiness_policy_path=real_settings.release_readiness_policy_path,
    )


@pytest.fixture
def patched_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Settings:
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    return settings


def _write_parsed_run(run_dir: Path) -> None:
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

    stmt = CanonicalStatement(
        statement_id="P1::A::1::IF",
        kind="IF",
        source_text="IF W > 1",
        location_kind=LocationKind.UNKNOWN,
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


def test_semantic_coverage_report_success_prints_summary(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-coverage-report", _RUN_ID])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert any(line.startswith("manifest_edition:") for line in lines)
    assert any(line.startswith("reconciliation_issues:") for line in lines)
    assert any(line == f"run_id: {_RUN_ID}" for line in lines)
    assert any(line.startswith("catalog_constructs:") for line in lines)
    assert lines[-1] == "observations: diagnostics/semantic-coverage-observations.json"


def test_semantic_coverage_report_persists_observations_file(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-coverage-report", _RUN_ID])

    assert result.exit_code == 0
    observations_path = run_dir / "diagnostics" / "semantic-coverage-observations.json"
    assert observations_path.is_file()
    payload = json.loads(observations_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == _RUN_ID
    assert payload["schema_version"] == "1.0"


def test_semantic_coverage_report_json_option_prints_full_artifact(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-coverage-report", _RUN_ID, "--json"])

    assert result.exit_code == 0
    json_start = result.stdout.index("{")
    payload = json.loads(result.stdout[json_start:])
    assert payload["run_id"] == _RUN_ID


def test_semantic_coverage_report_missing_run_exits_nonzero(patched_settings: Settings) -> None:
    result = runner.invoke(
        cli_module.app, ["semantic-coverage-report", "20260101T000000000000-ffffffff"]
    )
    assert result.exit_code != 0
