"""Reanudacion desde la UI (Prompt 13d, seccion 14/12)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from altamira_extractor.config import Settings

from ..api.conftest import RUN_ID, build_run_completed, build_run_up_to_guardrails_applied
from .conftest import SAME_ORIGIN


def test_resume_button_hidden_when_completed(client: TestClient, settings: Settings) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}")
    assert response.status_code == 200
    assert ">Reanudar<" not in response.text


def test_resume_button_shown_when_not_completed(client: TestClient, settings: Settings) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}")
    assert response.status_code == 200
    assert ">Reanudar<" in response.text
    assert f"/ui/runs/{RUN_ID}/resume" in response.text


def test_resume_redirects_to_status(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    response = client.post(
        f"/ui/runs/{RUN_ID}/resume",
        headers={"Origin": SAME_ORIGIN},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/ui/runs/{RUN_ID}"


def test_resume_completed_run_is_rejected_even_via_direct_post(
    client: TestClient, settings: Settings
) -> None:
    # COMPLETED oculta el boton, pero el servidor tambien lo rechaza si
    # se hace POST directo (defensa en profundidad, igual que la API).
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.post(
        f"/ui/runs/{RUN_ID}/resume", headers={"Origin": SAME_ORIGIN}
    )
    assert response.status_code == 409


def test_resume_nonexistent_run_is_404(client: TestClient) -> None:
    response = client.post(
        "/ui/runs/20260101T000000000000-ffffffff/resume",
        headers={"Origin": SAME_ORIGIN},
    )
    assert response.status_code == 404


def test_resume_missing_package_zip_is_sanitized_error(
    client: TestClient, settings: Settings
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    (run_dir / "input" / "package.zip").unlink()

    response = client.post(
        f"/ui/runs/{RUN_ID}/resume", headers={"Origin": SAME_ORIGIN}
    )
    assert response.status_code == 500
    assert str(settings.runs_dir) not in response.text


def test_resume_symlinked_package_zip_is_rejected(
    client: TestClient, settings: Settings
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    package_path = run_dir / "input" / "package.zip"
    real_target = run_dir / "input" / "real.zip"
    package_path.rename(real_target)
    try:
        os.symlink(real_target, package_path)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks no soportados en este entorno")

    response = client.post(
        f"/ui/runs/{RUN_ID}/resume", headers={"Origin": SAME_ORIGIN}
    )
    assert response.status_code == 500


def test_resume_does_not_ask_for_a_new_zip_upload(client: TestClient, settings: Settings) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}")
    assert 'type="file"' not in response.text
