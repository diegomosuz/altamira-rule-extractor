"""Tests HTTP end-to-end de la UI de gobierno operativo (Fase 15B1
Parte 16, secciones API/UI 95-112 y HEADERS 113-120,
`feat/final-hardening-release`).

Usa `base_url="https://testserver"` deliberadamente: httpx respeta el
atributo `Secure` de una cookie al decidir si la reenvia en la
siguiente request (incluso contra el transporte ASGI en memoria de
`TestClient`, sin TLS real) -- con `http://testserver` una cookie
`Secure=true` se fija pero nunca se reenvia, lo que rompe cualquier
flujo multi-request que dependa de sesion. `TRUSTED_PROXY_HEADERS`
exige `session_cookie_secure=true` (Parte 2), asi que este es el unico
`base_url` que permite probar el flujo completo con cookies reales."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
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
from altamira_extractor.pipeline.unified_materialization_service import (
    materialize_unified_activation,
)
from tests.pipeline._unified_materialization_fixtures import (
    build_materialization_fixture,
    write_authorization_yaml,
    write_run_dir,
)

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
_CHALLENGE_RE = re.compile(r'name="challenge_token" value="([^"]+)"')

_MARKER_HEADER = "X-Trusted-Proxy"
_MARKER_VALUE = "test-marker"
_USER_HEADER = "X-Verified-User"
_GROUPS_HEADER = "X-Verified-Groups"


@pytest.fixture
def run_setup(tmp_path: Path) -> tuple[Settings, str]:
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
    settings = Settings(runs_dir=runs_dir, incoming_dir=tmp_path / "incoming")
    return settings, fx.run_id


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


@pytest.fixture
def trusted_client(run_setup: tuple[Settings, str]) -> Iterator[TestClient]:
    settings, _run_id = run_setup
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        client.app.state.security_config = _trusted_config(  # type: ignore[attr-defined]
            groups={"reviewers": ApplicationRole.REVIEWER, "operators": ApplicationRole.OPERATOR}
        )
        # Cierre F15B1 ("DISABLED_DEV explicito") -- ver la misma nota
        # en `test_operational_governance_actions_integration.py`.
        client.app.state.security_misconfigured = False  # type: ignore[attr-defined]
        # Ver la misma nota en
        # `test_operational_governance_actions_integration.py`.
        client.app.state.session_secret = SecretStr(  # type: ignore[attr-defined]
            "test-only-synthetic-session-secret-32chars-min"
        )
        client.headers.update({"Origin": "https://testserver"})
        yield client


def _as(client: TestClient, user: str, group: str) -> None:
    client.headers.update(
        {_MARKER_HEADER: _MARKER_VALUE, _USER_HEADER: user, _GROUPS_HEADER: group}
    )


# 95. GET actions list responde 200 y expone acciones permitidas segun rol.
def test_actions_list_ok_for_operator(
    trusted_client: TestClient, run_setup: tuple[Settings, str]
) -> None:
    _settings, run_id = run_setup
    _as(trusted_client, "op1", "operators")
    response = trusted_client.get(f"/ui/runs/{run_id}/governance/actions")
    assert response.status_code == 200
    assert "ACTIVATE_UNIFIED_CANARY" in response.text


# 96. Un VIEWER (sin grupo mapeado) no ve acciones y un POST directo se rechaza con 403.
def test_viewer_cannot_execute(trusted_client: TestClient, run_setup: tuple[Settings, str]) -> None:
    _settings, run_id = run_setup
    _as(trusted_client, "viewer1", "no-such-group")
    list_response = trusted_client.get(f"/ui/runs/{run_id}/governance/actions")
    assert list_response.status_code == 200
    assert "ACTIVATE_UNIFIED_CANARY" not in list_response.text

    form_response = trusted_client.get(
        f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY"
    )
    assert form_response.status_code == 403


# 97-100. Flujo completo prepare -> confirm -> execute -> result -> audit.
def test_full_prepare_confirm_execute_flow(
    trusted_client: TestClient, run_setup: tuple[Settings, str]
) -> None:
    _settings, run_id = run_setup
    _as(trusted_client, "operator1", "operators")

    form = trusted_client.get(f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY")
    assert form.status_code == 200
    csrf_token = _CSRF_RE.search(form.text)
    assert csrf_token is not None
    group_ids = re.findall(r'name="approved_group_ids" value="([^"]+)"', form.text)

    prepare = trusted_client.post(
        f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/prepare",
        data={
            "csrf_token": csrf_token.group(1),
            "reason_code": "CANARY_APPROVED",
            "review_reference": "pytest-ticket",
            "approved_group_ids": group_ids,
            "target_generation_id": "",
        },
        follow_redirects=False,
    )
    assert prepare.status_code == 303
    confirm_url = prepare.headers["location"]

    confirm = trusted_client.get(confirm_url)
    assert confirm.status_code == 200
    csrf_token_2 = _CSRF_RE.search(confirm.text)
    challenge_token = _CHALLENGE_RE.search(confirm.text)
    assert csrf_token_2 is not None
    assert challenge_token is not None

    execute = trusted_client.post(
        f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/execute",
        data={"csrf_token": csrf_token_2.group(1), "challenge_token": challenge_token.group(1)},
        follow_redirects=False,
    )
    assert execute.status_code == 303
    result_url = execute.headers["location"]

    result = trusted_client.get(result_url)
    assert result.status_code == 200
    assert "UNIFIED" in result.text

    audit = trusted_client.get(f"/ui/runs/{run_id}/governance/audit")
    assert audit.status_code == 200
    assert "ACTIVATION_CANARY_SUCCEEDED" in audit.text
    assert "AUTHORIZATION_PREPARED" in audit.text

    # 101. Reintentar el MISMO challenge tras exito se rechaza (409) -- nunca doble-ejecucion.
    replay = trusted_client.post(
        f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/execute",
        data={"csrf_token": csrf_token_2.group(1), "challenge_token": challenge_token.group(1)},
        follow_redirects=False,
    )
    assert replay.status_code == 409


# 102. POST prepare sin token CSRF -> 403.
def test_prepare_without_csrf_token_rejected(
    trusted_client: TestClient, run_setup: tuple[Settings, str]
) -> None:
    _settings, run_id = run_setup
    _as(trusted_client, "operator1", "operators")
    trusted_client.get(f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY")
    response = trusted_client.post(
        f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/prepare",
        data={
            "reason_code": "CANARY_APPROVED",
            "review_reference": "pytest-ticket",
            "target_generation_id": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 403


# 113. Header de seguridad Content-Security-Policy presente en /ui/*.
# 114. X-Content-Type-Options: nosniff en todas las respuestas.
# 115. Permissions-Policy presente.
# 116. Cache-Control: no-store en paginas bajo /ui/runs/*.
# 117. X-Correlation-Id presente en la respuesta.
def test_security_headers_present(
    trusted_client: TestClient, run_setup: tuple[Settings, str]
) -> None:
    _settings, run_id = run_setup
    _as(trusted_client, "operator1", "operators")
    response = trusted_client.get(f"/ui/runs/{run_id}/governance/actions")
    assert response.status_code == 200
    assert "default-src 'self'" in response.headers.get("content-security-policy", "")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert "geolocation=()" in response.headers.get("permissions-policy", "")
    assert response.headers.get("cache-control") == "no-store"
    assert response.headers.get("x-correlation-id")


# 118. Un correlation_id externo valido (formato acotado) enviado por el proxy se respeta.
def test_external_correlation_id_is_echoed(
    trusted_client: TestClient, run_setup: tuple[Settings, str]
) -> None:
    _settings, run_id = run_setup
    _as(trusted_client, "operator1", "operators")
    response = trusted_client.get(
        f"/ui/runs/{run_id}/governance/actions", headers={"X-Correlation-Id": "req-12345"}
    )
    assert response.headers.get("x-correlation-id") == "req-12345"


# 119. Un run_id malformado -> 422, nunca un traceback.
def test_malformed_run_id_returns_422_without_traceback(trusted_client: TestClient) -> None:
    _as(trusted_client, "operator1", "operators")
    response = trusted_client.get("/ui/runs/not-a-real-run-id/governance/actions")
    assert response.status_code == 422
    assert "Traceback" not in response.text
    assert "site-packages" not in response.text
