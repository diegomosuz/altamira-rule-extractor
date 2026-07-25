"""Seguridad transversal (Prompt 13b): traversal en identificadores,
candidate_id real con '::' aceptado, ausencia de detalles internos en
errores."""

from __future__ import annotations

from fastapi.testclient import TestClient

from altamira_extractor.config import Settings

from .conftest import CANDIDATE_ID, RUN_ID, build_run_up_to_contexts_built


def test_run_id_traversal_rejected(client: TestClient) -> None:
    response = client.get("/api/runs/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code in (404, 422)
    assert "error" in response.json()


def test_run_id_with_backslash_rejected(client: TestClient) -> None:
    response = client.get("/api/runs/foo%5Cbar")
    assert response.status_code in (404, 422)


def test_candidate_id_with_slash_rejected(client: TestClient, settings: Settings) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/api/runs/{RUN_ID}/candidates/foo%2Fbar/context")
    # '%2F' se decodifica antes del ruteo: la ruta resultante ("foo/bar")
    # no matchea ningun path template registrado -> 404 de Starlette
    # (nunca llega a validate_candidate_id). Un 422 tambien seria
    # aceptable si el decodificado cambiara de comportamiento en otra
    # version de Starlette; lo que nunca debe pasar es servir el
    # recurso ni un 200.
    assert response.status_code in (404, 422)
    assert "error" in response.json()


def test_candidate_id_with_double_colon_accepted(client: TestClient, settings: Settings) -> None:
    # Formato real del pipeline (candidate_id contiene '::'): nunca debe
    # rechazarse por formato.
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)
    assert "::" in CANDIDATE_ID

    response = client.get(f"/api/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/context")
    assert response.status_code == 200


def test_error_responses_never_contain_absolute_paths(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/api/runs/{RUN_ID}/candidates/candidate-inexistente/context")
    assert response.status_code == 404
    body_text = response.text
    assert str(settings.runs_dir) not in body_text
    assert str(settings.data_dir) not in body_text


def test_starlette_http_exception_uses_the_stable_envelope(client: TestClient) -> None:
    # Ninguna ruta registrada matchea esto: Starlette levanta su propia
    # HTTPException(404) internamente (nunca pasa por ningun handler mio
    # de negocio) -- confirma que _handle_http_exception tambien produce
    # la envolvente estable, no el detail crudo de Starlette.
    response = client.get("/api/runs/this-does-not-exist-as-a-route/nested/unknown")
    assert response.status_code == 404
    body = response.json()
    assert set(body["error"].keys()) == {"code", "message"}
    assert body["error"]["code"] == "http_error"


def test_request_validation_error_uses_the_stable_envelope(client: TestClient) -> None:
    response = client.get("/api/runs", params={"limit": "not-a-number"})
    assert response.status_code == 422
    body = response.json()
    assert body == {
        "error": {"code": "invalid_request", "message": "parametros de solicitud invalidos"}
    }


def test_unexpected_exception_returns_generic_500_without_details(
    settings: Settings, monkeypatch: object
) -> None:
    import altamira_extractor.api.routers.runs as runs_module
    from altamira_extractor.api.app import create_app

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("segredo interno: password=hunter2 en /var/secret/path")

    monkeypatch.setattr(runs_module, "read_run_state", _boom)  # type: ignore[attr-defined]
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)

    # raise_server_exceptions=False: un cliente HTTP real nunca ve la
    # excepcion de Python, solo la respuesta 500 ya sanitizada por el
    # exception_handler(Exception) -- por defecto TestClient re-lanza la
    # excepcion original (util para depurar bugs propios, no para probar
    # el comportamiento real del handler ante clientes externos).
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.get(f"/api/runs/{RUN_ID}")

    assert response.status_code == 500
    body = response.json()
    assert body == {"error": {"code": "internal_error", "message": "error interno"}}
    assert "hunter2" not in response.text
    assert "/var/secret/path" not in response.text
