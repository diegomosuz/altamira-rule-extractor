"""Prueba de aislamiento EJECUTABLE de la capa de escritura del
gobierno operativo (Fase 15B1 Parte 17, `feat/final-hardening-release`).

Bloquea, con bloqueos ACTIVOS (nunca una simulacion pasiva) durante la
ejecucion de resolucion de identidad, RBAC, generacion de CSRF,
prepare, confirm, ejecucion de canary, fallback, rollback, lectura de
auditoria, UI y `TestClient`:

- `socket.socket.connect` / `socket.create_connection` (cero red, ni
  siquiera un intento fallido);
- lectura de `.env` (por nombre de archivo, en cualquier ruta);
- lectura de variables de entorno de proveedor LLM
  (`OPENAI_API_KEY`/`OPENAI_BASE_URL`/`OPENAI_MODEL`/
  `PWC_GENAI_API_KEY`/`PWC_GENAI_BASE_URL`/`PWC_GENAI_MODEL`/
  `LLM_PROVIDER`);
- inicializacion de un cliente HTTP externo (`httpx.AsyncClient`/
  `httpx.Client`) -- ningun paso de esta superficie necesita uno.

Deliberadamente NO bloquea escrituras bajo `activation/`/`audit/`
dentro del `run_dir` de prueba (`prepare`/`execute` SI escriben ahi por
diseno) -- en su lugar verifica que NINGUNA escritura ocurre FUERA del
`run_dir`, y ademas incluye una verificacion estatica AST (nunca
substring sobre texto crudo, que produce falsos positivos contra
documentacion) de que `security/*.py` y el router de escritura nunca
importan un cliente de proveedor."""

from __future__ import annotations

import ast
import hashlib
import inspect
import os
import re
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage
from altamira_extractor.contracts.run_state import RunState
from altamira_extractor.contracts.security_config import (
    ApplicationRole,
    AuthenticationMode,
    SecurityConfig,
)
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore
from altamira_extractor.pipeline.unified_materialization_service import (
    materialize_unified_activation,
)
from altamira_extractor.security import (
    correlation,
    csrf,
    fastapi_deps,
    identity_resolver,
    operational_audit_service,
    operational_challenge,
    operational_workflow,
    session,
)
from altamira_extractor.ui import governance_actions_router

from .pipeline._unified_materialization_fixtures import (
    build_materialization_fixture,
    write_authorization_yaml,
    write_run_dir,
)

_FORBIDDEN_PROVIDER_TOKENS = (
    "httpx",
    "openai",
    "OpenAI",
    "requests",
    "urllib.request",
    "AsyncClient",
)

_PROVIDER_ENV_VARS = (
    "LLM_PROVIDER",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "PWC_GENAI_API_KEY",
    "PWC_GENAI_BASE_URL",
    "PWC_GENAI_MODEL",
)

_SECURITY_MODULES = (
    correlation,
    csrf,
    fastapi_deps,
    identity_resolver,
    operational_audit_service,
    operational_challenge,
    operational_workflow,
    session,
    governance_actions_router,
)


def _imported_names(module: object) -> set[str]:
    tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(alias.name for alias in node.names)
    return names


# 1. Verificacion estatica: ningun modulo de escritura de gobierno importa un cliente HTTP.
def test_no_security_module_imports_a_provider_client() -> None:
    for module in _SECURITY_MODULES:
        imported = _imported_names(module)
        offenders = imported & set(_FORBIDDEN_PROVIDER_TOKENS)
        assert not offenders, f"{module.__name__} importa {offenders}"


_REAL_SOCKET_CONNECT = socket.socket.connect
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _raise_socket_connect(
    self: socket.socket, address: object, *args: object, **kwargs: object
) -> object:
    """Bloquea por DESTINO, nunca por mecanismo (cierre correctivo
    15B2-B, Seccion 7): en Windows, `asyncio.ProactorEventLoop` abre un
    self-pipe interno via `socket.socketpair()`, que interna mente usa
    `socket.connect()` de loopback -- eso es IPC del runtime, nunca una
    conexion de red externa real, y `TestClient.__enter__` lo dispara
    al construir su event loop. Loopback se deja pasar; cualquier otro
    destino sigue bloqueado."""
    host = address[0] if isinstance(address, tuple) and address else None
    if host in _LOOPBACK_HOSTS:
        return _REAL_SOCKET_CONNECT(self, address, *args, **kwargs)
    raise AssertionError("intento de conexion de red prohibido (socket.socket.connect)")


