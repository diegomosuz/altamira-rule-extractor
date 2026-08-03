"""Tests unitarios del comando CLI `candidate-promotion-assessment`
(Fase 9 de la ampliacion semantica, `feat/unified-candidate-promotion-
assessment`). Item 42 de los 50 tests obligatorios -- mismo patron que
`tests/test_cli_interprocedural_candidates_shadow.py`: sin Docker, sin
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

_HASH = "9" * 64
_RUN_ID = "20260101T000000000000-9abbccdd"
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


def test_candidate_promotion_assessment_summary_and_exit_0(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["candidate-promotion-assessment", _RUN_ID])

    assert result.exit_code == 0
    assert _RUN_ID in result.stdout
    assert "diagnostics" in result.stdout.lower() or "candidate-promotion-assessment" in (
        result.stdout
    )


def test_candidate_promotion_assessment_persists_artifact(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["candidate-promotion-assessment", _RUN_ID])

    assert result.exit_code == 0
    report_path = run_dir / "diagnostics" / "candidate-promotion-assessment.json"
    assert report_path.is_file()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == _RUN_ID
    assert payload["schema_version"] == "1.0"


def test_candidate_promotion_assessment_json_option(patched_settings: Settings) -> None:
    """`--json` imprime el resumen legible y, a continuacion, el
    artefacto JSON completo (mismo patron que el resto de comandos
    shadow) -- nunca reemplaza el resumen."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["candidate-promotion-assessment", _RUN_ID, "--json"])

    assert result.exit_code == 0
    json_start = result.stdout.index("{")
    payload = json.loads(result.stdout[json_start:])
    assert payload["run_id"] == _RUN_ID


def test_candidate_promotion_assessment_is_byte_for_byte_deterministic(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    runner.invoke(cli_module.app, ["candidate-promotion-assessment", _RUN_ID])
    report_path = run_dir / "diagnostics" / "candidate-promotion-assessment.json"
    first = report_path.read_bytes()

    runner.invoke(cli_module.app, ["candidate-promotion-assessment", _RUN_ID])
    second = report_path.read_bytes()

    assert first == second


def test_candidate_promotion_assessment_no_absolute_paths_no_timestamps(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["candidate-promotion-assessment", _RUN_ID])
    assert result.exit_code == 0
    assert str(patched_settings.runs_dir) not in result.stdout

    report_path = run_dir / "diagnostics" / "candidate-promotion-assessment.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert "timestamp" not in payload
    assert "generated_at" not in payload


def test_candidate_promotion_assessment_never_modifies_v1_or_run_json(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    v1_before = (run_dir / "artifacts" / "06-candidates.json").read_bytes()
    run_json_before = (run_dir / "run.json").read_bytes()

    runner.invoke(cli_module.app, ["candidate-promotion-assessment", _RUN_ID])

    assert (run_dir / "artifacts" / "06-candidates.json").read_bytes() == v1_before
    assert (run_dir / "run.json").read_bytes() == run_json_before


def test_candidate_promotion_assessment_nonexistent_run_exits_nonzero_and_sanitized(
    patched_settings: Settings,
) -> None:
    result = runner.invoke(
        cli_module.app, ["candidate-promotion-assessment", _NONEXISTENT_RUN_ID]
    )
    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert str(patched_settings.runs_dir) not in (result.stderr or "")
    assert "Traceback" not in result.stdout


def test_candidate_promotion_assessment_requires_parsed_stage(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_run_state(run_dir, stages=())

    result = runner.invoke(cli_module.app, ["candidate-promotion-assessment", _RUN_ID])

    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
