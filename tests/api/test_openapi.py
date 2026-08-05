"""OpenAPI (Prompt 13b): exactamente las rutas documentadas en
docs/ARCHITECTURE.md Seccion 5 -- ningun endpoint adicional, en
particular ningun `/guardrail` separado."""

from __future__ import annotations

from fastapi.testclient import TestClient

_EXPECTED_PATHS = {
    "/api/runs",
    "/api/runs/{run_id}",
    "/api/runs/{run_id}/resume",
    "/api/runs/{run_id}/candidates",
    "/api/runs/{run_id}/candidates/{candidate_id}/context",
    "/api/runs/{run_id}/candidates/{candidate_id}/rule",
    "/api/runs/{run_id}/download",
    "/health",
    "/ready",
    # Gobierno operativo read-only (Fase 15A) -- exclusivamente GET/HEAD.
    "/api/runs/{run_id}/governance",
    "/api/runs/{run_id}/governance/generations",
    "/api/runs/{run_id}/governance/generations/{generation_id}",
    "/api/runs/{run_id}/governance/events",
    "/api/runs/{run_id}/governance/groups",
    "/api/runs/{run_id}/governance/artifacts/{logical_name}",
}


def test_openapi_exposes_exactly_the_documented_paths(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = set(response.json()["paths"].keys())
    assert paths == _EXPECTED_PATHS


def test_openapi_has_no_guardrail_endpoint(client: TestClient) -> None:
    response = client.get("/openapi.json")
    paths = response.json()["paths"]
    assert not any("guardrail" in path for path in paths)


def test_openapi_rule_endpoint_present(client: TestClient) -> None:
    response = client.get("/openapi.json")
    paths = response.json()["paths"]
    assert "/api/runs/{run_id}/candidates/{candidate_id}/rule" in paths


def test_openapi_has_no_html_or_admin_endpoints(client: TestClient) -> None:
    response = client.get("/openapi.json")
    paths = response.json()["paths"]
    for methods in paths.values():
        assert "DELETE" not in {m.upper() for m in methods}
        assert "PATCH" not in {m.upper() for m in methods}
    assert not any("admin" in path for path in paths)


def test_response_models_are_typed(client: TestClient) -> None:
    response = client.get("/openapi.json")
    schema = response.json()
    rule_get = schema["paths"]["/api/runs/{run_id}/candidates/{candidate_id}/rule"]["get"]
    response_schema = rule_get["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema  # no es un dict vacio / sin tipar


def test_docs_and_openapi_are_reachable(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
