"""Prueba EJECUTABLE de aislamiento de red del control plane de
activacion unificada (Fase 14A, `feat/controlled-unified-activation`,
cierre solicitado). Bloquea `socket.socket.connect` y `socket.
create_connection` para que CUALQUIER intento de conexion real lance
de inmediato -- nunca una simulacion pasiva, nunca una aseveracion
"no deberia" sin verificacion en tiempo de ejecucion. Bajo ese bloqueo
(mas un bloqueo equivalente sobre variables de entorno de proveedor y
sobre la lectura de un archivo `.env`), ejercita selector de canary,
adaptadores, comparador, evaluador completo, servicio sobre un run
sintetico valido y el comando CLI in-process -- si CUALQUIERA de esos
componentes intentara abrir un socket, leer una credencial de
proveedor o leer `.env`, el test fallaria con la excepcion lanzada por
el guard, no con un simple mensaje de aserto.

Complementa (no reemplaza) los tests AST existentes de `tests/pipeline/
test_unified_activation_canary_selector.py` y `tests/pipeline/
test_unified_activation_negative_cases.py::
test_caso_h_no_fase_14a_module_ever_imports_a_real_provider_client`."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.unified_activation_config import (
    UnifiedActivationConfig,
    UnifiedActivationMode,
    UnifiedCanarySelectionStrategy,
    UnifiedFallbackPolicy,
)
from altamira_extractor.pipeline.unified_activation_canary_selector import select_canary
from altamira_extractor.pipeline.unified_activation_comparator import compare_references
from altamira_extractor.pipeline.unified_activation_evaluator import evaluate_unified_activation
from altamira_extractor.pipeline.unified_activation_reference_adapters import (
    adapt_unified_references,
    adapt_v1_references,
)
from altamira_extractor.pipeline.unified_activation_service import (
    compute_unified_activation_evaluation,
)

from .pipeline._unified_activation_fixtures import activation_golden_path
from .test_cli_unified_candidates_shadow import _RUN_ID, _write_parsed_run

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
    raise AssertionError(
        "intento de conexion de red prohibido en este test (socket.socket.connect)"
    )


def _raise_create_connection(*_args: object, **_kwargs: object) -> None:
    raise AssertionError(
        "intento de conexion de red prohibido en este test (socket.create_connection)"
    )


def _block_network_and_provider_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aplica los tres bloqueos simultaneamente: sockets, variables de
    entorno de proveedor y lectura de `.env`. Cualquier intento real
    lanza de inmediato -- nunca se degrada a un no-op silencioso."""
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


def test_network_isolation_canary_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network_and_provider_secrets(monkeypatch)
    config = UnifiedActivationConfig(
        mode=UnifiedActivationMode.UNIFIED_CANARY,
        canary_strategy=UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST,
        package_hash_allowlist=["a" * 64],
        fallback_policy=UnifiedFallbackPolicy.FALLBACK_TO_V1,
    )
    result = select_canary(config, source_package_hash="a" * 64, run_id="run-1")
    assert result.selected is True


def test_network_isolation_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network_and_provider_secrets(monkeypatch)
    gp = activation_golden_path()
    v1_references = adapt_v1_references(gp.v1_artifact)
    unified_references = adapt_unified_references(
        gp.unified_shadow, downstream=gp.downstream_artifact
    )
    assert v1_references != [] or unified_references != []


def test_network_isolation_comparator(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network_and_provider_secrets(monkeypatch)
    gp = activation_golden_path()
    v1_references = adapt_v1_references(gp.v1_artifact)
    unified_references = adapt_unified_references(
        gp.unified_shadow, downstream=gp.downstream_artifact
    )
    comparisons = compare_references(
        v1_references, unified_references, v1_available=True, unified_available=True
    )
    assert isinstance(comparisons, list)


def test_network_isolation_full_evaluator_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_network_and_provider_secrets(monkeypatch)
    gp = activation_golden_path()
    config = UnifiedActivationConfig(mode=UnifiedActivationMode.SHADOW_COMPARE)
    kwargs: dict[str, object] = {
        "run_id": gp.unified_shadow.run_id,
        "source_package_hash": gp.unified_shadow.source_package_hash,
        "config_hash": "c" * 64,
        "candidate_v1_artifact": gp.v1_artifact,
        "candidate_v1_artifact_hash": gp.unified_shadow.source_package_hash,
        "unified_shadow": gp.unified_shadow,
        "unified_candidates_shadow_hash": gp.unified_shadow.source_package_hash,
        "validation_report": gp.validation_report,
        "validation_report_hash": gp.unified_shadow.source_package_hash,
        "downstream_artifact": gp.downstream_artifact,
        "downstream_artifact_hash": gp.unified_shadow.source_package_hash,
    }
    artifact_1 = evaluate_unified_activation(config, **kwargs)  # type: ignore[arg-type]
    artifact_2 = evaluate_unified_activation(config, **kwargs)  # type: ignore[arg-type]
    assert artifact_1.to_stable_json() == artifact_2.to_stable_json()


def test_network_isolation_service_on_synthetic_run(
    patched_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("mode: V1_ONLY\n", encoding="utf-8")

    _block_network_and_provider_secrets(monkeypatch)

    artifact_1 = compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)
    artifact_2 = compute_unified_activation_evaluation(run_dir, _RUN_ID, config_path=config_path)

    assert artifact_1.readiness_disposition.value == "V1_ONLY_READY"
    assert artifact_1.to_stable_json() == artifact_2.to_stable_json()


def test_network_isolation_cli_success_in_process(
    patched_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`CliRunner.invoke` corre el comando DENTRO del mismo proceso de
    test (nunca un subprocess) -- el bloqueo de socket aplicado via
    `monkeypatch` cubre tambien esta invocacion."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_parsed_run(run_dir)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("mode: V1_ONLY\n", encoding="utf-8")

    _block_network_and_provider_secrets(monkeypatch)

    result = runner.invoke(
        cli_module.app, ["unified-activation-evaluate", _RUN_ID, "--config", str(config_path)]
    )

    assert result.exit_code == 0, result.stdout
    assert "readiness_disposition: V1_ONLY_READY" in result.stdout
    assert "materialization_enabled: False" in result.stdout
