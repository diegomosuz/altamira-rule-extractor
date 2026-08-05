"""Tests de la API de gobierno operativo (Fase 15A Parte 7/12, items
65-74, `feat/operational-governance-ui`)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings
from altamira_extractor.pipeline.unified_activation_store import UnifiedActivationStore

from .e2e_support import write_disabled_dev_security_config
from .pipeline._operational_governance_fixtures import (
    build_materialization_fixture,
    governance_run_dir,
    materialize_keep_v1,
    materialize_unified_canary,
)


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


# 65. overview JSON.
def test_overview_json(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/runs/{fx.run_id}/governance")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "HEALTHY_UNIFIED"
    assert body["schema_version"] == "1.0"


# 66. run inexistente.
def test_overview_for_nonexistent_run(tmp_path: Path) -> None:
    settings = _settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/runs/20260101T000000000000-deadbeef/governance")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


# 67. generations list.
def test_generations_list(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/runs/{fx.run_id}/governance/generations")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert {g["lane"] for g in body} == {"V1", "UNIFIED"}


# 68. generation detail.
def test_generation_detail(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    result = materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/api/runs/{fx.run_id}/governance/generations/{result.generation_id}"
        )
        missing = client.get(f"/api/runs/{fx.run_id}/governance/generations/generation-{'0' * 64}")
        malformed = client.get(f"/api/runs/{fx.run_id}/governance/generations/not-a-real-id")
    assert response.status_code == 200
    assert response.json()["generation_id"] == result.generation_id
    assert missing.status_code == 404
    assert malformed.status_code == 422


# 69. events list.
def test_events_list(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/runs/{fx.run_id}/governance/events")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert all(event["confirmed"] for event in body)


# 70. groups list.
def test_groups_list(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/runs/{fx.run_id}/governance/groups")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["group_id"] == fx.evaluation.unified_references[0].group_id


# 71. artifact download.
def test_artifact_download(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/runs/{fx.run_id}/governance/artifacts/candidates")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.content


# 72. corruption response.
def test_corruption_response(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    result = materialize_unified_canary(run_dir, fx, tmp_path)
    store = UnifiedActivationStore(run_dir)
    (store.generation_dir(result.generation_id) / "candidates.json").write_bytes(b"{bad}")
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/runs/{fx.run_id}/governance/artifacts/candidates")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "governance_artifact_blocked"


# 73. ausencia legitima.
def test_legitimate_absence_response(tmp_path: Path) -> None:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_keep_v1(run_dir, fx, tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/api/runs/{fx.run_id}/governance/artifacts/context-packages")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "governance_artifact_not_available"


# 74. ningun metodo de escritura.
def test_no_write_methods_registered() -> None:
    from fastapi.routing import APIRoute

    from altamira_extractor.api.routers.governance import router

    api_routes = [route for route in router.routes if isinstance(route, APIRoute)]
    assert api_routes, "el router de gobierno no expone ninguna ruta"
    for route in api_routes:
        methods = route.methods or set()
        forbidden = methods & {"POST", "PUT", "PATCH", "DELETE"}
        assert not forbidden, f"{route.path} expone metodos de escritura: {forbidden}"
