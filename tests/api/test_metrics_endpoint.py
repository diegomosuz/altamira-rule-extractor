"""Tests de `GET /internal/metrics` (Fase 15B2-B, Seccion 8; cierre
correctivo Seccion 2): el endpoint NUNCA bypassa el gate global de
`security_misconfigured` -- su propio gate de token/proxy es una capa
ADICIONAL, nunca un sustituto."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings
from tests.e2e_support import write_disabled_dev_security_config

_TOKEN_ENV_VAR = "ALTAMIRA_TEST_METRICS_TOKEN"
_TOKEN_VALUE = "synthetic-metrics-token-for-tests-only"
_PROXY_MARKER_HEADER = "X-Altamira-Metrics-Proxy-Marker"
_PROXY_MARKER_VALUE_ENV_VAR = "ALTAMIRA_TEST_METRICS_PROXY_MARKER"
_PROXY_MARKER_VALUE = "synthetic-proxy-marker-for-tests-only"


def _observability_yaml_disabled(tmp_path: Path) -> Path:
    path = tmp_path / "observability.yaml"
    path.write_text(
        "\n".join(["schema_version: '1.0'", "metrics:", "  mode: DISABLED"]) + "\n",
        encoding="utf-8",
    )
    return path


def _observability_yaml_enabled_internal_token(tmp_path: Path) -> Path:
    path = tmp_path / "observability.yaml"
    path.write_text(
        "\n".join(
            [
                "schema_version: '1.0'",
                "metrics:",
                "  mode: ENABLED",
                "  access_mode: INTERNAL_TOKEN",
                f"  access_token_env_var: {_TOKEN_ENV_VAR}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _observability_yaml_enabled_trusted_proxy(tmp_path: Path) -> Path:
    path = tmp_path / "observability.yaml"
    path.write_text(
        "\n".join(
            [
                "schema_version: '1.0'",
                "metrics:",
                "  mode: ENABLED",
                "  access_mode: TRUSTED_PROXY",
                f"  trusted_proxy_marker_header: {_PROXY_MARKER_HEADER}",
                f"  trusted_proxy_marker_value_env_var: {_PROXY_MARKER_VALUE_ENV_VAR}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _settings(
    tmp_path: Path,
    *,
    observability_config_path: Path,
    security_config_path: Path | None = None,
) -> Settings:
    return Settings(
        runs_dir=tmp_path / "runs",
        incoming_dir=tmp_path / "incoming",
        security_config_path=(
            security_config_path
            if security_config_path is not None
            else write_disabled_dev_security_config(tmp_path)
        ),
        observability_config_path=observability_config_path,
    )


def test_metrics_endpoint_returns_404_when_disabled_by_default(tmp_path: Path) -> None:
    """`config/observability.yaml` ausente ya cae al default seguro
    (metrics deshabilitadas) -- no hace falta un YAML explicito."""
    settings = _settings(
        tmp_path, observability_config_path=tmp_path / "does-not-exist-observability.yaml"
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/internal/metrics")
        assert response.status_code == 404


def test_metrics_endpoint_returns_404_when_explicitly_disabled(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, observability_config_path=_observability_yaml_disabled(tmp_path)
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/internal/metrics")
        assert response.status_code == 404


def test_metrics_endpoint_returns_404_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_TOKEN_ENV_VAR, _TOKEN_VALUE)
    settings = _settings(
        tmp_path, observability_config_path=_observability_yaml_enabled_internal_token(tmp_path)
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/internal/metrics")
        assert response.status_code == 404


def test_metrics_endpoint_returns_404_with_wrong_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_TOKEN_ENV_VAR, _TOKEN_VALUE)
    settings = _settings(
        tmp_path, observability_config_path=_observability_yaml_enabled_internal_token(tmp_path)
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/internal/metrics", headers={"X-Altamira-Metrics-Token": "wrong-token"}
        )
        assert response.status_code == 404


def test_metrics_endpoint_returns_200_with_correct_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Escenario B (cierre correctivo, Seccion 2): metrics habilitadas +
    seguridad valida + autorizacion correcta -> 200."""
    monkeypatch.setenv(_TOKEN_ENV_VAR, _TOKEN_VALUE)
    settings = _settings(
        tmp_path, observability_config_path=_observability_yaml_enabled_internal_token(tmp_path)
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/internal/metrics", headers={"X-Altamira-Metrics-Token": _TOKEN_VALUE}
        )
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "text/plain" in response.headers["content-type"]
        assert "altamira_http_requests_total" in response.text


def test_metrics_endpoint_never_echoes_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_TOKEN_ENV_VAR, _TOKEN_VALUE)
    settings = _settings(
        tmp_path, observability_config_path=_observability_yaml_enabled_internal_token(tmp_path)
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/internal/metrics", headers={"X-Altamira-Metrics-Token": _TOKEN_VALUE}
        )
        assert _TOKEN_VALUE not in response.text
        wrong = client.get("/internal/metrics", headers={"X-Altamira-Metrics-Token": "x"})
        assert _TOKEN_VALUE not in wrong.text


