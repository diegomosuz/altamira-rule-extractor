"""Tests de `GET /api/operations/component-diagnostics` (Fase 15B2-B,
Seccion 12): permiso `VIEW_SECURITY_STATUS`, 11 componentes, nunca
expone hostname/URI/usuario/token/path absoluto/texto de excepcion."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings
from altamira_extractor.contracts.security_config import (
    ApplicationRole,
    AuthenticationMode,
    SecurityConfig,
)
from altamira_extractor.pipeline import llm_client as llm_client_module
from tests.e2e_support import write_disabled_dev_security_config

_MARKER_HEADER = "X-Trusted-Proxy"
_MARKER_VALUE = "test-marker"
_USER_HEADER = "X-Verified-User"
_GROUPS_HEADER = "X-Verified-Groups"

_PATH = "/api/operations/component-diagnostics"


@pytest.fixture
def fake_jar(tmp_path: Path) -> Path:
    jar = tmp_path / "fake-parser.jar"
    jar.write_bytes(b"placeholder")
    return jar


def _base_settings(tmp_path: Path, *, parser_jar_path: Path) -> Settings:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        runs_dir=runs_dir,
        incoming_dir=tmp_path / "incoming",
        security_config_path=write_disabled_dev_security_config(tmp_path),
        parser_jar_path=parser_jar_path,
        neo4j_uri="bolt://does-not-resolve.invalid:7687",
    )


def _trusted_config(*, groups: dict[str, ApplicationRole]) -> SecurityConfig:
    return SecurityConfig(
        authentication_mode=AuthenticationMode.TRUSTED_PROXY_HEADERS,
        trusted_proxy_header_user=_USER_HEADER,
        trusted_proxy_header_groups=_GROUPS_HEADER,
        trusted_proxy_required_marker_header=_MARKER_HEADER,
        trusted_proxy_required_marker_value=SecretStr(_MARKER_VALUE),
        trusted_proxy_allowed_roles=list(set(groups.values())),
        group_role_mapping=groups,
        session_cookie_name="altamira_session",
        session_cookie_secure=True,
    )


def _trusted_client(
    settings: Settings, *, groups: dict[str, ApplicationRole]
) -> Iterator[TestClient]:
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        client.app.state.security_config = _trusted_config(groups=groups)  # type: ignore[attr-defined]
        client.app.state.security_misconfigured = False  # type: ignore[attr-defined]
        client.app.state.session_secret = SecretStr(  # type: ignore[attr-defined]
            "test-only-synthetic-session-secret-32chars-min"
        )
        client.headers.update({"Origin": "https://testserver"})
        yield client


@pytest.fixture
def operator_client(tmp_path: Path, fake_jar: Path) -> Iterator[TestClient]:
    settings = _base_settings(tmp_path, parser_jar_path=fake_jar)
    yield from _trusted_client(settings, groups={"operators": ApplicationRole.OPERATOR})


@pytest.fixture
def viewer_client(tmp_path: Path, fake_jar: Path) -> Iterator[TestClient]:
    settings = _base_settings(tmp_path, parser_jar_path=fake_jar)
    yield from _trusted_client(settings, groups={"viewers": ApplicationRole.VIEWER})


def _as(client: TestClient, user: str, group: str) -> None:
    client.headers.update(
        {_MARKER_HEADER: _MARKER_VALUE, _USER_HEADER: user, _GROUPS_HEADER: group}
    )


def test_forbidden_without_view_security_status_permission(viewer_client: TestClient) -> None:
    _as(viewer_client, "viewer1", "viewers")
    response = viewer_client.get(_PATH)
    assert response.status_code == 403


def test_operator_can_access_diagnostics(operator_client: TestClient) -> None:
    _as(operator_client, "op1", "operators")
    response = operator_client.get(_PATH)
    assert response.status_code == 200


def test_reports_exactly_eleven_known_components(operator_client: TestClient) -> None:
    _as(operator_client, "op1", "operators")
    body = operator_client.get(_PATH).json()
    component_ids = {c["component_id"] for c in body["components"]}
    assert component_ids == {
        "security_configuration",
        "parser_jar",
        "data_root",
        "executor",
        "neo4j_configuration",
        "neo4j_connectivity",
        "metrics",
        "logging",
        "functional_validation_configuration",
        "release_readiness_configuration",
        "provider_configuration",
    }


def test_parser_jar_reported_ready_when_present(operator_client: TestClient) -> None:
    _as(operator_client, "op1", "operators")
    body = operator_client.get(_PATH).json()
    component = next(c for c in body["components"] if c["component_id"] == "parser_jar")
    assert component["status"] == "READY"
    assert component["reason_code"] == "parser_jar_present"


def test_neo4j_connectivity_reported_not_ready_when_unreachable(
    operator_client: TestClient,
) -> None:
    """Sin un Neo4j real disponible en el entorno de test, la
    conectividad debe reportarse NOT_READY, nunca lanzar ni bloquear la
    respuesta mas alla del timeout corto configurado."""
    _as(operator_client, "op1", "operators")
    body = operator_client.get(_PATH).json()
    component = next(c for c in body["components"] if c["component_id"] == "neo4j_connectivity")
    assert component["status"] in ("NOT_READY", "DEGRADED")
    assert component["reason_code"] in (
        "neo4j_unreachable",
        "neo4j_timeout",
        "neo4j_authentication_failed",
    )


def test_provider_configuration_disabled_when_no_provider_set(operator_client: TestClient) -> None:
    _as(operator_client, "op1", "operators")
    body = operator_client.get(_PATH).json()
    component = next(c for c in body["components"] if c["component_id"] == "provider_configuration")
    assert component["status"] == "NOT_APPLICABLE"
    assert component["reason_code"] == "provider_disabled"


def test_provider_configuration_misconfigured_when_provider_set_without_credentials(
    tmp_path: Path, fake_jar: Path
) -> None:
    settings = _base_settings(tmp_path, parser_jar_path=fake_jar).model_copy(
        update={"llm_provider": "openai"}
    )
    for client in _trusted_client(settings, groups={"operators": ApplicationRole.OPERATOR}):
        _as(client, "op1", "operators")
        body = client.get(_PATH).json()
        component = next(
            c for c in body["components"] if c["component_id"] == "provider_configuration"
        )
        assert component["status"] == "NOT_READY"
        assert component["reason_code"] == "provider_misconfigured"


def test_response_never_exposes_absolute_paths_or_neo4j_uri(operator_client: TestClient) -> None:
    _as(operator_client, "op1", "operators")
    response = operator_client.get(_PATH)
    assert "does-not-resolve.invalid" not in response.text
    assert "bolt://" not in response.text


def test_data_root_and_executor_ready_in_normal_operation(operator_client: TestClient) -> None:
    _as(operator_client, "op1", "operators")
    body = operator_client.get(_PATH).json()
    statuses = {c["component_id"]: c["status"] for c in body["components"]}
    assert statuses["data_root"] == "READY"
    assert statuses["executor"] == "READY"
    assert statuses["logging"] == "READY"


def test_metrics_component_not_applicable_when_disabled_by_default(
    operator_client: TestClient,
) -> None:
    _as(operator_client, "op1", "operators")
    body = operator_client.get(_PATH).json()
    component = next(c for c in body["components"] if c["component_id"] == "metrics")
    assert component["status"] == "NOT_APPLICABLE"
    assert component["reason_code"] == "metrics_disabled"


def test_every_component_carries_the_three_required_for_booleans(
    operator_client: TestClient,
) -> None:
    _as(operator_client, "op1", "operators")
    body = operator_client.get(_PATH).json()
    for component in body["components"]:
        assert isinstance(component["required_for_ingestion"], bool)
        assert isinstance(component["required_for_query"], bool)
        assert isinstance(component["required_for_operational_actions"], bool)
        assert component["checked_at_utc"]


# --- Cierre correctivo, Seccion 5: diagnostico de proveedor sin red ---------

_PROVIDER_BASE_URL = "https://provider.altamira-diagnostics-test.invalid/v1"
_PROVIDER_API_KEY = "sk-synthetic-diagnostics-test-key-never-real"


def _configured_provider_settings(tmp_path: Path, fake_jar: Path) -> Settings:
    return _base_settings(tmp_path, parser_jar_path=fake_jar).model_copy(
        update={
            "llm_provider": "openai",
            "openai_base_url": _PROVIDER_BASE_URL,
            "openai_api_key": SecretStr(_PROVIDER_API_KEY),
            "openai_model": "gpt-4o-mini",
        }
    )


def test_provider_configuration_configured_when_fully_set_up(
    tmp_path: Path, fake_jar: Path
) -> None:
    settings = _configured_provider_settings(tmp_path, fake_jar)
    for client in _trusted_client(settings, groups={"operators": ApplicationRole.OPERATOR}):
        _as(client, "op1", "operators")
        body = client.get(_PATH).json()
        component = next(
            c for c in body["components"] if c["component_id"] == "provider_configuration"
        )
        assert component["status"] == "READY"
        assert component["reason_code"] == "provider_configured"


def test_provider_diagnostics_never_instantiates_the_chat_client(
    tmp_path: Path, fake_jar: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`resolve_llm_profile` (reutilizado por el diagnostico) SOLO
    valida forma/presencia de campos -- nunca construye el transporte
    HTTP real. Si `OpenAICompatibleChatClient.__init__` llegara a
    invocarse, este test lo detecta y falla."""

    def _fail_if_instantiated(self: object, *args: object, **kwargs: object) -> None:
        raise AssertionError(
            "OpenAICompatibleChatClient no debe instanciarse durante el diagnostico"
        )

    monkeypatch.setattr(
        llm_client_module.OpenAICompatibleChatClient, "__init__", _fail_if_instantiated
    )

    settings = _configured_provider_settings(tmp_path, fake_jar)
    for client in _trusted_client(settings, groups={"operators": ApplicationRole.OPERATOR}):
        _as(client, "op1", "operators")
        response = client.get(_PATH)
        assert response.status_code == 200
        component = next(
            c
            for c in response.json()["components"]
            if c["component_id"] == "provider_configuration"
        )
        assert component["status"] == "READY"


