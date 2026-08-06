"""Tests de `CorrelationLoggingMiddleware` endurecido (Fase 15B2-B,
Seccion 7): exactamente un evento `http_request_completed` por
request, forma cerrada de campos, nunca `principal_id`/path crudo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings
from tests.e2e_support import write_disabled_dev_security_config


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    return Settings(
        runs_dir=runs_dir,
        incoming_dir=tmp_path / "incoming",
        security_config_path=write_disabled_dev_security_config(tmp_path),
    )


def _http_request_completed_events(captured_text: str) -> list[dict[str, object]]:
    events = []
    for line in captured_text.strip().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event_name") == "http_request_completed":
            events.append(record)
    return events


def test_exactly_one_http_request_completed_event_per_request(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()  # descarta logs de arranque del lifespan
        client.get("/health")
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        assert len(events) == 1


def test_event_never_includes_principal_id(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        client.get("/health")
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        assert "principal_id" not in events[0]


def test_event_uses_route_template_not_raw_path(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        client.get("/api/runs/20260101T000000000000-deadbeef")
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        assert events[0]["http_route"] == "/api/runs/{run_id}"
        assert "20260101T000000000000-deadbeef" not in events[0]["http_route"]


def test_event_has_exactly_the_closed_field_set(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        client.get("/health")
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        expected_keys = {
            "timestamp_utc",
            "level",
            "logger",
            "message",
            "event_name",
            "correlation_id",
            "http_method",
            "http_route",
            "http_status_code",
            "duration_ms",
            "outcome",
        }
        assert set(events[0].keys()) == expected_keys


def test_event_reports_success_outcome_for_2xx(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        client.get("/health")
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        assert events[0]["outcome"] == "success"
        assert events[0]["http_status_code"] == 200


def test_correlation_id_matches_response_header(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        response = client.get("/health")
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        assert events[0]["correlation_id"] == response.headers["X-Correlation-Id"]


def test_unmatched_route_uses_closed_marker(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        client.get("/this-route-does-not-exist")
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        assert events[0]["http_route"] == "UNMATCHED_ROUTE"


# --- Cierre correctivo, Seccion 6: tests explicitos adicionales ------------


def test_run_id_value_never_appears_anywhere_in_the_event(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """No solo `http_route` usa la plantilla -- el `run_id` real no
    debe aparecer en NINGUN campo del evento, ni siquiera serializado
    completo como texto."""
    run_id = "20260101T000000000000-deadbeef"
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        client.get(f"/api/runs/{run_id}")
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        assert run_id not in json.dumps(events[0])


def test_raw_path_never_appears_for_a_dynamic_route(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        client.get("/api/runs/20260101T000000000000-cafef00d/candidates")
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        raw_path = "/api/runs/20260101T000000000000-cafef00d/candidates"
        assert events[0]["http_route"] == "/api/runs/{run_id}/candidates"
        assert raw_path not in json.dumps(events[0])


def test_query_string_never_appears_in_the_event(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        client.get("/api/runs?limit=5&offset=10")
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        serialized = json.dumps(events[0])
        assert "limit" not in serialized
        assert "offset" not in serialized
        assert "?" not in events[0]["http_route"]


def test_static_asset_request_never_leaks_the_filename(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """`/static/*` es servido por un `Mount` (StaticFiles), que nunca
    fija `request.scope["route"]` -- `_route_template` cae al marcador
    cerrado `UNMATCHED_ROUTE`, nunca al nombre de archivo real."""
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        client.get("/static/app.css")
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        assert events[0]["http_route"] == "UNMATCHED_ROUTE"
        assert "app.css" not in json.dumps(events[0])


def test_security_misconfigured_response_never_leaks_the_requested_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    misconfigured_settings = Settings(
        runs_dir=tmp_path / "runs",
        incoming_dir=tmp_path / "incoming",
        security_config_path=tmp_path / "does-not-exist-security.yaml",
    )
    with TestClient(create_app(misconfigured_settings)) as client:
        capsys.readouterr()
        response = client.get("/api/runs/20260101T000000000000-abad1dea/candidates")
        assert response.status_code == 503
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        serialized = json.dumps(events[0])
        assert "20260101T000000000000-abad1dea" not in serialized
        # El gate global corta ANTES del enrutador FastAPI: sin route
        # resuelta, `http_route` es el marcador cerrado, nunca el path.
        assert events[0]["http_route"] == "UNMATCHED_ROUTE"


def test_ready_event_has_exactly_the_closed_field_set(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(settings)) as client:
        capsys.readouterr()
        client.get("/ready")
        captured = capsys.readouterr()
        events = _http_request_completed_events(captured.err + captured.out)
        expected_keys = {
            "timestamp_utc",
            "level",
            "logger",
            "message",
            "event_name",
            "correlation_id",
            "http_method",
            "http_route",
            "http_status_code",
            "duration_ms",
            "outcome",
        }
        assert set(events[0].keys()) == expected_keys
        assert events[0]["http_route"] == "/ready"
