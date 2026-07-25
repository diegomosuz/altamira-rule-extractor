"""Rutas UI transversales (Prompt 13d): redirect raiz, exclusion del
OpenAPI JSON, assets locales, version/hash de HTMX, headers de
seguridad y alcance de la CSP."""

from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from altamira_extractor.config import Settings

from ..api.conftest import RUN_ID, build_run_completed

_HTMX_SHA256 = "71ea67185bfa8c98c39d31717c6fce5d852370fcdfd129db4543774d3145c0de"


def test_root_redirects_to_ui_runs(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/ui/runs"


def test_openapi_json_excludes_ui_routes(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    ui_paths = [p for p in spec["paths"] if p.startswith("/ui")]
    assert ui_paths == []
    # La API JSON ya autorizada sigue documentada tal cual.
    assert "/api/runs" in spec["paths"]
    assert "/health" in spec["paths"]


def test_docs_and_redoc_still_reachable(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_static_app_css_is_served(client: TestClient) -> None:
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_htmx_asset_matches_pinned_version_and_hash(client: TestClient) -> None:
    response = client.get("/static/htmx.min.js")
    assert response.status_code == 200
    content = response.content
    assert hashlib.sha256(content).hexdigest() == _HTMX_SHA256
    assert b'version:"2.0.10"' in content
    assert len(content) == 51238


def test_htmx_asset_never_downloaded_from_cdn_at_runtime(client: TestClient) -> None:
    # El archivo debe existir localmente ya servido por StaticFiles; no
    # hay ninguna llamada de red en el proceso de test (TestClient nunca
    # sale a Internet), asi que un 200 aqui ya prueba que es local.
    response = client.get("/static/htmx.min.js")
    assert response.status_code == 200
    assert "unpkg" not in str(response.request.url)
    assert "cdn" not in str(response.request.url)


def test_third_party_notices_present_and_served() -> None:
    from altamira_extractor.ui import STATIC_DIR

    notices = (STATIC_DIR / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "2.0.10" in notices
    assert "0BSD" in notices
    assert _HTMX_SHA256 in notices


def test_security_headers_present_on_all_responses(client: TestClient) -> None:
    for path in ("/ui/upload", "/docs", "/health", "/openapi.json"):
        response = client.get(path)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-frame-options"] == "DENY"


def test_csp_present_only_on_ui_paths(client: TestClient) -> None:
    ui_response = client.get("/ui/upload")
    assert "content-security-policy" in ui_response.headers
    assert "script-src 'self'" in ui_response.headers["content-security-policy"]
    assert "unsafe-inline" not in ui_response.headers["content-security-policy"]
    assert "unsafe-eval" not in ui_response.headers["content-security-policy"]

    root_response = client.get("/", follow_redirects=False)
    assert "content-security-policy" in root_response.headers


def test_csp_absent_on_docs_and_api(client: TestClient) -> None:
    # Confirma explicitamente que la CSP estricta NO se aplica de forma
    # que rompa /docs/-redoc (script-src 'self' bloquearia los assets de
    # Swagger UI/ReDoc, que no son locales).
    assert "content-security-policy" not in client.get("/docs").headers
    assert "content-security-policy" not in client.get("/redoc").headers
    assert "content-security-policy" not in client.get("/health").headers
    assert "content-security-policy" not in client.get("/openapi.json").headers


def test_ui_download_page_links_to_existing_binary_api_endpoint(
    client: TestClient, settings: Settings
) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/download")
    assert response.status_code == 200
    # url_for produce una URL absoluta (scheme+host+path); solo importa
    # que el path apunte exactamente al endpoint binario existente.
    assert f"/api/runs/{RUN_ID}/download" in response.text
    # nunca un segundo flujo de descarga HTML propio.
    assert "/download/file" not in response.text
