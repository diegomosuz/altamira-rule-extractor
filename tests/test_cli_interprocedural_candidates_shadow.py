"""Tests unitarios del comando CLI `interprocedural-candidates-shadow`
(Fase 8 de la ampliacion semantica,
`feat/interprocedural-rule-detectors-shadow`). Mismo patron que
`tests/test_cli_semantic_interprocedural_propagation.py`: sin Docker,
sin JAR, sin Neo4j, sin FastAPI -- solo filesystem local (`tmp_path`) via
`CliRunner`. Cubre CLI (item 34), comportamiento de filesystem del
servicio (item 33), ausencia opcional de CandidateArtifact V1 (item 37) y
de SemanticGraph/V2 (item 38), no modificacion de V1/V2 preexistentes
(item 39) y errores claros de filesystem (item 36)."""

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
    CanonicalDataItem,
    CanonicalLinkageDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
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

_HASH = "6" * 64
_RUN_ID = "20260101T000000000000-72bbbbbb"
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
    call_returning = CanonicalStatement(
        statement_id="CALLER::MAIN::0::CALL",
        kind=StatementKind.CALL,
        source_text="CALL 'CALLEE' RETURNING WS-R",
        location_kind=LocationKind.UNKNOWN,
        call_target_kind=CallTargetKind.LITERAL,
        called_program_name="CALLEE",
        call_returning_data_item="WS-R",
    )
    paragraph = CanonicalParagraph(
        name="MAIN",
        source_text="MAIN.",
        location_kind=LocationKind.UNKNOWN,
        statements=[call_returning],
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
                name="WS-R", qualified_name="WS-R", level=1, location_kind=LocationKind.UNKNOWN
            )
        ],
        paragraphs=[paragraph],
    )


def _build_callee() -> CanonicalProgram:
    move_stmt = CanonicalStatement(
        statement_id="CALLEE::MAIN::0::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE '0009' TO LK-R",
        location_kind=LocationKind.UNKNOWN,
        target_data_items=["LK-R"],
        variables_written=["LK-R"],
        assigned_literal="0009",
    )
    paragraph = CanonicalParagraph(
        name="MAIN",
        source_text="MAIN.",
        location_kind=LocationKind.UNKNOWN,
        statements=[move_stmt],
        variables_written=["LK-R"],
    )
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
                name="LK-R",
                qualified_name="LK-R",
                level=1,
                pic="X(4)",
                location_kind=LocationKind.UNKNOWN,
            )
        ],
        entry_returning_data_item="LK-R",
        paragraphs=[paragraph],
    )


def _write_valid_run(run_dir: Path, *, with_v1_candidates: bool = False) -> None:
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
    if with_v1_candidates:
        v1_artifact = CandidateArtifact(
            run_id=_RUN_ID,
            source_package_hash=_HASH,
            semantic_graph_hash=_HASH,
            invariants_query_hash=_HASH,
            q0_query_hash=_HASH,
            candidates=[],
        )
        atomic_write_json(run_dir / "artifacts" / "06-candidates.json", v1_artifact)


# --- 34. CLI: camino feliz --------------------------------------------------


def test_interprocedural_candidates_shadow_summary_and_exit_0(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"run_id: {_RUN_ID}"
    assert "candidates_total: 1" in result.stdout
    assert "deterministic: 1" in result.stdout
    assert "RETURN_CODE_RULE: 1" in result.stdout
    assert "BY_REFERENCE_RULE: 0" in result.stdout
    assert "STATE_TRANSITION_RULE: 0" in result.stdout
    # Ni V1 ni V2 estan disponibles en este fixture (with_v1_candidates=False
    # por defecto, sin artifacts/04-semantic-graph.json): la comparacion
    # nunca finge INTERPROCEDURAL_ONLY, queda NOT_EVALUATED (auditoria de
    # cierre, regla D).
    assert "interprocedural_only: 0" in result.stdout
    assert "not_evaluated: 1" in result.stdout
    assert lines[-1] == "report: diagnostics/interprocedural-rule-candidates-shadow.json"


def test_interprocedural_candidates_shadow_persisted_artifact_content(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])
    assert result.exit_code == 0

    artifact_path = run_dir / "diagnostics" / "interprocedural-rule-candidates-shadow.json"
    assert artifact_path.is_file()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == _RUN_ID
    assert payload["schema_version"] == "1.0"
    assert len(payload["candidates"]) == 1
    assert payload["candidates"][0]["rule_type"] == "RETURN_CODE_RULE"
    assert payload["candidates"][0]["output_literal"] == "0009"


def test_interprocedural_candidates_shadow_json_option(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID, "--json"])
    assert result.exit_code == 0
    json_start = result.stdout.index("{")
    payload = json.loads(result.stdout[json_start:])
    assert payload["run_id"] == _RUN_ID
    assert (run_dir / "diagnostics" / "interprocedural-rule-candidates-shadow.json").is_file()


# --- 33/35. Servicio: determinismo, sin timestamps, sin rutas absolutas ----


def test_interprocedural_candidates_shadow_is_byte_for_byte_deterministic(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    artifact_path = run_dir / "diagnostics" / "interprocedural-rule-candidates-shadow.json"

    first = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])
    assert first.exit_code == 0
    first_bytes = artifact_path.read_bytes()

    second = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])
    assert second.exit_code == 0
    second_bytes = artifact_path.read_bytes()

    assert first_bytes == second_bytes


