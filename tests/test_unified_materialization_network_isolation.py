"""Prueba EJECUTABLE de aislamiento de red del control plane de
materializacion controlada (Fase 14B, `feat/controlled-unified-materialization`,
items 104-106). Mismo patron que `tests/test_unified_activation_network_
isolation.py` (Fase 14A): bloquea `socket.socket.connect`/`socket.
create_connection`, lectura de variables de proveedor y lectura de
`.env` -- cualquier intento real lanza de inmediato, nunca una
simulacion pasiva."""

from __future__ import annotations

import ast
import inspect
import os
import socket
from pathlib import Path
from types import ModuleType

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.pipeline import (
    active_artifact_resolver,
    unified_activation_generation_builder,
    unified_activation_store,
    unified_activation_transition,
    unified_active_lane_router,
    unified_active_lane_service,
    unified_materialization_service,
    v1_activation_generation_builder,
)
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

_FORBIDDEN_PROVIDER_ENV_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "PWC_API_KEY",
        "PWC_GATEWAY_URL",
        "PWC_GATEWAY_TOKEN",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "ANTHROPIC_API_KEY",
        "LLM_PROVIDER",
    }
)


def _raise_socket_connect(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("intento de conexion de red prohibido (socket.socket.connect)")


def _raise_create_connection(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("intento de conexion de red prohibido (socket.create_connection)")


def _block_network_and_provider_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket.socket, "connect", _raise_socket_connect)
    monkeypatch.setattr(socket, "create_connection", _raise_create_connection)

    original_environ_get = os.environ.get

    def _guarded_environ_get(key: str, *args: object, **kwargs: object) -> object:
        if key in _FORBIDDEN_PROVIDER_ENV_KEYS:
            raise AssertionError(f"lectura prohibida de variable de proveedor: {key}")
        return original_environ_get(key, *args, **kwargs)

    monkeypatch.setattr(os.environ, "get", _guarded_environ_get)

    original_read_text = Path.read_text

    def _guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == ".env":
            raise AssertionError("lectura prohibida de .env")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _guarded_read_text)


def test_full_materialization_cycle_under_network_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_materialization_fixture()
    run_dir = write_run_dir(tmp_path, fx)
    eval_hash = evaluation_hash_of(run_dir)
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=fx.run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=fx.approved_group_ids,
    )

    _block_network_and_provider_secrets(monkeypatch)

    result_1 = materialize_unified_activation(run_dir, fx.run_id, authorization_path=auth_path)
    result_2 = materialize_unified_activation(run_dir, fx.run_id, authorization_path=auth_path)
    assert result_1.active_lane.value == "UNIFIED"
    assert result_2.idempotent is True


def test_cli_materialize_under_network_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_materialization_fixture()
    settings = Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
    )
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    run_dir = write_run_dir(settings.runs_dir, fx)
    eval_hash = evaluation_hash_of(run_dir)
    auth_path = tmp_path / "auth.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=fx.run_id,
        activation_evaluation_hash=eval_hash,
        action="ACTIVATE_UNIFIED_CANARY",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
        approved_group_ids=fx.approved_group_ids,
    )

    _block_network_and_provider_secrets(monkeypatch)

    result = runner.invoke(
        cli_module.app,
        ["unified-activation-materialize", fx.run_id, "--authorization", str(auth_path)],
    )
    assert result.exit_code == 0, result.stdout


_FASE_14B_MODULES: tuple[ModuleType, ...] = (
    active_artifact_resolver,
    unified_active_lane_router,
    unified_active_lane_service,
    unified_activation_generation_builder,
    unified_activation_store,
    unified_activation_transition,
    unified_materialization_service,
    v1_activation_generation_builder,
)

_FORBIDDEN_PROVIDER_MODULE_NAMES = frozenset({"llm_client", "httpx", "openai"})


def _imported_module_names(module: ModuleType) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module.split(".")[0])
    return names


# 104. no provider real (verificado estaticamente via AST).
def test_no_fase_14b_module_ever_imports_a_real_provider_client() -> None:
    for module in _FASE_14B_MODULES:
        imported = _imported_module_names(module)
        overlap = imported & _FORBIDDEN_PROVIDER_MODULE_NAMES
        assert not overlap, f"{module.__name__} importa un cliente de proveedor real: {overlap}"


def test_no_fase_14b_module_ever_imports_socket_or_requests() -> None:
    forbidden = frozenset({"socket", "requests", "urllib3"})
    for module in _FASE_14B_MODULES:
        imported = _imported_module_names(module)
        overlap = imported & forbidden
        assert not overlap, f"{module.__name__} importa un modulo de red: {overlap}"
