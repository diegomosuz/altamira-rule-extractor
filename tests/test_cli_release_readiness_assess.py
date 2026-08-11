"""Tests unitarios del comando CLI `release-readiness-assess` (Fase
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


_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES_DIR = _REPO_ROOT / "config" / "ground_truth" / "fixtures"
_RETURN_CODE_FIXTURE = _FIXTURES_DIR / "gt_return_code_001.cbl"
_ALL_GROUND_TRUTH_FIXTURES = (
    "gt_return_code_001.cbl",
    "gt_level88_return_code_001.cbl",
    "gt_negative_001.cbl",
    "gt_by_reference_output_caller_001.cbl",
    "gt_by_reference_output_callee_001.cbl",
    "gt_state_transition_001.cbl",
    "gt_state_transition_negative_001.cbl",
    "gt_calculation_001.cbl",
    "gt_calculation_unconditional_001.cbl",
    "gt_calculation_unconditional_002.cbl",
)


def _write_parsed_run(
    run_dir: Path, *, copy_return_code_fixture: bool = False, copy_all_fixtures: bool = False
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
        statement_id="PROG1::A::1::IF",
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

    if copy_return_code_fixture:
        # Aplicabilidad (checkpoint correctivo): sin esto, ningun caso
        # del catalogo tendria su fixture set presente en este run
        # sintetico y el dataset entero seria NOT_APPLICABLE.
        extracted_dir = run_dir / "work" / "extracted" / "01-codigo" / "cobol"
        extracted_dir.mkdir(parents=True)
        (extracted_dir / "GTRC001.cbl").write_bytes(_RETURN_CODE_FIXTURE.read_bytes())

    if copy_all_fixtures:
        # Completitud (segundo checkpoint correctivo): con una sola
        # fixture copiada, coverage_status queda PARTIALLY_EVALUATED
        # (BY_REFERENCE_OUTPUT/LEVEL_88/NEGATIVE siguen pendientes) y
        # engineering_functional_readiness nunca sale de NOT_EVALUATED
        # -- para probar un FAIL_ENGINEERING real (no solo ausencia de
        # senal) hace falta que el catalogo COMPLETO quede aplicable,
        # aunque el canonical program sintetico ("PROG1") nunca produzca
        # candidatos reales (0 detectados, todos los REQUIRED MISSING).
        extracted_dir = run_dir / "work" / "extracted" / "01-codigo" / "cobol"
        extracted_dir.mkdir(parents=True, exist_ok=True)
        for filename in _ALL_GROUND_TRUTH_FIXTURES:
            (extracted_dir / filename).write_bytes((_FIXTURES_DIR / filename).read_bytes())


def test_release_readiness_assess_success_prints_disposition(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir, copy_return_code_fixture=True)

    result = runner.invoke(cli_module.app, ["release-readiness-assess", _RUN_ID])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"run_id: {_RUN_ID}"
    assert any(line.startswith("disposition:") for line in lines)
    assert any(line.startswith("criteria:") for line in lines)
    assert lines[-1] == "assessment: diagnostics/release-readiness-assessment.json"


def test_release_readiness_assess_not_met_when_ground_truth_program_absent(
    patched_settings: Settings,
) -> None:
    # Checkpoint correctivo (completitud): TODAS las fixtures del
    # catalogo estan presentes (coverage_status=COMPLETELY_EVALUATED),
    # pero el canonical program sintetico no coincide con ninguno de los
    # programas esperados -- los REQUIRED quedan MISSING de forma
    # deterministica, nunca FUNCTIONAL_CRITERIA_MET por omision de
    # cobertura.
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir, copy_all_fixtures=True)

    result = runner.invoke(cli_module.app, ["release-readiness-assess", _RUN_ID])

    assert result.exit_code == 0
    assert "disposition: FUNCTIONAL_CRITERIA_NOT_MET" in result.stdout


def test_release_readiness_assess_not_evaluated_when_ground_truth_not_applicable(
    patched_settings: Settings,
) -> None:
    # Checkpoint correctivo: sin ninguna fixture real presente, el
    # dataset entero es NOT_APPLICABLE -- la disposicion global debe ser
    # NOT_EVALUATED, nunca FUNCTIONAL_CRITERIA_NOT_MET (eso penalizaria
    # un paquete sin ground truth como si hubiese fallado).
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    result = runner.invoke(cli_module.app, ["release-readiness-assess", _RUN_ID])

    assert result.exit_code == 0
    assert "disposition: NOT_EVALUATED" in result.stdout
    assert "FUNCTIONAL_CRITERIA_NOT_MET" not in result.stdout


def test_release_readiness_assess_persists_assessment_file(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir, copy_return_code_fixture=True)

    result = runner.invoke(cli_module.app, ["release-readiness-assess", _RUN_ID])

    assert result.exit_code == 0
    assessment_path = run_dir / "diagnostics" / "release-readiness-assessment.json"
    assert assessment_path.is_file()
    payload = json.loads(assessment_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == _RUN_ID
    assert payload["schema_version"] == "1.0"


def test_release_readiness_assess_missing_run_exits_nonzero(patched_settings: Settings) -> None:
    result = runner.invoke(
        cli_module.app, ["release-readiness-assess", "20260101T000000000000-ffffffff"]
    )
    assert result.exit_code != 0