def test_provider_diagnostics_never_constructs_an_http_transport(
    tmp_path: Path, fake_jar: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defensa adicional e independiente de la anterior: ni siquiera un
    `httpx.AsyncClient` crudo debe construirse -- prueba que no hay
    ningun camino alternativo hacia el transporte HTTP real que usa
    `OpenAICompatibleChatClient` (sockets, DNS) durante el diagnostico
    de proveedor. NUNCA se parchea `httpx.Client` (sincrono): el propio
    `TestClient` de starlette esta construido sobre el, parchearlo
    romperia la fixture antes de llegar al request real -- el mismo
    matiz que el guard de sockets en `test_readiness_extended.py`."""

    def _fail_async(self: object, *args: object, **kwargs: object) -> None:
        raise AssertionError("httpx.AsyncClient no debe construirse durante el diagnostico")

    settings = _configured_provider_settings(tmp_path, fake_jar)
    for client in _trusted_client(settings, groups={"operators": ApplicationRole.OPERATOR}):
        _as(client, "op1", "operators")
        monkeypatch.setattr(httpx.AsyncClient, "__init__", _fail_async)
        response = client.get(_PATH)
        assert response.status_code == 200


def test_provider_diagnostics_never_exposes_base_url_or_api_key(
    tmp_path: Path, fake_jar: Path
) -> None:
    settings = _configured_provider_settings(tmp_path, fake_jar)
    for client in _trusted_client(settings, groups={"operators": ApplicationRole.OPERATOR}):
        _as(client, "op1", "operators")
        response = client.get(_PATH)
        assert _PROVIDER_BASE_URL not in response.text
        assert _PROVIDER_API_KEY not in response.text
        assert "provider.altamira-diagnostics-test.invalid" not in response.text


def test_provider_diagnostics_misconfigured_never_exposes_exception_text(
    tmp_path: Path, fake_jar: Path
) -> None:
    """Proveedor configurado con datos incompletos (falta `model`):
    MISCONFIGURED, pero el mensaje de `LlmConfigurationError` nunca se
    refleja en la respuesta -- solo el `reason_code` cerrado."""
    settings = _base_settings(tmp_path, parser_jar_path=fake_jar).model_copy(
        update={
            "llm_provider": "openai",
            "openai_base_url": _PROVIDER_BASE_URL,
            "openai_api_key": SecretStr(_PROVIDER_API_KEY),
            "openai_model": None,
        }
    )
    for client in _trusted_client(settings, groups={"operators": ApplicationRole.OPERATOR}):
        _as(client, "op1", "operators")
        response = client.get(_PATH)
        body = response.json()
        component = next(
            c for c in body["components"] if c["component_id"] == "provider_configuration"
        )
        assert component["status"] == "NOT_READY"
        assert component["reason_code"] == "provider_misconfigured"
        assert "falta" not in response.text
        assert "model" not in json.dumps(component)
