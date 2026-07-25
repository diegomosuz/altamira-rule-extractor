"""POST /api/runs/{run_id}/resume (Prompt 13b)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from altamira_extractor.config import Settings

from .conftest import RUN_ID, build_run_completed, build_run_up_to_guardrails_applied


def test_resume_existing_run_returns_202(client: TestClient, settings: Settings) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.post(f"/api/runs/{RUN_ID}/resume")
    assert response.status_code == 202
    body = response.json()
    assert body["run_id"] == RUN_ID
    assert body["status"] == "accepted"


def test_resume_nonexistent_run_returns_404(client: TestClient) -> None:
    response = client.post("/api/runs/20260101T000000000000-ffffffff/resume")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


def test_resume_completed_run_returns_409(client: TestClient, settings: Settings) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.post(f"/api/runs/{RUN_ID}/resume")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_not_resumable"


def test_resume_missing_package_zip_returns_500(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    (run_dir / "input" / "package.zip").unlink()

    response = client.post(f"/api/runs/{RUN_ID}/resume")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "artifact_corrupted"


def test_resume_symlinked_package_zip_returns_500(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)
    package_path = run_dir / "input" / "package.zip"
    real_target = run_dir / "input" / "real.zip"
    package_path.rename(real_target)
    try:
        os.symlink(real_target, package_path)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks no soportados en este entorno")

    response = client.post(f"/api/runs/{RUN_ID}/resume")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "artifact_corrupted"


def test_resume_does_not_duplicate_stage_execution(client: TestClient, settings: Settings) -> None:
    # Ademas de _upsert_stage (ya probado en tests/pipeline/test_runner.py),
    # verifica que el endpoint no reconstruye la secuencia de etapas a
    # mano: la StageExecution GUARDRAILS_APPLIED sigue siendo unica
    # despues del resume (aunque el background real quede stubeado por
    # este test al no ejecutar run_ingestion de verdad -- el 202 en si
    # mismo no reescribe run.json).
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_guardrails_applied(run_dir, RUN_ID)

    client.post(f"/api/runs/{RUN_ID}/resume")

    detail = client.get(f"/api/runs/{RUN_ID}").json()
    stage_names = [s["stage"] for s in detail["stages"]]
    assert len(stage_names) == len(set(stage_names))
