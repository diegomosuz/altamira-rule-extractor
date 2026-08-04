"""Tests de seguridad web de gobierno operativo (Fase 15A Parte 10/12,
items 94-106, `feat/operational-governance-ui`).

Items 99-104 (sin `.env`, sin proveedor, sin red, sin escritura de
filesystem, sin fallback, sin transicion) se verifican de forma
EJECUTABLE con bloqueos activos en
`tests/test_operational_governance_isolation.py` (Fase 15A Parte 13) --
este archivo cubre los payloads de inyeccion/traversal (94-98) y la
superficie de escritura (105-106)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from altamira_extractor.api.app import create_app
from altamira_extractor.config import Settings

from .pipeline._operational_governance_fixtures import (
    build_materialization_fixture,
    governance_run_dir,
    materialize_unified_canary,
)


def _settings_for(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir=data_dir, runs_dir=data_dir / "runs", incoming_dir=data_dir / "incoming"
    )
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    return settings


_PATH_TRAVERSAL_PAYLOADS = [
    "../../../etc/passwd",
    "..%2f..%2f..%2fetc%2fpasswd",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
]
_WINDOWS_ABSOLUTE_PAYLOADS = ["C:\\Windows\\System32\\config\\SAM", "C%3A%5CWindows"]
_UNC_PAYLOADS = ["\\\\server\\share\\file", "%5C%5Cserver%5Cshare"]
_CRLF_PAYLOADS = ["candidates%0d%0aSet-Cookie:%20evil=1", "candidates%0dX-Injected:%201"]
_SCRIPT_PAYLOADS = ["<script>alert(1)</script>", "'\"><img src=x onerror=alert(1)>"]


def _run(tmp_path: Path) -> tuple[Settings, str]:
    fx = build_materialization_fixture()
    settings = _settings_for(tmp_path)
    run_dir = governance_run_dir(settings.runs_dir, fx)
    materialize_unified_canary(run_dir, fx, tmp_path)
    return settings, fx.run_id


# 94. path traversal.
def test_path_traversal_rejected_in_generation_id(tmp_path: Path) -> None:
    settings, run_id = _run(tmp_path)
    with TestClient(create_app(settings)) as client:
        for payload in _PATH_TRAVERSAL_PAYLOADS:
            response = client.get(f"/api/runs/{run_id}/governance/generations/{payload}")
            assert response.status_code in (404, 422), payload


# 95. Windows absolute path.
def test_windows_absolute_path_rejected(tmp_path: Path) -> None:
    settings, run_id = _run(tmp_path)
    with TestClient(create_app(settings)) as client:
        for payload in _WINDOWS_ABSOLUTE_PAYLOADS:
            response = client.get(f"/api/runs/{run_id}/governance/generations/{payload}")
            assert response.status_code in (404, 422), payload


# 96. UNC.
def test_unc_path_rejected(tmp_path: Path) -> None:
    settings, run_id = _run(tmp_path)
    with TestClient(create_app(settings)) as client:
        for payload in _UNC_PAYLOADS:
            response = client.get(f"/api/runs/{run_id}/governance/generations/{payload}")
            assert response.status_code in (404, 422), payload


# 97. CR/LF.
def test_crlf_payload_rejected_in_logical_name(tmp_path: Path) -> None:
    settings, run_id = _run(tmp_path)
    with TestClient(create_app(settings)) as client:
        for payload in _CRLF_PAYLOADS:
            response = client.get(f"/api/runs/{run_id}/governance/artifacts/{payload}")
            assert response.status_code == 422, payload
            assert "\r" not in response.text
            assert "Set-Cookie" not in response.headers


# 98. script injection.
def test_script_injection_rejected_in_path_params(tmp_path: Path) -> None:
    settings, run_id = _run(tmp_path)
    with TestClient(create_app(settings)) as client:
        for payload in _SCRIPT_PAYLOADS:
            response = client.get(f"/api/runs/{run_id}/governance/generations/{payload}")
            assert response.status_code in (404, 422), payload
            assert "<script>" not in response.text


def test_script_injection_rejected_via_run_id(tmp_path: Path) -> None:
    """Un payload con `/` embebido (p. ej. `</script>`) desplaza el
    resto de la ruta y nunca coincide con `/{run_id}/governance` (404);
    uno sin `/` si llega al parametro `run_id` y lo rechaza el regex
    dedicado (422) -- ambos resultados son igualmente seguros, ninguno
    ejecuta ni refleja el payload sin escapar."""
    settings, _run_id = _run(tmp_path)
    with TestClient(create_app(settings)) as client:
        for payload in _SCRIPT_PAYLOADS:
            response = client.get(f"/api/runs/{payload}/governance")
            assert response.status_code in (404, 422), payload
            assert "<script>" not in response.text


# 105. no authorization upload -- ningun endpoint de gobierno acepta
# multipart/upload de ningun tipo (ni autorizaciones, ni paquetes).
def test_no_endpoint_accepts_file_upload(tmp_path: Path) -> None:
    import inspect

    from fastapi.routing import APIRoute

    from altamira_extractor.api.routers.governance import router

    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        signature = inspect.signature(route.endpoint)
        for name, parameter in signature.parameters.items():
            assert "UploadFile" not in str(parameter.annotation), (
                f"{route.path} acepta un upload: {name}"
            )


# 106. no POST/PUT/PATCH/DELETE (repetido a nivel de app completa, no
# solo del router de gobierno -- ver tambien item 74).
def test_no_write_http_methods_anywhere_under_governance(tmp_path: Path) -> None:
    settings, run_id = _run(tmp_path)
    with TestClient(create_app(settings)) as client:
        for method in ("post", "put", "patch", "delete"):
            response = client.request(method, f"/api/runs/{run_id}/governance")
            assert response.status_code in (404, 405), (method, response.status_code)
            response_ui = client.request(method, f"/ui/runs/{run_id}/governance")
            assert response_ui.status_code in (404, 405), (method, response_ui.status_code)
