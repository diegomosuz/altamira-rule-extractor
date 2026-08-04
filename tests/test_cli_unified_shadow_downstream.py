"""Tests del servicio de filesystem y del comando CLI
`unified-shadow-downstream` (Fase 13 de la ampliacion semantica,
`feat/unified-shadow-downstream-pipeline`). Mismo patron que
`tests/test_cli_unified_shadow_validate.py` (Fase 12), del que reutiliza
el helper de escenario REAL-pero-vacio -- sin Docker, sin JAR, sin
Neo4j, solo filesystem local (`tmp_path`) via `CliRunner`. El escenario
V2 real con un grupo `COMPLETED` se cubre en
`tests/parser_integration/test_unified_shadow_downstream_integration.py`
(Fase 13 Parte 13)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage
from altamira_extractor.contracts.semantic_graph import SemanticGraph
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.errors import UnifiedShadowDownstreamError
from altamira_extractor.pipeline.unified_shadow_downstream_service import (
    compute_unified_shadow_downstream_artifact,
    unified_shadow_downstream_artifact_path,
    write_unified_shadow_downstream_artifact,
)
from altamira_extractor.pipeline.unified_shadow_validation_service import (
    compute_unified_shadow_validation_report,
    write_unified_shadow_validation_report,
)

from .test_cli_unified_candidates_shadow import (
    _HASH,
    _NONEXISTENT_RUN_ID,
    _RUN_ID,
    _generate_review_package_and_plan_via_cli,
    _write_parsed_run,
    _write_run_state,
)

runner = CliRunner()


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


def _write_semantic_graph(run_dir: Path) -> None:
    graph = SemanticGraph(source_package_hash=_HASH)
    path = run_dir / "artifacts" / "04-semantic-graph.json"
    atomic_write_json(path, graph)


def _generate_empty_downstream_sources(run_dir: Path, tmp_path: Path) -> None:
    """Escenario REAL pero vacio (cero shadow members/groups) --
    reutiliza exactamente el mismo helper de Fase 11/12 (`unified-
    candidates-shadow`/`unified-shadow-validate` via CLI), y agrega el
    unico artefacto que Fase 13 requiere ademas: un `SemanticGraph`
    valido (vacio, ya que no hay ningun grupo que lo necesite en este
    escenario)."""
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    result = runner.invoke(cli_module.app, ["unified-candidates-shadow", _RUN_ID])
    assert result.exit_code == 0, result.stdout
    report = compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    write_unified_shadow_validation_report(run_dir, report)
    _write_semantic_graph(run_dir)


# ---------------------------------------------------------------------------
# Servicio: compute_unified_shadow_downstream_artifact
# ---------------------------------------------------------------------------


def test_service_nonexistent_run_raises(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _NONEXISTENT_RUN_ID
    with pytest.raises(UnifiedShadowDownstreamError):
        compute_unified_shadow_downstream_artifact(
            run_dir, _NONEXISTENT_RUN_ID, settings=patched_settings
        )


def test_service_stage_not_parsed_raises(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_run_state(run_dir, stages=(PipelineStage.RECEIVED,))
    with pytest.raises(UnifiedShadowDownstreamError, match="PARSED"):
        compute_unified_shadow_downstream_artifact(run_dir, _RUN_ID, settings=patched_settings)


def test_service_missing_unified_shadow_raises(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    with pytest.raises(UnifiedShadowDownstreamError, match="unified-candidates-shadow"):
        compute_unified_shadow_downstream_artifact(run_dir, _RUN_ID, settings=patched_settings)


def test_service_missing_validation_report_raises(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    result = runner.invoke(cli_module.app, ["unified-candidates-shadow", _RUN_ID])
    assert result.exit_code == 0, result.stdout

    with pytest.raises(UnifiedShadowDownstreamError, match="validacion"):
        compute_unified_shadow_downstream_artifact(run_dir, _RUN_ID, settings=patched_settings)


def test_service_missing_semantic_graph_raises(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)
    result = runner.invoke(cli_module.app, ["unified-candidates-shadow", _RUN_ID])
    assert result.exit_code == 0, result.stdout
    report = compute_unified_shadow_validation_report(run_dir, _RUN_ID)
    write_unified_shadow_validation_report(run_dir, report)

    with pytest.raises(UnifiedShadowDownstreamError, match="grafo semantico"):
        compute_unified_shadow_downstream_artifact(run_dir, _RUN_ID, settings=patched_settings)


def test_service_invalid_unified_shadow_json_raises(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _generate_empty_downstream_sources(run_dir, tmp_path)
    (run_dir / "diagnostics" / "unified-candidates-shadow.json").write_text(
        "{ not valid json", encoding="utf-8"
    )

    with pytest.raises(UnifiedShadowDownstreamError):
        compute_unified_shadow_downstream_artifact(run_dir, _RUN_ID, settings=patched_settings)


def test_service_empty_scenario_produces_not_executed_without_raising(
    patched_settings: Settings, tmp_path: Path
) -> None:
    """Cero shadow groups es una respuesta VALIDA (NOT_EXECUTED), nunca
    un error tecnico -- refleja exactamente la misma politica que
    Fase 12 aplica a REVIEW_REQUIRED con cero grupos."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _generate_empty_downstream_sources(run_dir, tmp_path)

    artifact = compute_unified_shadow_downstream_artifact(
        run_dir, _RUN_ID, settings=patched_settings
    )

    assert artifact.disposition.value == "NOT_EXECUTED"
    assert artifact.group_results == []


