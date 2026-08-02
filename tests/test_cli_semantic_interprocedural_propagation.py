"""Tests unitarios del comando CLI `semantic-interprocedural-propagation`
(Fase 7 de la ampliacion semantica,
`feat/interprocedural-propagation-shadow`). Mismo patron que
`tests/test_cli_semantic_interprocedural.py`: sin Docker, sin JAR, sin
Neo4j, sin FastAPI -- solo filesystem local (`tmp_path`) via
`CliRunner`. Cubre CLI (27), comportamiento de filesystem del servicio
(28) y errores claros (29)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.canonical import (
    CanonicalCallArgument,
    CanonicalDataItem,
    CanonicalEntryParameter,
    CanonicalLinkageDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    CallPassingMode,
    CallTargetKind,
    LocationKind,
    PipelineStage,
    SourceFormat,
    StageStatus,
    StatementKind,
)
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.pipeline.artifact_store import atomic_write_json

runner = CliRunner()

_HASH = "5" * 64
_RUN_ID = "20260101T000000000000-71aaaaaa"
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


def _build_caller() -> CanonicalProgram:
    move_stmt = CanonicalStatement(
        statement_id="CALLER::MAIN::0::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE '0005' TO WS-LIT",
        location_kind=LocationKind.UNKNOWN,
        target_data_items=["WS-LIT"],
        variables_written=["WS-LIT"],
        assigned_literal="0005",
    )
    call_ok = CanonicalStatement(
        statement_id="CALLER::MAIN::1::CALL",
        kind=StatementKind.CALL,
        source_text="CALL 'CALLEE' USING BY CONTENT WS-LIT",
        location_kind=LocationKind.UNKNOWN,
        call_target_kind=CallTargetKind.LITERAL,
        called_program_name="CALLEE",
        call_arguments=[
            CanonicalCallArgument(
                ordinal=1,
                expression="WS-LIT",
                data_item_name="WS-LIT",
                qualified_data_item_name="WS-LIT",
                passing_mode=CallPassingMode.CONTENT,
                location_kind=LocationKind.UNKNOWN,
            )
        ],
    )
    call_missing = CanonicalStatement(
        statement_id="CALLER::MAIN::2::CALL",
        kind=StatementKind.CALL,
        source_text="CALL 'MISSING-PROG'",
        location_kind=LocationKind.UNKNOWN,
        call_target_kind=CallTargetKind.LITERAL,
        called_program_name="MISSING-PROG",
    )
    call_dynamic = CanonicalStatement(
        statement_id="CALLER::MAIN::3::CALL",
        kind=StatementKind.CALL,
        source_text="CALL WS-PROGRAM-NAME",
        location_kind=LocationKind.UNKNOWN,
        variables_read=["WS-PROGRAM-NAME"],
        call_target_kind=CallTargetKind.DYNAMIC,
        called_program_expression="WS-PROGRAM-NAME",
    )
    paragraph = CanonicalParagraph(
        name="MAIN",
        source_text="MAIN.",
        location_kind=LocationKind.UNKNOWN,
        statements=[move_stmt, call_ok, call_missing, call_dynamic],
        variables_read=["WS-PROGRAM-NAME"],
        variables_written=["WS-LIT"],
    )
    return CanonicalProgram(
        schema_version="1.2",
        program_name="CALLER",
        source_file="01-codigo/cobol/CALLER.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[
            CanonicalDataItem(
                name="WS-LIT",
                qualified_name="WS-LIT",
                level=1,
                pic="X(10)",
                location_kind=LocationKind.UNKNOWN,
            ),
            CanonicalDataItem(
                name="WS-PROGRAM-NAME",
                qualified_name="WS-PROGRAM-NAME",
                level=1,
                pic="X(8)",
                location_kind=LocationKind.UNKNOWN,
            ),
        ],
        paragraphs=[paragraph],
    )


def _build_callee() -> CanonicalProgram:
    return CanonicalProgram(
        schema_version="1.2",
        program_name="CALLEE",
        source_file="01-codigo/cobol/CALLEE.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        linkage_data_items=[
            CanonicalLinkageDataItem(
                name="LK-IN",
                qualified_name="LK-IN",
                level=1,
                pic="X(10)",
                location_kind=LocationKind.UNKNOWN,
            )
        ],
        entry_parameters=[
            CanonicalEntryParameter(
                ordinal=1,
                name="LK-IN",
                qualified_name="LK-IN",
                linkage_item_qualified_name="LK-IN",
                passing_mode=CallPassingMode.CONTENT,
                location_kind=LocationKind.UNKNOWN,
            )
        ],
        paragraphs=[],
    )


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
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_dir.mkdir(parents=True)
    atomic_write_json(canonical_dir / "CALLER.json", _build_caller())
    atomic_write_json(canonical_dir / "CALLEE.json", _build_callee())


# --- 27. CLI: camino feliz -------------------------------------------------------


def test_semantic_interprocedural_propagation_summary_and_exit_0(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-interprocedural-propagation", _RUN_ID])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"run_id: {_RUN_ID}"
    assert "programs: 2" in result.stdout
    assert "call_sites: 3" in result.stdout
    assert "eligible_calls: 1" in result.stdout
    assert "propagated_calls: 1" in result.stdout
    assert "blocked_calls: 2" in result.stdout
    assert "entry_facts: 1" in result.stdout
    assert lines[-1] == "report: diagnostics/interprocedural-propagation.json"


def test_semantic_interprocedural_propagation_persisted_artifact_content(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-interprocedural-propagation", _RUN_ID])
    assert result.exit_code == 0

    artifact_path = run_dir / "diagnostics" / "interprocedural-propagation.json"
    assert artifact_path.is_file()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == _RUN_ID
    assert payload["schema_version"] == "1.0"
    assert payload["semantic_effects_schema_version"] == "1.2"

    entry_facts = [f for f in payload["facts"] if f["kind"] == "ENTRY_FACT"]
    assert len(entry_facts) == 1
    assert entry_facts[0]["status"] == "PROPAGATED"
    assert entry_facts[0]["literal"] == "0005"
    assert entry_facts[0]["actual_name"] == "WS-LIT"
    assert entry_facts[0]["formal_name"] == "LK-IN"


def test_semantic_interprocedural_propagation_json_option(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(
        cli_module.app, ["semantic-interprocedural-propagation", _RUN_ID, "--json"]
    )
    assert result.exit_code == 0
    json_start = result.stdout.index("{")
    payload = json.loads(result.stdout[json_start:])
    assert payload["run_id"] == _RUN_ID
    assert (run_dir / "diagnostics" / "interprocedural-propagation.json").is_file()


# --- 24/25 (apoyo): determinismo, sin timestamps, sin rutas absolutas -----------


def test_semantic_interprocedural_propagation_is_byte_for_byte_deterministic(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    artifact_path = run_dir / "diagnostics" / "interprocedural-propagation.json"

    first = runner.invoke(cli_module.app, ["semantic-interprocedural-propagation", _RUN_ID])
    assert first.exit_code == 0
    first_bytes = artifact_path.read_bytes()

    second = runner.invoke(cli_module.app, ["semantic-interprocedural-propagation", _RUN_ID])
    assert second.exit_code == 0
    second_bytes = artifact_path.read_bytes()

    assert first_bytes == second_bytes


def test_semantic_interprocedural_propagation_no_timestamps_no_absolute_paths(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    result = runner.invoke(cli_module.app, ["semantic-interprocedural-propagation", _RUN_ID])
    assert result.exit_code == 0
    text = (run_dir / "diagnostics" / "interprocedural-propagation.json").read_text(
        encoding="utf-8"
    )
    for forbidden in ("generated_at", "timestamp", "created_at", "updated_at"):
        assert forbidden not in text
    assert str(patched_settings.runs_dir) not in text


# --- 28. Servicio de filesystem: composicion sin mutar diagnostics preexistentes


def test_semantic_interprocedural_propagation_composes_without_mutating_siblings(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    effects_result = runner.invoke(cli_module.app, ["semantic-effects", _RUN_ID])
    assert effects_result.exit_code == 0, effects_result.stderr
    effects_bytes_before = (run_dir / "diagnostics" / "semantic-effects.json").read_bytes()

    propagation_result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID])
    assert propagation_result.exit_code == 0, propagation_result.stderr
    propagation_bytes_before = (run_dir / "diagnostics" / "semantic-propagation.json").read_bytes()

    linkage_result = runner.invoke(cli_module.app, ["semantic-interprocedural", _RUN_ID])
    assert linkage_result.exit_code == 0, linkage_result.stderr
    linkage_bytes_before = (
        run_dir / "diagnostics" / "interprocedural-call-linkage.json"
    ).read_bytes()

    result = runner.invoke(cli_module.app, ["semantic-interprocedural-propagation", _RUN_ID])
    assert result.exit_code == 0, result.stderr

    assert (run_dir / "diagnostics" / "semantic-effects.json").read_bytes() == effects_bytes_before
    assert (
        run_dir / "diagnostics" / "semantic-propagation.json"
    ).read_bytes() == propagation_bytes_before
    assert (
        run_dir / "diagnostics" / "interprocedural-call-linkage.json"
    ).read_bytes() == linkage_bytes_before


def test_semantic_interprocedural_propagation_never_modifies_run_json_or_canonical(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    run_json_before = (run_dir / "run.json").read_bytes()
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_before = {
        path.name: path.read_bytes() for path in sorted(canonical_dir.glob("*.json"))
    }

    result = runner.invoke(cli_module.app, ["semantic-interprocedural-propagation", _RUN_ID])
    assert result.exit_code == 0

    assert (run_dir / "run.json").read_bytes() == run_json_before
    canonical_after = {
        path.name: path.read_bytes() for path in sorted(canonical_dir.glob("*.json"))
    }
    assert canonical_after == canonical_before


def test_semantic_interprocedural_propagation_never_creates_v1_or_v2_artifacts(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-interprocedural-propagation", _RUN_ID])
    assert result.exit_code == 0

    assert not (run_dir / "artifacts" / "06-candidates.json").exists()
    assert not (run_dir / "artifacts" / "04-semantic-graph.json").exists()
    assert not (run_dir / "diagnostics" / "v2-candidates-shadow.json").exists()
    diagnostics_files = sorted(p.name for p in (run_dir / "diagnostics").glob("*.json"))
    assert diagnostics_files == ["interprocedural-propagation.json"]


# --- 29. Errores claros -----------------------------------------------------------


def test_semantic_interprocedural_propagation_requires_parsed_stage(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    run_dir.mkdir(parents=True)
    now = datetime.now(UTC)
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH,
        current_stage=PipelineStage.VALIDATED,
        stages=[],
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)

    result = runner.invoke(cli_module.app, ["semantic-interprocedural-propagation", _RUN_ID])

    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert str(patched_settings.runs_dir) not in (result.stderr or "")
    assert not (run_dir / "diagnostics" / "interprocedural-propagation.json").exists()


def test_semantic_interprocedural_propagation_nonexistent_run_exits_nonzero_and_sanitized(
    patched_settings: Settings,
) -> None:
    result = runner.invoke(
        cli_module.app, ["semantic-interprocedural-propagation", _NONEXISTENT_RUN_ID]
    )
    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert str(patched_settings.runs_dir) not in (result.stderr or "")


def test_semantic_interprocedural_propagation_missing_canonical_dir_is_sanitized_error(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    run_dir.mkdir(parents=True)
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
    # artifacts/02-canonical/ deliberadamente ausente.

    result = runner.invoke(cli_module.app, ["semantic-interprocedural-propagation", _RUN_ID])

    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert str(patched_settings.runs_dir) not in (result.stderr or "")
    assert not (run_dir / "diagnostics").exists()
