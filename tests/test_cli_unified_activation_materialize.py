"""Tests del servicio de materializacion y de los comandos CLI
(Fase 14B Parte 12-13/15 items 87-103,
`feat/controlled-unified-materialization`). Sin Docker, sin JAR, sin
Neo4j -- solo filesystem local (`tmp_path`) via `CliRunner`, reutilizando
el escenario sintetico `activation_golden_path()`."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.pipeline.errors import UnifiedMaterializationError
from altamira_extractor.pipeline.unified_materialization_service import (
    materialize_unified_activation,
)

from .pipeline._unified_materialization_fixtures import (
    build_materialization_fixture,
    evaluation_hash_of,
    write_authorization_yaml,
    write_run_dir,
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


def _settings_for(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "d", runs_dir=tmp_path / "d" / "runs", incoming_dir=tmp_path / "d" / "i"
    )


def _prepare(settings: Settings) -> tuple[Path, str, str, list[str]]:
    fx = build_materialization_fixture()
    run_dir = write_run_dir(settings.runs_dir, fx)
    eval_hash = evaluation_hash_of(run_dir)
    return run_dir, fx.run_id, eval_hash, fx.approved_group_ids


# ---------------------------------------------------------------------------
# Servicio (items 87-94)
# ---------------------------------------------------------------------------


def test_service_keep_v1(tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, _groups = _prepare(_settings_for(tmp_path))
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="KEEP_V1",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        reason_code="KEEP_BASELINE",
        approved_group_ids=[],
        fallback_authorized=False,
    )
    result = materialize_unified_activation(run_dir, run_id, authorization_path=auth_path)
    assert result.active_lane.value == "V1"
    assert result.action.value == "KEEP_V1"


def test_service_canary(tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(_settings_for(tmp_path))
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    result = materialize_unified_activation(run_dir, run_id, authorization_path=auth_path)
    assert result.active_lane.value == "UNIFIED"


def test_service_primary(tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(_settings_for(tmp_path))

    # No hay modo PRIMARY en Fase 14A; se reusa la evaluacion de canary
    # pero se autoriza PRIMARY para ejercitar exclusivamente Fase 14B.
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_PRIMARY",
        expected_readiness_disposition="READY_FOR_PRIMARY_TRIAL",
        reason_code="PRIMARY_TRIAL_APPROVED",
        approved_group_ids=groups,
    )
    with pytest.raises(UnifiedMaterializationError):
        # La evaluacion real es READY_FOR_UNIFIED_CANARY, no PRIMARY_TRIAL
        # -- confirma que el servicio nunca activa PRIMARY sin la
        # disposicion exacta.
        materialize_unified_activation(run_dir, run_id, authorization_path=auth_path)


def test_service_fallback(tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(_settings_for(tmp_path))
    canary_auth = tmp_path / "canary.yaml"
    write_authorization_yaml(
        canary_auth,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    materialize_unified_activation(run_dir, run_id, authorization_path=canary_auth)

    fallback_auth = tmp_path / "fallback.yaml"
    write_authorization_yaml(
        fallback_auth,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="FALLBACK_TO_V1",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        reason_code="ACTIVE_GENERATION_INVALID",
        approved_group_ids=[],
    )
    result = materialize_unified_activation(run_dir, run_id, authorization_path=fallback_auth)
    assert result.active_lane.value == "V1"


def test_service_rollback(tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(_settings_for(tmp_path))
    canary_auth = tmp_path / "canary.yaml"
    write_authorization_yaml(
        canary_auth,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    materialize_unified_activation(run_dir, run_id, authorization_path=canary_auth)

    rollback_auth = tmp_path / "rollback.yaml"
    write_authorization_yaml(
        rollback_auth,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ROLLBACK_TO_PREVIOUS",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        reason_code="OPERATOR_ROLLBACK",
        approved_group_ids=[],
        rollback_authorized=True,
    )
    result = materialize_unified_activation(run_dir, run_id, authorization_path=rollback_auth)
    assert result.active_lane.value == "V1"


def test_service_idempotence(tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(_settings_for(tmp_path))
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    first = materialize_unified_activation(run_dir, run_id, authorization_path=auth_path)
    second = materialize_unified_activation(run_dir, run_id, authorization_path=auth_path)
    assert first.generation_id == second.generation_id
    assert second.idempotent is True


# 93. autorizacion ausente.
def test_service_missing_authorization_raises(tmp_path: Path) -> None:
    run_dir, run_id, _eval_hash, _groups = _prepare(_settings_for(tmp_path))
    with pytest.raises(UnifiedMaterializationError):
        materialize_unified_activation(
            run_dir, run_id, authorization_path=tmp_path / "does-not-exist.yaml"
        )


# 94. autorizacion YAML invalida.
def test_service_invalid_yaml_raises(tmp_path: Path) -> None:
    run_dir, run_id, _eval_hash, _groups = _prepare(_settings_for(tmp_path))
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("not: [valid\n  yaml", encoding="utf-8")
    with pytest.raises(UnifiedMaterializationError):
        materialize_unified_activation(run_dir, run_id, authorization_path=bad_yaml)


def test_service_stale_evaluation_hash_rejected(tmp_path: Path) -> None:
    run_dir, run_id, _eval_hash, groups = _prepare(_settings_for(tmp_path))
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=run_id,
        activation_evaluation_hash="0" * 64,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    with pytest.raises(UnifiedMaterializationError):
        materialize_unified_activation(run_dir, run_id, authorization_path=auth_path)


def test_service_wrong_run_id_rejected(tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(_settings_for(tmp_path))
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id="20260101T000000000000-wrongrun",
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    with pytest.raises(UnifiedMaterializationError):
        materialize_unified_activation(run_dir, run_id, authorization_path=auth_path)


# ---------------------------------------------------------------------------
# CLI (items 95-103)
# ---------------------------------------------------------------------------


# 95. CLI materialize success.
def test_cli_materialize_success(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(patched_settings)
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    result = runner.invoke(
        cli_module.app,
        ["unified-activation-materialize", run_id, "--authorization", str(auth_path)],
    )
    assert result.exit_code == 0, result.stdout
    assert "active_lane: UNIFIED" in result.stdout


# 96. CLI materialize JSON.
def test_cli_materialize_json(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(patched_settings)
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    result = runner.invoke(
        cli_module.app,
        [
            "unified-activation-materialize",
            run_id,
            "--authorization",
            str(auth_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert '"active_lane": "UNIFIED"' in result.stdout


# 97. CLI status.
def test_cli_status(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(patched_settings)
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    runner.invoke(
        cli_module.app,
        ["unified-activation-materialize", run_id, "--authorization", str(auth_path)],
    )
    result = runner.invoke(cli_module.app, ["unified-activation-status", run_id])
    assert result.exit_code == 0, result.stdout
    assert "integrity_status: OK" in result.stdout


# 98. CLI resolve.
def test_cli_resolve(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(patched_settings)
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    runner.invoke(
        cli_module.app,
        ["unified-activation-materialize", run_id, "--authorization", str(auth_path)],
    )
    result = runner.invoke(
        cli_module.app, ["unified-activation-resolve", run_id, "--artifact", "candidates"]
    )
    assert result.exit_code == 0, result.stdout
    assert "status: RESOLVED" in result.stdout


# 99. CLI resolve con fallback.
def test_cli_resolve_with_fallback(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(patched_settings)
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    materialize_result = materialize_unified_activation(
        run_dir, run_id, authorization_path=auth_path
    )
    generation_dir = run_dir / "activation" / "generations" / materialize_result.generation_id
    corrupt_path = generation_dir / "candidates.json"
    corrupt_path.write_bytes(b"{corrupted}")

    result = runner.invoke(
        cli_module.app, ["unified-activation-resolve", run_id, "--artifact", "candidates"]
    )
    assert result.exit_code == 0, result.stdout
    assert "status: FALLBACK_APPLIED" in result.stdout
    assert "fallback_applied: True" in result.stdout


# 100. CLI rollback.
def test_cli_rollback(patched_settings: Settings, tmp_path: Path) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(patched_settings)
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    runner.invoke(
        cli_module.app,
        ["unified-activation-materialize", run_id, "--authorization", str(auth_path)],
    )
    rollback_auth = tmp_path / "rollback.yaml"
    write_authorization_yaml(
        rollback_auth,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ROLLBACK_TO_PREVIOUS",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        reason_code="OPERATOR_ROLLBACK",
        approved_group_ids=[],
        rollback_authorized=True,
    )
    result = runner.invoke(
        cli_module.app,
        ["unified-activation-rollback", run_id, "--authorization", str(rollback_auth)],
    )
    assert result.exit_code == 0, result.stdout
    assert "active_lane: V1" in result.stdout


# 101. CLI error tecnico.
def test_cli_technical_error_exits_nonzero(patched_settings: Settings, tmp_path: Path) -> None:
    result = runner.invoke(
        cli_module.app,
        [
            "unified-activation-materialize",
            "20260101T000000000000-99999999",
            "--authorization",
            str(tmp_path / "missing.yaml"),
        ],
    )
    assert result.exit_code != 0


# 102. CLI sin traceback.
def test_cli_error_never_shows_traceback(patched_settings: Settings, tmp_path: Path) -> None:
    result = runner.invoke(
        cli_module.app,
        [
            "unified-activation-materialize",
            "20260101T000000000000-99999999",
            "--authorization",
            str(tmp_path / "missing.yaml"),
        ],
    )
    assert "Traceback" not in result.stdout


# 103. CLI sin rutas absolutas.
def test_cli_error_never_shows_absolute_paths(patched_settings: Settings, tmp_path: Path) -> None:
    result = runner.invoke(
        cli_module.app,
        [
            "unified-activation-materialize",
            "20260101T000000000000-99999999",
            "--authorization",
            str(tmp_path / "missing.yaml"),
        ],
    )
    assert str(patched_settings.runs_dir) not in result.stdout


def test_cli_rollback_rejects_non_rollback_action(
    patched_settings: Settings, tmp_path: Path
) -> None:
    run_dir, run_id, eval_hash, groups = _prepare(patched_settings)
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=groups,
    )
    result = runner.invoke(
        cli_module.app,
        ["unified-activation-rollback", run_id, "--authorization", str(auth_path)],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.stdout


def test_cli_help_has_no_forbidden_options() -> None:
    for command in (
        "unified-activation-materialize",
        "unified-activation-status",
        "unified-activation-resolve",
        "unified-activation-rollback",
    ):
        result = runner.invoke(cli_module.app, [command, "--help"])
        lowered = result.stdout.lower()
        for forbidden in (
            "--provider",
            "--api-key",
            "--endpoint",
            "--model",
            "--force",
            "--skip-validation",
            "--percentage",
            "--lane",
        ):
            assert forbidden not in lowered
