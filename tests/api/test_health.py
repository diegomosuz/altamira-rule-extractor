"""GET /health (Prompt 13b): liveness pura."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_does_not_require_any_run(client: TestClient) -> None:
    # runs_dir ni siquiera existe todavia: /health no depende de eso.
    response = client.get("/health")
    assert response.status_code == 200
