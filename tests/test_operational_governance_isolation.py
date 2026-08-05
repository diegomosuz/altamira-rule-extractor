"""Prueba de aislamiento EJECUTABLE de la capa de gobierno operativo
(Fase 15A Parte 13, `feat/operational-governance-ui`).

Bloquea, con bloqueos ACTIVOS (nunca una simulacion pasiva) durante la
ejecucion del reader, el adapter de grupos, la API JSON, la pagina
HTML, un fragment HTMX y las descargas GET/HEAD:

- `socket.socket.connect` / `socket.create_connection` (cero red);
- `pathlib.Path.write_text` / `write_bytes` / `unlink` / `rename` /
  `replace` -- UNICAMENTE dentro del `run_dir` de prueba (nunca
  globalmente: escrituras legitimas de Starlette/pytest sobre objetos
  en memoria o temporales EXTERNOS al run nunca se bloquean, tal como
  exige el enunciado);
- `os.replace` -- misma restriccion de alcance;
- `pipeline.artifact_store.atomic_write_json` (cero escritura atomica,
  sin importar el destino);
- las 7 funciones de transicion de Fase 14B (`initialize_v1`,
  `activate_unified_canary`, `activate_unified_primary`,
  `fallback_to_v1`, `rollback_to_previous`, `rollback_to_generation`,
  `keep_current`) -- cero transicion;
- `unified_active_lane_service.resolve_with_fallback` -- cero fallback
  ejecutable.

La lectura de archivos EXISTENTES sigue funcionando normalmente
(`open`/`read_bytes`/`read_text` nunca se bloquean) -- solo las
escrituras."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings
from altamira_extractor.pipeline.operational_governance_group_adapter import (
    build_unified_group_summaries,
)
from altamira_extractor.pipeline.operational_governance_reader import (
    build_operational_governance_overview,
)
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore

from .e2e_support import write_disabled_dev_security_config
from .pipeline._operational_governance_fixtures import (
    build_materialization_fixture,
    governance_run_dir,
    materialize_unified_canary,
)


def _raise_socket_connect(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("intento de conexion de red prohibido (socket.socket.connect)")


def _raise_create_connection(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("intento de conexion de red prohibido (socket.create_connection)")


def _raise_transition_call(name: str) -> Any:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(f"transicion prohibida desde el read model de gobierno: {name}")

    return _raise


def _raise_fallback_call(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("fallback ejecutable prohibido desde el read model de gobierno")


def _raise_atomic_write(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("escritura atomica prohibida desde el read model de gobierno")


def _within(path: str | os.PathLike[str], run_dir: Path) -> bool:
    try:
        candidate = Path(path).resolve()
    except (OSError, TypeError, ValueError):
        return False
    return candidate == run_dir or run_dir in candidate.parents


def _install_write_guards(monkeypatch: pytest.MonkeyPatch, run_dir: Path) -> None:
    run_dir = run_dir.resolve()

    original_write_text = Path.write_text
    original_write_bytes = Path.write_bytes
    original_unlink = Path.unlink
    original_rename = Path.rename
    original_replace_path = Path.replace
    original_os_replace = os.replace

    def guarded_write_text(self: Path, *args: object, **kwargs: object) -> int:
        if _within(self, run_dir):
            raise AssertionError(f"Path.write_text prohibido dentro del run: {self}")
        return original_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

    def guarded_write_bytes(self: Path, *args: object, **kwargs: object) -> int:
        if _within(self, run_dir):
            raise AssertionError(f"Path.write_bytes prohibido dentro del run: {self}")
        return original_write_bytes(self, *args, **kwargs)  # type: ignore[arg-type]

    def guarded_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if _within(self, run_dir):
            raise AssertionError(f"Path.unlink prohibido dentro del run: {self}")
        original_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

    def guarded_rename(self: Path, *args: object, **kwargs: object) -> Path:
        if _within(self, run_dir):
            raise AssertionError(f"Path.rename prohibido dentro del run: {self}")
        return original_rename(self, *args, **kwargs)  # type: ignore[arg-type]

    def guarded_replace_path(self: Path, *args: object, **kwargs: object) -> Path:
        if _within(self, run_dir):
            raise AssertionError(f"Path.replace prohibido dentro del run: {self}")
        return original_replace_path(self, *args, **kwargs)  # type: ignore[arg-type]

    def guarded_os_replace(
        src: str | os.PathLike[str], dst: str | os.PathLike[str], *args: object, **kwargs: object
    ) -> None:
        if _within(src, run_dir) or _within(dst, run_dir):
            raise AssertionError(f"os.replace prohibido dentro del run: {src} -> {dst}")
        original_os_replace(src, dst, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", guarded_write_text)
    monkeypatch.setattr(Path, "write_bytes", guarded_write_bytes)
    monkeypatch.setattr(Path, "unlink", guarded_unlink)
    monkeypatch.setattr(Path, "rename", guarded_rename)
    monkeypatch.setattr(Path, "replace", guarded_replace_path)
    monkeypatch.setattr(os, "replace", guarded_os_replace)


def _install_all_guards(monkeypatch: pytest.MonkeyPatch, run_dir: Path) -> None:
    monkeypatch.setattr(socket.socket, "connect", _raise_socket_connect)
    monkeypatch.setattr(socket, "create_connection", _raise_create_connection)

    monkeypatch.setattr(
        "altamira_extractor.pipeline.artifact_store.atomic_write_json", _raise_atomic_write
    )

    # Fase 15A (reader/adapter/API/UI) NUNCA importa estas 7 funciones
    # (confirmado: ningun `from .unified_activation_transition import
    # ...` en ninguno de sus modulos) -- se parchea la unica ubicacion
    # real (el modulo fuente) como red de seguridad: si algun cambio
    # futuro agregara una llamada directa, este test la detectaria de
    # inmediato.
    for name in (
        "initialize_v1",
        "activate_unified_canary",
        "activate_unified_primary",
        "fallback_to_v1",
        "rollback_to_previous",
        "rollback_to_generation",
        "keep_current",
    ):
        monkeypatch.setattr(
            f"altamira_extractor.pipeline.unified_activation_transition.{name}",
            _raise_transition_call(name),
        )

    monkeypatch.setattr(
        "altamira_extractor.pipeline.unified_active_lane_service.resolve_with_fallback",
        _raise_fallback_call,
    )

    _install_write_guards(monkeypatch, run_dir)


def _settings_for(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir,
        runs_dir=data_dir / "runs",
        incoming_dir=data_dir / "incoming",
        security_config_path=write_disabled_dev_security_config(tmp_path),
    )
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    return settings


def test_full_governance_surface_under_write_and_network_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)

    store = UnifiedActivationStore(run_dir)
    pointer_before = store.read_active_pointer()
    assert pointer_before is not None
    active_manifest = store.read_generation_manifest(pointer_before.active_generation_id)

    before_snapshot = {
        p.relative_to(run_dir).as_posix(): p.read_bytes()
        for p in sorted(run_dir.rglob("*"))
        if p.is_file()
    }

    _install_all_guards(monkeypatch, run_dir)

    # 1. reader.
    overview = build_operational_governance_overview(run_dir, fx.run_id)
    assert overview.status.value == "HEALTHY_UNIFIED"

    # 2. adapter de grupos.
    groups = build_unified_group_summaries(store, active_manifest)
    assert len(groups) == 1

    with TestClient(create_app(settings)) as client:
        # 3. overview API.
        api_response = client.get(f"/api/runs/{fx.run_id}/governance")
        assert api_response.status_code == 200

        # 4. pagina HTML.
        html_response = client.get(f"/ui/runs/{fx.run_id}/governance")
        assert html_response.status_code == 200

        # 5. fragment HTMX.
        fragment_response = client.get(f"/ui/runs/{fx.run_id}/governance/generations-fragment")
        assert fragment_response.status_code == 200

        # 6. descarga HEAD.
        head_response = client.head(f"/api/runs/{fx.run_id}/governance/artifacts/candidates")
        assert head_response.status_code == 200

        # 7. descarga GET.
        get_response = client.get(f"/api/runs/{fx.run_id}/governance/artifacts/candidates")
        assert get_response.status_code == 200
        assert get_response.content

    # Cero escritura real: el run_dir permanece byte a byte identico.
    after_snapshot = {
        p.relative_to(run_dir).as_posix(): p.read_bytes()
        for p in sorted(run_dir.rglob("*"))
        if p.is_file()
    }
    assert before_snapshot == after_snapshot

    # Cero transicion: el puntero activo permanece exactamente igual.
    pointer_after = store.read_active_pointer()
    assert pointer_after is not None
    assert pointer_before.to_stable_json() == pointer_after.to_stable_json()
