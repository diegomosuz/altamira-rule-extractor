"""Tests unitarios del comando CLI `functional-validate` (Fase 15B2-A,
Parte H). Mismo patron que `tests/test_cli_semantic_coverage.py`: sin
Docker, sin JAR, sin Neo4j -- solo filesystem local (`tmp_path`) via
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


_REPO_ROOT = Path(__file__).resolve().parents[1]
_RETURN_CODE_FIXTURE = (
    _REPO_ROOT / "config" / "ground_truth" / "fixtures" / "gt_return_code_001.cbl"
)


def _write_parsed_run(
    run_dir: Path, *, program_name: str = "PROG1", copy_return_code_fixture: bool = False
) -> None:
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
        statement_id=f"{program_name}::A::1::IF",
        kind="IF",
        source_text="IF W > 1",
        location_kind=LocationKind.UNKNOWN,
    )
    paragraph = CanonicalParagraph(
        name="A", source_text="A.", location_kind=LocationKind.UNKNOWN, statements=[stmt]
    )
    program = CanonicalProgram(
        program_name=program_name,
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

    if copy_return_code_fixture:
        # Aplicabilidad (checkpoint correctivo): copia la fixture REAL
        # byte a byte bajo work/extracted/ para que el caso
        # gt-positive-return-code-if-else quede APPLICABLE -- sin esto,
        # ningun caso del catalogo tendria su fixture set presente en
        # este run sintetico y el dataset entero seria NOT_APPLICABLE.
        extracted_dir = run_dir / "work" / "extracted" / "01-codigo" / "cobol"
        extracted_dir.mkdir(parents=True)
        (extracted_dir / "GTRC001.cbl").write_bytes(_RETURN_CODE_FIXTURE.read_bytes())


def test_functional_validate_success_prints_metrics(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["functional-validate", _RUN_ID])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"run_id: {_RUN_ID}"
    assert any(line.startswith("ground_truth_catalog_edition:") for line in lines)
    assert any(line.startswith("tp=") for line in lines)
    assert any(line.startswith("precision:") for line in lines)
    assert lines[-1] == "report: diagnostics/functional-validation-report.json"


def test_functional_validate_reports_missing_when_ground_truth_program_absent(
    patched_settings: Settings,
) -> None:
    # La fixture real gt_return_code_001.cbl SI esta presente en este run
    # (checkpoint correctivo: aplicabilidad), pero el canonical program
    # sintetico se llama PROG1, no GTRC001 -- el caso queda APPLICABLE
    # pero MISSING de forma deterministica (mismatch de programa, nunca
    # por ausencia de fixture).
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir, copy_return_code_fixture=True)

    result = runner.invoke(cli_module.app, ["functional-validate", _RUN_ID])

    assert result.exit_code == 0
    assert "MISSING" in result.stdout


def test_functional_validate_reports_not_evaluated_when_ground_truth_not_applicable(
    patched_settings: Settings,
) -> None:
    # Checkpoint correctivo: sin ninguna fixture real del catalogo
    # presente en work/extracted/, el dataset completo queda
    # NOT_APPLICABLE -- nunca MISSING (eso confundiria "paquete distinto"
    # con "regresion detectada").
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["functional-validate", _RUN_ID])

    assert result.exit_code == 0
    assert "MISSING" not in result.stdout
    assert "NOT_EVALUATED" in result.stdout


def test_functional_validate_persists_report_file(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["functional-validate", _RUN_ID])

    assert result.exit_code == 0
    report_path = run_dir / "diagnostics" / "functional-validation-report.json"
    assert report_path.is_file()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == _RUN_ID
    # Fase 15B4-CANDIDATE-QUALITY-5C: bump de contrato (validation_source/
    # productive_candidate_count/artifact_chain_integrity/final_rule_linkage).
    assert payload["schema_version"] == "1.1"


def test_functional_validate_missing_run_exits_nonzero(patched_settings: Settings) -> None:
    result = runner.invoke(
        cli_module.app, ["functional-validate", "20260101T000000000000-ffffffff"]
    )
    assert result.exit_code != 0
