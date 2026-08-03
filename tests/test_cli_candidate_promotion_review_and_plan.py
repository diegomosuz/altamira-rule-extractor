"""Tests unitarios de los comandos CLI `candidate-promotion-review-
package`/`candidate-promotion-plan` (Fase 10 de la ampliacion
semantica, `feat/controlled-candidate-promotion-plan`). Items 44/45 de
los 55 tests obligatorios -- mismo patron que
`tests/test_cli_candidate_promotion_assessment.py`: sin Docker, sin
JAR, sin Neo4j, sin FastAPI -- solo filesystem local (`tmp_path`) via
`CliRunner`."""

from __future__ import annotations

import hashlib
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
from altamira_extractor.contracts.enums import (
    LocationKind,
    PipelineStage,
    SourceFormat,
    StageStatus,
    StatementKind,
)
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.pipeline.artifact_store import atomic_write_json

runner = CliRunner()

_HASH = "7" * 64
_RUN_ID = "20260101T000000000000-7abbccdd"
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


def _write_run_state(run_dir: Path, *, stages: tuple[PipelineStage, ...]) -> None:
    now = datetime.now(UTC)
    executions = [
        StageExecution(
            stage=stage,
            status=StageStatus.SUCCEEDED,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
        )
        for stage in stages
    ]
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH,
        current_stage=stages[-1] if stages else PipelineStage.RECEIVED,
        stages=executions,
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)


def _write_canonical(run_dir: Path) -> None:
    stmt = CanonicalStatement(
        statement_id="P1::A::0::IF",
        kind=StatementKind.IF,
        source_text="IF CONDICION",
        location_kind=LocationKind.EXACT,
        source_file="a.cbl",
        line_start=10,
        line_end=10,
        expression="CONDICION",
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


def _write_v1_candidates(run_dir: Path) -> None:
    artifact = CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash=_HASH,
        semantic_graph_hash=_HASH,
        invariants_query_hash=_HASH,
        q0_query_hash=_HASH,
        candidates=[],
    )
    atomic_write_json(run_dir / "artifacts" / "06-candidates.json", artifact)


def _write_parsed_run(run_dir: Path) -> None:
    _write_run_state(run_dir, stages=(PipelineStage.PARSED,))
    _write_canonical(run_dir)
    _write_v1_candidates(run_dir)


# ---------------------------------------------------------------------------
# Item 44: CLI candidate-promotion-review-package
# ---------------------------------------------------------------------------


def test_review_package_cli_summary_and_exit_0(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["candidate-promotion-review-package", _RUN_ID])

    assert result.exit_code == 0, result.stdout
    assert f"run_id: {_RUN_ID}" in result.stdout
    report_path = run_dir / "diagnostics" / "candidate-promotion-review-package.json"
    assert report_path.is_file()


def test_review_package_cli_json_option(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(
        cli_module.app, ["candidate-promotion-review-package", _RUN_ID, "--json"]
    )

    assert result.exit_code == 0
    json_start = result.stdout.index("{")
    payload = json.loads(result.stdout[json_start:])
    assert payload["run_id"] == _RUN_ID


def test_review_package_cli_nonexistent_run_exits_nonzero_and_sanitized(
    patched_settings: Settings,
) -> None:
    result = runner.invoke(
        cli_module.app, ["candidate-promotion-review-package", _NONEXISTENT_RUN_ID]
    )
    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert "Traceback" not in result.stdout


# ---------------------------------------------------------------------------
# Item 45: CLI candidate-promotion-plan
# ---------------------------------------------------------------------------


def _generate_review_package_via_cli(run_dir: Path) -> None:
    result = runner.invoke(cli_module.app, ["candidate-promotion-review-package", _RUN_ID])
    assert result.exit_code == 0, result.stdout


def _write_manifest(tmp_path: Path, run_dir: Path) -> Path:
    report_path = run_dir / "diagnostics" / "candidate-promotion-review-package.json"
    package_bytes = report_path.read_bytes()
    review_package_hash = hashlib.sha256(package_bytes).hexdigest()
    payload = json.loads(package_bytes.decode("utf-8"))
    manifest_payload = {
        "schema_version": "1.0",
        "review_package_hash": review_package_hash,
        "assessment_artifact_hash": payload["assessment_artifact_hash"],
        "run_id": _RUN_ID,
        "decisions": [],
    }
    manifest_path = tmp_path / "decisions.json"
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    return manifest_path


def test_plan_cli_summary_and_exit_0(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_via_cli(run_dir)
    manifest_path = _write_manifest(tmp_path, run_dir)

    result = runner.invoke(
        cli_module.app,
        ["candidate-promotion-plan", _RUN_ID, "--decisions", str(manifest_path)],
    )

    assert result.exit_code == 0, result.stdout
    assert f"run_id: {_RUN_ID}" in result.stdout
    report_path = run_dir / "diagnostics" / "candidate-promotion-plan.json"
    assert report_path.is_file()


def test_plan_cli_json_option(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_via_cli(run_dir)
    manifest_path = _write_manifest(tmp_path, run_dir)

    result = runner.invoke(
        cli_module.app,
        ["candidate-promotion-plan", _RUN_ID, "--decisions", str(manifest_path), "--json"],
    )

    assert result.exit_code == 0
    json_start = result.stdout.index("{")
    payload = json.loads(result.stdout[json_start:])
    assert payload["run_id"] == _RUN_ID


def test_plan_cli_requires_decisions_option(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["candidate-promotion-plan", _RUN_ID])
    assert result.exit_code != 0


def test_plan_cli_missing_decisions_file_exits_nonzero_and_sanitized(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_via_cli(run_dir)

    result = runner.invoke(
        cli_module.app,
        [
            "candidate-promotion-plan",
            _RUN_ID,
            "--decisions",
            str(tmp_path / "does-not-exist.json"),
        ],
    )
    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert "Traceback" not in result.stdout