def test_service_never_writes_by_itself(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _generate_empty_downstream_sources(run_dir, tmp_path)

    compute_unified_shadow_downstream_artifact(run_dir, _RUN_ID, settings=patched_settings)

    assert not unified_shadow_downstream_artifact_path(run_dir).exists()


def test_service_determinism(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _generate_empty_downstream_sources(run_dir, tmp_path)

    artifact_1 = compute_unified_shadow_downstream_artifact(
        run_dir, _RUN_ID, settings=patched_settings
    )
    artifact_2 = compute_unified_shadow_downstream_artifact(
        run_dir, _RUN_ID, settings=patched_settings
    )

    assert artifact_1.to_stable_json() == artifact_2.to_stable_json()


# ---------------------------------------------------------------------------
# CLI: unified-shadow-downstream
# ---------------------------------------------------------------------------


def test_cli_nonexistent_run_exits_nonzero_and_sanitized(patched_settings: Settings) -> None:
    result = runner.invoke(cli_module.app, ["unified-shadow-downstream", _NONEXISTENT_RUN_ID])
    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_stage_not_reached_exits_nonzero_and_sanitized(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_run_state(run_dir, stages=(PipelineStage.RECEIVED,))

    result = runner.invoke(cli_module.app, ["unified-shadow-downstream", _RUN_ID])
    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_missing_unified_shadow_exits_nonzero_no_artifact(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    _generate_review_package_and_plan_via_cli(run_dir, tmp_path)

    result = runner.invoke(cli_module.app, ["unified-shadow-downstream", _RUN_ID])
    assert result.exit_code != 0
    assert "Traceback" not in result.stdout
    assert not unified_shadow_downstream_artifact_path(run_dir).exists()


def test_cli_empty_scenario_exits_0_with_not_executed(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _generate_empty_downstream_sources(run_dir, tmp_path)

    result = runner.invoke(cli_module.app, ["unified-shadow-downstream", _RUN_ID])

    assert result.exit_code == 0, result.stdout
    assert "disposition: NOT_EXECUTED" in result.stdout
    assert unified_shadow_downstream_artifact_path(run_dir).is_file()


def test_cli_json_option_prints_full_artifact(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _generate_empty_downstream_sources(run_dir, tmp_path)

    result = runner.invoke(cli_module.app, ["unified-shadow-downstream", _RUN_ID, "--json"])

    assert result.exit_code == 0, result.stdout
    assert '"schema_version"' in result.stdout
    assert '"provider": "DETERMINISTIC_FAKE"' in result.stdout


def test_cli_never_modifies_preexisting_artifacts(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _generate_empty_downstream_sources(run_dir, tmp_path)

    unified_shadow_before = (
        run_dir / "diagnostics" / "unified-candidates-shadow.json"
    ).read_bytes()
    validation_report_before = (
        run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    ).read_bytes()
    v1_before = (run_dir / "artifacts" / "06-candidates.json").read_bytes()

    result = runner.invoke(cli_module.app, ["unified-shadow-downstream", _RUN_ID])
    assert result.exit_code == 0, result.stdout

    assert (
        run_dir / "diagnostics" / "unified-candidates-shadow.json"
    ).read_bytes() == unified_shadow_before
    assert (
        run_dir / "diagnostics" / "unified-shadow-validation-report.json"
    ).read_bytes() == validation_report_before
    assert (run_dir / "artifacts" / "06-candidates.json").read_bytes() == v1_before


def test_cli_only_new_file_is_the_downstream_artifact(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _generate_empty_downstream_sources(run_dir, tmp_path)
    before = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()}

    result = runner.invoke(cli_module.app, ["unified-shadow-downstream", _RUN_ID])
    assert result.exit_code == 0, result.stdout

    after = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()}
    assert after - before == {"diagnostics/unified-shadow-downstream.json"}


def test_cli_never_registered_under_the_wrong_name() -> None:
    result = runner.invoke(cli_module.app, ["--help"])
    assert "unified-shadow-downstream" in result.stdout


def test_cli_has_no_provider_or_credential_options() -> None:
    result = runner.invoke(cli_module.app, ["unified-shadow-downstream", "--help"])
    assert result.exit_code == 0, result.stdout
    lowered = result.stdout.lower()
    for forbidden in ("--provider", "--api-key", "--endpoint", "--model", "--manifest"):
        assert forbidden not in lowered


def test_service_write_helper_writes_atomically(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _generate_empty_downstream_sources(run_dir, tmp_path)
    artifact = compute_unified_shadow_downstream_artifact(
        run_dir, _RUN_ID, settings=patched_settings
    )

    path = write_unified_shadow_downstream_artifact(run_dir, artifact)

    assert path == unified_shadow_downstream_artifact_path(run_dir)
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == artifact.to_stable_json()
