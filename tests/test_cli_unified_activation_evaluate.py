"""Tests del servicio de filesystem y del comando CLI
`unified-activation-evaluate` (Fase 14A Partes 9-10,
`feat/controlled-unified-activation`). Mismo patron que
`tests/test_cli_unified_shadow_downstream.py`: sin Docker, sin JAR,
sin Neo4j -- solo filesystem local (`tmp_path`) via `CliRunner`. El
escenario real (JAR + Neo4j) se cubre en
`tests/parser_integration/test_unified_activation_evaluate_integration.py`
(Fase 14A Parte 12)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.contracts.unified_shadow_downstream import UnifiedShadowDownstreamArtifact
from altamira_extractor.contracts.unified_shadow_validation import (
    UnifiedShadowValidationDisposition,
    UnifiedShadowValidationReport,
)
from altamira_extractor.pipeline import unified_activation_service
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.errors import UnifiedActivationError
from altamira_extractor.pipeline.unified_activation_service import (
    compute_unified_activation_evaluation,
    unified_activation_evaluation_path,
    write_unified_activation_evaluation,
)

from .pipeline._unified_activation_fixtures import ActivationGoldenPath, activation_golden_path
from .test_cli_unified_candidates_shadow import (
    _HASH,
    _NONEXISTENT_RUN_ID,
    _RUN_ID,
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


def _write_config_yaml(path: Path, *, mode: str = "V1_ONLY", extra: str = "") -> None:
    path.write_text(f"mode: {mode}\n{extra}", encoding="utf-8")


def _write_activation_golden_path_run(
    run_dir: Path,
    gp: ActivationGoldenPath,
    *,
    validation_report: UnifiedShadowValidationReport | None = None,
    downstream_artifact: UnifiedShadowDownstreamArtifact | None = None,
) -> None:
    """Escribe en disco un run REAL-pero-sintetico completo (run.json
    PARSED + los cuatro artefactos que el servicio carga blandamente)
    a partir de `activation_golden_path()` (Fase 14A Parte 8) -- sin
    Docker, sin JAR, sin Neo4j."""
    run_id = gp.unified_shadow.run_id
    now = datetime.now(UTC)
    state = RunState(
        run_id=run_id,
        package_filename="input/package.zip",
        source_package_hash=gp.unified_shadow.source_package_hash,
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
    atomic_write_json(run_dir / "artifacts" / "06-candidates.json", gp.v1_artifact)
    atomic_write_json(run_dir / "diagnostics" / "unified-candidates-shadow.json", gp.unified_shadow)
    atomic_write_json(
        run_dir / "diagnostics" / "unified-shadow-validation-report.json",
        validation_report if validation_report is not None else gp.validation_report,
    )
    atomic_write_json(
        run_dir / "diagnostics" / "unified-shadow-downstream.json",
        downstream_artifact if downstream_artifact is not None else gp.downstream_artifact,
    )


# ---------------------------------------------------------------------------
# Servicio: compute_unified_activation_evaluation
# ---------------------------------------------------------------------------


def test_service_nonexistent_run_raises(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _NONEXISTENT_RUN_ID
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)
    with pytest.raises(UnifiedActivationError):
        compute_unified_activation_evaluation(run_dir, _NONEXISTENT_RUN_ID, config_path=config_path)


def test_service_stage_not_parsed_raises(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_run_state(run_dir, stages=(PipelineStage.RECEIVED,))
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)
    with pytest.raises(UnifiedActivationError, match="PARSED"):
        compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)


def test_service_missing_config_file_raises(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "does-not-exist.yaml"
    with pytest.raises(UnifiedActivationError):
        compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)


def test_service_invalid_yaml_raises(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("mode: [this is not\n  valid: yaml", encoding="utf-8")
    with pytest.raises(UnifiedActivationError):
        compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)


def test_service_schema_incompatible_yaml_raises(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path, mode="NOT_A_REAL_MODE")
    with pytest.raises(UnifiedActivationError):
        compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)


def test_service_materialization_true_in_yaml_rejected(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path, extra="materialization_enabled: true\n")
    with pytest.raises(UnifiedActivationError):
        compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)


def test_service_rejects_symlink_config_path(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    real_file = tmp_path / "real-config.yaml"
    _write_config_yaml(real_file)
    symlink_path = tmp_path / "config-link.yaml"
    try:
        symlink_path.symlink_to(real_file)
    except OSError:
        pytest.skip("symlinks no soportados en este entorno")
    with pytest.raises(UnifiedActivationError, match="symlink"):
        compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=symlink_path)


def test_service_missing_v1_artifact_never_raises_and_reports_not_evaluated(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    (run_dir / "artifacts" / "06-candidates.json").unlink()
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)

    artifact = compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)

    assert artifact.readiness_disposition.value == "NOT_EVALUATED"


def test_service_v1_artifact_source_hash_mismatch_raises(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    mismatched = CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash="9" * 64,
        semantic_graph_hash=_HASH,
        invariants_query_hash=_HASH,
        q0_query_hash=_HASH,
        candidates=[],
    )
    atomic_write_json(run_dir / "artifacts" / "06-candidates.json", mismatched)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)

    with pytest.raises(UnifiedActivationError):
        compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)


def test_service_v1_only_success(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)

    artifact = compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)

    assert artifact.readiness_disposition.value == "V1_ONLY_READY"
    assert artifact.materialization_enabled is False


def test_service_never_writes_by_itself(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)

    compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)

    assert not unified_activation_evaluation_path(run_dir).exists()


def test_service_determinism(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)

    artifact_1 = compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)
    artifact_2 = compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)

    assert artifact_1.to_stable_json() == artifact_2.to_stable_json()


def test_service_config_hash_is_normalized_representation(
    patched_settings: Settings, tmp_path: Path
) -> None:
    """Dos YAML formateados distinto pero logicamente identicos deben
    producir el mismo `config_hash` -- se calcula sobre
    `to_stable_json()` del `UnifiedActivationConfig` YA VALIDADO, nunca
    sobre los bytes crudos del YAML."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)

    config_a = tmp_path / "a.yaml"
    config_a.write_text("mode: V1_ONLY\n", encoding="utf-8")
    config_b = tmp_path / "b.yaml"
    config_b.write_text("mode:   V1_ONLY   # comentario distinto\n", encoding="utf-8")

    artifact_a = compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_a)
    artifact_b = compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_b)

    assert artifact_a.config_hash == artifact_b.config_hash


