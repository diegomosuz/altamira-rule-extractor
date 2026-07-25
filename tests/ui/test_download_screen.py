"""Pantalla descarga (Prompt 13d, seccion 19)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from altamira_extractor.config import Settings

from ..api.conftest import RUN_ID, build_run_completed, build_run_up_to_guardrails_applied


def test_download_requires_completed(client: TestClient, settings: Settings) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/download")
    assert response.status_code == 409


def test_download_shows_brief_info_and_button(client: TestClient, settings: Settings) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/download")
    assert response.status_code == 200
    assert RUN_ID in response.text
    assert "COMPLETED" in response.text
    assert "Descargar ZIP de reglas" in response.text


def test_download_page_never_lists_internal_filesystem(
    client: TestClient, settings: Settings
) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/download")
    assert str(settings.runs_dir) not in response.text
    assert "artifacts/09-guardrails" not in response.text
    assert "artifacts/06-candidates.json" not in response.text


def test_download_nonexistent_run_is_404(client: TestClient) -> None:
    response = client.get("/ui/runs/20260101T000000000000-ffffffff/download")
    assert response.status_code == 404


def test_actual_zip_download_via_existing_binary_endpoint(
    client: TestClient, settings: Settings
) -> None:
    import hashlib
    import io
    import zipfile

    from ..api.conftest import CANDIDATE_ID

    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    page = client.get(f"/ui/runs/{RUN_ID}/download")
    assert page.status_code == 200

    zip_response = client.get(f"/api/runs/{RUN_ID}/download")
    assert zip_response.status_code == 200
    assert zip_response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
        expected_md = hashlib.sha256(CANDIDATE_ID.encode("utf-8")).hexdigest() + ".md"
        assert set(archive.namelist()) == {"rules-manifest.json", expected_md}
