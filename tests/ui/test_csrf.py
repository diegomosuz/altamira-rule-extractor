"""CSRF minimo por Origin/Referer (Prompt 13d, seccion 10): sin token,
sin sesion, sin cookie -- solo verificacion estricta de mismo origen en
las 2 rutas mutantes."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from altamira_extractor.config import Settings

from ..api.conftest import RUN_ID, build_run_up_to_guardrails_applied
from ..pipeline.conftest import build_valid_package_zip
from .conftest import FOREIGN_ORIGIN, SAME_ORIGIN


def _upload(client: TestClient, settings: Settings, *, headers: dict[str, str]) -> httpx.Response:
    zip_path = build_valid_package_zip(settings.data_dir.parent / "package.zip")
    with zip_path.open("rb") as fh:
        return client.post(  # type: ignore[no-any-return]
            "/ui/runs",
            files={"file": ("package.zip", fh, "application/zip")},
            headers=headers,
            follow_redirects=False,
        )


def test_upload_accepted_with_matching_origin(client: TestClient, settings: Settings) -> None:
    response = _upload(client, settings, headers={"Origin": SAME_ORIGIN})
    assert response.status_code == 303


def test_upload_rejected_with_foreign_origin(client: TestClient, settings: Settings) -> None:
    response = _upload(client, settings, headers={"Origin": FOREIGN_ORIGIN})
    assert response.status_code == 403
    # /ui/runs es una ruta UI: el error se sirve como HTML (error.html),
    # nunca JSON, igual que cualquier otro error bajo /ui/*.
    assert "text/html" in response.headers["content-type"]
    assert "csrf_rejected" in response.text


def test_upload_accepted_with_matching_referer_fallback(
    client: TestClient, settings: Settings
) -> None:
    response = _upload(
        client, settings, headers={"Referer": f"{SAME_ORIGIN}/ui/upload"}
    )
    assert response.status_code == 303


def test_upload_rejected_with_foreign_referer(client: TestClient, settings: Settings) -> None:
    response = _upload(
        client, settings, headers={"Referer": f"{FOREIGN_ORIGIN}/steal"}
    )
    assert response.status_code == 403


def test_upload_rejected_when_origin_and_referer_both_missing(
    client: TestClient, settings: Settings
) -> None:
    response = _upload(client, settings, headers={})
    assert response.status_code == 403


def test_origin_takes_precedence_over_referer(client: TestClient, settings: Settings) -> None:
    # Origin correcto pero Referer ajeno: Origin gobierna (orden exacto
    # exigido por el plan), asi que se acepta.
    response = _upload(
        client,
        settings,
        headers={"Origin": SAME_ORIGIN, "Referer": f"{FOREIGN_ORIGIN}/steal"},
    )
    assert response.status_code == 303


def test_resume_accepted_with_matching_origin(client: TestClient, settings: Settings) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.post(
        f"/ui/runs/{RUN_ID}/resume",
        headers={"Origin": SAME_ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_resume_rejected_with_foreign_origin(client: TestClient, settings: Settings) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.post(
        f"/ui/runs/{RUN_ID}/resume",
        headers={"Origin": FOREIGN_ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_resume_rejected_when_origin_and_referer_both_missing(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.post(f"/ui/runs/{RUN_ID}/resume", follow_redirects=False)
    assert response.status_code == 403


def test_csrf_rejection_message_is_sanitized(client: TestClient, settings: Settings) -> None:
    response = _upload(client, settings, headers={"Origin": FOREIGN_ORIGIN})
    assert response.status_code == 403
    assert FOREIGN_ORIGIN not in response.text
    assert "Traceback" not in response.text


def test_upload_rejected_with_malformed_origin(client: TestClient, settings: Settings) -> None:
    # "http://[::1" (IPv6 sin cerrar) hace que urlsplit levante ValueError
    # en la implementacion ingenua: debe rechazarse con 403, nunca 500.
    response = _upload(client, settings, headers={"Origin": "http://[::1"})
    assert response.status_code == 403


def test_upload_accepted_with_null_origin_and_same_origin_fetch_site(
    client: TestClient, settings: Settings
) -> None:
    # Caso real observado: navegacion same-origin (subida de archivo)
    # que el navegador etiqueta con Origin: null, respaldada por
    # Sec-Fetch-Site: same-origin. El POST multipart real avanza mas
    # alla del CSRF (303 = redirect tras aceptar y procesar el upload).
    response = _upload(
        client,
        settings,
        headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 303


def test_upload_rejected_with_null_origin_and_cross_site_fetch_site(
    client: TestClient, settings: Settings
) -> None:
    response = _upload(
        client,
        settings,
        headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403


def test_upload_rejected_with_null_origin_and_same_site_fetch_site(
    client: TestClient, settings: Settings
) -> None:
    response = _upload(
        client,
        settings,
        headers={"Origin": "null", "Sec-Fetch-Site": "same-site"},
    )
    assert response.status_code == 403


def test_upload_rejected_with_null_origin_and_no_fetch_site(
    client: TestClient, settings: Settings
) -> None:
    response = _upload(client, settings, headers={"Origin": "null"})
    assert response.status_code == 403


def test_upload_rejected_with_null_origin_and_none_fetch_site(
    client: TestClient, settings: Settings
) -> None:
    response = _upload(
        client,
        settings,
        headers={"Origin": "null", "Sec-Fetch-Site": "none"},
    )
    assert response.status_code == 403


def test_null_origin_not_saved_by_matching_referer(
    client: TestClient, settings: Settings
) -> None:
    # Regla 5: Referer no debe salvar un Origin: null con Sec-Fetch-Site
    # distinto de same-origin, ni siquiera si el Referer es legitimo.
    response = _upload(
        client,
        settings,
        headers={
            "Origin": "null",
            "Sec-Fetch-Site": "cross-site",
            "Referer": f"{SAME_ORIGIN}/ui/upload",
        },
    )
    assert response.status_code == 403