def test_interprocedural_candidates_shadow_no_timestamps_no_absolute_paths(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    result = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])
    assert result.exit_code == 0
    text = (run_dir / "diagnostics" / "interprocedural-rule-candidates-shadow.json").read_text(
        encoding="utf-8"
    )
    for forbidden in ("generated_at", "timestamp", "created_at", "updated_at"):
        assert forbidden not in text
    assert str(patched_settings.runs_dir) not in text


# --- 37. Ausencia de CandidateArtifact V1 nunca es un error -----------------


def test_interprocedural_candidates_shadow_works_without_v1_candidates(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir, with_v1_candidates=False)
    assert not (run_dir / "artifacts" / "06-candidates.json").exists()

    result = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(
        (run_dir / "diagnostics" / "interprocedural-rule-candidates-shadow.json").read_text(
            encoding="utf-8"
        )
    )
    # V1 ausente (y V2, que depende de V1) -- nunca se finge
    # INTERPROCEDURAL_ONLY (auditoria de cierre, regla D): la comparacion
    # queda NOT_EVALUATED, con la dimension V1 explicitamente NOT_EVALUATED
    # y un diagnostico trazable en el artefacto.
    assert payload["comparisons"][0]["status"] == "NOT_EVALUATED"
    assert payload["comparisons"][0]["v1_relation"] == "NOT_EVALUATED"
    assert "V1_CANDIDATES_UNAVAILABLE_ARTIFACTS_06_CANDIDATES_JSON_ABSENT" in payload["diagnostics"]


# --- 38. Ausencia de SemanticGraph (V2) nunca es un error -------------------


def test_interprocedural_candidates_shadow_works_without_semantic_graph(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir, with_v1_candidates=True)
    assert not (run_dir / "artifacts" / "04-semantic-graph.json").exists()

    result = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(
        (run_dir / "diagnostics" / "interprocedural-rule-candidates-shadow.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["summary"]["matched_v2_count"] == 0
    assert payload["summary"]["related_v2_count"] == 0


# --- 39. Nunca modifica V1/V2/artifacts/run.json preexistentes -------------


def test_interprocedural_candidates_shadow_never_modifies_v1_candidates_or_run_json(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir, with_v1_candidates=True)
    run_json_before = (run_dir / "run.json").read_bytes()
    v1_before = (run_dir / "artifacts" / "06-candidates.json").read_bytes()
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_before = {
        path.name: path.read_bytes() for path in sorted(canonical_dir.glob("*.json"))
    }

    result = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])
    assert result.exit_code == 0

    assert (run_dir / "run.json").read_bytes() == run_json_before
    assert (run_dir / "artifacts" / "06-candidates.json").read_bytes() == v1_before
    canonical_after = {
        path.name: path.read_bytes() for path in sorted(canonical_dir.glob("*.json"))
    }
    assert canonical_after == canonical_before


def test_interprocedural_candidates_shadow_never_creates_v1_v2_or_other_diagnostics(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])
    assert result.exit_code == 0

    assert not (run_dir / "artifacts" / "06-candidates.json").exists()
    assert not (run_dir / "artifacts" / "04-semantic-graph.json").exists()
    assert not (run_dir / "diagnostics" / "v2-candidates-shadow.json").exists()
    assert not (run_dir / "diagnostics" / "interprocedural-propagation.json").exists()
    diagnostics_files = sorted(p.name for p in (run_dir / "diagnostics").glob("*.json"))
    assert diagnostics_files == ["interprocedural-rule-candidates-shadow.json"]


def test_interprocedural_candidates_shadow_composes_without_mutating_siblings(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    propagation_result = runner.invoke(
        cli_module.app, ["semantic-interprocedural-propagation", _RUN_ID]
    )
    assert propagation_result.exit_code == 0, propagation_result.stderr
    propagation_bytes_before = (
        run_dir / "diagnostics" / "interprocedural-propagation.json"
    ).read_bytes()

    result = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])
    assert result.exit_code == 0, result.stderr

    assert (
        run_dir / "diagnostics" / "interprocedural-propagation.json"
    ).read_bytes() == propagation_bytes_before


# --- 36. Errores claros de filesystem ---------------------------------------


def test_interprocedural_candidates_shadow_requires_parsed_stage(
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

    result = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])

    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert str(patched_settings.runs_dir) not in (result.stderr or "")
    assert not (run_dir / "diagnostics" / "interprocedural-rule-candidates-shadow.json").exists()


def test_interprocedural_candidates_shadow_nonexistent_run_exits_nonzero_and_sanitized(
    patched_settings: Settings,
) -> None:
    result = runner.invoke(
        cli_module.app, ["interprocedural-candidates-shadow", _NONEXISTENT_RUN_ID]
    )
    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert str(patched_settings.runs_dir) not in (result.stderr or "")


def test_interprocedural_candidates_shadow_missing_canonical_dir_is_sanitized_error(
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

    result = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])

    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert str(patched_settings.runs_dir) not in (result.stderr or "")
    assert not (run_dir / "diagnostics").exists()


def test_interprocedural_candidates_shadow_corrupt_v1_candidates_is_sanitized_error(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    (run_dir / "artifacts" / "06-candidates.json").write_text("{not valid json", encoding="utf-8")

    result = runner.invoke(cli_module.app, ["interprocedural-candidates-shadow", _RUN_ID])

    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert str(patched_settings.runs_dir) not in (result.stderr or "")
    assert not (run_dir / "diagnostics" / "interprocedural-rule-candidates-shadow.json").exists()
