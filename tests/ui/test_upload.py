"""Pantalla upload (Prompt 13d, seccion 12/9)."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient

from altamira_extractor.config import Settings

from ..e2e_support import write_disabled_dev_security_config
from ..pipeline.conftest import build_valid_package_zip
from .conftest import SAME_ORIGIN


def test_upload_form_shows_max_size(client: TestClient, settings: Settings) -> None:
    response = client.get("/ui/upload")
    assert response.status_code == 200
    expected_mb = round(settings.max_package_size_bytes / 1048576, 1)
    assert f"{expected_mb}" in response.text
    assert 'enctype="multipart/form-data"' in response.text
    assert 'type="file"' in response.text


def test_upload_valid_zip_redirects_to_status_303(
    client: TestClient, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(settings.data_dir.parent / "package.zip")
    with zip_path.open("rb") as fh:
        response = client.post(
            "/ui/runs",
            files={"file": ("package.zip", fh, "application/zip")},
            headers={"Origin": SAME_ORIGIN},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/ui/runs/")


def test_upload_valid_zip_with_htmx_header_uses_hx_redirect(
    client: TestClient, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(settings.data_dir.parent / "package.zip")
    with zip_path.open("rb") as fh:
        response = client.post(
            "/ui/runs",
            files={"file": ("package.zip", fh, "application/zip")},
            headers={"Origin": SAME_ORIGIN, "HX-Request": "true"},
            follow_redirects=False,
        )
    assert response.status_code == 200
    assert response.headers["hx-redirect"].startswith("/ui/runs/")


def test_upload_empty_file_shows_sanitized_error_on_form(
    client: TestClient, settings: Settings
) -> None:
    response = client.post(
        "/ui/runs",
        files={"file": ("package.zip", b"", "application/zip")},
        headers={"Origin": SAME_ORIGIN},
    )
    assert response.status_code == 400
    assert "upload" in response.text.lower()
    assert 'type="file"' in response.text  # el formulario se re-muestra


def test_upload_missing_file_field_is_usage_error(client: TestClient) -> None:
    response = client.post("/ui/runs", data={}, headers={"Origin": SAME_ORIGIN})
    assert response.status_code == 422


def test_upload_exceeds_limit_shows_sanitized_error(tmp_path: Path) -> None:
    from altamira_extractor.api.app import create_app

    settings = Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
        max_package_size_bytes=10,
        security_config_path=write_disabled_dev_security_config(tmp_path),
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/ui/runs",
            files={"file": ("package.zip", b"x" * 1000, "application/zip")},
            headers={"Origin": SAME_ORIGIN},
        )
    assert response.status_code == 413
    assert 'type="file"' in response.text


def test_upload_does_not_execute_pipeline_synchronously(
    client: TestClient, settings: Settings
) -> None:
    # No hay JAR/Neo4j en este test unitario: si create_run_ui bloqueara
    # ejecutando el pipeline completo dentro del request, este test
    # fallaria por timeout o por un traceback de infraestructura
    # ausente. Un 303 rapido confirma que solo RECEIVED corrio sincrono.
    zip_path = build_valid_package_zip(settings.data_dir.parent / "package.zip")
    with zip_path.open("rb") as fh:
        response = client.post(
            "/ui/runs",
            files={"file": ("package.zip", fh, "application/zip")},
            headers={"Origin": SAME_ORIGIN},
            follow_redirects=False,
        )
    assert response.status_code == 303


def test_upload_does_not_reimplement_zip_validator(
    client: TestClient, settings: Settings
) -> None:
    # ZIP invalido: se acepta la subida (RECEIVED) y el resto del
    # pipeline (real, via prepare_received/run_ingestion) es quien
    # decide el fallo -- la UI nunca reimplementa PackageValidator.
    garbage = io.BytesIO(b"esto no es un zip")
    response = client.post(
        "/ui/runs",
        files={"file": ("package.zip", garbage, "application/zip")},
        headers={"Origin": SAME_ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 303