def test_token_never_appears_in_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cierre correctivo, Seccion 2, item 5: ni un token correcto ni uno
    incorrecto deben aparecer en la salida JSON estructurada (incluido
    `http_route`, que es siempre la plantilla `/internal/metrics`, nunca
    incluye query string ni headers)."""
    monkeypatch.setenv(_TOKEN_ENV_VAR, _TOKEN_VALUE)
    settings = _settings(
        tmp_path, observability_config_path=_observability_yaml_enabled_internal_token(tmp_path)
    )
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        client.get("/internal/metrics", headers={"X-Altamira-Metrics-Token": _TOKEN_VALUE})
        client.get("/internal/metrics", headers={"X-Altamira-Metrics-Token": "wrong-token-xyz"})
        captured = capsys.readouterr()
        combined = captured.err + captured.out
        assert _TOKEN_VALUE not in combined
        assert "wrong-token-xyz" not in combined
        for line in combined.strip().splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            assert record.get("http_route") == "/internal/metrics"


def test_metrics_endpoint_excluded_from_openapi_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_TOKEN_ENV_VAR, _TOKEN_VALUE)
    settings = _settings(
        tmp_path, observability_config_path=_observability_yaml_enabled_internal_token(tmp_path)
    )
    with TestClient(create_app(settings)) as client:
        schema = client.get("/openapi.json").json()
        assert "/internal/metrics" not in schema["paths"]


# --- Cierre correctivo, Seccion 2: el gate global SIEMPRE gana --------------


def test_security_misconfigured_blocks_metrics_even_with_correct_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Escenario D: metrics habilitadas, token correcto, pero
    `security_misconfigured=true` -> NUNCA 200, nunca expone el payload
    Prometheus; la respuesta la produce el gate global (503), no el
    router de metrics."""
    monkeypatch.setenv(_TOKEN_ENV_VAR, _TOKEN_VALUE)
    settings = Settings(
        runs_dir=tmp_path / "runs",
        incoming_dir=tmp_path / "incoming",
        security_config_path=tmp_path / "does-not-exist-security.yaml",
        observability_config_path=_observability_yaml_enabled_internal_token(tmp_path),
    )
    with TestClient(create_app(settings)) as client:
        assert client.app.state.security_misconfigured is True  # type: ignore[attr-defined]

        response = client.get(
            "/internal/metrics", headers={"X-Altamira-Metrics-Token": _TOKEN_VALUE}
        )
        assert response.status_code == 503
        assert response.status_code != 200
        assert "altamira_http_requests_total" not in response.text
        assert response.json()["error"]["code"] == "security_misconfigured"


def test_security_misconfigured_blocks_metrics_with_trusted_proxy_marker_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Escenario D, variante TRUSTED_PROXY: un marcador de proxy
    correcto tampoco bypassa el gate global."""
    monkeypatch.setenv(_PROXY_MARKER_VALUE_ENV_VAR, _PROXY_MARKER_VALUE)
    settings = Settings(
        runs_dir=tmp_path / "runs",
        incoming_dir=tmp_path / "incoming",
        security_config_path=tmp_path / "does-not-exist-security.yaml",
        observability_config_path=_observability_yaml_enabled_trusted_proxy(tmp_path),
    )
    with TestClient(create_app(settings)) as client:
        assert client.app.state.security_misconfigured is True  # type: ignore[attr-defined]

        response = client.get(
            "/internal/metrics", headers={_PROXY_MARKER_HEADER: _PROXY_MARKER_VALUE}
        )
        assert response.status_code == 503
        assert "altamira_http_requests_total" not in response.text


def test_misconfigured_response_is_uniform_with_other_blocked_routes(tmp_path: Path) -> None:
    """Escenario D, item 4: la respuesta de `/internal/metrics` cuando
    esta misconfigured es identica en forma (status/codigo/envelope) a
    la de cualquier otra ruta bloqueada por el mismo gate -- no hay un
    camino de respuesta especial para metrics."""
    settings = Settings(
        runs_dir=tmp_path / "runs",
        incoming_dir=tmp_path / "incoming",
        security_config_path=tmp_path / "does-not-exist-security.yaml",
    )
    with TestClient(create_app(settings)) as client:
        metrics_response = client.get("/internal/metrics")
        runs_response = client.get("/api/runs")

        assert metrics_response.status_code == runs_response.status_code == 503
        assert metrics_response.json() == runs_response.json()


def test_metrics_not_in_misconfigured_allowlist(tmp_path: Path) -> None:
    """Verificacion directa y explicita de la correccion: la allowlist
    del gate global de seguridad solo contiene las excepciones
    preexistentes (`/health`, `/ready`, `/static/*`) -- nunca
    `/internal/metrics`."""
    from altamira_extractor.api.app import _MISCONFIGURED_ALLOWED_PATHS

    assert _MISCONFIGURED_ALLOWED_PATHS == frozenset({"/health", "/ready"})
    assert "/internal/metrics" not in _MISCONFIGURED_ALLOWED_PATHS