def test_service_write_helper_writes_atomically(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)
    artifact = compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)

    path = write_unified_activation_evaluation(run_dir, artifact)

    assert path == unified_activation_evaluation_path(run_dir)
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == artifact.to_stable_json()


def test_service_write_failure_raises_and_leaves_no_partial_file(
    patched_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)
    artifact = compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(unified_activation_service, "atomic_write_json", _boom)

    with pytest.raises(OSError):
        write_unified_activation_evaluation(run_dir, artifact)

    assert not unified_activation_evaluation_path(run_dir).exists()


# ---------------------------------------------------------------------------
# CLI: unified-activation-evaluate
# ---------------------------------------------------------------------------


def test_cli_nonexistent_run_exits_nonzero_and_sanitized(
    patched_settings: Settings, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)
    result = runner.invoke(
        cli_module.app,
        ["unified-activation-evaluate", _NONEXISTENT_RUN_ID, "--config", str(config_path)],
    )
    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_missing_config_exits_nonzero_and_sanitized(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    result = runner.invoke(
        cli_module.app,
        ["unified-activation-evaluate", _RUN_ID, "--config", str(tmp_path / "missing.yaml")],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.stdout
    assert not unified_activation_evaluation_path(run_dir).exists()


def test_cli_invalid_yaml_exits_nonzero_no_partial_file(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("mode: [this is not\n  valid: yaml", encoding="utf-8")

    result = runner.invoke(
        cli_module.app, ["unified-activation-evaluate", _RUN_ID, "--config", str(config_path)]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.stdout
    assert not unified_activation_evaluation_path(run_dir).exists()


def test_cli_success_v1_only_exits_0(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)

    result = runner.invoke(
        cli_module.app, ["unified-activation-evaluate", _RUN_ID, "--config", str(config_path)]
    )

    assert result.exit_code == 0, result.stdout
    assert "readiness_disposition: V1_ONLY_READY" in result.stdout
    assert "materialization_enabled: False" in result.stdout
    assert unified_activation_evaluation_path(run_dir).is_file()


def test_cli_json_option_prints_full_artifact(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)

    result = runner.invoke(
        cli_module.app,
        ["unified-activation-evaluate", _RUN_ID, "--config", str(config_path), "--json"],
    )

    assert result.exit_code == 0, result.stdout
    assert '"schema_version"' in result.stdout
    assert '"materialization_enabled": false' in result.stdout


def test_cli_never_modifies_preexisting_artifacts(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)

    run_json_before = (run_dir / "run.json").read_bytes()
    v1_before = (run_dir / "artifacts" / "06-candidates.json").read_bytes()

    result = runner.invoke(
        cli_module.app, ["unified-activation-evaluate", _RUN_ID, "--config", str(config_path)]
    )
    assert result.exit_code == 0, result.stdout

    assert (run_dir / "run.json").read_bytes() == run_json_before
    assert (run_dir / "artifacts" / "06-candidates.json").read_bytes() == v1_before


def test_cli_only_new_file_is_the_activation_evaluation(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)
    before = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()}

    result = runner.invoke(
        cli_module.app, ["unified-activation-evaluate", _RUN_ID, "--config", str(config_path)]
    )
    assert result.exit_code == 0, result.stdout

    after = {p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*") if p.is_file()}
    assert after - before == {"diagnostics/unified-activation-evaluation.json"}


def test_cli_never_copies_the_config_file_into_the_run(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(config_path)

    result = runner.invoke(
        cli_module.app, ["unified-activation-evaluate", _RUN_ID, "--config", str(config_path)]
    )
    assert result.exit_code == 0, result.stdout

    for candidate in run_dir.rglob("*"):
        if candidate.is_file():
            assert candidate.suffix not in (".yaml", ".yml")


def test_cli_blocked_result_still_exits_0(patched_settings: Settings, tmp_path: Path) -> None:
    """Un resultado BLOCKED es una evaluacion CONTRACTUALMENTE VALIDA
    (no un error tecnico): debe persistirse y terminar con exit code 0
    -- solo un fallo TECNICO (run inexistente, YAML invalido, escritura
    fallida) produce un exit code distinto de cero."""
    gp = activation_golden_path()
    run_dir = patched_settings.runs_dir / gp.unified_shadow.run_id
    blocked_validation_report = gp.validation_report.model_copy(
        update={"disposition": UnifiedShadowValidationDisposition.REVIEW_REQUIRED}
    )
    _write_activation_golden_path_run(run_dir, gp, validation_report=blocked_validation_report)
    config_path = tmp_path / "config.yaml"
    _write_config_yaml(
        config_path,
        mode="UNIFIED_CANARY",
        extra=(
            "canary_strategy: EXPLICIT_ALLOWLIST\n"
            f"package_hash_allowlist: [{gp.unified_shadow.source_package_hash!r}]\n"
            "fallback_policy: FALLBACK_TO_V1\n"
        ),
    )

    result = runner.invoke(
        cli_module.app,
        ["unified-activation-evaluate", gp.unified_shadow.run_id, "--config", str(config_path)],
    )

    assert result.exit_code == 0, result.stdout
    assert "readiness_disposition: BLOCKED" in result.stdout
    assert "effective_lane: V1" in result.stdout
    assert unified_activation_evaluation_path(run_dir).is_file()


def test_cli_never_registered_under_the_wrong_name() -> None:
    result = runner.invoke(cli_module.app, ["--help"])
    assert "unified-activation-evaluate" in result.stdout


def test_cli_has_no_provider_or_credential_or_decision_options() -> None:
    result = runner.invoke(cli_module.app, ["unified-activation-evaluate", "--help"])
    assert result.exit_code == 0, result.stdout
    lowered = result.stdout.lower()
    for forbidden in (
        "--provider",
        "--api-key",
        "--endpoint",
        "--model",
        "--materialize",
        "--materialization",
        "--canary-percentage",
        "--decision",
    ):
        assert forbidden not in lowered