def _raise_create_connection(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("intento de conexion de red prohibido (socket.create_connection)")


# Regresion Windows (cierre correctivo 15B2-B, Seccion 7): ver la misma
# nota en `test_operational_governance_isolation.py`.
def test_socket_guard_allows_loopback_but_blocks_external_destination() -> None:
    dummy_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(AssertionError, match="intento de conexion de red prohibido"):
            _raise_socket_connect(dummy_socket, ("93.184.216.34", 443))
    finally:
        dummy_socket.close()

    probe_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            _raise_socket_connect(probe_socket, ("127.0.0.1", 1))
    finally:
        probe_socket.close()


def _raise_httpx_client_init(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("inicializacion de cliente HTTP externo prohibida")


def _install_env_var_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    original_get = os.environ.get

    def guarded_get(key: str, default: object = None) -> object:
        if key in _PROVIDER_ENV_VARS:
            raise AssertionError(f"lectura de variable de proveedor LLM prohibida: {key}")
        return original_get(key, default)

    monkeypatch.setattr(os.environ, "get", guarded_get)


def _install_dotenv_read_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes

    def guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == ".env":
            raise AssertionError("lectura de .env prohibida")
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    def guarded_read_bytes(self: Path, *args: object, **kwargs: object) -> bytes:
        if self.name == ".env":
            raise AssertionError("lectura de .env prohibida")
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)


def _within(path: str | os.PathLike[str], run_dir: Path) -> bool:
    try:
        candidate = Path(path).resolve()
    except (OSError, TypeError, ValueError):
        return False
    return candidate == run_dir or run_dir in candidate.parents


def _install_outside_write_guard(monkeypatch: pytest.MonkeyPatch, run_dir: Path) -> None:
    """INVERSO del guard de Fase 15A: aqui SI se permiten escrituras
    dentro de `run_dir` (`prepare`/`execute` escriben legitimamente bajo
    `activation/`/`audit/`) -- solo se bloquean escrituras FUERA."""
    run_dir = run_dir.resolve()
    original_write_text = Path.write_text
    original_write_bytes = Path.write_bytes

    def guarded_write_text(self: Path, *args: object, **kwargs: object) -> int:
        if not _within(self, run_dir):
            raise AssertionError(f"Path.write_text fuera del run prohibido: {self}")
        return original_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

    def guarded_write_bytes(self: Path, *args: object, **kwargs: object) -> int:
        if not _within(self, run_dir):
            raise AssertionError(f"Path.write_bytes fuera del run prohibido: {self}")
        return original_write_bytes(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", guarded_write_text)
    monkeypatch.setattr(Path, "write_bytes", guarded_write_bytes)


def _install_all_guards(monkeypatch: pytest.MonkeyPatch, run_dir: Path) -> None:
    monkeypatch.setattr(socket.socket, "connect", _raise_socket_connect)
    monkeypatch.setattr(socket, "create_connection", _raise_create_connection)
    _install_env_var_guard(monkeypatch)
    _install_dotenv_read_guard(monkeypatch)
    _install_outside_write_guard(monkeypatch, run_dir)
    try:
        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _raise_httpx_client_init)
        monkeypatch.setattr(httpx, "Client", _raise_httpx_client_init)
    except ImportError:
        pass


_MARKER_HEADER = "X-Trusted-Proxy"
_MARKER_VALUE = "isolation-marker"
_USER_HEADER = "X-Verified-User"
_GROUPS_HEADER = "X-Verified-Groups"


def _trusted_config() -> SecurityConfig:
    return SecurityConfig(
        authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS,
        trusted_proxy_header_user=_USER_HEADER,
        trusted_proxy_header_groups=_GROUPS_HEADER,
        trusted_proxy_required_marker_header=_MARKER_HEADER,
        trusted_proxy_required_marker_value=SecretStr(_MARKER_VALUE),
        trusted_proxy_allowed_roles=[ApplicationRole.OPERATOR],
        group_role_mapping={"operators": ApplicationRole.OPERATOR},
        session_cookie_name="altamira_session",
        session_cookie_secure=True,
    )


@pytest.fixture
def isolated_run(tmp_path: Path) -> tuple[Settings, str]:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (tmp_path / "incoming").mkdir()

    fx = build_materialization_fixture()
    run_dir = write_run_dir(runs_dir, fx)
    eval_path = run_dir / "diagnostics" / "unified-activation-evaluation.json"
    eval_hash = hashlib.sha256(eval_path.read_bytes()).hexdigest()
    auth_path = tmp_path / "bootstrap.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=fx.run_id,
        activation_evaluation_hash=eval_hash,
        action="KEEP_V1",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
    )
    materialize_unified_activation(run_dir, fx.run_id, authorization_path=auth_path)
    atomic_write_json(
        run_dir / "run.json",
        RunState(
            run_id=fx.run_id,
            package_filename="package.zip",
            current_stage=PipelineStage.COMPLETED,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
    )
    return Settings(runs_dir=runs_dir, incoming_dir=tmp_path / "incoming"), fx.run_id


def test_full_write_surface_under_network_and_provider_block(
    isolated_run: tuple[Settings, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, run_id = isolated_run
    run_dir = settings.runs_dir / run_id

    _install_all_guards(monkeypatch, run_dir)

    with TestClient(create_app(settings), base_url="https://testserver") as client:
        client.app.state.security_config = _trusted_config()  # type: ignore[attr-defined]
        # Cierre F15B1 ("DISABLED_DEV explicito") -- ver la misma nota
        # en `test_operational_governance_actions_integration.py`.
        client.app.state.security_misconfigured = False  # type: ignore[attr-defined]
        # Ver la misma nota en
        # `test_operational_governance_actions_integration.py`.
        client.app.state.session_secret = SecretStr(  # type: ignore[attr-defined]
            "test-only-synthetic-session-secret-32chars-min"
        )
        client.headers.update(
            {
                "Origin": "https://testserver",
                _MARKER_HEADER: _MARKER_VALUE,
                _USER_HEADER: "operator1",
                _GROUPS_HEADER: "operators",
            }
        )

        # 1. Resolucion de identidad + RBAC + generacion de CSRF (via GET form).
        form = client.get(f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY")
        assert form.status_code == 200
        csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', form.text)
        group_ids = re.findall(r'name="approved_group_ids" value="([^"]+)"', form.text)
        assert csrf_token is not None

        # 2. prepare.
        prepare = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/prepare",
            data={
                "csrf_token": csrf_token.group(1),
                "reason_code": "CANARY_APPROVED",
                "review_reference": "isolation-ticket",
                "approved_group_ids": group_ids,
                "target_generation_id": "",
            },
            follow_redirects=False,
        )
        assert prepare.status_code == 303

        # 3. confirm.
        confirm = client.get(prepare.headers["location"])
        assert confirm.status_code == 200
        csrf_token_2 = re.search(r'name="csrf_token" value="([^"]+)"', confirm.text)
        challenge_token = re.search(r'name="challenge_token" value="([^"]+)"', confirm.text)
        assert csrf_token_2 is not None
        assert challenge_token is not None

        # 4. ejecucion de canary.
        execute = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/execute",
            data={
                "csrf_token": csrf_token_2.group(1),
                "challenge_token": challenge_token.group(1),
            },
            follow_redirects=False,
        )
        assert execute.status_code == 303

        # 5. lectura de auditoria + UI de gobierno de solo lectura (Fase 15A).
        audit = client.get(f"/ui/runs/{run_id}/governance/audit")
        assert audit.status_code == 200
        assert "ACTIVATION_CANARY_SUCCEEDED" in audit.text

        governance_page = client.get(f"/ui/runs/{run_id}/governance")
        assert governance_page.status_code == 200

    # Confirma que la transicion realmente ocurrio (operacion local correcta,
    # no solo "no crasheo").
    pointer = UnifiedActivationStore(run_dir).read_active_pointer()
    assert pointer is not None
    assert pointer.active_lane.value == "UNIFIED"
