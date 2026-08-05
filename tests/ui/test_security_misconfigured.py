"""Comportamiento HTTP end-to-end del cierre Fase 15B1 ("DISABLED_DEV
explicito"): `config/security.yaml` ausente/invalido dejando la app en
`SECURITY_MISCONFIGURED` (fail-closed, incluye el pipeline V1
pre-existente); `DISABLED_DEV`/`TRUSTED_PROXY_HEADERS` EXPLICITOS
comportandose como antes. Reutiliza el mismo patron de bootstrap real
que `tests/security/test_operational_challenge_consumption.py`
(`_bootstrap_run`) -- sin JAR, sin Neo4j, mismo estilo que el resto de
`tests/pipeline/_unified_materialization_fixtures.py`."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings
from altamira_extractor.pipeline.unified_materialization_service import (
    materialize_unified_activation,
)
from tests.pipeline._unified_materialization_fixtures import (
    build_materialization_fixture,
    write_authorization_yaml,
    write_run_dir,
)

_DISABLED_DEV_YAML = "\n".join(
    [
        'schema_version: "1.0"',
        "authentication_mode: DISABLED_DEV",
        'trusted_proxy_header_user: "X-User"',
        'trusted_proxy_required_marker_header: "X-Marker"',
        'trusted_proxy_required_marker_value: "dev-marker-not-enforced"',
        'session_cookie_name: "altamira_session"',
        "session_cookie_secure: false",
    ]
)

_TRUSTED_PROXY_YAML = "\n".join(
    [
        'schema_version: "1.0"',
        "authentication_mode: TRUSTED_PROXY_HEADERS",
        'trusted_proxy_header_user: "X-User"',
        'trusted_proxy_required_marker_header: "X-Marker"',
        'trusted_proxy_required_marker_value: "synthetic-marker-value"',
        'session_cookie_name: "altamira_session"',
        "session_cookie_secure: true",
    ]
)


def _bootstrap_run(tmp_path: Path) -> tuple[Path, str]:
    fx = build_materialization_fixture()
    runs_dir = tmp_path / "runs"
    run_dir = write_run_dir(runs_dir, fx)
    eval_hash_path = run_dir / "diagnostics" / "unified-activation-evaluation.json"
    eval_hash = hashlib.sha256(eval_hash_path.read_bytes()).hexdigest()
    auth_path = tmp_path / "bootstrap-authorization.yaml"
    write_authorization_yaml(
        auth_path,
        run_id=fx.run_id,
        activation_evaluation_hash=eval_hash,
        action="KEEP_V1",
        expected_readiness_disposition="READY_FOR_UNIFIED_CANARY",
    )
    materialize_unified_activation(run_dir, fx.run_id, authorization_path=auth_path)
    return runs_dir, fx.run_id


def _settings_missing_config(tmp_path: Path, runs_dir: Path) -> Settings:
    return Settings(
        runs_dir=runs_dir,
        incoming_dir=tmp_path / "incoming",
        security_config_path=tmp_path / "does-not-exist-security.yaml",
    )


def _settings_with_config(tmp_path: Path, runs_dir: Path, yaml_text: str) -> Settings:
    path = tmp_path / "security.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    return Settings(
        runs_dir=runs_dir,
        incoming_dir=tmp_path / "incoming",
        security_config_path=path,
    )


@pytest.fixture
def misconfigured_client(tmp_path: Path) -> Iterator[tuple[TestClient, str]]:
    runs_dir, run_id = _bootstrap_run(tmp_path)
    settings = _settings_missing_config(tmp_path, runs_dir)
    with TestClient(create_app(settings)) as client:
        yield client, run_id


# --- Estado ausente/invalido: SECURITY_MISCONFIGURED ------------------------


def test_health_reports_alive_when_misconfigured(
    misconfigured_client: tuple[TestClient, str],
) -> None:
    client, _run_id = misconfigured_client
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_fails_when_config_missing(
    misconfigured_client: tuple[TestClient, str],
) -> None:
    client, _run_id = misconfigured_client
    response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["reason"] == "security_misconfigured"


def test_ui_runs_does_not_expose_data_when_misconfigured(
    misconfigured_client: tuple[TestClient, str],
) -> None:
    client, _run_id = misconfigured_client
    response = client.get("/ui/runs")
    assert response.status_code == 503


def test_api_runs_does_not_expose_data_when_misconfigured(
    misconfigured_client: tuple[TestClient, str],
) -> None:
    client, _run_id = misconfigured_client
    response = client.get("/api/runs")
    assert response.status_code == 503


def test_run_detail_does_not_expose_data_when_misconfigured(
    misconfigured_client: tuple[TestClient, str],
) -> None:
    client, run_id = misconfigured_client
    response = client.get(f"/ui/runs/{run_id}")
    assert response.status_code == 503


def test_governance_actions_reject_without_creating_anonymous_principal(
    misconfigured_client: tuple[TestClient, str],
) -> None:
    """Ni siquiera el principal anonimo de DISABLED_DEV se construye --
    503 antes de resolver ninguna identidad, a diferencia de un 200
    de solo lectura o un 403 de permiso insuficiente."""
    client, run_id = misconfigured_client
    response = client.get(f"/ui/runs/{run_id}/governance/actions")
    assert response.status_code == 503
    body_text = response.text
    assert "anonymous-dev" not in body_text


def test_upload_does_not_execute_when_misconfigured(
    misconfigured_client: tuple[TestClient, str],
) -> None:
    client, _run_id = misconfigured_client
    response = client.post(
        "/ui/runs",
        files={"file": ("package.zip", b"x" * 100, "application/zip")},
    )
    assert response.status_code == 503


def test_static_assets_still_reachable_when_misconfigured(
    misconfigured_client: tuple[TestClient, str],
) -> None:
    """Necesario para que la propia pagina de error 503 se renderice
    con estilo -- no es una fuga de datos (assets inmutables sin
    contenido de ningun run)."""
    client, _run_id = misconfigured_client
    response = client.get("/static/app.css")
    assert response.status_code == 200


def test_openapi_docs_blocked_when_misconfigured(
    misconfigured_client: tuple[TestClient, str],
) -> None:
    client, _run_id = misconfigured_client
    assert client.get("/openapi.json").status_code == 503
    assert client.get("/docs").status_code == 503


# --- Config invalida (presente pero corrupta): mismo fail-closed -----------


def test_invalid_yaml_also_fails_closed(tmp_path: Path) -> None:
    runs_dir, _run_id = _bootstrap_run(tmp_path)
    path = tmp_path / "security.yaml"
    path.write_text("not: [valid, yaml", encoding="utf-8")
    settings = Settings(
        runs_dir=runs_dir, incoming_dir=tmp_path / "incoming", security_config_path=path
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        ready = client.get("/ready")
        assert ready.status_code == 503
        assert client.get("/ui/runs").status_code == 503


# --- DISABLED_DEV explicito: comportamiento previo intacto -----------------


def test_disabled_dev_explicit_allows_read_only_access(tmp_path: Path) -> None:
    runs_dir, run_id = _bootstrap_run(tmp_path)
    settings = _settings_with_config(tmp_path, runs_dir, _DISABLED_DEV_YAML)
    with TestClient(create_app(settings)) as client:
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json()["ready"] is True

        actions = client.get(f"/ui/runs/{run_id}/governance/actions")
        assert actions.status_code == 200
        assert "Modo desarrollo (DISABLED_DEV)" in actions.text
        # Solo lectura: ninguna accion privilegiada visible, y un intento
        # directo de prepare se rechaza (VIEWER-solo, sin permiso).
        assert "ACTIVATE_UNIFIED_CANARY" not in actions.text
        denied = client.post(
            f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY/prepare",
            data={
                "reason_code": "CANARY_APPROVED",
                "review_reference": "x",
                "approved_group_ids": [],
                "target_generation_id": "",
            },
        )
        assert denied.status_code == 403


def test_disabled_dev_explicit_operations_denied(tmp_path: Path) -> None:
    runs_dir, run_id = _bootstrap_run(tmp_path)
    settings = _settings_with_config(tmp_path, runs_dir, _DISABLED_DEV_YAML)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{run_id}/governance/actions/ACTIVATE_UNIFIED_CANARY")
        assert response.status_code == 403


# --- TRUSTED_PROXY_HEADERS explicito: marker/identidad/secreto obligatorios -


def test_trusted_proxy_explicit_rejects_missing_marker(tmp_path: Path) -> None:
    runs_dir, run_id = _bootstrap_run(tmp_path)
    settings = _settings_with_config(tmp_path, runs_dir, _TRUSTED_PROXY_YAML)
    settings = settings.model_copy(update={"session_secret": SecretStr("a" * 40)})
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/ui/runs/{run_id}/governance/actions")
        assert response.status_code == 401


def test_trusted_proxy_explicit_rejects_missing_identity_header(tmp_path: Path) -> None:
    runs_dir, run_id = _bootstrap_run(tmp_path)
    settings = _settings_with_config(tmp_path, runs_dir, _TRUSTED_PROXY_YAML)
    settings = settings.model_copy(update={"session_secret": SecretStr("a" * 40)})
    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/ui/runs/{run_id}/governance/actions",
            headers={"X-Marker": "synthetic-marker-value"},
        )
        assert response.status_code == 401


def test_trusted_proxy_explicit_accepts_valid_identity(tmp_path: Path) -> None:
    runs_dir, run_id = _bootstrap_run(tmp_path)
    settings = _settings_with_config(tmp_path, runs_dir, _TRUSTED_PROXY_YAML)
    settings = settings.model_copy(update={"session_secret": SecretStr("a" * 40)})
    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/ui/runs/{run_id}/governance/actions",
            headers={"X-Marker": "synthetic-marker-value", "X-User": "viewer1"},
        )
        assert response.status_code == 200


def test_trusted_proxy_explicit_without_session_secret_fails_startup(tmp_path: Path) -> None:
    runs_dir, _run_id = _bootstrap_run(tmp_path)
    settings = _settings_with_config(tmp_path, runs_dir, _TRUSTED_PROXY_YAML)
    assert settings.session_secret is None
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="ALTAMIRA_SESSION_SECRET"), TestClient(app):
        pass
